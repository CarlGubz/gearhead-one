"""Persistent face / edge references (mitigation of the topological naming problem).

``idx:N`` selectors break as soon as an upstream change renumbers the faces of a body. This
module gives every face a *provenance tag* while the feature tree is replayed, and stores
references as signatures that survive renumbering and moderate parameter changes.

Provenance
    ``(feature_id, k)``: the feature whose tool created the face and the index of that face in
    the tool. Faces untouched by a boolean keep their tag (same TShape); faces that were split
    or trimmed inherit the tag of the face whose surface they lie on.

Reference selector
    ``ref:[{...}, ...]`` - one signature per face / edge::

        {"k": "face", "o": ["f1", 5], "g": "PLANE", "c": [x,y,z], "n": [nx,ny,nz], "a": area, "r": radius}
        {"k": "edge", "o": [["f1",4],["f3",0]], "g": "CIRCLE", "c": midpoint, "l": length, "r": radius, "d": dir}

Resolution prefers candidates with the same provenance and picks the best geometric match among
them; when the origin no longer exists it falls back to the best geometric match over the whole
body (reported as "repaired") and only fails when nothing is reasonably close.
"""
from __future__ import annotations

import json
import math
import weakref
from typing import Optional

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Sphere
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

Tag = tuple[str, int]
FALLBACK_LIMIT = 1.0  # max geometric score accepted when the provenance match is gone

# provenance of finished bodies, so assemblies / simulations can resolve refs on a part's shape
_REGISTRY: "weakref.WeakKeyDictionary[cq.Shape, list[Tag]]" = weakref.WeakKeyDictionary()


def register(shape: Optional[cq.Shape], prov: list[Tag]) -> None:
    if shape is not None:
        try:
            _REGISTRY[shape] = prov
        except TypeError:
            pass


def provenance_of(shape: cq.Shape) -> Optional[list[Tag]]:
    try:
        return _REGISTRY.get(shape)
    except TypeError:
        return None


# ------------------------------------------------------------------ geometry
def _diag(shape: cq.Shape) -> float:
    return shape.BoundingBox().DiagonalLength or 1.0


def _point_normal(face: cq.Face, diag: float) -> tuple[cq.Vector, cq.Vector]:
    """A point strictly on the face (projected centroid of one mesh triangle) and the normal there."""
    p = None
    try:
        verts, tris = face.tessellate(max(diag * 0.02, 0.05), 0.5)
        if tris:
            a, b, c = (verts[i] for i in tris[len(tris) // 2])
            m = (a + b + c) * (1.0 / 3.0)
            proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(m.x, m.y, m.z), BRep_Tool.Surface_s(face.wrapped))
            if proj.NbPoints():
                q = proj.NearestPoint()
                p = cq.Vector(q.X(), q.Y(), q.Z())
    except Exception:
        p = None
    if p is None:
        p = face.Center()
    try:
        n = face.normalAt(p)
    except Exception:
        n = cq.Vector(0, 0, 1)
    return p, n


class _FaceIndex:
    """Bounding boxes + faces for fast 'does this point lie on one of these faces' queries."""

    def __init__(self, faces: list[cq.Face], tol: float):
        self.faces = faces
        self.tol = tol
        self.boxes = [f.BoundingBox() for f in faces]

    def find(self, p: cq.Vector, n: cq.Vector) -> Optional[int]:
        t = self.tol
        vtx = None
        for i, (f, bb) in enumerate(zip(self.faces, self.boxes)):
            if not (bb.xmin - t <= p.x <= bb.xmax + t and bb.ymin - t <= p.y <= bb.ymax + t and bb.zmin - t <= p.z <= bb.zmax + t):
                continue
            vtx = vtx or cq.Vertex.makeVertex(p.x, p.y, p.z)
            if vtx.distance(f) > t:
                continue
            try:
                if abs(f.normalAt(p).dot(n)) < 0.98:
                    continue
            except Exception:
                continue
            return i
        return None


class Tracker:
    """Keeps the provenance tag of every face of the body being rebuilt."""

    def __init__(self):
        self.prov: list[Tag] = []
        self.warnings: list[str] = []

    def reset(self, body: cq.Shape, fid: str) -> None:
        self.prov = [(fid, i) for i in range(len(body.Faces()))]

    def update(self, prev: Optional[cq.Shape], new: cq.Shape, fid: str, tools: list[cq.Shape] = ()) -> None:
        diag = _diag(new)
        tol = 1e-5 * diag + 1e-7
        prev_faces = prev.Faces() if prev is not None else []
        by_hash: dict[int, list[int]] = {}
        for i, f in enumerate(prev_faces):
            by_hash.setdefault(hash(f), []).append(i)
        tool_idx = _FaceIndex([f for t in tools for f in t.Faces()], tol) if tools else None
        prev_idx = _FaceIndex(prev_faces, tol) if prev_faces else None
        out: list[Tag] = []
        fresh = 0
        for f in new.Faces():
            same = next((i for i in by_hash.get(hash(f), []) if prev_faces[i].isSame(f)), None)
            if same is not None and same < len(self.prov):
                out.append(self.prov[same])
                continue
            p, n = _point_normal(f, diag)
            j = tool_idx.find(p, n) if tool_idx else None
            if j is not None:
                out.append((fid, j))
                continue
            i = prev_idx.find(p, n) if prev_idx else None
            if i is not None and i < len(self.prov):
                out.append(self.prov[i])
                continue
            out.append((fid, 1000 + fresh))  # genuinely new face (fillet / chamfer / shell wall ...)
            fresh += 1
        self.prov = out


# ----------------------------------------------------------------- signatures
def _r(v, nd=6):
    return [round(float(v.x), nd), round(float(v.y), nd), round(float(v.z), nd)]


def _face_extra(face: cq.Face) -> dict:
    try:
        ad = BRepAdaptor_Surface(face.wrapped)
        t = ad.GetType()
        if t == GeomAbs_Cylinder:
            c = ad.Cylinder()
            d = c.Axis().Direction()
            return {"r": round(c.Radius(), 6), "d": [round(d.X(), 6), round(d.Y(), 6), round(d.Z(), 6)]}
        if t == GeomAbs_Cone:
            return {"r": round(ad.Cone().RefRadius(), 6)}
        if t == GeomAbs_Sphere:
            return {"r": round(ad.Sphere().Radius(), 6)}
    except Exception:
        pass
    return {}


def face_signature(face: cq.Face, tag: Optional[Tag]) -> dict:
    c = face.Center()
    sig = {"k": "face", "o": list(tag) if tag else None, "g": face.geomType(), "c": _r(c), "a": round(face.Area(), 6)}
    if sig["g"] == "PLANE":
        try:
            sig["n"] = _r(face.normalAt(c))
        except Exception:
            pass
    sig.update(_face_extra(face))
    return sig


def _edge_faces(body: cq.Shape) -> tuple[TopTools_IndexedDataMapOfShapeListOfShape, dict[int, list[int]], list[cq.Face]]:
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(body.wrapped, TopAbs_EDGE, TopAbs_FACE, m)
    faces = body.Faces()
    by_hash: dict[int, list[int]] = {}
    for i, f in enumerate(faces):
        by_hash.setdefault(hash(f), []).append(i)
    return m, by_hash, faces


def _adjacent_tags(edge: cq.Edge, emap, by_hash, faces, prov) -> list:
    if prov is None or not emap.Contains(edge.wrapped):
        return []
    tags = set()
    for s in emap.FindFromKey(edge.wrapped):
        f = cq.Face(s)
        i = next((i for i in by_hash.get(hash(f), []) if faces[i].isSame(f)), None)
        if i is not None and i < len(prov):
            tags.add(tuple(prov[i]))
    return sorted([list(t) for t in tags])


def edge_signature(edge: cq.Edge, adjacent: list) -> dict:
    sig = {"k": "edge", "o": adjacent or None, "g": edge.geomType(), "c": _r(edge.positionAt(0.5)), "l": round(edge.Length(), 6)}
    try:
        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() == GeomAbs_Circle:
            sig["r"] = round(ad.Circle().Radius(), 6)
    except Exception:
        pass
    if sig["g"] == "LINE":
        d = (edge.endPoint() - edge.startPoint())
        if d.Length > 1e-12:
            sig["d"] = _r(d.normalized())
    return sig


def make_reference(body: cq.Shape, kind: str, indices: list[int], prov: Optional[list[Tag]] = None) -> str:
    """`ref:` selector for faces / edges of `body` given by index."""
    prov = prov if prov is not None else provenance_of(body)
    if kind == "face":
        faces = body.Faces()
        sigs = [face_signature(faces[i], prov[i] if prov and i < len(prov) else None) for i in indices]
    else:
        edges = body.Edges()
        emap, by_hash, faces = _edge_faces(body)
        sigs = [edge_signature(edges[i], _adjacent_tags(edges[i], emap, by_hash, faces, prov)) for i in indices]
    return "ref:" + json.dumps(sigs, separators=(",", ":"))


# ----------------------------------------------------------------- resolution
def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _score(want: dict, have: dict, diag: float) -> float:
    if want.get("g") != have.get("g"):
        return math.inf
    s = 4.0 * math.dist(want["c"], have["c"]) / diag
    for k in ("a", "l", "r"):
        if k in want and k in have:
            s += abs(want[k] - have[k]) / max(abs(want[k]), abs(have[k]), 1e-9)
    if "n" in want and "n" in have:
        s += 2.0 * (1.0 - _dot(want["n"], have["n"]))  # opposite normals are a poor match
    if "d" in want and "d" in have:
        s += 4.0 * (1.0 - abs(_dot(want["d"], have["d"])))
    return s


def resolve_reference(body: cq.Shape, spec: str, kind: str, prov: Optional[list[Tag]] = None,
                      warnings: Optional[list[str]] = None) -> list[cq.Shape]:
    sigs = json.loads(spec)
    if isinstance(sigs, dict):
        sigs = [sigs]
    prov = prov if prov is not None else provenance_of(body)
    diag = _diag(body)
    if kind == "face":
        items = body.Faces()
        cands = [face_signature(f, prov[i] if prov and i < len(prov) else None) for i, f in enumerate(items)]
    else:
        items = body.Edges()
        emap, by_hash, faces = _edge_faces(body)
        cands = [edge_signature(e, _adjacent_tags(e, emap, by_hash, faces, prov)) for e in items]
    out: list[cq.Shape] = []
    for want in sigs:
        if want.get("k", kind) != kind:
            raise ValueError(f"reference is a {want.get('k')}, a {kind} is needed here")
        same_origin = [i for i, c in enumerate(cands) if want.get("o") and c.get("o") == want["o"]]
        pool = same_origin or range(len(cands))
        best = min(pool, key=lambda i: _score(want, cands[i], diag), default=None)
        if best is None:
            raise ValueError(f"{kind} reference lost (body has no {kind}s)")
        score = _score(want, cands[best], diag)
        if not same_origin:
            if score > FALLBACK_LIMIT:
                origin = f" from feature {want['o'][0]}" if want.get("o") and isinstance(want["o"][0], str) else ""
                raise ValueError(f"{kind} reference{origin} lost: no {want.get('g', '').lower()} {kind} near {want['c']}")
            if warnings is not None:
                warnings.append(f"{kind} reference re-attached by geometry (its original {kind} no longer exists)")
        if not any(items[best].isSame(o) for o in out):
            out.append(items[best])
    return out
