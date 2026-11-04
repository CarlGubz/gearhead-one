"""Assembly kernel: component poses, geometric mates, interference check, BOM, export.

Poses are 4x4 homogeneous matrices.  The stored representation is
``position`` (mm) + ``rotation`` (Euler angles in degrees, applied as R = Rz @ Ry @ Rx).

Mates are solved *simultaneously* by ``mate_solver`` (all mate equations in one least-squares
problem, DOF / conflict reporting). A few sequential passes of the classic flush / concentric /
point moves below are run first only as a warm start, so large initial misalignments converge.
See ``mate_solver.MATE_TYPES`` for the mate types and their parameters.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Trsf

from . import modeling3d


# ------------------------------------------------------------------ transforms
def euler_matrix(position, rotation_deg) -> np.ndarray:
    rx, ry, rz = (math.radians(float(a)) for a in rotation_deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    M = np.eye(4)
    M[:3, :3] = Rz @ Ry @ Rx
    M[:3, 3] = [float(c) for c in position]
    return M


def decompose(M: np.ndarray) -> tuple[list[float], list[float]]:
    R = M[:3, :3]
    sy = -R[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) < 0.999999:
        rx = math.atan2(R[2, 1], R[2, 2])
        rz = math.atan2(R[1, 0], R[0, 0])
    else:  # gimbal lock
        rx = math.atan2(-R[1, 2], R[1, 1])
        rz = 0.0
    return [float(x) for x in M[:3, 3]], [math.degrees(rx), math.degrees(ry), math.degrees(rz)]


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Minimal rotation matrix taking unit vector a onto unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-12:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1, 0])
        axis /= np.linalg.norm(axis)
        return axis_angle(axis, math.pi)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def rotate_about(point: np.ndarray, R: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = point - R @ point
    return T


def translation(t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, 3] = t
    return T


def apply_pose(shape: cq.Shape, M: np.ndarray) -> cq.Shape:
    """Return a transformed copy of ``shape`` (rotation is re-orthogonalised by OCC)."""
    trsf = gp_Trsf()
    trsf.SetValues(*[float(M[i, j]) for i in range(3) for j in range(4)])
    return cq.Shape.cast(BRepBuilderAPI_Transform(shape.wrapped, trsf, True).Shape())


def _tp(M: np.ndarray, p) -> np.ndarray:
    return (M @ np.array([p[0], p[1], p[2], 1.0]))[:3]


def _td(M: np.ndarray, d) -> np.ndarray:
    return M[:3, :3] @ np.array(d, dtype=float)


# -------------------------------------------------------------- face geometry
def _face_plane(face: cq.Face) -> tuple[np.ndarray, np.ndarray]:
    c = face.Center()
    n = face.normalAt(c)
    return np.array([c.x, c.y, c.z]), np.array([n.x, n.y, n.z])


def _cyl_axis(face: cq.Face) -> tuple[np.ndarray, np.ndarray]:
    surf = BRepAdaptor_Surface(face.wrapped)
    cyl = surf.Cylinder()
    ax = cyl.Axis()
    loc, d = ax.Location(), ax.Direction()
    return np.array([loc.X(), loc.Y(), loc.Z()]), np.array([d.X(), d.Y(), d.Z()])


def _first_face(shape: cq.Shape, sel: str) -> cq.Face:
    faces = modeling3d.resolve_faces(shape, sel)
    if not faces:
        raise ValueError(f"face selector '{sel}' matched nothing")
    return faces[0]


# --------------------------------------------------------------------- solver
def _apply_mate(mate: dict, poses: dict[int, np.ndarray], shapes: dict[int, cq.Shape], fixed: set[int]) -> str:
    a_id, b_id = mate["comp_a"], mate["comp_b"]
    p = mate.get("params", {})
    t = mate["type"]
    a_id, b_id, swap = (a_id, b_id, False)
    if b_id in fixed and a_id not in fixed:
        a_id, b_id, swap = b_id, a_id, True
    elif a_id in fixed and b_id in fixed:
        return "both components are fixed"
    a_key, b_key = ("b_", "a_") if swap else ("a_", "b_")
    Ma, Mb = poses[a_id], poses[b_id]

    if t == "flush":
        fa = _first_face(shapes[a_id], p[f"{a_key}face"])
        fb = _first_face(shapes[b_id], p[f"{b_key}face"])
        ca, na = _face_plane(fa)
        cb, nb = _face_plane(fb)
        ca, na = _tp(Ma, ca), _td(Ma, na)
        cb, nb = _tp(Mb, cb), _td(Mb, nb)
        target = na if p.get("same_direction") else -na
        R = rotation_between(nb, target)
        poses[b_id] = rotate_about(cb, R) @ Mb
        nb2 = R @ nb  # now (anti)parallel to na
        off = float(p.get("offset", 0) or 0)
        if p.get("align_center"):
            t_vec = ca + na * off - cb
        else:
            t_vec = na * (float(np.dot(ca - cb, na)) + off)
        poses[b_id] = translation(t_vec) @ poses[b_id]
        return "ok"

    if t == "concentric":
        fa = _first_face(shapes[a_id], p[f"{a_key}face"])
        fb = _first_face(shapes[b_id], p[f"{b_key}face"])
        if fa.geomType() != "CYLINDER" or fb.geomType() != "CYLINDER":
            return "concentric needs two cylindrical faces"
        pa, da = _cyl_axis(fa)
        pb, db = _cyl_axis(fb)
        pa, da = _tp(Ma, pa), _td(Ma, da)
        pb, db = _tp(Mb, pb), _td(Mb, db)
        da = da / np.linalg.norm(da)
        db = db / np.linalg.norm(db)
        target = -da if p.get("flip") else da
        if np.dot(db, target) < 0 and not p.get("flip"):
            target = -da  # axes may point either way: take the smaller rotation
        elif np.dot(db, target) < 0:
            target = da
        R = rotation_between(db, target)
        poses[b_id] = rotate_about(pb, R) @ Mb
        spin = float(p.get("rotate_deg", 0) or 0)
        if spin:
            poses[b_id] = rotate_about(pb, axis_angle(da, math.radians(spin))) @ poses[b_id]
        diff = pa - pb
        t_vec = diff - da * float(np.dot(diff, da))
        slide = float(p.get("slide", 0) or 0)
        if slide:
            t_vec = t_vec + da * slide
        poses[b_id] = translation(t_vec) @ poses[b_id]
        return "ok"

    if t == "point":
        pa = _tp(Ma, p[f"{a_key}point"])
        pb = _tp(Mb, p[f"{b_key}point"])
        off = np.array(p.get("offset", [0, 0, 0]), dtype=float)
        poses[b_id] = translation(pa + off - pb) @ Mb
        return "ok"
    return f"unknown mate type '{t}'"


WARM_START_TYPES = {"flush", "concentric", "point"}


def solve(components: list[dict], mates: list[dict], part_shapes: dict[int, cq.Shape], passes: int = 4) -> dict:
    """components: [{id, part_id, position, rotation, fixed}] ; mates: [{id,type,comp_a,comp_b,params}]"""
    from . import mate_solver

    poses = {c["id"]: euler_matrix(c["position"], c["rotation"]) for c in components}
    shapes = {c["id"]: part_shapes[c["part_id"]] for c in components}
    fixed = {c["id"] for c in components if c.get("fixed")}
    # warm start: classic sequential moves (ignore their errors, the simultaneous solve reports)
    for _ in range(passes):
        before = {k: v.copy() for k, v in poses.items()}
        for m in mates:
            if m["type"] in WARM_START_TYPES and m["comp_a"] in poses and m["comp_b"] in poses:
                try:
                    _apply_mate(m, poses, shapes, fixed)
                except Exception:
                    pass
        if all(np.allclose(before[k], poses[k], atol=1e-9) for k in poses):
            break
    res = mate_solver.solve(components, mates, shapes, poses)
    out = {}
    for cid, M in poses.items():
        pos, rot = decompose(M)
        out[cid] = {"position": pos, "rotation": rot, "matrix": M.ravel().tolist()}
    return {"components": out, "mate_status": res["mate_status"], "dof": res["dof"], "status": res["status"]}


# ---------------------------------------------------------------- analysis
def world_shapes(components: list[dict], part_shapes: dict[int, cq.Shape]) -> dict[int, cq.Shape]:
    return {
        c["id"]: apply_pose(part_shapes[c["part_id"]], euler_matrix(c["position"], c["rotation"]))
        for c in components
        if c["part_id"] in part_shapes
    }


def interference(components: list[dict], part_shapes: dict[int, cq.Shape], min_volume: float = 1e-3) -> list[dict]:
    ws = world_shapes(components, part_shapes)
    ids = list(ws)
    names = {c["id"]: c["name"] for c in components}
    hits = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            A, B = ws[ids[i]], ws[ids[j]]
            ba, bb = A.BoundingBox(), B.BoundingBox()
            if ba.xmax < bb.xmin or bb.xmax < ba.xmin or ba.ymax < bb.ymin or bb.ymax < ba.ymin or ba.zmax < bb.zmin or bb.zmax < ba.zmin:
                continue
            try:
                inter = A.intersect(B)
                vol = inter.Volume()
            except Exception:
                continue
            if vol > min_volume:
                bbx = inter.BoundingBox()
                hits.append(
                    {
                        "comp_a": ids[i], "comp_b": ids[j], "name_a": names[ids[i]], "name_b": names[ids[j]],
                        "volume_mm3": vol,
                        "center": [(bbx.xmin + bbx.xmax) / 2, (bbx.ymin + bbx.ymax) / 2, (bbx.zmin + bbx.zmax) / 2],
                    }
                )
    return hits


def bill_of_materials(components: list[dict], parts: dict[int, dict], part_shapes: dict[int, cq.Shape]) -> list[dict]:
    rows: dict[int, dict] = {}
    for c in components:
        pid = c["part_id"]
        if pid not in rows:
            part = parts[pid]
            shp = part_shapes.get(pid)
            vol = shp.Volume() if shp is not None else 0.0
            mat = modeling3d.material(part.get("material", "steel"))
            rows[pid] = {
                "part_id": pid, "name": part["name"], "material": part.get("material", "steel"), "quantity": 0,
                "volume_mm3": vol, "unit_mass_kg": vol * 1e-9 * mat["density"],
            }
        rows[pid]["quantity"] += 1
    out = sorted(rows.values(), key=lambda r: r["name"])
    for i, r in enumerate(out, 1):
        r["item"] = i
        r["total_mass_kg"] = r["unit_mass_kg"] * r["quantity"]
    return out


def export_assembly_step(components: list[dict], part_shapes: dict[int, cq.Shape]) -> bytes:
    import os
    import tempfile

    asm = cq.Assembly(name="assembly")
    for c in components:
        shp = part_shapes.get(c["part_id"])
        if shp is None:
            continue
        asm.add(apply_pose(shp, euler_matrix(c["position"], c["rotation"])), name=c["name"])
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "assembly.step")
        asm.export(path, exportType="STEP")
        with open(path, "rb") as fh:
            return fh.read()
