"""Simultaneous assembly mate solver.

Every non-fixed component gets six unknowns (rotation vector about its own centre + translation)
applied on top of its current pose; all mate equations are solved together with a damped
least-squares solve, so mates no longer fight each other the way a sequential solver does.
Free degrees of freedom stay where they were (least-change solution), the remaining DOF count
comes from the rank of the Jacobian, and mates that cannot be satisfied together are reported.

Mate types (faces are selectors on the part-local shape, see modeling3d; points are part-local):

  flush         a_face, b_face planes in contact; offset, same_direction, align_center
  distance      plane-plane (parallel, B at `value` along A's normal; flip), point-point, axis-axis
  angle         `value` degrees between two plane normals / cylinder axes
  parallel      plane normals / axes parallel
  perpendicular plane normals / axes perpendicular
  concentric    cylinder / cone axes coincide (or sphere centres)
  tangent       plane-cylinder, plane-sphere, cylinder-cylinder (outside contact)
  point         a_point, b_point coincide (+ offset [x,y,z])
  lock          B keeps its current pose relative to A
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere
from scipy.optimize import least_squares

from . import modeling3d

MATE_TYPES: dict[str, list[str]] = {
    "flush": ["a_face", "b_face", "offset", "same_direction", "align_center"],
    "distance": ["a_face", "b_face", "value", "flip"],
    "angle": ["a_face", "b_face", "value"],
    "parallel": ["a_face", "b_face"],
    "perpendicular": ["a_face", "b_face"],
    "concentric": ["a_face", "b_face"],
    "tangent": ["a_face", "b_face"],
    "point": ["a_point", "b_point", "offset"],
    "lock": [],
}


# ------------------------------------------------------------------ geometry
@dataclass
class Geo:
    kind: str  # plane | axis | sphere | point
    p: np.ndarray  # point on plane / axis point / sphere centre / the point
    u: Optional[np.ndarray] = None  # plane normal / axis direction
    r: float = 0.0


def face_geo(face: cq.Face) -> Geo:
    ad = BRepAdaptor_Surface(face.wrapped)
    t = ad.GetType()
    if t == GeomAbs_Plane:
        c = face.Center()
        n = face.normalAt(c)
        return Geo("plane", np.array([c.x, c.y, c.z]), np.array([n.x, n.y, n.z]))
    if t in (GeomAbs_Cylinder, GeomAbs_Cone):
        g = ad.Cylinder() if t == GeomAbs_Cylinder else ad.Cone()
        ax = g.Axis()
        loc, d = ax.Location(), ax.Direction()
        r = g.Radius() if t == GeomAbs_Cylinder else g.RefRadius()
        # put the axis point at the face centre's projection so 'distance along the axis' stays local
        c = face.Center()
        p = np.array([loc.X(), loc.Y(), loc.Z()])
        u = np.array([d.X(), d.Y(), d.Z()])
        p = p + u * float(np.dot(np.array([c.x, c.y, c.z]) - p, u))
        return Geo("axis", p, u, float(r))
    if t == GeomAbs_Sphere:
        s = ad.Sphere()
        c = s.Location()
        return Geo("sphere", np.array([c.X(), c.Y(), c.Z()]), None, float(s.Radius()))
    raise ValueError(f"{face.geomType().lower()} faces cannot be used in mates (use planes, cylinders, cones or spheres)")


def _side_geo(shape: cq.Shape, params: dict, side: str) -> Optional[Geo]:
    if f"{side}_point" in params and params[f"{side}_point"] is not None:
        return Geo("point", np.array(params[f"{side}_point"], dtype=float))
    sel = params.get(f"{side}_face")
    if not sel:
        return None
    faces = modeling3d.resolve_faces(shape, sel)
    if not faces:
        raise ValueError(f"face selector '{sel}' matched nothing")
    return face_geo(faces[0])


# ---------------------------------------------------------------- pose maths
def rodrigues(w: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(w))
    if th < 1e-12:
        K = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])
        return np.eye(3) + K
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def rot_log(R: np.ndarray) -> np.ndarray:
    c = max(-1.0, min(1.0, (np.trace(R) - 1) / 2))
    th = math.acos(c)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if th < 1e-9:
        return 0.5 * v
    if math.pi - th < 1e-6:  # near 180 deg: axis from the symmetric part
        A = (R + np.eye(3)) / 2
        k = np.sqrt(np.clip(np.diag(A), 0, None))
        k = k / (np.linalg.norm(k) or 1)
        return k * th
    return v * th / (2 * math.sin(th))


def _perturbed(M0: np.ndarray, centre: np.ndarray, x6: np.ndarray) -> np.ndarray:
    R = rodrigues(x6[:3])
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = centre - R @ centre + x6[3:]
    return T @ M0


def _world(g: Geo, M: np.ndarray) -> Geo:
    R, t = M[:3, :3], M[:3, 3]
    return Geo(g.kind, R @ g.p + t, None if g.u is None else R @ g.u, g.r)


def _dir(g: Geo) -> np.ndarray:
    if g.u is None:
        raise ValueError(f"a {g.kind} has no direction")
    return g.u / np.linalg.norm(g.u)


def _off_axis(p: np.ndarray, a: Geo) -> np.ndarray:
    d = _dir(a)
    v = p - a.p
    return v - d * float(np.dot(v, d))


# ------------------------------------------------------------------ residuals
def mate_residual(t: str, ga: Geo, gb: Geo, params: dict, L: float, rel0: Optional[np.ndarray] = None,
                  Ma: Optional[np.ndarray] = None, Mb: Optional[np.ndarray] = None) -> np.ndarray:
    """Residual vector (mm, or unit vectors scaled by L) of one mate in world coordinates."""
    def need(*kinds_a_b):
        for ka, kb in kinds_a_b:
            if ga.kind in ka and gb.kind in kb:
                return True
        raise ValueError(f"{t} mate does not support {ga.kind} + {gb.kind}")

    if t == "lock":
        rel = np.linalg.inv(Ma) @ Mb
        return np.concatenate([rot_log(rel0[:3, :3].T @ rel[:3, :3]) * L, rel[:3, 3] - rel0[:3, 3]])
    if t == "flush":
        need((("plane",), ("plane",)))
        na, nb = _dir(ga), _dir(gb)
        s = -1.0 if params.get("same_direction") else 1.0
        off = float(params.get("offset", 0) or 0)
        res = [(na + s * nb) * L, [float(np.dot(gb.p - ga.p, na)) - off]]
        if params.get("align_center"):
            v = gb.p - ga.p
            res.append(v - na * float(np.dot(v, na)))
        return np.concatenate(res)
    if t == "distance":
        d = float(params.get("value", 0) or 0)
        if ga.kind == "plane" and gb.kind == "plane":
            na, nb = _dir(ga), _dir(gb)
            sign = -1.0 if params.get("flip") else 1.0
            return np.concatenate([np.cross(na, nb) * L, [float(np.dot(gb.p - ga.p, na)) - sign * d]])
        if ga.kind == "axis" and gb.kind == "axis":
            return np.concatenate([np.cross(_dir(ga), _dir(gb)) * L, [float(np.linalg.norm(_off_axis(gb.p, ga))) - d]])
        need((("point", "sphere"), ("point", "sphere")))
        return np.array([float(np.linalg.norm(gb.p - ga.p)) - d])
    if t in ("angle", "parallel", "perpendicular"):
        need((("plane", "axis"), ("plane", "axis")))
        ua, ub = _dir(ga), _dir(gb)
        if t == "parallel":
            return np.cross(ua, ub) * L
        if t == "perpendicular":
            return np.array([float(np.dot(ua, ub))]) * L
        return np.array([float(np.dot(ua, ub)) - math.cos(math.radians(float(params.get("value", 90) or 0)))]) * L
    if t == "concentric":
        if ga.kind == "sphere" and gb.kind == "sphere":
            return gb.p - ga.p
        need((("axis",), ("axis",)))
        return np.concatenate([np.cross(_dir(ga), _dir(gb)) * L, _off_axis(gb.p, ga)])
    if t == "tangent":
        if ga.kind == "plane" and gb.kind in ("axis", "sphere"):
            pl, cu = ga, gb
        elif gb.kind == "plane" and ga.kind in ("axis", "sphere"):
            pl, cu = gb, ga
        elif ga.kind == "axis" and gb.kind == "axis":
            return np.concatenate([np.cross(_dir(ga), _dir(gb)) * L, [float(np.linalg.norm(_off_axis(gb.p, ga))) - (ga.r + gb.r)]])
        else:
            raise ValueError(f"tangent mate does not support {ga.kind} + {gb.kind}")
        n = _dir(pl)
        res = [[float(np.dot(cu.p - pl.p, n)) - cu.r]]
        if cu.kind == "axis":
            res.insert(0, [float(np.dot(_dir(cu), n)) * L])
        return np.concatenate(res)
    if t == "point":
        need((("point", "sphere"), ("point", "sphere")))
        return gb.p - ga.p - np.array(params.get("offset", [0, 0, 0]) or [0, 0, 0], dtype=float)
    raise ValueError(f"unknown mate type '{t}'")


# --------------------------------------------------------------------- solver
def solve(components: list[dict], mates: list[dict], shapes: dict[int, cq.Shape], poses: dict[int, np.ndarray]) -> dict:
    """Solve all mates at once. `poses` (comp id -> 4x4) is the starting guess and is updated in place."""
    fixed = {c["id"] for c in components if c.get("fixed")}
    statuses: dict[int, str] = {}
    L = max([shapes[c["id"]].BoundingBox().DiagonalLength for c in components if c["id"] in shapes] + [1.0]) / 4

    terms = []
    for m in mates:
        a, b, p = m["comp_a"], m["comp_b"], m.get("params", {}) or {}
        if a not in poses or b not in poses:
            statuses[m["id"]] = "component missing"
            continue
        if a in fixed and b in fixed:
            statuses[m["id"]] = "both components are fixed"
            continue
        try:
            if m["type"] == "lock":
                ga = gb = Geo("point", np.zeros(3))
            else:
                ga, gb = _side_geo(shapes[a], p, "a"), _side_geo(shapes[b], p, "b")
                if ga is None or gb is None:
                    raise ValueError("pick a face (or point) on both components")
            rel0 = np.linalg.inv(poses[a]) @ poses[b]
            mate_residual(m["type"], _world(ga, poses[a]), _world(gb, poses[b]), p, L, rel0, poses[a], poses[b])  # validate
            terms.append((m, a, b, ga, gb, rel0))
        except Exception as exc:
            statuses[m["id"]] = f"{type(exc).__name__}: {exc}"

    involved = {t[1] for t in terms} | {t[2] for t in terms}
    free = [c["id"] for c in components if c["id"] not in fixed and c["id"] in involved]
    slot = {cid: i for i, cid in enumerate(free)}
    M0 = {cid: poses[cid].copy() for cid in poses}
    centres = {}
    for cid in free:
        bb = shapes[cid].BoundingBox()
        centres[cid] = (M0[cid] @ np.array([(bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, (bb.zmin + bb.zmax) / 2, 1.0]))[:3]

    def pose(cid, x):
        if cid not in slot:
            return M0[cid]
        return _perturbed(M0[cid], centres[cid], x[6 * slot[cid]: 6 * slot[cid] + 6])

    def residuals(x, per_mate=False):
        out = []
        for m, a, b, ga, gb, rel0 in terms:
            Ma, Mb = pose(a, x), pose(b, x)
            r = mate_residual(m["type"], _world(ga, Ma), _world(gb, Mb), m.get("params", {}) or {}, L, rel0, Ma, Mb)
            out.append(r)
        if per_mate:
            return out
        return np.concatenate(out) if out else np.zeros(0)

    n = 6 * len(free)
    dof, status = n, "solved"
    if n and terms:
        x0 = np.zeros(n)
        scale = np.tile([1.0, 1.0, 1.0, L, L, L], len(free))
        res = least_squares(residuals, x0, x_scale=scale, method="trf", xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=400)
        x = res.x
        sv = np.linalg.svd(res.jac, compute_uv=False) if res.jac.size else np.zeros(0)
        rank = int(np.sum(sv > 1e-7 * (sv.max() if sv.size else 1.0)))
        dof = n - rank
        per = residuals(x, per_mate=True)
        tol = 1e-5 * max(L, 1.0)
        bad = False
        for (m, *_), r in zip(terms, per):
            ok = float(np.max(np.abs(r))) < tol if len(r) else True
            statuses[m["id"]] = "ok" if ok else f"not satisfied (residual {float(np.max(np.abs(r))):.3g})"
            bad |= not ok
        for cid in free:
            poses[cid] = pose(cid, x)
        status = "conflict" if bad else ("fully_constrained" if dof == 0 else "under_constrained")
    elif terms:
        for m, *_ in terms:
            statuses[m["id"]] = "ok"
    return {"mate_status": statuses, "dof": dof, "status": status}
