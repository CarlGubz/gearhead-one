"""
Geometry and load exchange between the FEA, CFD and CAD workspaces of one project.

    CAD part / assembly ──STEP──▶ FEA model (Gmsh mesh; CAD materials become FEA materials)
    CAD part / assembly ──tessellation──▶ CFD obstacles ("mesh" shape: a closed triangle surface)
    FEA model ──primitives as parametric CAD features, other parts as sewn B-rep solids (STEP)──▶ CAD parts
    FEA model ──surface triangles of each part──▶ CFD obstacles
    CFD obstacles ──box / cylinder / sphere features, imported solids as STEP──▶ CAD parts
    CFD obstacles ──STEP──▶ FEA model
    CFD run results ──surface pressure → pressure loads, wall temperature → temperature supports──▶ active FEA model

Units: FEA and CFD work in metres, CAD in millimetres; every conversion happens here.

Coordinates: a solid sent to CFD keeps its source position in `obstacle.source.ref_centre` (centre of its bounding box
in the source's coordinates, metres) while the obstacle itself may be placed anywhere in the CFD domain. Mapping CFD
results back onto an FEA model uses   cfd_point = fea_point - ref_centre + obstacle_centre,   so loads land on the
right faces even after the body was moved in the wind tunnel.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import layers, models, workspaces
from .database import get_db
from .deps import owned_project

router = APIRouter(prefix="/api/projects/{project_id}/bridge", tags=["bridge"])

MM = 1000.0          # metres -> millimetres
_SOURCE_LABEL = {"fea_model": "FEA model", "cad_part": "CAD part", "cad_assembly": "CAD assembly"}
MAX_SEW_TRIANGLES = 12_000


# ===================================================================================== request bodies
class ToFea(BaseModel):
    source: Literal["cad_part", "cad_assembly", "cfd_obstacles"]
    id: Optional[str] = Field(None, description="CAD part / assembly id, or CFD case id")
    obstacle_ids: Optional[List[str]] = None
    target: Literal["new_model", "active_model"] = "new_model"
    name: Optional[str] = Field(None, max_length=80)
    size: float = Field(0.0, ge=0, description="target element size in metres, 0 = automatic")
    order: Literal[1, 2] = 2
    family: Literal["tet", "hex"] = "tet"


class ToCad(BaseModel):
    source: Literal["fea_model", "cfd_obstacles"]
    layer_id: Optional[str] = Field(None, description="FEA model of the stack; default = the active model")
    id: Optional[str] = Field(None, description="CFD case id (source cfd_obstacles)")
    obstacle_ids: Optional[List[str]] = None


class ToCfd(BaseModel):
    source: Literal["fea_model", "cad_part", "cad_assembly"]
    id: Optional[str] = Field(None, description="CAD part / assembly id")
    layer_id: Optional[str] = None
    case_id: Optional[str] = Field(None, description="add to this CFD case; omitted = a new case")
    case_name: Optional[str] = Field(None, max_length=200)
    dimension: Literal["2d", "3d"] = "3d"
    fit_domain: bool = Field(True, description="size the domain (and mesh) around the bodies, like a wind tunnel")
    position: Optional[List[float]] = Field(None, description="CFD position of the bodies' centre [x, y, z]; default: domain centre")
    thermal: Literal["adiabatic", "fixed"] = "adiabatic"
    temperature: float = 293.15


class CfdToFea(BaseModel):
    run_id: str
    obstacle_id: str
    pressure: bool = True
    temperature: bool = False
    bins: int = Field(12, ge=1, le=64, description="number of pressure / temperature levels (one load per level)")
    frame: int = -1


# ===================================================================================== shared helpers
def _core():
    from . import main as core  # late import: app.main includes this router

    return core


def _layer(project: models.Project, layer_id: Optional[str]) -> models.ModelLayer:
    rows = project.layers or []
    lid = layer_id or project.active_layer_id
    for row in rows:
        if row.id == lid:
            return row
    raise HTTPException(404, "Model not found")


def _fea_state(project: models.Project, layer_id: Optional[str]) -> Dict[str, Any]:
    return layers.model_state(project, _layer(project, layer_id))


def _cad_project(db: Session, project: models.Project):
    return workspaces.cad_project_for(db, project)


def _cad_part(db: Session, project: models.Project, part_id: Optional[str]):
    from .cad import models as cm

    cp = _cad_project(db, project)
    try:
        part = db.get(cm.Part, int(part_id)) if part_id is not None else None
    except (TypeError, ValueError):
        part = None
    if part is None or part.project_id != cp.id:
        raise HTTPException(404, "CAD part not found")
    return part


def _cad_assembly(db: Session, project: models.Project, aid: Optional[str]):
    from .cad import models as cm

    cp = _cad_project(db, project)
    try:
        a = db.get(cm.Assembly, int(aid)) if aid is not None else None
    except (TypeError, ValueError):
        a = None
    if a is None or a.project_id != cp.id:
        raise HTTPException(404, "CAD assembly not found")
    return a


def _cfd_case(db: Session, project: models.Project, case_id: Optional[str]):
    from .cfd.models import CfdCase

    case = db.get(CfdCase, case_id) if case_id else None
    if case is None or case.project_id != project.id:
        raise HTTPException(404, "CFD case not found")
    return case


def _part_shape(db: Session, part):
    from .cad.services import store

    return store.require_shape(db, part)


def _cad_material_to_fea(name: str) -> dict:
    from .cad.services import modeling3d
    from .schemas import MaterialUpdate

    m = modeling3d.material(name)
    return MaterialUpdate(name=str(m.get("label") or name)[:60], E=float(m.get("E", 200)) * 1e9, nu=float(m.get("nu", 0.3)),
                          density=float(m.get("density", 7850)), conductivity=float(m.get("k", 50)),
                          specific_heat=float(m.get("cp", 490)),
                          yield_stress=float(m.get("yield_strength", 0) or 0) * 1e6).model_dump()


def _fea_material_to_cad(mat: Optional[dict]) -> str:
    """CAD library material closest in density to an FEA material (steel when unknown)."""
    from .cad.services import modeling3d

    rho = float((mat or {}).get("density") or 0)
    if rho <= 0:
        return "steel"
    return min(modeling3d.MATERIALS, key=lambda k: abs(float(modeling3d.MATERIALS[k].get("density", 0)) - rho))


def _store_step(raw: bytes) -> str:
    from .cad.config import settings as cad_settings

    name = f"{uuid.uuid4().hex}.step"
    (cad_settings.uploads_dir / name).write_bytes(raw)
    return name


def _new_cad_part(db: Session, cp, name: str, material: str, features: List[dict]):
    from .cad import models as cm
    from .cad.services import modeling3d, store

    feats = []
    for k, (ftype, params, fname) in enumerate(features):
        feats.append({"id": f"f{k + 1}", "type": ftype, "name": fname, "suppressed": False,
                      "params": {**modeling3d.default_params(ftype), **params}})
    part = cm.Part(project_id=cp.id, name=name[:200], material=material, color=modeling3d.material(material)["color"],
                   parameters={}, features=feats)
    db.add(part)
    db.flush()
    res = store.build_part(db, part)
    errors = [s.get("error") for s in res.statuses if s.get("error")]
    return part, errors


# ------------------------------------------------------------------------------ geometry conversions
def _solid_from_triangles(V_mm: np.ndarray, T: np.ndarray):
    """A B-rep solid sewn from a closed triangle surface (cadquery / OpenCascade, in-process)."""
    import cadquery as cq

    faces = []
    for a, b, c in T:
        pa, pb, pc = V_mm[a], V_mm[b], V_mm[c]
        if np.linalg.norm(np.cross(pb - pa, pc - pa)) < 1e-12:
            continue
        wire = cq.Wire.makePolygon([cq.Vector(*pa), cq.Vector(*pb), cq.Vector(*pc)], close=True)
        faces.append(cq.Face.makeFromWires(wire))
    if len(faces) < 4:
        raise HTTPException(422, "The surface has too few triangles to form a solid")
    shell = cq.Shell.makeShell(faces)
    try:
        solid = cq.Solid.makeSolid(shell)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"The surface is not closed, so no solid could be built ({str(exc)[:120]})")
    if solid.Volume() < 0:
        solid = cq.Solid(solid.wrapped.Reversed())
    return solid


def _step_bytes(shapes: list) -> bytes:
    """STEP file (mm) of one or several shapes."""
    import cadquery as cq

    from .cad.services import modeling3d

    shape = shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    data, _ = modeling3d.export_shape(shape, "step")
    return data


def _tessellate(shape, scale: float = 1.0 / MM) -> tuple[np.ndarray, np.ndarray]:
    """Triangle surface of a CAD shape, converted to metres."""
    bb = shape.BoundingBox()
    tol = max((bb.DiagonalLength or 1.0) * 0.002, 0.01)
    verts, tris = shape.tessellate(tol, 0.3)
    V = np.array([[v.x, v.y, v.z] for v in verts], dtype=float) * scale
    T = np.array(tris, dtype=np.int64).reshape(-1, 3)
    if len(T) < 4:
        raise HTTPException(422, "The CAD shape could not be tessellated")
    return V, T


def _fea_part_surfaces(state: Dict[str, Any]) -> List[dict]:
    """Closed surface (metres) of every part of an FEA model: [{part, V, T}]."""
    from .fea import structural as struct_mod
    from .parts import effective_parts

    if struct_mod.is_structural(state.get("element_type")):
        raise HTTPException(422, "Beam, truss, shell and spring models have no closed solid surface to send. "
                                 "Use a solid model (tet or brick elements).")
    nodes = np.asarray(state.get("nodes") or [], dtype=float)
    faces = np.asarray(state.get("surface_faces") or [], dtype=np.int64).reshape(-1, 3)
    if not len(nodes) or not len(faces):
        raise HTTPException(422, "The FEA model has no mesh yet")
    owner = np.asarray(state.get("surface_parts") or [0] * len(faces), dtype=np.int64)
    if len(owner) != len(faces):
        owner = np.zeros(len(faces), dtype=np.int64)
    out = []
    for k, part in enumerate(effective_parts(state.get("parts"), len(state.get("elements") or []))):
        T = faces[owner == k]
        if len(T) < 4:
            continue
        used = np.unique(T)
        remap = np.full(len(nodes), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        out.append({"part": part, "V": nodes[used], "T": remap[T], "elems": (part.get("elem_start", 0), part.get("elem_end", 0))})
    if not out:
        raise HTTPException(422, "The FEA model has no solid parts")
    return out


# ------------------------------------------------------------------------------ CFD obstacle building
def _unique_id(existing: set, stem: str) -> str:
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem.lower())[:24] or "body"
    k, cand = 1, base
    while cand in existing:
        k += 1
        cand = f"{base}{k}"
    existing.add(cand)
    return cand


def _mesh_obstacle(V: np.ndarray, T: np.ndarray, name: str, oid: str, source: dict) -> dict:
    from .cfd.config import get_settings as cfd_settings
    from .cfd.solver.meshsolid import decimate

    V, T = decimate(V, T, cfd_settings().max_obstacle_triangles)
    lo, hi = V.min(axis=0), V.max(axis=0)
    centre = (lo + hi) / 2
    rel = np.round(V - centre, 9)
    return {"id": oid, "name": name[:60], "shape": "mesh", "x": float(centre[0]), "y": float(centre[1]),
            "z": float(centre[2]), "vertices": rel.tolist(), "triangles": T.tolist(),
            "source": {**source, "ref_centre": centre.tolist(), "size": (hi - lo).tolist()},
            "width": float(max(hi[0] - lo[0], 1e-9)), "height": float(max(hi[1] - lo[1], 1e-9))}


def _place_bodies(case_cfg: dict, bodies: List[dict], body: ToCfd) -> List[str]:
    """Position new mesh obstacles in the domain (and size the domain around them when asked). Returns notes."""
    notes = []
    lo = np.min([np.array(b["source"]["ref_centre"]) - np.array(b["source"]["size"]) / 2 for b in bodies], axis=0)
    hi = np.max([np.array(b["source"]["ref_centre"]) + np.array(b["source"]["size"]) / 2 for b in bodies], axis=0)
    size = np.maximum(hi - lo, 1e-6)
    group_c = (lo + hi) / 2
    nd = 3 if case_cfg.get("dimension") == "3d" else 2
    if body.fit_domain:
        m = float(size[:nd].max())      # 2-D: only the in-plane extent matters (the section is what flows)
        tidy = lambda v: float(f"{v:.4g}")  # noqa: E731  (no 1.4000000000000001 in the Setup fields)
        dom = {"width": tidy(size[0] + 6 * m), "height": tidy(size[1] + 3 * m),
               "depth": tidy(size[2] + 3 * m) if nd == 3 else float((case_cfg.get("domain") or {}).get("depth") or 1.0)}
        case_cfg["domain"] = dom
        centre = np.round(np.array([2 * m + size[0] / 2, 1.5 * m + size[1] / 2, 1.5 * m + size[2] / 2]), 9)
        # resolution: ~10-14 cells across the smallest body extent, bounded by a cell budget
        smallest = float(size[:nd].min())
        budget = 120_000 if nd == 3 else 60_000
        vol = dom["width"] * dom["height"] * (dom["depth"] if nd == 3 else 1.0)
        h = max(smallest / (10 if nd == 3 else 14), (vol / budget) ** (1.0 / nd))
        mesh = {"nx": int(np.clip(round(dom["width"] / h), 8, 1000)), "ny": int(np.clip(round(dom["height"] / h), 8, 1000)),
                "nz": int(np.clip(round(dom["depth"] / h), 4, 1000)) if nd == 3 else case_cfg.get("mesh", {}).get("nz", 16)}
        case_cfg["mesh"] = mesh
        b = case_cfg.setdefault("boundaries", {})
        speed = (b.get("left") or {}).get("velocity") or case_cfg.get("initial", {}).get("velocity_x") or 1.0
        b["left"] = {**(b.get("left") or {}), "type": "inlet", "velocity": speed}
        b["right"] = {**(b.get("right") or {}), "type": "outlet"}
        for side in ("bottom", "top", "back", "front"):
            b[side] = {**(b.get(side) or {}), "type": "symmetry"}
        notes.append(f"Domain sized around the bodies: {dom['width']:.4g} x {dom['height']:.4g}"
                     + (f" x {dom['depth']:.4g}" if nd == 3 else "") + f" m, mesh {mesh['nx']} x {mesh['ny']}"
                     + (f" x {mesh['nz']}" if nd == 3 else "") + "; inlet left, outlet right, symmetry sides.")
    else:
        d = case_cfg.get("domain") or {}
        centre = np.array(body.position or [d.get("width", 1) / 2, d.get("height", 1) / 2, d.get("depth", 1) / 2], dtype=float)
        if len(centre) < 3:
            centre = np.r_[centre, [d.get("depth", 1) / 2] * (3 - len(centre))]
    for b in bodies:
        off = np.array(b["source"]["ref_centre"]) - group_c
        b["x"], b["y"] = float(centre[0] + off[0]), float(centre[1] + off[1])
        b["z"] = float(centre[2] + off[2]) if nd == 3 else None
        b["thermal"], b["temperature"] = body.thermal, body.temperature
    if nd == 2:
        notes.append("2-D case: each body is cut by the plane through its centre (z = centre of the source geometry).")
    return notes


# ===================================================================================== routes
@router.get("/sources")
def sources(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Everything that can be sent between workspaces in this project."""
    from .cfd.models import CfdRun
    from .fea import structural as struct_mod

    layers.ensure(db, project)
    cp = _cad_project(db, project)
    db.commit()
    fea = []
    for row in sorted(project.layers, key=lambda l: -l.position):
        st = layers.model_state(project, row)
        fea.append({"id": row.id, "name": row.name, "active": row.id == project.active_layer_id,
                    "n_elements": len(st.get("elements") or []), "element_type": st.get("element_type"),
                    "solid": bool(st.get("elements")) and not struct_mod.is_structural(st.get("element_type")),
                    "parts": [{"id": p.get("id"), "name": p.get("name"), "source": (p.get("source") or {}).get("kind")}
                              for p in (st.get("parts") or [])]})
    cases = []
    for c in workspaces.cfd_cases_of(db, project):
        runs = db.query(CfdRun).filter(CfdRun.project_id == c.id).order_by(CfdRun.created_at.desc()).limit(20).all()
        cases.append({"id": c.id, "name": c.name, "dimension": (c.config or {}).get("dimension", "2d"),
                      "obstacles": [{"id": o.get("id"), "name": o.get("name"), "shape": o.get("shape"),
                                     "source": (o.get("source") or {}).get("kind")} for o in (c.config or {}).get("obstacles", [])],
                      "runs": [{"id": r.id, "name": r.name, "status": r.status,
                                "obstacles": [{"id": o.get("id"), "name": o.get("name")} for o in (r.config or {}).get("obstacles", [])]}
                               for r in runs]})
    return {
        "fea": {"models": fea},
        "cad": {"cad_project_id": cp.id,
                "parts": [{"id": p.id, "name": p.name, "material": p.material} for p in sorted(cp.parts, key=lambda p: p.id)],
                "assemblies": [{"id": a.id, "name": a.name, "components": len(a.components)}
                               for a in sorted(cp.assemblies, key=lambda a: a.id)]},
        "cfd": {"cases": cases},
    }


# ------------------------------------------------------------------------------------------- -> FEA
@router.post("/to-fea")
def to_fea(body: ToFea, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Mesh a CAD part / assembly or CFD obstacles into an FEA model (a new model on the stack by default)."""
    from .fea import cad as fea_cad
    from .fea.model import FEMesh
    from .parts import new_id as new_mat_id

    core = _core()
    layers.ensure(db, project)
    names: List[str] = []
    mats: List[dict] = []
    if body.source == "cad_part":
        part = _cad_part(db, project, body.id)
        raw, stem = _step_bytes([_part_shape(db, part)]), part.name
        names, mats = [part.name], [_cad_material_to_fea(part.material)]
        src = {"kind": "cad_part", "cad_part_id": part.id, "label": f"CAD part “{part.name}”"}
    elif body.source == "cad_assembly":
        from .cad.routers.assemblies import _components_for_kernel, _shapes
        from .cad.services import assembly as asmsvc

        a = _cad_assembly(db, project, body.id)
        comps = _components_for_kernel(a)
        raw, stem = asmsvc.export_assembly_step(comps, _shapes(db, a)), a.name
        from .cad import models as cm

        for c in comps:
            p = db.get(cm.Part, c["part_id"])
            names.append(c["name"])
            mats.append(_cad_material_to_fea(p.material if p else "steel"))
        src = {"kind": "cad_assembly", "cad_assembly_id": a.id, "label": f"CAD assembly “{a.name}”"}
    else:
        case = _cfd_case(db, project, body.id)
        shapes, names = _cfd_obstacle_shapes(case.config, body.obstacle_ids, back_to_source=True)
        raw, stem = _step_bytes(shapes), case.name
        src = {"kind": "cfd_obstacles", "cfd_case_id": case.id, "label": f"CFD case “{case.name}” obstacles"}
    try:
        res = fea_cad.mesh_cad(raw, f"{stem}.step", body.size, body.order, "mm", {"family": body.family})
    except fea_cad.CadError as exc:
        raise HTTPException(400, f"Could not mesh the geometry: {exc}")
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    try:
        mesh = FEMesh(res.nodes, res.elems, res.element_type)
    except ValueError as exc:
        raise HTTPException(400, f"The mesh is invalid: {exc}")
    if body.target == "new_model":
        try:
            layers.add(db, project, (body.name or f"{stem} (from {body.source.split('_')[0].upper()})")[:80], "cae")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    parts = core._parts_from_ends(stem[:40], res.ends, mesh.n_elems)
    if len(names) == len(parts):
        for p, n in zip(parts, names):
            p["name"] = str(n)[:60]
    core._set_mesh(project, mesh, parts=parts)
    core._tag_parts(project, {**src, "file": f"{stem}.step", "unit": "mm", "size": body.size, "family": body.family,
                              "order": body.order})
    if mats:
        project.material = mats[0]
        if len({m["name"] for m in mats}) > 1 and len(mats) == len(project.parts):
            lib, by_name = [], {mats[0]["name"]: "default"}
            for m in mats:
                if m["name"] not in by_name:
                    item = {**m, "id": new_mat_id(lib, "m")}
                    lib.append(item)
                    by_name[m["name"]] = item["id"]
            project.materials = lib
            project.parts = [{**p, "material_id": by_name[m["name"]]} for p, m in zip(project.parts, mats)]
    out = core._commit(db, project, f"Mesh from {src['label']}")
    return core.schemas.ProjectOut.model_validate(out)


def _cfd_obstacle_shapes(cfg: dict, ids: Optional[List[str]], back_to_source: bool) -> tuple[list, list]:
    """CAD shapes (mm) of CFD obstacles. Imported solids go back to their source position when `back_to_source`."""
    import cadquery as cq

    obs = [o for o in (cfg or {}).get("obstacles", []) if not ids or o.get("id") in ids]
    if not obs:
        raise HTTPException(422, "The CFD case has no obstacles to send")
    nd = 3 if cfg.get("dimension") == "3d" else 2
    depth = float((cfg.get("domain") or {}).get("depth") or 1.0)
    shapes, names = [], []
    for o in obs:
        zc = o.get("z") if (nd == 3 and o.get("z") is not None) else depth / 2
        span = o.get("depth") if (nd == 3 and o.get("depth")) else depth
        if o.get("shape") == "rectangle":
            s = cq.Solid.makeBox(o["width"] * MM, o["height"] * MM, span * MM,
                                 cq.Vector((o["x"] - o["width"] / 2) * MM, (o["y"] - o["height"] / 2) * MM, (zc - span / 2) * MM))
        elif o.get("shape") == "circle":
            s = cq.Solid.makeCylinder(o["radius"] * MM, span * MM, cq.Vector(o["x"] * MM, o["y"] * MM, (zc - span / 2) * MM))
        elif o.get("shape") == "sphere":
            s = cq.Workplane("XY").sphere(o["radius"] * MM).val().translate(cq.Vector(o["x"] * MM, o["y"] * MM, zc * MM))
        else:
            rel = np.asarray(o.get("vertices") or [], dtype=float)
            src = o.get("source") or {}
            centre = np.array(src["ref_centre"]) if back_to_source and src.get("ref_centre") else np.array([o["x"], o["y"], zc])
            s = _solid_from_triangles((rel + centre) * MM, np.asarray(o.get("triangles") or [], dtype=np.int64).reshape(-1, 3))
        shapes.append(s)
        names.append(o.get("name") or o.get("id"))
    return shapes, names


# ------------------------------------------------------------------------------------------- -> CAD
_PRIMITIVE_BOX = {  # nominal bounding box (lo, hi) of each FEA primitive, metres
    "box": lambda d: ([-d["length"] / 2, -d["width"] / 2, -d["height"] / 2], [d["length"] / 2, d["width"] / 2, d["height"] / 2]),
    "cylinder": lambda d: ([-d["radius"], -d["radius"], -d["height"] / 2], [d["radius"], d["radius"], d["height"] / 2]),
    "sphere": lambda d: ([-d["radius"]] * 3, [d["radius"]] * 3),
    "tube": lambda d: ([-d["outer_radius"], -d["outer_radius"], -d["height"] / 2], [d["outer_radius"], d["outer_radius"], d["height"] / 2]),
    "plate_hole": lambda d: ([-d["length"] / 2, -d["width"] / 2, -d["thickness"] / 2], [d["length"] / 2, d["width"] / 2, d["thickness"] / 2]),
    "lbracket": lambda d: ([0, 0, 0], [d["length"], d["height"], d["depth"]]),
    "cone": lambda d: ([-max(d["bottom_radius"], d["top_radius"])] * 2 + [-d["height"] / 2],
                       [max(d["bottom_radius"], d["top_radius"])] * 2 + [d["height"] / 2]),
    "torus": lambda d: ([-(d["major_radius"] + d["minor_radius"])] * 2 + [-d["minor_radius"]],
                        [d["major_radius"] + d["minor_radius"]] * 2 + [d["minor_radius"]]),
}


def _primitive_features(shape: str, d: Dict[str, float], shift: np.ndarray) -> Optional[list]:
    """Parametric CAD features (mm) that rebuild an FEA primitive, moved by `shift` (metres)."""
    s = [float(round(v * MM, 6)) for v in shift]
    at = lambda dx=0.0, dy=0.0, dz=0.0: [s[0] + dx * MM, s[1] + dy * MM, s[2] + dz * MM]  # noqa: E731
    mm = lambda v: float(round(v * MM, 6))  # noqa: E731
    if shape == "box":
        return [("box", {"length": mm(d["length"]), "width": mm(d["width"]), "height": mm(d["height"]), "centered": True,
                         "position": at(), "op": "add"}, "Box")]
    if shape == "cylinder":
        return [("cylinder", {"radius": mm(d["radius"]), "height": mm(d["height"]), "axis": "Z", "centered": True,
                              "position": at(), "op": "add"}, "Cylinder")]
    if shape == "sphere":
        return [("sphere", {"radius": mm(d["radius"]), "position": at(), "op": "add"}, "Sphere")]
    if shape == "cone":
        return [("cone", {"radius1": mm(d["bottom_radius"]), "radius2": mm(d["top_radius"]), "height": mm(d["height"]),
                          "axis": "Z", "position": at(dz=-d["height"] / 2), "op": "add"}, "Cone")]
    if shape == "tube":
        return [("cylinder", {"radius": mm(d["outer_radius"]), "height": mm(d["height"]), "axis": "Z", "centered": True,
                              "position": at(), "op": "add"}, "Tube outer"),
                ("cylinder", {"radius": mm(d["inner_radius"]), "height": mm(2 * d["height"]), "axis": "Z", "centered": True,
                              "position": at(), "op": "cut"}, "Tube bore")]
    if shape == "plate_hole":
        return [("box", {"length": mm(d["length"]), "width": mm(d["width"]), "height": mm(d["thickness"]), "centered": True,
                         "position": at(), "op": "add"}, "Plate"),
                ("cylinder", {"radius": mm(d["hole_radius"]), "height": mm(2 * d["thickness"]), "axis": "Z", "centered": True,
                              "position": at(), "op": "cut"}, "Hole")]
    if shape == "lbracket":
        a, b, t, dp = d["length"], d["height"], d["thickness"], d["depth"]
        return [("box", {"length": mm(a), "width": mm(b), "height": mm(dp), "centered": False, "position": at(), "op": "add"},
                 "Bracket block"),
                ("box", {"length": mm(a - t), "width": mm(b - t), "height": mm(3 * dp), "centered": False,
                         "position": at(t, t, -dp), "op": "cut"}, "Bracket cut-out")]
    return None   # torus: no CAD primitive -> exact B-rep through STEP


def _torus_step(d: Dict[str, float], shift: np.ndarray) -> bytes:
    import cadquery as cq

    s = cq.Solid.makeTorus(d["major_radius"] * MM, d["minor_radius"] * MM).translate(cq.Vector(*(shift * MM)))
    return _step_bytes([s])


@router.post("/to-cad")
def to_cad(body: ToCad, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Create CAD parts from an FEA model (one part per FEA part) or from CFD obstacles (one part per obstacle)."""
    from .cad.routers.parts import part_out
    from .fea.meshgen import DEFAULT_DIMS

    layers.ensure(db, project)
    cp = _cad_project(db, project)
    created, notes = [], []
    if body.source == "fea_model":
        state = _fea_state(project, body.layer_id)
        from .fea import structural as struct_mod
        if struct_mod.is_structural(state.get("element_type")):
            raise HTTPException(422, "Beam, truss, shell and spring models are not solids; only solid models can become CAD parts.")
        if not state.get("elements"):
            raise HTTPException(422, "The FEA model has no mesh yet")
        nodes = np.asarray(state["nodes"], dtype=float)
        elems = state["elements"]
        mats = {"default": state.get("material") or {}, **{m["id"]: m for m in state.get("materials") or []}}
        surfaces = {s["part"].get("id"): s for s in _fea_part_surfaces(state)}
        from .cad import models as cm
        from .parts import effective_parts

        for part in effective_parts(state.get("parts"), len(elems)):
            src = part.get("source") or {}
            material = _fea_material_to_cad(mats.get(part.get("material_id") or "default"))
            name = f"{part.get('name') or 'Part'} (from FEA)"
            if src.get("kind") == "cad_part" and src.get("cad_part_id") and db.get(cm.Part, src["cad_part_id"]) is not None \
                    and db.get(cm.Part, src["cad_part_id"]).project_id == cp.id:
                notes.append(f"“{part.get('name')}” was meshed from CAD part #{src['cad_part_id']}, which still exists - not duplicated.")
                continue
            used = np.unique(np.asarray([n for e in elems[part.get("elem_start", 0):part.get("elem_end", len(elems))] for n in e]))
            lo = nodes[used].min(axis=0) if len(used) else np.zeros(3)
            feats = None
            if src.get("kind") == "primitive" and src.get("shape") in _PRIMITIVE_BOX:
                dims = {**DEFAULT_DIMS.get(src["shape"], {}), **{k: float(v) for k, v in (src.get("dims") or {}).items()}}
                nominal_lo = np.array(_PRIMITIVE_BOX[src["shape"]](dims)[0], dtype=float)
                shift = lo - nominal_lo      # where the part sits now (offsets and later moves included)
                feats = _primitive_features(src["shape"], dims, shift)
                if feats is None:            # torus
                    fname = _store_step(_torus_step(dims, shift))
                    feats = [("import_step", {"file": fname}, "Torus (from FEA)")]
            if feats is None:
                surf = surfaces.get(part.get("id"))
                if surf is None:
                    notes.append(f"“{part.get('name')}” has no closed surface and was skipped.")
                    continue
                from .cfd.solver.meshsolid import decimate

                V, T = decimate(surf["V"], surf["T"], MAX_SEW_TRIANGLES)
                fname = _store_step(_step_bytes([_solid_from_triangles(V * MM, T)]))
                feats = [("import_step", {"file": fname}, f"{part.get('name')} surface (from FEA)")]
                notes.append(f"“{part.get('name')}” became a solid sewn from its {len(T)} surface triangles "
                             "(no parametric history: it was not a primitive).")
            cpart, errors = _new_cad_part(db, cp, name, material, feats)
            if errors:
                notes.append(f"“{name}”: {errors[0]}")
            created.append(cpart)
    else:
        case = _cfd_case(db, project, body.id)
        cfg = case.config or {}
        obs = [o for o in cfg.get("obstacles", []) if not body.obstacle_ids or o.get("id") in body.obstacle_ids]
        if not obs:
            raise HTTPException(422, "The CFD case has no obstacles to send")
        nd = 3 if cfg.get("dimension") == "3d" else 2
        depth = float((cfg.get("domain") or {}).get("depth") or 1.0)
        if nd == 2 and any(o.get("shape") != "mesh" for o in obs):
            notes.append(f"2-D case: rectangles and circles were extruded over the case depth ({depth:g} m).")
        for o in obs:
            zc = o.get("z") if (nd == 3 and o.get("z") is not None) else depth / 2
            span = o.get("depth") if (nd == 3 and o.get("depth")) else depth
            name = f"{o.get('name') or o.get('id')} (from CFD)"
            if o.get("shape") == "rectangle":
                feats = [("box", {"length": o["width"] * MM, "width": o["height"] * MM, "height": span * MM, "centered": True,
                                  "position": [o["x"] * MM, o["y"] * MM, zc * MM], "op": "add"}, "Box")]
            elif o.get("shape") == "circle":
                feats = [("cylinder", {"radius": o["radius"] * MM, "height": span * MM, "axis": "Z", "centered": True,
                                       "position": [o["x"] * MM, o["y"] * MM, zc * MM], "op": "add"}, "Cylinder")]
            elif o.get("shape") == "sphere":
                feats = [("sphere", {"radius": o["radius"] * MM, "position": [o["x"] * MM, o["y"] * MM, zc * MM], "op": "add"},
                          "Sphere")]
            else:
                shapes, _ = _cfd_obstacle_shapes({**cfg, "obstacles": [o]}, None, back_to_source=True)
                feats = [("import_step", {"file": _store_step(_step_bytes(shapes))}, "Imported solid (from CFD)")]
            cpart, errors = _new_cad_part(db, cp, name, "aluminum" if o.get("thermal") == "fixed" else "steel", feats)
            if errors:
                notes.append(f"“{name}”: {errors[0]}")
            created.append(cpart)
    db.commit()
    return {"parts": [part_out(p) for p in created], "notes": notes, "cad_project_id": cp.id}


# ------------------------------------------------------------------------------------------- -> CFD
@router.post("/to-cfd")
def to_cfd(body: ToCfd, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Add an FEA model or a CAD part / assembly to a CFD case as solid obstacles (a new case by default)."""
    from .cfd.models import CfdCase
    from .cfd.schemas import CaseConfig, Material
    from .cfd.templates import build_template

    layers.ensure(db, project)
    surfaces: List[tuple] = []        # (name, V metres, T, source)
    if body.source == "fea_model":
        row = _layer(project, body.layer_id)
        for s in _fea_part_surfaces(layers.model_state(project, row)):
            surfaces.append((s["part"].get("name") or "Part", s["V"], s["T"],
                             {"kind": "fea_model", "layer_id": row.id, "part_id": s["part"].get("id"), "label": row.name}))
    elif body.source == "cad_part":
        part = _cad_part(db, project, body.id)
        V, T = _tessellate(_part_shape(db, part))
        surfaces.append((part.name, V, T, {"kind": "cad_part", "cad_part_id": part.id, "label": part.name}))
    else:
        from .cad.routers.assemblies import _components_for_kernel, _shapes
        from .cad.services import assembly as asmsvc

        a = _cad_assembly(db, project, body.id)
        comps = _components_for_kernel(a)
        world = asmsvc.world_shapes(comps, _shapes(db, a))
        for c in comps:
            shp = world.get(c["id"])
            if shp is None:
                continue
            V, T = _tessellate(shp)
            surfaces.append((c["name"], V, T, {"kind": "cad_assembly", "cad_assembly_id": a.id, "component_id": c["id"],
                                               "label": f"{a.name} / {c['name']}"}))
        if not surfaces:
            raise HTTPException(422, "The assembly has no components with geometry")

    if body.case_id:
        case = _cfd_case(db, project, body.case_id)
        cfg = dict(case.config)
        if body.dimension != cfg.get("dimension", "2d") and body.fit_domain:
            cfg["dimension"] = body.dimension
    else:
        cfg = build_template("blank")[2].model_dump(mode="json")
        cfg["dimension"] = body.dimension
        cfg["material"] = Material().model_dump()     # air
        case = None
    existing = {o.get("id") for o in cfg.get("obstacles", [])}
    new_obs = [_mesh_obstacle(V, T, name, _unique_id(existing, name), src) for name, V, T, src in surfaces]
    notes = _place_bodies(cfg, new_obs, body)
    if case is None and body.fit_domain:
        # a laminar starting point: Re ~ 100 on the body length (air), editable in Setup
        nd = 3 if cfg.get("dimension") == "3d" else 2
        L = max(max(o["source"]["size"][:nd]) for o in new_obs)
        nu = cfg["material"]["viscosity"] / cfg["material"]["density"]
        u = float(f"{float(np.clip(100 * nu / max(L, 1e-9), 1e-3, 10.0)):.3g}")
        cfg["boundaries"]["left"]["velocity"] = u
        cfg.setdefault("initial", {})["velocity_x"] = u
        notes.append(f"Inlet speed set to {u:.3g} m/s (Re ≈ 100 on the body length) - change it in Setup → Boundary conditions.")
    cfg["obstacles"] = list(cfg.get("obstacles", [])) + new_obs
    try:
        cfg = CaseConfig.model_validate(cfg).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"The CFD case would be invalid: {exc}")
    if case is None:
        case = CfdCase(project_id=project.id, name=(body.case_name or f"Flow around {surfaces[0][0]}")[:200],
                       description=f"Created from the {_SOURCE_LABEL[body.source]} of this project.", template=None, config=cfg)
        db.add(case)
    else:
        case.config = cfg
    db.commit()
    return {"case_id": case.id, "case_name": case.name, "obstacles": [o["id"] for o in new_obs],
            "triangles": [len(o["triangles"]) for o in new_obs], "notes": notes}


# ------------------------------------------------------------------------------------------- CFD results -> FEA loads
def _cfd_cell_fields(run_dir, frame: int) -> tuple[dict, np.ndarray, dict]:
    """Cell-centred p (Pa) / T arrays, the solid mask and the grid meta of a CFD result (2-D or 3-D)."""
    from .cfd.solver import post

    final, latest = run_dir / "result.npz", run_dir / "latest.npz"
    path = final if final.exists() else latest
    if not path.exists():
        raise HTTPException(409, "That run has no results yet")
    res = post.load_result(path)
    meta = res["meta"]
    if post.is_cc(res) and meta.get("dim", 3) == 3:
        f = post._cc_frame(res, frame)
        return {"p": f["p"], "T": f.get("T")}, np.asarray(res["solid"], dtype=bool), meta
    pv = post.plane_view(res, frame)
    return {"p": pv["fields"]["p"], "T": pv["fields"].get("T")}, np.asarray(pv["solid"], dtype=bool).reshape(pv["n"]), meta


def _bin(values: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = float(np.min(values)), float(np.max(values))
    if hi - lo < 1e-12 * max(1.0, abs(hi)) or k == 1:
        return np.zeros(len(values), dtype=int), np.array([lo, hi])
    edges = np.linspace(lo, hi, k + 1)
    return np.clip(np.searchsorted(edges, values, "right") - 1, 0, k - 1), edges


@router.post("/cfd-to-fea")
def cfd_to_fea(body: CfdToFea, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """
    Map a CFD result onto the active FEA model: the fluid pressure on the body's surface becomes pressure loads and
    the fluid temperature next to it becomes temperature supports. Each is grouped into `bins` levels (one load /
    support per level, magnitude = the level's mean), which keeps the model editable. The FEA model is assumed to be
    the geometry the obstacle was made from (same coordinates; the obstacle may have been moved in the CFD domain).
    """
    from .cfd.config import get_settings as cfd_settings
    from .cfd.models import CfdCase, CfdRun

    core = _core()
    run = db.get(CfdRun, body.run_id)
    case = db.get(CfdCase, run.project_id) if run else None
    if run is None or case is None or case.project_id != project.id:
        raise HTTPException(404, "CFD run not found")
    ob = next((o for o in (run.config or {}).get("obstacles", []) if o.get("id") == body.obstacle_id), None)
    if ob is None:
        raise HTTPException(404, "That obstacle is not part of the run")
    if not (body.pressure or body.temperature):
        raise HTTPException(422, "Choose pressure and/or temperature")
    fields, solid, meta = _cfd_cell_fields(cfd_settings().runs_dir / run.id, body.frame)
    if body.temperature and (fields.get("T") is None or not (run.config.get("models") or {}).get("energy")):
        raise HTTPException(422, "The run has no temperature field (the energy equation was off)")
    state = _fea_state(project, None)
    from .fea import structural as struct_mod
    if struct_mod.is_structural(state.get("element_type")) or not state.get("elements"):
        raise HTTPException(422, "The active FEA model must be a meshed solid model")

    nd = 3 if (run.config.get("dimension") == "3d") else 2
    n = solid.shape
    L = [meta["lx"], meta["ly"]] + ([meta["lz"]] if nd == 3 else [])
    h = np.array([L[a] / n[a] for a in range(nd)])
    nodes = np.asarray(state["nodes"], dtype=float)
    faces = np.asarray(state["surface_faces"], dtype=np.int64).reshape(-1, 3)
    depth = float((run.config.get("domain") or {}).get("depth") or 1.0)
    zc = ob.get("z") if (nd == 3 and ob.get("z") is not None) else depth / 2
    ob_centre = np.array([ob["x"], ob["y"], zc])
    ref = np.array((ob.get("source") or {}).get("ref_centre") or ((nodes.min(axis=0) + nodes.max(axis=0)) / 2))

    A, B, C = nodes[faces[:, 0]], nodes[faces[:, 1]], nodes[faces[:, 2]]
    cen = (A + B + C) / 3
    nrm = np.cross(B - A, C - A)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.divide(nrm, ln, out=np.zeros_like(nrm), where=ln > 0)
    pts = cen - ref + ob_centre                       # face centres in CFD coordinates
    if nd == 2:
        # 2-D results: only faces near the section plane (z = centre of the source) carry a meaningful value
        keep_2d = np.abs(cen[:, 2] - ref[2]) <= max(float(np.ptp(nodes[:, 2])) * 0.5, 1e-12)
    else:
        keep_2d = np.ones(len(faces), dtype=bool)

    def cell_of(P):
        idx = np.floor(P[:, :nd] / h).astype(np.int64)
        inside = np.all((idx >= 0) & (idx < np.array(n)), axis=1)
        return np.clip(idx, 0, np.array(n) - 1), inside

    value_cell = np.full((len(faces), nd), -1, dtype=np.int64)
    found = np.zeros(len(faces), dtype=bool)
    step = float(h.max())
    for k in (1.0, 1.75, 2.5, 3.5):                   # walk outwards (either side: normals may point in) to a fluid cell
        for sgn in (1.0, -1.0):
            todo = ~found & keep_2d
            if not todo.any():
                break
            idx, inside = cell_of(pts[todo] + sgn * k * step * nrm[todo])
            fluid = inside & ~solid[tuple(idx.T)]
            rows = np.flatnonzero(todo)[fluid]
            value_cell[rows] = idx[fluid]
            found[rows] = True
    if not found.any():
        raise HTTPException(422, "No surface face of the FEA model lies next to fluid in this run. Is the active FEA model "
                                 "the geometry of that obstacle?")
    loads = list(state.get("loads") or [])
    bcs = list(state.get("boundary_conditions") or [])
    summary = {"faces_mapped": int(found.sum()), "faces_total": int(len(faces))}
    hit = np.flatnonzero(found)
    cells = tuple(value_cell[hit].T)
    tag = f"CFD {run.name or run.id[:6]}"
    if body.pressure:
        p = np.asarray(fields["p"], dtype=float)[cells]
        b, edges = _bin(p, body.bins)
        for k in range(len(edges) - 1):
            sel = hit[b == k]
            if not len(sel):
                continue
            loads.append({"id": core._new_id(), "name": f"{tag} p {edges[k]:.4g}…{edges[k + 1]:.4g} Pa", "load_type": "pressure",
                          "node_ids": [], "vector": [0.0, 0.0, 0.0], "moment": [0.0, 0.0, 0.0],
                          "faces": faces[sel].tolist(), "magnitude": float(np.mean(p[b == k]))})
        summary["pressure_range_pa"] = [float(p.min()), float(p.max())]
    if body.temperature:
        T = np.asarray(fields["T"], dtype=float)[cells]
        node_sum, node_cnt = np.zeros(len(nodes)), np.zeros(len(nodes))
        for c in range(3):
            np.add.at(node_sum, faces[hit, c], T)
            np.add.at(node_cnt, faces[hit, c], 1)
        nid = np.flatnonzero(node_cnt > 0)
        Tn = node_sum[nid] / node_cnt[nid]
        b, edges = _bin(Tn, body.bins)
        for k in range(len(edges) - 1):
            sel = nid[b == k]
            if not len(sel):
                continue
            bcs.append({"id": core._new_id(), "name": f"{tag} T {edges[k]:.4g}…{edges[k + 1]:.4g} K", "bc_type": "temperature",
                        "node_ids": sel.tolist(), "ref_id": None, "dofs": [], "values": [float(np.mean(Tn[b == k]))]})
        summary["temperature_range_k"] = [float(Tn.min()), float(Tn.max())]
    project.loads = loads
    project.boundary_conditions = bcs
    out = core._commit(db, project, f"Loads from {tag}")
    return {"project": core.schemas.ProjectOut.model_validate(out).model_dump(mode="json"), "summary": summary}

