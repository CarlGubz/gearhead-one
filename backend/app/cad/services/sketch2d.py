"""2D sketching kernel.

* entity -> OpenCascade edge conversion (built in the local XY plane, then moved
  onto the sketch plane when a 3D face is requested)
* loop detection (closed profiles), nesting (outer / holes) and face building
* shapely regions for area / offset / CAM
* a geometric constraint solver (scipy least-squares) with DOF reporting
* transform operations (move / rotate / mirror / patterns / offset)
* DXF import & export (ezdxf), SVG export

Entity schema (all coordinates are sketch-local, millimetres, angles in degrees):

    {"id": "e1", "type": "line",     "p1": [x, y], "p2": [x, y]}
    {"id": "e2", "type": "circle",   "c": [x, y], "r": 5}
    {"id": "e3", "type": "arc",      "c": [x, y], "r": 5, "start": 0, "end": 90}   # CCW
    {"id": "e4", "type": "rect",     "p1": [x, y], "p2": [x, y]}
    {"id": "e5", "type": "polyline", "points": [[x, y], ...], "closed": true}
    {"id": "e6", "type": "polygon",  "c": [x, y], "r": 10, "sides": 6, "rotation": 0}
    {"id": "e7", "type": "slot",     "p1": [x, y], "p2": [x, y], "width": 6}
    {"id": "e8", "type": "ellipse",  "c": [x, y], "rx": 10, "ry": 5, "rotation": 0}
    {"id": "e9", "type": "spline",   "points": [[x, y], ...], "closed": false}
    any entity may carry "construction": true  (ignored when building profiles)
    and "layer": "<name>" (display / DXF layer; see DEFAULT_LAYER)
"""
from __future__ import annotations

import io
import math
from typing import Any, Callable

import cadquery as cq
import numpy as np
from scipy.optimize import least_squares
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

CLUSTER_TOL = 1e-3  # endpoint merge tolerance (mm)

PLANES = {
    "XY": "XY",
    "YZ": "YZ",
    "XZ": "XZ",  # CadQuery convention: normal is -Y
    "ZX": "ZX",
}


DEFAULT_LAYER = {"name": "0", "color": None, "visible": True, "locked": False}


def normalise_layers(layers: list[dict] | None, entities: list[dict] | None = None) -> list[dict]:
    """Unique, complete layer records; layer "0" always exists and layers used by entities are added."""
    out: dict[str, dict] = {"0": dict(DEFAULT_LAYER)}
    for l in layers or []:
        name = str(l.get("name") or "").strip()
        if name:
            out[name] = {**DEFAULT_LAYER, **{k: l[k] for k in ("color", "visible", "locked") if k in l}, "name": name}
    for e in entities or []:
        if e.get("layer") and e["layer"] not in out:
            out[e["layer"]] = {**DEFAULT_LAYER, "name": e["layer"]}
    return list(out.values())


def V(x: float, y: float) -> cq.Vector:
    return cq.Vector(float(x), float(y), 0.0)


# --------------------------------------------------------------------------- ids
def ensure_ids(entities: list[dict]) -> list[dict]:
    used = {e.get("id") for e in entities if e.get("id")}
    n = 1
    for e in entities:
        if not e.get("id"):
            while f"e{n}" in used:
                n += 1
            e["id"] = f"e{n}"
            used.add(e["id"])
    return entities


# ------------------------------------------------------------- entity -> edges
def _regular_polygon_pts(c, r, sides, rot_deg):
    return [
        (
            c[0] + r * math.cos(math.radians(rot_deg) + 2 * math.pi * i / sides),
            c[1] + r * math.sin(math.radians(rot_deg) + 2 * math.pi * i / sides),
        )
        for i in range(sides)
    ]


def _poly_edges(pts: list, closed: bool) -> list[cq.Edge]:
    pts = [tuple(p) for p in pts]
    if closed and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        if math.dist(a, b) > 1e-9:
            out.append(cq.Edge.makeLine(V(*a), V(*b)))
    return out


def _arc_edge(c, r, a0_deg, a1_deg) -> cq.Edge:
    a0, a1 = math.radians(a0_deg), math.radians(a1_deg)
    while a1 <= a0:
        a1 += 2 * math.pi
    am = 0.5 * (a0 + a1)

    def p(a):
        return V(c[0] + r * math.cos(a), c[1] + r * math.sin(a))

    return cq.Edge.makeThreePointArc(p(a0), p(am), p(a1))


def entity_edges(e: dict) -> list[cq.Edge]:
    t = e["type"]
    if t == "line":
        return _poly_edges([e["p1"], e["p2"]], False)
    if t == "circle":
        return [cq.Edge.makeCircle(float(e["r"]), V(*e["c"]), cq.Vector(0, 0, 1))]
    if t == "arc":
        return [_arc_edge(e["c"], float(e["r"]), float(e["start"]), float(e["end"]))]
    if t == "rect":
        (x1, y1), (x2, y2) = e["p1"], e["p2"]
        return _poly_edges([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True)
    if t == "polyline":
        return _poly_edges(e["points"], bool(e.get("closed", False)))
    if t == "polygon":
        return _poly_edges(
            _regular_polygon_pts(e["c"], float(e["r"]), int(e.get("sides", 6)), float(e.get("rotation", 0))), True
        )
    if t == "slot":
        (x1, y1), (x2, y2) = e["p1"], e["p2"]
        w = float(e["width"]) / 2
        ang = math.atan2(y2 - y1, x2 - x1)
        nx, ny = -math.sin(ang) * w, math.cos(ang) * w
        a_deg = math.degrees(ang)
        edges = [
            cq.Edge.makeLine(V(x1 + nx, y1 + ny), V(x2 + nx, y2 + ny)),
            _arc_edge((x2, y2), w, a_deg - 90, a_deg + 90),
            cq.Edge.makeLine(V(x2 - nx, y2 - ny), V(x1 - nx, y1 - ny)),
            _arc_edge((x1, y1), w, a_deg + 90, a_deg + 270),
        ]
        return edges
    if t == "ellipse":
        rx, ry = float(e["rx"]), float(e["ry"])
        rot = math.radians(float(e.get("rotation", 0)))
        major, minor, xd = (rx, ry, rot) if rx >= ry else (ry, rx, rot + math.pi / 2)
        return [
            cq.Edge.makeEllipse(
                major, minor, V(*e["c"]), cq.Vector(0, 0, 1), cq.Vector(math.cos(xd), math.sin(xd), 0), 360.0, 360.0
            )
        ]
    if t == "spline":
        pts = [V(*p) for p in e["points"]]
        closed = bool(e.get("closed", False))
        if closed and len(pts) > 2:
            return [cq.Edge.makeSpline(pts, periodic=True)]
        return [cq.Edge.makeSpline(pts)]
    if t == "point":
        return []
    raise ValueError(f"unknown sketch entity type: {t}")


# ------------------------------------------------------------------ loops
def _sample_edge(edge: cq.Edge, reverse: bool = False) -> list[tuple[float, float]]:
    gt = edge.geomType()
    if gt == "LINE":
        n = 2
    elif gt == "CIRCLE":
        n = max(12, min(96, int(edge.Length() / 0.5)))
    else:
        n = max(16, min(160, int(edge.Length() / 0.5)))
    pts = []
    for i in range(n + 1):
        p = edge.positionAt(i / n)
        pts.append((p.x, p.y))
    return pts[::-1] if reverse else pts


class _Loops:
    def __init__(self):
        self.wires: list[cq.Wire] = []
        self.polys: list[Polygon] = []
        self.polylines: list[list[tuple[float, float]]] = []
        self.open_chains: list[list[tuple[float, float]]] = []
        self.warnings: list[str] = []


def build_loops(entities: list[dict]) -> _Loops:
    """Group the non-construction entities into closed loops (OCC wires + polygons)."""
    out = _Loops()
    edges: list[cq.Edge] = []
    for e in entities:
        if e.get("construction"):
            continue
        try:
            edges.extend(entity_edges(e))
        except Exception as exc:  # bad entity -> skip with warning
            out.warnings.append(f"entity {e.get('id')}: {exc}")
    if not edges:
        return out

    # cluster endpoints so snapping noise does not break closure
    reps: list[tuple[float, float]] = []

    def key(p) -> int:
        for i, r in enumerate(reps):
            if abs(r[0] - p.x) < CLUSTER_TOL and abs(r[1] - p.y) < CLUSTER_TOL:
                return i
        reps.append((p.x, p.y))
        return len(reps) - 1

    info = [(ed, key(ed.startPoint()), key(ed.endPoint())) for ed in edges]

    # union-find on endpoint clusters -> connected components of edges
    parent = list(range(len(reps)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for _, a, b in info:
        parent[find(a)] = find(b)
    comps: dict[int, list[int]] = {}
    for i, (_, a, _) in enumerate(info):
        comps.setdefault(find(a), []).append(i)

    for idxs in comps.values():
        deg: dict[int, int] = {}
        for i in idxs:
            _, a, b = info[i]
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        closed = all(d % 2 == 0 for d in deg.values()) and all(d == 2 for d in deg.values())
        # ordered chain
        remaining = set(idxs)
        first = idxs[0]
        chain = [(first, False)]
        remaining.discard(first)
        cur = info[first][2]
        start = info[first][1]
        while remaining and cur is not None:
            nxt = None
            for i in list(remaining):
                _, a, b = info[i]
                if a == cur:
                    nxt = (i, False)
                    cur = b
                    break
                if b == cur:
                    nxt = (i, True)
                    cur = a
                    break
            if nxt is None:
                break
            chain.append(nxt)
            remaining.discard(nxt[0])
        pts: list[tuple[float, float]] = []
        for i, rev in chain:
            s = _sample_edge(info[i][0], rev)
            pts.extend(s if not pts else s[1:])
        if closed and not remaining and len(pts) >= 3:
            try:
                wire = cq.Wire.assembleEdges([info[i][0] for i in idxs])
                poly = Polygon(pts)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.area < 1e-9:
                    out.warnings.append("degenerate loop ignored")
                    continue
                out.wires.append(wire)
                out.polys.append(poly)
                out.polylines.append(pts)
            except Exception as exc:
                out.warnings.append(f"could not build wire: {exc}")
        else:
            out.open_chains.append(pts)
    if out.open_chains:
        out.warnings.append(f"{len(out.open_chains)} open chain(s) are not closed and are ignored for profiles")
    return out


def classify_loops(polys: list[Polygon]) -> list[tuple[int, list[int]]]:
    """Return [(outer_index, [hole_indices])] using even-odd nesting depth."""
    n = len(polys)
    depth = [0] * n
    parent = [-1] * n
    for i in range(n):
        containers = [j for j in range(n) if j != i and polys[j].contains(polys[i])]
        depth[i] = len(containers)
        if containers:
            parent[i] = max(containers, key=lambda j: depth_of(polys, j))  # deepest container
    result = []
    for i in range(n):
        if depth[i] % 2 == 0:
            holes = [j for j in range(n) if parent[j] == i and depth[j] % 2 == 1]
            result.append((i, holes))
    return result


def depth_of(polys: list[Polygon], j: int) -> int:
    return sum(1 for k in range(len(polys)) if k != j and polys[k].contains(polys[j]))


def sketch_region(entities: list[dict]):
    """Shapely geometry (Polygon / MultiPolygon) of the filled sketch profile."""
    loops = build_loops(entities)
    parts = []
    for oi, holes in classify_loops(loops.polys):
        parts.append(Polygon(loops.polylines[oi], [loops.polylines[h] for h in holes]).buffer(0))
    if not parts:
        return Polygon()
    return unary_union(parts)


def plane_of(name: str, offset: float = 0.0) -> cq.Plane:
    name = PLANES.get(name.upper(), "XY")
    pl = cq.Plane.named(name)
    if offset:
        pl = cq.Plane(origin=pl.zDir * offset, xDir=pl.xDir, normal=pl.zDir)
    return pl


def profile_faces(entities: list[dict], plane_name: str = "XY", offset: float = 0.0) -> list[cq.Face]:
    """OCC faces (outer boundary with holes) placed on the sketch plane in 3D."""
    loops = build_loops(entities)
    if not loops.wires:
        raise ValueError("sketch has no closed profile")
    pl = plane_of(plane_name, offset)
    faces = []
    for oi, holes in classify_loops(loops.polys):
        f = cq.Face.makeFromWires(loops.wires[oi], [loops.wires[h] for h in holes])
        faces.append(f.transformShape(pl.rG))
    return faces


def path_wire(entities: list[dict], plane_name: str = "XY", offset: float = 0.0) -> cq.Wire:
    """First chain (open or closed) of the sketch as a 3D wire; used for sweep paths."""
    edges: list[cq.Edge] = []
    for e in entities:
        if not e.get("construction"):
            edges.extend(entity_edges(e))
    if not edges:
        raise ValueError("path sketch is empty")
    wire = cq.Wire.assembleEdges(edges)
    return wire.transformShape(plane_of(plane_name, offset).rG)


def analyse(entities: list[dict]) -> dict:
    loops = build_loops(entities)
    region = sketch_region(entities)
    bounds = None
    xs, ys = [], []
    for e in entities:
        try:
            for ed in entity_edges(e):
                for p in _sample_edge(ed):
                    xs.append(p[0])
                    ys.append(p[1])
        except Exception:
            pass
        if e["type"] == "point":
            xs.append(e["p"][0])
            ys.append(e["p"][1])
    if xs:
        bounds = [min(xs), min(ys), max(xs), max(ys)]
    return {
        "closed_loops": len(loops.wires),
        "open_chains": len(loops.open_chains),
        "area": float(region.area),
        "perimeter": float(sum(Polygon(p).length for p in loops.polylines)),
        "centroid": list(region.centroid.coords[0]) if not region.is_empty else None,
        "bounds": bounds,
        "warnings": loops.warnings,
        "valid_profile": len(loops.wires) > 0 and not loops.open_chains,
    }


def render_polylines(entities: list[dict]) -> list[dict]:
    """Sampled polylines for lightweight display clients."""
    out = []
    for e in entities:
        try:
            pts: list[tuple[float, float]] = []
            for ed in entity_edges(e):
                s = _sample_edge(ed)
                pts.extend(s if not pts else s[1:])
            out.append({"id": e["id"], "points": pts, "construction": bool(e.get("construction"))})
        except Exception:
            pass
    return out


# ============================================================ constraint solver
class _Layout:
    def __init__(self, entities: list[dict]):
        self.idx: dict[str, dict[str, int]] = {}
        self.types: dict[str, str] = {}
        x: list[float] = []
        for e in entities:
            d: dict[str, int] = {}
            t = e["type"]

            def add(name, vals):
                d[name] = len(x)
                x.extend(float(v) for v in vals)

            if t in ("line", "rect"):
                add("p1", e["p1"])
                add("p2", e["p2"])
            elif t == "point":
                add("p", e["p"])
            elif t == "circle":
                add("c", e["c"])
                add("r", [e["r"]])
            elif t == "arc":
                add("c", e["c"])
                add("r", [e["r"]])
                add("a0", [math.radians(e["start"])])
                add("a1", [math.radians(e["end"])])
            elif t == "polyline":
                for i, p in enumerate(e["points"]):
                    add(f"p{i}", p)
            elif t == "polygon":
                add("c", e["c"])
                add("r", [e["r"]])
            elif t == "ellipse":
                add("c", e["c"])
            elif t == "slot":
                add("p1", e["p1"])
                add("p2", e["p2"])
                add("w", [e["width"]])
            self.idx[e["id"]] = d
            self.types[e["id"]] = t
        self.x0 = np.array(x, dtype=float)


def _pt(L: _Layout, x: np.ndarray, ref: str) -> np.ndarray:
    eid, _, key = ref.partition(".")
    d = L.idx[eid]
    t = L.types[eid]
    if key in ("start", "end") and t == "arc":
        c = x[d["c"] : d["c"] + 2]
        r = x[d["r"]]
        a = x[d["a0" if key == "start" else "a1"]]
        return c + r * np.array([math.cos(a), math.sin(a)])
    if key == "mid" and t == "line":
        return 0.5 * (x[d["p1"] : d["p1"] + 2] + x[d["p2"] : d["p2"] + 2])
    if key == "":
        key = "p1" if "p1" in d else ("c" if "c" in d else "p")
    if key not in d:
        raise KeyError(f"unknown point reference '{ref}'")
    return x[d[key] : d[key] + 2]


def _line_pts(L, x, eid):
    d = L.idx[eid]
    return x[d["p1"] : d["p1"] + 2], x[d["p2"] : d["p2"] + 2]


def _radius(L, x, eid):
    return x[L.idx[eid]["r"]]


def _center(L, x, eid):
    d = L.idx[eid]
    return x[d["c"] : d["c"] + 2]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def _residuals(L: _Layout, constraints: list[dict], x: np.ndarray) -> np.ndarray:
    r: list[float] = []
    for c in constraints:
        t = c["type"]
        a, b = c.get("a"), c.get("b")
        v = c.get("value")
        if t in ("horizontal", "vertical"):
            k = 1 if t == "horizontal" else 0
            if b is None:
                p, q = _line_pts(L, x, a)
            else:
                p, q = _pt(L, x, a), _pt(L, x, b)
            r.append(p[k] - q[k])
        elif t == "coincident":
            r.extend(_pt(L, x, a) - _pt(L, x, b))
        elif t == "distance":
            if b is None:
                p, q = _line_pts(L, x, a)
            else:
                p, q = _pt(L, x, a), _pt(L, x, b)
            r.append(np.linalg.norm(p - q) - float(v))
        elif t in ("horizontal_distance", "vertical_distance"):
            k = 0 if t == "horizontal_distance" else 1
            r.append((_pt(L, x, b)[k] - _pt(L, x, a)[k]) - float(v))
        elif t == "fixed":
            r.extend(_pt(L, x, a) - np.array(v, dtype=float))
        elif t == "radius":
            r.append(_radius(L, x, a) - float(v))
        elif t == "diameter":
            r.append(2 * _radius(L, x, a) - float(v))
        elif t in ("parallel", "perpendicular", "angle"):
            p1, p2 = _line_pts(L, x, a)
            q1, q2 = _line_pts(L, x, b)
            da, db = _unit(p2 - p1), _unit(q2 - q1)
            if t == "parallel":
                r.append(_cross(da, db))
            elif t == "perpendicular":
                r.append(float(np.dot(da, db)))
            else:
                ang = math.atan2(_cross(da, db), float(np.dot(da, db)))
                r.append(_wrap(ang - math.radians(float(v))))
        elif t == "equal":
            if L.types[a] in ("circle", "arc") and L.types[b] in ("circle", "arc"):
                r.append(_radius(L, x, a) - _radius(L, x, b))
            else:
                p1, p2 = _line_pts(L, x, a)
                q1, q2 = _line_pts(L, x, b)
                r.append(np.linalg.norm(p2 - p1) - np.linalg.norm(q2 - q1))
        elif t == "tangent":
            ta, tb = L.types[a], L.types[b]
            if ta == "line" and tb in ("circle", "arc"):
                p1, p2 = _line_pts(L, x, a)
                d = _unit(p2 - p1)
                r.append(abs(_cross(d, _center(L, x, b) - p1)) - _radius(L, x, b))
            elif tb == "line" and ta in ("circle", "arc"):
                p1, p2 = _line_pts(L, x, b)
                d = _unit(p2 - p1)
                r.append(abs(_cross(d, _center(L, x, a) - p1)) - _radius(L, x, a))
            else:
                dist = np.linalg.norm(_center(L, x, a) - _center(L, x, b))
                ra, rb = _radius(L, x, a), _radius(L, x, b)
                r.append(dist - (abs(ra - rb) if c.get("internal") else ra + rb))
        elif t == "concentric":
            r.extend(_center(L, x, a) - _center(L, x, b))
        elif t == "point_on_line":
            p1, p2 = _line_pts(L, x, b)
            r.append(_cross(_unit(p2 - p1), _pt(L, x, a) - p1))
        elif t == "point_on_circle":
            r.append(np.linalg.norm(_pt(L, x, a) - _center(L, x, b)) - _radius(L, x, b))
        elif t == "midpoint":
            p1, p2 = _line_pts(L, x, b)
            r.extend(_pt(L, x, a) - 0.5 * (p1 + p2))
        elif t == "symmetric":  # a,b points symmetric about line c
            p1, p2 = _line_pts(L, x, c["c"])
            d = _unit(p2 - p1)
            pa, pb = _pt(L, x, a), _pt(L, x, b)
            r.append(_cross(d, 0.5 * (pa + pb) - p1))
            r.append(float(np.dot(d, pb - pa)))
        else:
            raise ValueError(f"unknown constraint type '{t}'")
    return np.array(r, dtype=float)


def _write_back(L: _Layout, entities: list[dict], x: np.ndarray) -> list[dict]:
    out = []
    for e in entities:
        e = dict(e)
        d = L.idx[e["id"]]
        t = e["type"]

        def g2(k):
            return [float(x[d[k]]), float(x[d[k] + 1])]

        if t in ("line", "rect"):
            e["p1"], e["p2"] = g2("p1"), g2("p2")
        elif t == "point":
            e["p"] = g2("p")
        elif t == "circle":
            e["c"], e["r"] = g2("c"), float(x[d["r"]])
        elif t == "arc":
            e["c"], e["r"] = g2("c"), float(x[d["r"]])
            e["start"], e["end"] = math.degrees(x[d["a0"]]), math.degrees(x[d["a1"]])
        elif t == "polyline":
            e["points"] = [g2(f"p{i}") for i in range(len(e["points"]))]
        elif t == "polygon":
            e["c"], e["r"] = g2("c"), float(x[d["r"]])
        elif t == "ellipse":
            e["c"] = g2("c")
        elif t == "slot":
            e["p1"], e["p2"], e["width"] = g2("p1"), g2("p2"), float(x[d["w"]])
        out.append(e)
    return out


def solve_constraints(entities: list[dict], constraints: list[dict]) -> dict:
    entities = ensure_ids([dict(e) for e in entities])
    L = _Layout(entities)
    n = len(L.x0)
    if n == 0:
        return {"entities": entities, "dof": 0, "status": "empty", "max_residual": 0.0, "iterations": 0}
    if not constraints:
        return {"entities": entities, "dof": n, "status": "unconstrained", "max_residual": 0.0, "iterations": 0}
    try:
        res = least_squares(
            lambda x: _residuals(L, constraints, x), L.x0, method="trf", xtol=1e-13, ftol=1e-13, gtol=1e-13, max_nfev=400
        )
    except (KeyError, ValueError) as exc:
        return {"entities": entities, "dof": n, "status": "error", "message": str(exc), "max_residual": None}
    max_res = float(np.max(np.abs(res.fun))) if len(res.fun) else 0.0
    sv = np.linalg.svd(res.jac, compute_uv=False) if res.jac.size else np.array([])
    rank = int(np.sum(sv > 1e-7 * (sv.max() if sv.size else 1.0)))
    dof = n - rank
    if max_res > 1e-4:
        status = "conflict"  # over-constrained or contradictory dimensions
    elif dof == 0:
        status = "fully_constrained"
    else:
        status = "solved"
    new_entities = _write_back(L, entities, res.x) if status != "conflict" else entities
    return {
        "entities": new_entities,
        "dof": dof,
        "status": status,
        "max_residual": max_res,
        "iterations": int(res.nfev),
    }


# ============================================================== transform ops
def _tf_entity(e: dict, fp: Callable, fang: Callable, mirrored: bool, scale: float, new_id: str) -> dict:
    e = {k: (list(v) if isinstance(v, list) else v) for k, v in e.items()}
    e["id"] = new_id
    t = e["type"]
    if t in ("line",):
        e["p1"], e["p2"] = list(fp(e["p1"])), list(fp(e["p2"]))
    elif t == "rect":
        (x1, y1), (x2, y2) = e["p1"], e["p2"]
        pts = [fp((x1, y1)), fp((x2, y1)), fp((x2, y2)), fp((x1, y2))]
        # stays an axis aligned rect only when the transform maps axes to axes
        axis_aligned = all(abs(pts[0][0] - pts[1][0]) < 1e-9 or abs(pts[0][1] - pts[1][1]) < 1e-9 for _ in [0])
        if axis_aligned:
            e["p1"], e["p2"] = list(pts[0]), list(pts[2])
        else:
            e = {"id": new_id, "type": "polyline", "points": [list(p) for p in pts], "closed": True}
    elif t == "point":
        e["p"] = list(fp(e["p"]))
    elif t in ("circle", "polygon"):
        e["c"], e["r"] = list(fp(e["c"])), e["r"] * scale
        if t == "polygon":
            e["rotation"] = fang(e.get("rotation", 0))
    elif t == "arc":
        e["c"], e["r"] = list(fp(e["c"])), e["r"] * scale
        a0, a1 = fang(e["start"]), fang(e["end"])
        e["start"], e["end"] = (a1, a0) if mirrored else (a0, a1)
    elif t in ("polyline", "spline"):
        e["points"] = [list(fp(p)) for p in e["points"]]
    elif t == "slot":
        e["p1"], e["p2"], e["width"] = list(fp(e["p1"])), list(fp(e["p2"])), e["width"] * scale
    elif t == "ellipse":
        e["c"] = list(fp(e["c"]))
        e["rx"], e["ry"] = e["rx"] * scale, e["ry"] * scale
        e["rotation"] = fang(e.get("rotation", 0))
    return e


def transform_entities(entities: list[dict], op: dict) -> list[dict]:
    """Apply a geometric operation and return NEW entities (originals are untouched).

    ops: translate{dx,dy} | rotate{angle,center} | mirror{p1,p2} | scale{factor,center}
         linear_pattern{count,dx,dy} | circular_pattern{count,angle,center}
         offset{distance}
    """
    kind = op["op"]
    ids = op.get("ids")
    src = [e for e in entities if (ids is None or e["id"] in ids)]
    used = {e["id"] for e in entities}
    counter = [1]

    def nid() -> str:
        while f"e{counter[0]}" in used:
            counter[0] += 1
        used.add(f"e{counter[0]}")
        return f"e{counter[0]}"

    def rigid(fp, fang, mirrored=False, scale=1.0):
        return [_tf_entity(e, fp, fang, mirrored, scale, nid()) for e in src]

    if kind == "translate":
        dx, dy = float(op.get("dx", 0)), float(op.get("dy", 0))
        return rigid(lambda p: (p[0] + dx, p[1] + dy), lambda a: a)
    if kind == "rotate":
        th = math.radians(float(op["angle"]))
        cx, cy = op.get("center", [0, 0])
        co, si = math.cos(th), math.sin(th)
        return rigid(
            lambda p: (cx + (p[0] - cx) * co - (p[1] - cy) * si, cy + (p[0] - cx) * si + (p[1] - cy) * co),
            lambda a: a + float(op["angle"]),
        )
    if kind == "scale":
        f = float(op["factor"])
        cx, cy = op.get("center", [0, 0])
        return rigid(lambda p: (cx + (p[0] - cx) * f, cy + (p[1] - cy) * f), lambda a: a, False, f)
    if kind == "mirror":
        (x1, y1), (x2, y2) = op["p1"], op["p2"]
        ang = math.atan2(y2 - y1, x2 - x1)
        c2, s2 = math.cos(2 * ang), math.sin(2 * ang)

        def fp(p):
            dx, dy = p[0] - x1, p[1] - y1
            return (x1 + dx * c2 + dy * s2, y1 + dx * s2 - dy * c2)

        return rigid(fp, lambda a: 2 * math.degrees(ang) - a, True)
    if kind == "linear_pattern":
        out: list[dict] = []
        for i in range(1, int(op["count"])):
            dx, dy = float(op["dx"]) * i, float(op["dy"]) * i
            out += [_tf_entity(e, lambda p, dx=dx, dy=dy: (p[0] + dx, p[1] + dy), lambda a: a, False, 1.0, nid()) for e in src]
        return out
    if kind == "circular_pattern":
        cx, cy = op.get("center", [0, 0])
        count = int(op["count"])
        total = float(op.get("angle", 360))
        step = total / count if abs(total - 360) < 1e-9 else total / max(count - 1, 1)
        out = []
        for i in range(1, count):
            a = step * i
            th = math.radians(a)
            co, si = math.cos(th), math.sin(th)
            out += [
                _tf_entity(
                    e,
                    lambda p, co=co, si=si: (cx + (p[0] - cx) * co - (p[1] - cy) * si, cy + (p[0] - cx) * si + (p[1] - cy) * co),
                    lambda ang, a=a: ang + a,
                    False,
                    1.0,
                    nid(),
                )
                for e in src
            ]
        return out
    if kind == "offset":
        d = float(op["distance"])
        region = sketch_region(src)
        shape = region.buffer(d, join_style=2 if op.get("sharp") else 1)
        polys = [shape] if isinstance(shape, Polygon) else list(shape.geoms) if not shape.is_empty else []
        out = []
        for pg in polys:
            out.append({"id": nid(), "type": "polyline", "points": [list(p) for p in pg.exterior.coords[:-1]], "closed": True})
            for h in pg.interiors:
                out.append({"id": nid(), "type": "polyline", "points": [list(p) for p in h.coords[:-1]], "closed": True})
        return out
    raise ValueError(f"unknown sketch op '{kind}'")


# ================================================================ DXF / SVG
def _bulge_arc(p1, p2, bulge):
    theta = 4 * math.atan(bulge)
    chord = math.dist(p1, p2)
    r = abs(chord / (2 * math.sin(theta / 2)))
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    h = math.sqrt(max(r * r - (chord / 2) ** 2, 0.0))
    ux, uy = (p2[0] - p1[0]) / chord, (p2[1] - p1[1]) / chord
    nx, ny = -uy, ux
    sgn = 1 if bulge > 0 else -1
    big = 1 if abs(theta) > math.pi else -1
    cx, cy = mx + sgn * big * -1 * nx * h * -1, my + sgn * big * -1 * ny * h * -1
    # choose centre so that both end points lie on circle and the sweep direction matches the bulge sign
    cands = [(mx + nx * h, my + ny * h), (mx - nx * h, my - ny * h)]

    def sweep_ok(c):
        a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
        a2 = math.atan2(p2[1] - c[1], p2[0] - c[0])
        d = (a2 - a1) % (2 * math.pi) if bulge > 0 else -((a1 - a2) % (2 * math.pi))
        return abs(abs(d) - abs(theta)) < 1e-6

    cx, cy = next((c for c in cands if sweep_ok(c)), cands[0])
    a1 = math.degrees(math.atan2(p1[1] - cy, p1[0] - cx))
    a2 = math.degrees(math.atan2(p2[1] - cy, p2[0] - cx))
    return {"type": "arc", "c": [cx, cy], "r": r, "start": a1, "end": a2} if bulge > 0 else {
        "type": "arc", "c": [cx, cy], "r": r, "start": a2, "end": a1
    }


def import_dxf(data: bytes, with_layers: bool = False):
    """DXF modelspace -> entities (each tagged with its DXF layer). with_layers=True also returns the layer table."""
    import ezdxf
    from ezdxf import colors as dxf_colors

    text = data.decode("utf-8", errors="ignore")
    doc = ezdxf.read(io.StringIO(text))
    out: list[dict] = []
    for e in doc.modelspace():
        t = e.dxftype()
        n_before = len(out)
        if t == "LINE":
            out.append({"type": "line", "p1": [e.dxf.start.x, e.dxf.start.y], "p2": [e.dxf.end.x, e.dxf.end.y]})
        elif t == "CIRCLE":
            out.append({"type": "circle", "c": [e.dxf.center.x, e.dxf.center.y], "r": e.dxf.radius})
        elif t == "ARC":
            out.append(
                {"type": "arc", "c": [e.dxf.center.x, e.dxf.center.y], "r": e.dxf.radius,
                 "start": e.dxf.start_angle, "end": e.dxf.end_angle}
            )
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1], p[4] if len(p) > 4 else 0.0) for p in e.get_points("xyseb")]
            closed = bool(e.closed)
            if any(abs(p[2]) > 1e-9 for p in pts):
                n = len(pts)
                last = n if closed else n - 1
                for i in range(last):
                    a, b = pts[i], pts[(i + 1) % n]
                    if abs(a[2]) > 1e-9 and math.dist(a[:2], b[:2]) > 1e-9:
                        out.append(_bulge_arc(a[:2], b[:2], a[2]))
                    else:
                        out.append({"type": "line", "p1": list(a[:2]), "p2": list(b[:2])})
            else:
                out.append({"type": "polyline", "points": [[p[0], p[1]] for p in pts], "closed": closed})
        elif t == "POLYLINE" and not e.is_3d_polyline and not e.is_polygon_mesh:
            pts = [[v.dxf.location.x, v.dxf.location.y] for v in e.vertices]
            out.append({"type": "polyline", "points": pts, "closed": bool(e.is_closed)})
        elif t == "ELLIPSE":
            ax = e.dxf.major_axis
            rx = math.hypot(ax.x, ax.y)
            full = abs((e.dxf.end_param - e.dxf.start_param) % (2 * math.pi)) < 1e-9 or abs(e.dxf.end_param - e.dxf.start_param - 2 * math.pi) < 1e-9
            if full:
                out.append(
                    {"type": "ellipse", "c": [e.dxf.center.x, e.dxf.center.y], "rx": rx, "ry": rx * e.dxf.ratio,
                     "rotation": math.degrees(math.atan2(ax.y, ax.x))}
                )
            else:
                pts = [[v.x, v.y] for v in e.flattening(0.05)]
                out.append({"type": "polyline", "points": pts, "closed": False})
        elif t == "SPLINE":
            pts = [[v.x, v.y] for v in e.flattening(0.05)]
            if len(pts) >= 2:
                out.append({"type": "polyline", "points": pts, "closed": bool(e.closed)})
        layer = e.dxf.get("layer", "0")
        for x in out[n_before:]:
            if layer == "CONSTRUCTION":
                x["construction"] = True
            elif layer != "0":
                x["layer"] = layer
    ensure_ids(out)
    if not with_layers:
        return out
    layers = []
    for l in doc.layers:
        name = l.dxf.name
        if name in ("0", "CONSTRUCTION", "Defpoints"):
            continue
        rgb = l.rgb if l.rgb is not None else dxf_colors.aci2rgb(abs(l.color) or 7)
        color = "#%02x%02x%02x" % tuple(rgb)
        layers.append({"name": name, "color": None if color in ("#ffffff", "#000000") else color,
                       "visible": l.is_on(), "locked": l.is_locked()})
    return out, normalise_layers(layers, out)


def export_dxf(entities: list[dict], layers: list[dict] | None = None) -> str:
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = 4  # millimetres
    for l in normalise_layers(layers, entities):
        if l["name"] == "0":
            continue
        lay = doc.layers.add(l["name"])
        if l.get("color"):
            c = l["color"].lstrip("#")
            lay.rgb = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
        if not l.get("visible", True):
            lay.off()
        if l.get("locked"):
            lay.lock()
    if any(e.get("construction") and not e.get("layer") for e in entities):
        doc.layers.add("CONSTRUCTION", linetype="DASHED" if "DASHED" in doc.linetypes else "Continuous")
    msp = doc.modelspace()
    for e in entities:
        t = e["type"]
        attrs = {"layer": e.get("layer") or ("CONSTRUCTION" if e.get("construction") else "0")}
        if t == "line":
            msp.add_line(tuple(e["p1"]), tuple(e["p2"]), dxfattribs=attrs)
        elif t == "circle":
            msp.add_circle(tuple(e["c"]), e["r"], dxfattribs=attrs)
        elif t == "arc":
            msp.add_arc(tuple(e["c"]), e["r"], e["start"], e["end"], dxfattribs=attrs)
        elif t == "rect":
            (x1, y1), (x2, y2) = e["p1"], e["p2"]
            msp.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True, dxfattribs=attrs)
        elif t == "polyline":
            msp.add_lwpolyline([tuple(p) for p in e["points"]], close=bool(e.get("closed")), dxfattribs=attrs)
        elif t == "polygon":
            msp.add_lwpolyline(_regular_polygon_pts(e["c"], e["r"], int(e.get("sides", 6)), e.get("rotation", 0)),
                               close=True, dxfattribs=attrs)
        elif t == "ellipse":
            rot = math.radians(e.get("rotation", 0))
            rx, ry = e["rx"], e["ry"]
            major = (rx * math.cos(rot), rx * math.sin(rot), 0)
            msp.add_ellipse((e["c"][0], e["c"][1], 0), major_axis=major, ratio=ry / rx, dxfattribs=attrs)
        elif t == "spline":
            msp.add_spline(fit_points=[(p[0], p[1], 0) for p in e["points"]], dxfattribs=attrs)
        elif t == "slot":
            for ed in entity_edges(e):
                pts = _sample_edge(ed)
                msp.add_lwpolyline(pts, dxfattribs=attrs)
        elif t == "point":
            msp.add_point(tuple(e["p"]), dxfattribs=attrs)
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue()


def export_svg(entities: list[dict], margin: float = 5.0) -> str:
    polys = render_polylines(entities)
    xs = [p[0] for pl in polys for p in pl["points"]]
    ys = [p[1] for pl in polys for p in pl["points"]]
    if not xs:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    x0, x1, y0, y1 = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
    w, h = x1 - x0, y1 - y0
    paths = []
    for pl in polys:
        d = "M" + " L".join(f"{p[0] - x0:.4f},{y1 - p[1]:.4f}" for p in pl["points"])
        dash = ' stroke-dasharray="4 3" stroke="#888"' if pl["construction"] else ' stroke="#000"'
        paths.append(f'<path d="{d}" fill="none" stroke-width="0.35"{dash}/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" '
        f'viewBox="0 0 {w:.4f} {h:.4f}">\n' + "\n".join(paths) + "\n</svg>\n"
    )
