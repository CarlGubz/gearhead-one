"""
Gearhead backend - FastAPI application (FEA + CFD + CAD in one platform).

  1. Accounts (PostgreSQL-backed) and GCash subscriptions - see routers/
  2. Project CRUD (per user), project file export/import (one file carries FEA, CFD and CAD - see projectfile.py)
  3. FEA pre-processing: primitive meshes (tet/hex, linear/quadratic), mesh import (.inp/.msh), materials,
     steps/settings, boundary conditions, loads, reference points, constraints, named sets
  4. FEA solve on the built-in FE engine (app/fea) - no external solver is used
  5. FEA post-processing data for the browser (contours, mode shapes, time histories)
  6. CFD workbench under /api/cfd (app/cfd: cases, solver backends, runs, results) - formerly CFDFlow
  7. CAD / CAM workbench under /api/cad (app/cad: sketches, parts, assemblies, drawings, CAM, CAD simulation) -
     formerly CADWeb
  8. Geometry / load exchange between the three workspaces under /api/projects/{id}/bridge (bridge.py)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from typing import Any, Dict, List

from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from . import agent as agent_mod, history, jobs, layers, models, parts as parts_mod, plans, schemas
from .config import settings
from .database import Base, engine, get_db
from .deps import current_user, owned_project, require_pro_for, tier_of, verify_origin
from .fea import cad, gmsh_options, inp, meshgen, structmesh
from .fea import structural as struct_mod
from .fea.elements import ELEMENT_TYPES, element_type as get_element_type
from .fea.model import FEMesh
from .fea.runner import ANALYSIS_TYPES
from .ratelimit import RateLimiter
from .routers import auth as auth_router, billing as billing_router
from . import projectfile, workspaces
from .cad import main as cad_app, models as _cad_models  # noqa: F401  (registers the cad_ tables)
from .cfd import main as cfd_app, models as _cfd_models  # noqa: F401  (registers the cfd_ tables)

Base.metadata.create_all(bind=engine)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_NODES = 120_000
STALE_JOB_MINUTES = 30
MAX_ACTIVE_JOBS = 2


def _migrate_schema():
    """
    Minimal, idempotent migration: `create_all` only creates missing tables, so columns added to an
    existing `projects` table later are backfilled here. Safe to run on every startup.
    """
    inspector = inspect(engine)
    if "projects" not in inspector.get_table_names():
        return
    tables = inspector.get_table_names()
    with engine.begin() as conn:
        # v5 model stack: history and jobs are tagged with the model they belong to (layers.ensure adopts old rows)
        if "project_history" in tables and "layer_id" not in {c["name"] for c in inspector.get_columns("project_history")}:
            conn.execute(text("ALTER TABLE project_history ADD COLUMN layer_id VARCHAR(32) DEFAULT ''"))
            conn.execute(text("UPDATE project_history SET layer_id = '' WHERE layer_id IS NULL"))
        if "jobs" in tables and "layer_id" not in {c["name"] for c in inspector.get_columns("jobs")}:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN layer_id VARCHAR(32)"))
    existing = {c["name"] for c in inspector.get_columns("projects")}
    additions = {
        "analysis_type": "VARCHAR", "n_modes": "INTEGER", "gravity": "JSON",
        "element_type": "VARCHAR", "settings": "JSON", "reference_points": "JSON",
        "constraints": "JSON", "node_sets": "JSON", "materials": "JSON", "parts": "JSON", "surface_parts": "JSON",
        "surface_mid": "JSON", "history_pos": "INTEGER", "history_labels": "JSON", "active_layer_id": "VARCHAR(32)",
    }
    with engine.begin() as conn:
        for col, typ in additions.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE projects ADD COLUMN {col} {typ}"))
        if "analysis_type" not in existing:
            conn.execute(text("UPDATE projects SET analysis_type = 'static' WHERE analysis_type IS NULL"))
        if "n_modes" not in existing:
            conn.execute(text("UPDATE projects SET n_modes = 6 WHERE n_modes IS NULL"))
        if "gravity" not in existing:
            conn.execute(text(
                "UPDATE projects SET gravity = '{\"enabled\": false, \"x\": 0.0, \"y\": 0.0, \"z\": -9.81}' "
                "WHERE gravity IS NULL"))
        if "element_type" not in existing:
            conn.execute(text("UPDATE projects SET element_type = 'C3D4' WHERE element_type IS NULL"))
        if "owner_id" not in existing:
            # Projects created before accounts existed stay ownerless (hidden).
            conn.execute(text("ALTER TABLE projects ADD COLUMN owner_id VARCHAR(32)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_owner_id ON projects (owner_id)"))
            if settings.is_postgres:
                conn.execute(text(
                    "ALTER TABLE projects ADD CONSTRAINT fk_projects_owner "
                    "FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE"))


_migrate_schema()
cad_app.migrate()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfd_app.start()       # CFD run scheduler: spawns one solver worker process per queued run
    yield
    cfd_app.stop()


app = FastAPI(
    title="Gearhead",
    description="Browser-based FEA, CFD and CAD/CAM suite: standalone FE solver, CFD workbench with OpenFOAM / "
                "FEniCSx / PyFR backends, parametric CAD, drawings, CAM and CAD simulation",
    version="4.0.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_origin)],
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

if settings.extra_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.extra_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(auth_router.router)
app.include_router(billing_router.router)
app.include_router(billing_router.webhooks)
cfd_app.mount(app)
cad_app.mount(app)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Database unavailable")
    return {"status": "ok", "solver": "gearhead-fea", "external_solver_required": False,
            "workspaces": ["fea", "cfd", "cad"], "version": app.version}


@app.get("/api/capabilities")
def capabilities():
    """What the engine can do - the UI builds its pickers from this."""
    return {
        "analysis_types": ANALYSIS_TYPES,
        "free_analysis_types": ["static"],
        "element_types": [{"id": k, "nodes": v.n_nodes, "kind": v.kind} for k, v in ELEMENT_TYPES.items()],
        "shapes": meshgen.SHAPES,
        "shape_defaults": meshgen.DEFAULT_DIMS,
        "gmsh": {"available": cad.available(), "version": cad.version(), "options": gmsh_options.catalogue(),
                 "sweepable_shapes": ["box", "cylinder", "tube", "plate_hole", "lbracket"]},
        "structural_types": [{"id": k, "nodes": n} for k, n in struct_mod.STRUCT_TYPES.items()],
        "structural_shapes": structmesh.KINDS,
        "cad_import": {"available": cad.available(), "formats": list(cad.FORMATS), "units": list(cad.UNITS)},
    }


# ------------------------------------------------------------------ helpers
def _commit(db: Session, project: models.Project, label: str = None) -> models.Project:
    """Commit an edit. With a label the edit also becomes an undo step (see history.py)."""
    if label:
        before = history.capture(project, before=True) if (project.history_pos is None or project.history_pos < 0) else None
        history.record(db, project, label, before)
    db.commit()
    db.refresh(project)
    return project


def _new_id() -> str:
    return models.gen_id()


def _is_struct(project: models.Project) -> bool:
    return struct_mod.is_structural(project.element_type)


def _fem_mesh(project: models.Project) -> FEMesh:
    if not project.elements:
        raise HTTPException(400, "Project has no mesh yet")
    if _is_struct(project):
        raise HTTPException(400, "This is a structural (beam / shell / truss / spring) model; the operation is for solid meshes.")
    return FEMesh(project.nodes, project.elements, project.element_type or "C3D4")


def _struct_triangles(etype: str, elems) -> list:
    """Render triangles of a shell model (two per quad: first all (0,1,2), then all (0,2,3)); none for line elements."""
    if etype != "S4" or len(elems) == 0:
        return []
    e = [list(map(int, q)) for q in elems]
    return [[q[0], q[1], q[2]] for q in e] + [[q[0], q[2], q[3]] for q in e]


def _struct_surface_parts(etype: str, parts: list, ne: int) -> list:
    if etype != "S4":
        return []
    import numpy as np
    starts = np.array([p["elem_start"] for p in sorted(parts, key=lambda q: q["elem_start"])])
    per_elem = np.searchsorted(starts, np.arange(ne), side="right") - 1
    return per_elem.tolist() * 2


def _clean_section(etype: str, section) -> dict:
    try:
        return struct_mod.validate_section(etype, section or struct_mod.default_section(etype))
    except struct_mod.StructuralError as exc:
        raise HTTPException(422, str(exc))


def _set_structural(project: models.Project, nodes, elems, etype: str, parts=None, section=None,
                    material_id: str = "default", name: str = "Body") -> None:
    """Install a structural mesh as one part (or the given parts) and clear everything that referenced the old one."""
    import numpy as np
    nodes = np.asarray(nodes, dtype=float)
    elems = np.asarray(elems, dtype=np.int64)
    if etype not in struct_mod.STRUCT_TYPES:
        raise HTTPException(422, f"Structural element type must be one of: {', '.join(struct_mod.STRUCT_TYPES)}")
    if elems.ndim != 2 or elems.shape[1] != struct_mod.STRUCT_TYPES[etype]:
        raise HTTPException(422, f"{etype} elements need {struct_mod.STRUCT_TYPES[etype]} nodes each")
    if len(elems) == 0 or len(nodes) == 0:
        raise HTTPException(422, "A model needs at least one element")
    if elems.min() < 0 or elems.max() >= len(nodes):
        raise HTTPException(422, "Element connectivity references a node that does not exist")
    if len(nodes) > MAX_NODES:
        raise HTTPException(413, f"Mesh too large ({len(nodes)} nodes, limit {MAX_NODES}).")
    if not np.all(np.isfinite(nodes)):
        raise HTTPException(422, "Node coordinates must be finite numbers")
    project.nodes = nodes.tolist()
    project.elements = elems.tolist()
    project.element_type = etype
    project.surface_faces = _struct_triangles(etype, elems)
    project.surface_mid = []
    sec = _clean_section(etype, section)
    project.parts = parts if parts else [{"id": "p0", "name": name, "elem_start": 0, "elem_end": int(len(elems)),
                                          "material_id": material_id, "section": sec}]
    project.surface_parts = _struct_surface_parts(etype, project.parts, len(elems))
    project.boundary_conditions = []
    project.loads = []
    project.constraints = []
    project.reference_points = []
    project.node_sets = []


def _set_surface(project: models.Project, mesh: FEMesh) -> None:
    """Render triangles of the solid's boundary, plus the mid-side node of every triangle edge for quadratic meshes."""
    b = mesh.boundary()
    project.surface_faces = b.tris.tolist()
    project.surface_mid = b.tri_mid.tolist() if mesh.et.n_nodes > mesh.et.n_corner else []


def _set_mesh(project: models.Project, mesh: FEMesh, part_name: str = "Body", parts=None) -> None:
    """Install a mesh (as one part, or the given parts) and clear everything that referenced the old one."""
    if mesh.n_nodes > MAX_NODES:
        raise HTTPException(413, f"Mesh too large ({mesh.n_nodes} nodes, limit {MAX_NODES}). Lower the mesh density.")
    mesh.ensure_positive_orientation()
    project.nodes = mesh.nodes.tolist()
    project.elements = mesh.elems.tolist()
    project.element_type = mesh.et.name
    _set_surface(project, mesh)
    project.parts = parts if parts else parts_mod.default_parts(mesh.n_elems, part_name)
    project.surface_parts = parts_mod.surface_parts(mesh, project.parts)
    project.boundary_conditions = []
    project.loads = []
    project.constraints = []
    project.reference_points = []
    project.node_sets = []


def _primitive_source(payload, offset=None) -> dict:
    """Input geometry of a part (the Geometry panel lists it next to the output mesh)."""
    src = {"kind": "primitive", "shape": payload.shape, "label": meshgen.SHAPES.get(payload.shape, payload.shape),
           "dims": dict(payload.dims or {}), "divisions": payload.divisions, "mesher": payload.mesher}
    if offset is not None:
        src["offset"] = list(offset)
    return src


def _structural_source(payload, offset=None) -> dict:
    src = {"kind": "structural", "shape": payload.kind, "label": structmesh.NAMES.get(payload.kind, payload.kind),
           "dims": dict(payload.dims or {}), "divisions": payload.divisions}
    if offset is not None:
        src["offset"] = list(offset)
    return src


def _tag_parts(project: models.Project, source: dict) -> None:
    """Attach the input geometry to every part that does not have one yet."""
    project.parts = [p if p.get("source") else {**p, "source": dict(source)} for p in (project.parts or [])]


def _check_node_ids(project: models.Project, ids) -> None:
    n = len(project.nodes or [])
    if any((not isinstance(i, int)) or i < 0 or i >= n for i in ids):
        raise HTTPException(422, "A node id is outside the mesh")


def _ref_ids(project: models.Project) -> set:
    return {r["id"] for r in (project.reference_points or [])}


def _ensure_parts(project: models.Project) -> list:
    """Persist the implicit single part of a pre-parts project and return the parts list."""
    if not project.parts:
        project.parts = parts_mod.effective_parts(None, len(project.elements or []))
        project.surface_parts = [0] * len(project.surface_faces or [])
    return [dict(p) for p in project.parts]


def _material_ids(project: models.Project) -> set:
    return {"default"} | {m["id"] for m in (project.materials or [])}


def _apply_inp_parts(project: models.Project, mesh: FEMesh, model) -> None:
    """Turn the parts / materials found in an .inp file into project parts and library entries."""
    ids = {}  # file material name -> project material id
    order = []
    for p in model.parts:
        if p.get("material") and p["material"] in model.materials and p["material"] not in order:
            order.append(p["material"])
    if not order and model.materials:
        order = list(model.materials)[:1]
    library = []
    for i, nm in enumerate(order):
        try:
            item = schemas.MaterialItem(name=nm[:60], **model.materials[nm]).model_dump()
        except ValueError:
            continue
        if i == 0:
            project.material = {**item}
            ids[nm] = "default"
        else:
            item["id"] = parts_mod.new_id(library, "m")
            library.append(item)
            ids[nm] = item["id"]
    project.materials = library
    plist = []
    for q in model.parts:
        plist.append({"id": parts_mod.new_id(plist, "p"), "name": str(q["name"])[:60], "elem_start": q["elem_start"],
                      "elem_end": q["elem_end"], "material_id": ids.get(q.get("material"), "default")})
    if plist:
        parts_mod.check_tiling(plist, mesh.n_elems)
        project.parts = plist
        project.surface_parts = parts_mod.surface_parts(mesh, plist)


# ---------------------------------------------------------------- Projects
def _limit_check(user: models.User, db: Session) -> None:
    limit = plans.limits_for(tier_of(user)).max_projects
    if limit is not None:
        used = db.query(models.Project).filter(models.Project.owner_id == user.id).count()
        if used >= limit:
            raise HTTPException(
                402, f"The Free plan includes {limit} projects. Upgrade to Pro for unlimited projects.")


def _blank_project(user: models.User, name: str, description: str = "") -> models.Project:
    return models.Project(owner_id=user.id, name=name.strip(), description=description or "", **layers.blank_model())


@app.post("/api/projects", response_model=schemas.ProjectOut)
def create_project(payload: schemas.ProjectCreate, user: models.User = Depends(current_user),
                   db: Session = Depends(get_db)):
    _limit_check(user, db)
    project = _blank_project(user, payload.name, payload.description or "")
    db.add(project)
    layers.ensure(db, project)
    return _commit(db, project)


@app.get("/api/projects", response_model=List[schemas.ProjectSummary])
def list_projects(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return (db.query(models.Project).filter(models.Project.owner_id == user.id)
            .order_by(models.Project.updated_at.desc()).all())


PROJECT_FILE_KEYS = ["name", "description", "nodes", "elements", "element_type", "material", "materials", "parts",
                     "boundary_conditions", "loads", "analysis_type", "n_modes", "gravity", "settings",
                     "reference_points", "constraints", "node_sets"]


@app.post("/api/projects/import", response_model=schemas.ProjectOut)
def import_project(data: Dict[str, Any] = Body(...), user: models.User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """
    Create a project from a file previously saved with 'Save project' (see projectfile.py). Version 3 files carry the
    FEA model stack, the CFD cases and the CAD documents; version 2 files the FEA model stack in `layers` (bottom
    first); version 1 files (and the top-level keys of versions 2 and 3) hold a single FEA model.
    """
    projectfile.check_format(data)
    _limit_check(user, db)
    project = _blank_project(user, str(data.get("name") or "Imported project")[:120], str(data.get("description") or "")[:2000])
    _load_fea_stack(db, project, data, user)
    projectfile.import_sections(db, project, data)
    return _commit(db, project)


def _load_fea_stack(db: Session, project: models.Project, data: Dict[str, Any], user: models.User) -> None:
    """The FEA part of a project file into a new (not yet added) project."""
    stack = data.get("layers")
    if not isinstance(stack, list) or not stack:
        _load_model_file(project, data, user)
        db.add(project)
        layers.ensure(db, project)
        return
    if len(stack) > layers.MAX_LAYERS:
        raise HTTPException(422, f"A project holds at most {layers.MAX_LAYERS} models")
    db.add(project)
    active = 0
    for k, entry in enumerate(stack):
        if not isinstance(entry, dict) or not isinstance(entry.get("model"), dict):
            raise HTTPException(422, f"Invalid project file: model {k + 1} has no model data")
        name = str(entry.get("name") or f"Model {k + 1}")[:80]
        process = entry.get("process") if entry.get("process") in layers.PROCESSES else "cae"
        if k:
            layers.add(db, project, name, process)
        else:
            layers.ensure(db, project, name, process)
        _load_model_file(project, entry["model"], user)
        if entry.get("active"):
            active = k
    rows = sorted(project.layers, key=lambda l: l.position)
    layers.activate(db, project, rows[active].id)
    for row, entry in zip(rows, stack):
        row.visible = entry.get("visible") is not False or row.id == project.active_layer_id


def _file_models(data: Dict[str, Any]) -> list:
    """The FEA models of a project file as [(name, process, model dict)], bottom of the stack first."""
    stack = data.get("layers")
    if isinstance(stack, list) and stack:
        return [(str(e.get("name") or f"Model {k + 1}")[:80], e.get("process") if e.get("process") in layers.PROCESSES else "cae",
                 e["model"]) for k, e in enumerate(stack) if isinstance(e, dict) and isinstance(e.get("model"), dict)]
    return [(str(data.get("name") or "Imported model")[:80], "cae", data)] if data.get("elements") else []


@app.post("/api/projects/{project_id}/merge-file")
def merge_file(data: Dict[str, Any] = Body(...), fea: bool = True, cfd: bool = True, cad: bool = True,
               project: models.Project = Depends(owned_project), user: models.User = Depends(current_user),
               db: Session = Depends(get_db)):
    """
    Open a project file *into this project* (instead of as a new one): its FEA models are added on top of the model
    stack, its CFD cases next to this project's cases and its CAD documents into this project's CAD project. Any
    Gearhead file works from any workspace - an FEA-only file adds models, a CAD-only file adds CAD documents.
    """
    projectfile.check_format(data)
    added_models = 0
    if fea:
        entries = _file_models(data)
        if len(project.layers or []) + len(entries) > layers.MAX_LAYERS:
            raise HTTPException(422, f"A project holds at most {layers.MAX_LAYERS} models")
        for name, process, model in entries:
            layers.add(db, project, name, process)
            _load_model_file(project, model, user)
            added_models += 1
    added = projectfile.import_sections(db, project, data, cfd=cfd, cad=cad)
    _commit(db, project, "Open file into project" if added_models else None)
    return {"added": {"fea_models": added_models, **added}, "workspaces": workspaces.summary(db, project)}


def _load_model_file(project: models.Project, data: Dict[str, Any], user: models.User) -> None:
    """Validate one model of a project file into the project's columns (the active model)."""
    try:
        if data.get("elements") and struct_mod.is_structural(data.get("element_type")):
            _set_structural(project, data["nodes"], data["elements"], data["element_type"])
        elif data.get("elements"):
            mesh = FEMesh(data["nodes"], data["elements"], data.get("element_type") or "C3D4")
            _set_mesh(project, mesh)
        for key in ("material", "boundary_conditions", "loads", "gravity", "settings",
                    "reference_points", "constraints", "node_sets"):
            if key in data and data[key] is not None:
                setattr(project, key, data[key])
        project.material = schemas.MaterialUpdate(**{**(project.material or {}), **(data.get("material") or {})}).model_dump()
        lib = []
        for m in data.get("materials") or []:
            item = schemas.MaterialItem(**{k: v for k, v in m.items() if k != "id"}).model_dump()
            item["id"] = str(m.get("id") or parts_mod.new_id(lib, "m"))
            lib.append(item)
        project.materials = lib
        if project.elements and data.get("parts"):
            ids = {"default"} | {m["id"] for m in lib}
            plist = []
            for q in data["parts"]:
                q = dict(q)
                q["id"] = str(q.get("id") or parts_mod.new_id(plist, "p"))
                q["material_id"] = q.get("material_id") if q.get("material_id") in ids else "default"
                q["name"] = str(q.get("name") or q["id"])[:60]
                if _is_struct(project):
                    q["section"] = _clean_section(project.element_type, q.get("section"))
                plist.append(q)
            parts_mod.check_tiling(plist, len(project.elements))
            project.parts = plist
            project.surface_parts = (_struct_surface_parts(project.element_type, plist, len(project.elements)) if _is_struct(project)
                                     else parts_mod.surface_parts(FEMesh(project.nodes, project.elements, project.element_type), plist))
        atype = data.get("analysis_type") or "static"
        project.analysis_type = atype if atype in plans.limits_for(tier_of(user)).analysis_types else "static"
        project.n_modes = max(1, min(int(data.get("n_modes") or 6), 50))
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"Invalid project file: {exc}")


@app.get("/api/projects/{project_id}/history")
def project_history(project: models.Project = Depends(owned_project)):
    return history.info(project)


@app.post("/api/projects/{project_id}/undo", response_model=schemas.ProjectOut)
def undo_edit(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    try:
        history.undo(db, project)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _commit(db, project)


@app.post("/api/projects/{project_id}/redo", response_model=schemas.ProjectOut)
def redo_edit(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    try:
        history.redo(db, project)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _commit(db, project)


@app.post("/api/projects/{project_id}/history/{seq}", response_model=schemas.ProjectOut)
def goto_history(seq: int, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Jump to one state of the active model's undo history (the History list of the Model panel)."""
    try:
        history.goto(db, project, seq)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc))
    return _commit(db, project)


@app.get("/api/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    if not project.layers:            # created before the model stack existed
        layers.ensure(db, project)
        return _commit(db, project)
    return project


@app.delete("/api/projects/{project_id}")
def delete_project(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    workspaces.delete_project_data(db, project)   # CFD cases + run files, CAD project + uploads
    db.delete(project)
    db.commit()
    return {"ok": True}


@app.get("/api/projects/{project_id}/workspaces")
def project_workspaces(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """What the FEA, CFD and CAD workspaces of a project hold. Creates the (empty) CAD project on first use, so
    `cad.cad_project_id` is always set."""
    workspaces.cad_project_for(db, project)
    db.commit()
    return workspaces.summary(db, project)


@app.get("/api/projects/{project_id}/export/project")
def export_project(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    # top-level keys = the active model, so version-1 readers still open the file; `layers` = the whole stack, bottom first
    layers.ensure(db, project)
    data = {"format": projectfile.FORMAT, "version": projectfile.VERSION, "workspaces": ["fea", "cfd", "cad"]}
    data.update({k: getattr(project, k) for k in PROJECT_FILE_KEYS})
    data["layers"] = [
        {"name": l.name, "process": l.process, "visible": bool(l.visible), "active": l.id == project.active_layer_id,
         "model": {k: v for k, v in layers.model_state(project, l).items() if k in PROJECT_FILE_KEYS}}
        for l in sorted(project.layers, key=lambda q: q.position)]
    data.update(projectfile.export_sections(db, project))
    db.commit()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.name)[:60] or "project"
    return Response(content=json.dumps(data), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.gearhead.json"'})


@app.get("/api/projects/{project_id}/export/inp")
def export_inp(project: models.Project = Depends(owned_project)):
    if _is_struct(project):
        raise HTTPException(400, "Structural (beam / shell / truss / spring) models cannot be exported to .inp yet. Use 'Save project'.")
    _fem_mesh(project)
    from .jobs import project_to_dict
    try:
        body = inp.export_inp(project_to_dict(project))
    except (ValueError, inp.InpError) as exc:
        raise HTTPException(400, str(exc))
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.name)[:60] or "model"
    return Response(content=body, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.inp"'})


# ------------------------------------------------------------- Model stack
def _layer_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError:
        raise HTTPException(404, "Model not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/projects/{project_id}/layers", response_model=schemas.ProjectOut)
def add_layer(payload: schemas.LayerCreate, project: models.Project = Depends(owned_project),
              db: Session = Depends(get_db)):
    """Put a new model on top of the stack (empty, or a copy of `duplicate_of`) and make it the active one."""
    _layer_call(layers.add, db, project, payload.name, payload.process, payload.duplicate_of)
    return _commit(db, project)


@app.put("/api/projects/{project_id}/layers/{layer_id}", response_model=schemas.ProjectOut)
def update_layer(layer_id: str, payload: schemas.LayerUpdate, project: models.Project = Depends(owned_project),
                 db: Session = Depends(get_db)):
    layers.ensure(db, project)
    _layer_call(layers.update, project, layer_id, payload.name, payload.process, payload.visible)
    return _commit(db, project)


@app.post("/api/projects/{project_id}/layers/{layer_id}/activate", response_model=schemas.ProjectOut)
def activate_layer(layer_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    _layer_call(layers.activate, db, project, layer_id)
    return _commit(db, project)


@app.post("/api/projects/{project_id}/layers/{layer_id}/move", response_model=schemas.ProjectOut)
def move_layer(layer_id: str, payload: schemas.LayerMove, project: models.Project = Depends(owned_project),
               db: Session = Depends(get_db)):
    layers.ensure(db, project)
    _layer_call(layers.move, project, layer_id, payload.direction)
    return _commit(db, project)


@app.delete("/api/projects/{project_id}/layers/{layer_id}", response_model=schemas.ProjectOut)
def delete_layer(layer_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    _layer_call(layers.delete, db, project, layer_id)
    return _commit(db, project)


@app.get("/api/projects/{project_id}/layers/{layer_id}/preview")
def layer_preview(layer_id: str, project: models.Project = Depends(owned_project)):
    """Geometry of an inactive model, for drawing it as a reference ghost next to the active one."""
    layer = next((l for l in project.layers or [] if l.id == layer_id), None)
    if layer is None:
        raise HTTPException(404, "Model not found")
    state = layers.model_state(project, layer)
    etype = state.get("element_type") or "C3D4"
    line = struct_mod.is_structural(etype) and etype != "S4"
    return {"id": layer.id, "nodes": state.get("nodes") or [], "surface_faces": state.get("surface_faces") or [],
            "segments": [e[:2] for e in state.get("elements") or []] if line else []}


# ------------------------------------------------------------------- Mesh
def _make_primitive(payload: schemas.PrimitiveMeshRequest, force_type: str = None) -> FEMesh:
    """Mesh a primitive with the built-in generators or with Gmsh. `force_type` pins the element type (adding a body)."""
    try:
        et = get_element_type(force_type or payload.element_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if payload.mesher != "gmsh":
        try:
            return meshgen.generate(payload.shape, payload.dims, payload.divisions, et.name)
        except ValueError as exc:
            raise HTTPException(400, f"Mesh generation failed: {exc}")
    opts = dict(payload.gmsh or {})
    if force_type or "family" not in opts:
        opts["family"] = "hex" if et.kind == "hex" else "tet"
    if force_type or "order" not in opts:
        opts["order"] = 2 if et.n_nodes > et.n_corner else 1
    try:
        res = cad.mesh_primitive(payload.shape, payload.dims, payload.divisions, opts)
    except cad.CadError as exc:
        raise HTTPException(400, f"Mesh generation failed: {exc}")
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    try:
        mesh = FEMesh(res.nodes, res.elems, res.element_type)
    except ValueError as exc:
        raise HTTPException(400, f"Mesh generation failed: {exc}")
    if mesh.n_elems > meshgen.MAX_ELEMENTS:
        raise HTTPException(400, f"Mesh generation failed: Mesh would have {mesh.n_elems} elements "
                                 f"(limit {meshgen.MAX_ELEMENTS}). Use a larger element size.")
    return mesh


def _parts_from_ends(stem: str, ends, n_elems: int) -> list:
    """One part per solid: `ends` holds the cumulative element count after each solid."""
    plist, start = [], 0
    for k, end in enumerate(int(e) for e in ends[:50]):
        plist.append({"id": f"p{k}", "name": stem if len(ends) == 1 else f"{stem} {k + 1}", "elem_start": start,
                      "elem_end": end, "material_id": "default"})
        start = end
    if start < n_elems and plist:                    # more than 50 solids: the rest joins the last part
        plist[-1]["elem_end"] = n_elems
    return plist


@app.post("/api/projects/{project_id}/mesh/primitive", response_model=schemas.ProjectOut)
def generate_primitive(payload: schemas.PrimitiveMeshRequest, project: models.Project = Depends(owned_project),
                       db: Session = Depends(get_db)):
    mesh = _make_primitive(payload)
    _set_mesh(project, mesh, meshgen.SHAPES.get(payload.shape, "Body"))
    _tag_parts(project, _primitive_source(payload))
    return _commit(db, project, "Generate mesh")


@app.post("/api/projects/{project_id}/mesh/append", response_model=schemas.ProjectOut)
def append_part(payload: schemas.AppendPartRequest, project: models.Project = Depends(owned_project),
                db: Session = Depends(get_db)):
    """Add another primitive as a separate body (use ties/rigid constraints to join bodies). It takes the model's element type."""
    base = _fem_mesh(project)
    part = _make_primitive(payload, base.et.name)
    if len(payload.offset) != 3:
        raise HTTPException(422, "offset needs 3 numbers")
    import numpy as np
    old_parts = _ensure_parts(project)
    mid = payload.material_id or "default"
    if mid not in _material_ids(project):
        raise HTTPException(422, "Unknown material")
    part.nodes += np.array(payload.offset, dtype=float)
    merged = FEMesh(np.vstack([base.nodes, part.nodes]), np.vstack([base.elems, part.elems + base.n_nodes]), base.et.name)
    label = (payload.name or meshgen.SHAPES.get(payload.shape, "Body")).strip()
    taken = {p["name"] for p in old_parts}
    name, n = label, 2
    while name in taken:
        name, n = f"{label} {n}", n + 1
    new_part = {"id": parts_mod.new_id(old_parts, "p"), "name": name, "elem_start": base.n_elems,
                "elem_end": merged.n_elems, "material_id": mid, "source": _primitive_source(payload, payload.offset)}
    keep = (project.boundary_conditions, project.loads, project.reference_points, project.constraints, project.node_sets)
    _set_mesh(project, merged, parts=[*old_parts, new_part])
    # node ids of the original bodies are unchanged, so the existing setup stays valid
    (project.boundary_conditions, project.loads, project.reference_points,
     project.constraints, project.node_sets) = keep
    return _commit(db, project, "Add body")


@app.post("/api/projects/{project_id}/mesh/structural", response_model=schemas.ProjectOut)
def generate_structural(payload: schemas.StructuralMeshRequest, project: models.Project = Depends(owned_project),
                        db: Session = Depends(get_db)):
    """Generate a beam / truss / spring / shell model (separate model class: 6 DOFs per node)."""
    try:
        nodes, elems = structmesh.generate(payload.kind, payload.dims, payload.element_type, payload.divisions)
    except struct_mod.StructuralError as exc:
        raise HTTPException(400, f"Mesh generation failed: {exc}")
    mid = payload.material_id or "default"
    if mid not in _material_ids(project):
        raise HTTPException(422, "Unknown material")
    _set_structural(project, nodes, elems, payload.element_type, section=payload.section, material_id=mid,
                    name=structmesh.NAMES.get(payload.kind, "Body"))
    _tag_parts(project, _structural_source(payload))
    return _commit(db, project, "Generate structural mesh")


@app.post("/api/projects/{project_id}/mesh/structural/append", response_model=schemas.ProjectOut)
def append_structural(payload: schemas.AppendStructuralRequest, project: models.Project = Depends(owned_project),
                      db: Session = Depends(get_db)):
    """Add members to a structural model as a new part (own material and section); coincident nodes are joined."""
    import numpy as np
    from scipy.spatial import cKDTree
    if not _is_struct(project) or not project.elements:
        raise HTTPException(400, "Generate a structural model first")
    if payload.element_type != project.element_type:
        raise HTTPException(422, f"This model uses {project.element_type} elements; the new part must use the same type.")
    if len(payload.offset) != 3:
        raise HTTPException(422, "offset needs 3 numbers")
    try:
        nodes, elems = structmesh.generate(payload.kind, payload.dims, payload.element_type, payload.divisions)
    except struct_mod.StructuralError as exc:
        raise HTTPException(400, f"Mesh generation failed: {exc}")
    mid = payload.material_id or "default"
    if mid not in _material_ids(project):
        raise HTTPException(422, "Unknown material")
    base_nodes = np.array(project.nodes, dtype=float)
    base_elems = np.array(project.elements, dtype=np.int64)
    new_nodes = nodes + np.array(payload.offset, dtype=float)
    mapping = -np.ones(len(new_nodes), dtype=np.int64)
    if payload.merge:
        scale = max(float(np.ptp(base_nodes, axis=0).max()), float(np.ptp(new_nodes, axis=0).max()), 1.0)
        dist, idx = cKDTree(base_nodes).query(new_nodes)
        hit = dist <= 1e-9 * scale
        mapping[hit] = idx[hit]
    fresh = np.where(mapping < 0)[0]
    mapping[fresh] = len(base_nodes) + np.arange(len(fresh))
    merged_nodes = np.vstack([base_nodes, new_nodes[fresh]])
    new_elems = mapping[elems]
    if payload.element_type in ("B31", "T3D2", "SPRING2") and np.any(new_elems[:, 0] == new_elems[:, 1]):
        raise HTTPException(422, "A new element would collapse onto a single node")
    old_parts = _ensure_parts(project)
    label = (payload.name or structmesh.NAMES.get(payload.kind, "Body")).strip()
    taken = {p["name"] for p in old_parts}
    name, k = label, 2
    while name in taken:
        name, k = f"{label} {k}", k + 1
    new_part = {"id": parts_mod.new_id(old_parts, "p"), "name": name, "elem_start": len(base_elems),
                "elem_end": len(base_elems) + len(new_elems), "material_id": mid,
                "section": _clean_section(payload.element_type, payload.section),
                "source": _structural_source(payload, payload.offset)}
    keep = (project.boundary_conditions, project.loads, project.node_sets)
    all_elems = np.vstack([base_elems, new_elems])
    _set_structural(project, merged_nodes, all_elems, payload.element_type, parts=[*old_parts, new_part])
    project.boundary_conditions, project.loads, project.node_sets = keep   # node ids of existing members are unchanged
    if payload.element_type == "S4":                                      # face-based loads would address old triangle lists
        project.loads = [l for l in project.loads if not l.get("faces")]
    return _commit(db, project, "Add members")


@app.post("/api/projects/{project_id}/mesh/import", response_model=schemas.ProjectOut)
async def import_mesh(file: UploadFile = File(...), project: models.Project = Depends(owned_project),
                      db: Session = Depends(get_db)):
    """Load a mesh from a CalculiX/Abaqus .inp or a Gmsh 2.2 ASCII .msh file."""
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (limit 25 MB)")
    name = (file.filename or "").lower()
    try:
        body = raw.decode("utf-8", errors="replace")
        model = None
        msh_parts = None
        if name.endswith(".msh"):
            try:
                mesh = inp.import_msh(body)
            except inp.InpError as text_error:            # not Gmsh 2.2 ASCII: let Gmsh itself read it (v4, binary, high order)
                if not cad.available():
                    raise text_error
                try:
                    res = cad.mesh_msh(raw, "m")
                except cad.CadError as exc:
                    raise HTTPException(400, f"Could not read mesh: {exc}")
                mesh = FEMesh(res.nodes, res.elems, res.element_type)
                msh_parts = _parts_from_ends(os.path.splitext(os.path.basename(file.filename or "Mesh"))[0][:40] or "Mesh",
                                             res.ends, mesh.n_elems)
        elif name.endswith(".inp"):
            model = inp.import_inp_model(body)
            mesh = model.mesh
        else:
            raise HTTPException(415, "Unsupported file type. Use .inp (Abaqus/CalculiX) or .msh (Gmsh 2.2 ASCII).")
    except (inp.InpError, ValueError) as exc:
        raise HTTPException(400, f"Could not read mesh: {exc}")
    _set_mesh(project, mesh, parts=msh_parts if msh_parts and len(msh_parts) > 1 else None)
    if model is not None:
        _apply_inp_parts(project, mesh, model)
    _tag_parts(project, {"kind": "mesh_file", "file": os.path.basename(file.filename or "mesh")[:120],
                         "format": "msh" if name.endswith(".msh") else "inp"})
    return _commit(db, project, "Import mesh")


@app.post("/api/projects/{project_id}/mesh/structural/custom", response_model=schemas.ProjectOut)
def custom_structural(payload: schemas.CustomStructuralMesh, project: models.Project = Depends(owned_project),
                      db: Session = Depends(get_db)):
    """Arbitrary truss / frame / spring / shell layout from node coordinates and connectivity."""
    _set_structural(project, payload.nodes, payload.elements, payload.element_type, section=payload.section)
    _tag_parts(project, {"kind": "custom", "label": "Custom layout"})
    return _commit(db, project, "Upload structural mesh")


@app.post("/api/projects/{project_id}/mesh/cad", response_model=schemas.ProjectOut)
def import_cad(file: UploadFile = File(...), size: float = Form(0.0), order: int = Form(2), unit: str = Form("mm"),
               family: str = Form("tet"), options: str = Form(""),
               project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """
    Mesh a STEP / IGES / BREP / STL file with Gmsh. Each solid becomes a part. `family` is "tet" (Tet4 / Tet10) or "hex"
    (Hex8 / Hex20), `order` 1 or 2, `size` the target element size in metres (0 = automatic) and `unit` the length unit of the
    file (mm, cm, m, in). `options` is an optional JSON object with any further Gmsh option (algorithms, refinement regions,
    optimisers - see fea/gmsh_options.py). The geometry kernel runs in a separate process with a time limit.
    """
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (limit 25 MB)")
    try:
        extra = json.loads(options) if options.strip() else {}
        if not isinstance(extra, dict):
            raise ValueError("options must be a JSON object")
    except ValueError as exc:
        raise HTTPException(422, f"Invalid Gmsh options: {exc}")
    extra.setdefault("family", family)
    try:
        res = cad.mesh_cad(raw, file.filename or "", size, order, unit, extra)
    except cad.CadError as exc:
        raise HTTPException(400, f"Could not import the CAD file: {exc}")
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    try:
        mesh = FEMesh(res.nodes, res.elems, res.element_type)
    except ValueError as exc:
        raise HTTPException(400, f"The imported mesh is invalid: {exc}")
    stem = (os.path.splitext(os.path.basename(file.filename or "CAD"))[0] or "CAD")[:40]
    try:
        _set_mesh(project, mesh, parts=_parts_from_ends(stem, res.ends, mesh.n_elems))
    except ValueError as exc:
        raise HTTPException(400, f"The imported mesh is invalid: {exc}")
    _tag_parts(project, {"kind": "cad", "file": os.path.basename(file.filename or "CAD")[:120], "unit": unit,
                         "size": size, "family": extra.get("family", family), "order": order})
    return _commit(db, project, "Import CAD")


@app.post("/api/projects/{project_id}/mesh/custom", response_model=schemas.ProjectOut)
def upload_custom_mesh(nodes: List[List[float]], elements: List[List[int]], element_type: str = "C3D4",
                       project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    try:
        mesh = FEMesh(nodes, elements, element_type)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid mesh: {exc}")
    _set_mesh(project, mesh)
    _tag_parts(project, {"kind": "custom", "label": "Uploaded mesh"})
    return _commit(db, project, "Upload mesh")


@app.get("/api/projects/{project_id}/mesh/quality")
def mesh_quality(project: models.Project = Depends(owned_project)):
    if _is_struct(project):
        import numpy as np
        nodes = np.array(project.nodes, dtype=float)
        elems = np.array(project.elements, dtype=np.int64)
        if len(elems) == 0:
            raise HTTPException(400, "Project has no mesh yet")
        X = nodes[elems]
        out = {"n_nodes": int(len(nodes)), "n_elements": int(len(elems)), "element_type": project.element_type,
               "model_class": "structural"}
        if project.element_type == "S4":
            n = np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 1]) / 2.0
            out["total_area"] = float(np.linalg.norm(n, axis=1).sum())
        else:
            out["total_length"] = float(np.linalg.norm(X[:, 1] - X[:, 0], axis=1).sum())
        return out
    return _fem_mesh(project).quality()


# ------------------------------------------------------- Model definition
@app.put("/api/projects/{project_id}/material", response_model=schemas.ProjectOut)
def set_material(payload: schemas.MaterialUpdate, project: models.Project = Depends(owned_project),
                 db: Session = Depends(get_db)):
    project.material = payload.model_dump()
    return _commit(db, project, "Edit default material")


# ------------------------------------------------------ materials & parts
def _library_item(payload: schemas.MaterialItem, mid: str) -> dict:
    item = payload.model_dump()
    item["id"] = mid
    return item


@app.post("/api/projects/{project_id}/materials", response_model=schemas.ProjectOut)
def add_material(payload: schemas.MaterialItem, project: models.Project = Depends(owned_project),
                 db: Session = Depends(get_db)):
    lib = list(project.materials or [])
    if len(lib) >= 30:
        raise HTTPException(422, "At most 30 extra materials per project")
    lib.append(_library_item(payload, parts_mod.new_id(lib, "m")))
    project.materials = lib
    return _commit(db, project, "Add material")


@app.put("/api/projects/{project_id}/materials/{material_id}", response_model=schemas.ProjectOut)
def update_material(material_id: str, payload: schemas.MaterialItem, project: models.Project = Depends(owned_project),
                    db: Session = Depends(get_db)):
    if material_id == "default":
        project.material = payload.model_dump()
        return _commit(db, project, "Edit material")
    lib = list(project.materials or [])
    for i, m in enumerate(lib):
        if m["id"] == material_id:
            lib[i] = _library_item(payload, material_id)
            project.materials = lib
            return _commit(db, project, "Edit material")
    raise HTTPException(404, "Material not found")


@app.delete("/api/projects/{project_id}/materials/{material_id}", response_model=schemas.ProjectOut)
def delete_material(material_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    if material_id == "default":
        raise HTTPException(400, "The default material cannot be deleted")
    if material_id not in _material_ids(project):
        raise HTTPException(404, "Material not found")
    project.materials = [m for m in (project.materials or []) if m["id"] != material_id]
    project.parts = [{**p, "material_id": "default" if p.get("material_id") == material_id else p.get("material_id")}
                     for p in (project.parts or [])]
    return _commit(db, project, "Delete material")


@app.put("/api/projects/{project_id}/parts/{part_id}", response_model=schemas.ProjectOut)
def update_part(part_id: str, payload: schemas.PartUpdate, project: models.Project = Depends(owned_project),
                db: Session = Depends(get_db)):
    plist = _ensure_parts(project)
    part = next((p for p in plist if p["id"] == part_id), None)
    if part is None:
        raise HTTPException(404, "Part not found")
    if payload.name is not None:
        part["name"] = payload.name.strip()
    if payload.material_id is not None:
        if payload.material_id not in _material_ids(project):
            raise HTTPException(422, "Unknown material")
        part["material_id"] = payload.material_id
    if payload.section is not None:
        if not _is_struct(project):
            raise HTTPException(422, "Sections apply to structural (beam / shell / truss / spring) parts only")
        part["section"] = _clean_section(project.element_type, payload.section)
    project.parts = plist
    return _commit(db, project, "Edit part")


@app.post("/api/projects/{project_id}/parts/{part_id}/translate", response_model=schemas.ProjectOut)
def translate_part(part_id: str, payload: schemas.TranslateRequest, project: models.Project = Depends(owned_project),
                   db: Session = Depends(get_db)):
    import numpy as np
    plist = _ensure_parts(project)
    try:
        nodes = parts_mod.translate(np.array(project.nodes, dtype=float), np.array(project.elements, dtype=np.int64),
                                    plist, part_id, payload.offset)
    except KeyError:
        raise HTTPException(404, "Part not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    project.nodes = nodes.tolist()
    project.parts = plist
    return _commit(db, project, "Move part")


@app.delete("/api/projects/{project_id}/parts/{part_id}", response_model=schemas.ProjectOut)
def delete_part(part_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Remove a body. Mesh is compressed; BCs, loads, constraints and sets are remapped (or dropped if emptied)."""
    import numpy as np
    plist = _ensure_parts(project)
    try:
        nodes, elems, new_parts, remap, _ = parts_mod.remove_part(
            np.array(project.nodes, dtype=float), np.array(project.elements, dtype=np.int64), plist, part_id)
    except KeyError:
        raise HTTPException(404, "Part not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    setup = parts_mod.remap_setup({
        "boundary_conditions": project.boundary_conditions, "loads": project.loads, "constraints": project.constraints,
        "node_sets": project.node_sets, "settings": project.settings}, remap, part_id)
    if _is_struct(project):
        project.nodes = nodes.tolist()
        project.elements = elems.tolist()
        project.parts = new_parts
        project.surface_faces = _struct_triangles(project.element_type, elems)
        project.surface_mid = []
        project.surface_parts = _struct_surface_parts(project.element_type, new_parts, len(elems))
    else:
        mesh = FEMesh(nodes, elems, project.element_type or "C3D4")
        project.nodes = mesh.nodes.tolist()
        project.elements = mesh.elems.tolist()
        project.parts = new_parts
        _set_surface(project, mesh)
        project.surface_parts = parts_mod.surface_parts(mesh, new_parts)
    for key, value in setup.items():
        setattr(project, key, value)
    return _commit(db, project, "Delete part")


@app.put("/api/projects/{project_id}/analysis-type", response_model=schemas.ProjectOut)
def set_analysis_type(payload: schemas.AnalysisTypeUpdate, project: models.Project = Depends(owned_project),
                      user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.analysis_type not in ANALYSIS_TYPES:
        raise HTTPException(400, f"analysis_type must be one of: {', '.join(ANALYSIS_TYPES)}")
    require_pro_for(payload.analysis_type, user)
    thermal = {"thermal", "thermal_transient"}
    if (payload.analysis_type in thermal) != ((project.analysis_type or "static") in thermal):
        # Untyped legacy BCs change meaning (displacement vs temperature) between these families;
        # BCs with an explicit bc_type are unambiguous and are kept (thermo-mechanical needs both).
        project.boundary_conditions = [b for b in (project.boundary_conditions or []) if b.get("bc_type")]
    project.analysis_type = payload.analysis_type
    project.n_modes = payload.n_modes
    return _commit(db, project, "Change analysis type")


@app.put("/api/projects/{project_id}/settings", response_model=schemas.ProjectOut)
def set_settings(payload: schemas.SettingsUpdate, project: models.Project = Depends(owned_project),
                 db: Session = Depends(get_db)):
    cur = dict(project.settings or {})
    for k, v in payload.model_dump(exclude_none=True).items():
        if k == "clear":
            continue
        cur[k] = v
    for k in payload.clear or []:
        cur.pop(k, None)
    project.settings = cur
    if "n_modes" in cur:
        project.n_modes = int(cur["n_modes"])
    return _commit(db, project, "Change analysis settings")


@app.put("/api/projects/{project_id}/gravity", response_model=schemas.ProjectOut)
def set_gravity(payload: schemas.GravityUpdate, project: models.Project = Depends(owned_project),
                db: Session = Depends(get_db)):
    project.gravity = payload.model_dump()
    return _commit(db, project, "Change gravity")


@app.post("/api/projects/{project_id}/bcs", response_model=schemas.ProjectOut)
def add_bc(payload: schemas.BoundaryCondition, project: models.Project = Depends(owned_project),
           db: Session = Depends(get_db)):
    bc = payload.model_dump()
    if bc.get("ref_id"):
        if bc["ref_id"] not in _ref_ids(project):
            raise HTTPException(422, "Unknown reference point")
        if any(d not in range(6) for d in bc["dofs"]):
            raise HTTPException(422, "Reference-point DOFs must be 0..5")
    else:
        _check_node_ids(project, bc["node_ids"])
        if not bc["node_ids"]:
            raise HTTPException(422, "Select at least one node first")
        limit = 1 if bc.get("bc_type") == "temperature" else (6 if _is_struct(project) else 3)
        if any(d < 0 or d >= max(limit, 1) for d in bc["dofs"]) and bc.get("bc_type") != "temperature":
            raise HTTPException(422, "DOFs must be 0..5 (x, y, z, rotation x, y, z)" if _is_struct(project)
                                else "DOFs must be 0 (x), 1 (y) or 2 (z)")
    bc["id"] = bc.get("id") or _new_id()
    project.boundary_conditions = [*(project.boundary_conditions or []), bc]
    return _commit(db, project, "Add support")


@app.delete("/api/projects/{project_id}/bcs/{bc_id}", response_model=schemas.ProjectOut)
def delete_bc(bc_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    project.boundary_conditions = [b for b in (project.boundary_conditions or []) if b.get("id") != bc_id]
    return _commit(db, project, "Delete support")


@app.post("/api/projects/{project_id}/loads", response_model=schemas.ProjectOut)
def add_load(payload: schemas.Load, project: models.Project = Depends(owned_project),
             db: Session = Depends(get_db)):
    ld = payload.model_dump()
    kind = ld["load_type"]
    valid = {"force", "pressure", "traction", "ref_force", "heat_flux", "surface_flux", "body_heat",
             "convection", "radiation", "line_load", "point_mass"}
    if kind not in valid:
        raise HTTPException(422, f"load_type must be one of: {', '.join(sorted(valid))}")
    _check_node_ids(project, ld["node_ids"])
    if kind in ("pressure", "traction", "surface_flux", "convection", "radiation") and not ld.get("faces"):
        raise HTTPException(422, "Select at least one face first")
    if kind in ("force", "heat_flux", "point_mass") and not ld["node_ids"]:
        raise HTTPException(422, "Select at least one node first")
    if kind in ("line_load", "point_mass") and not _is_struct(project):
        raise HTTPException(422, f"'{kind}' loads apply to structural (beam / shell / truss / spring) models only")
    if kind == "point_mass" and (ld.get("magnitude") is None or ld["magnitude"] <= 0):
        raise HTTPException(422, "A point mass needs a positive magnitude (kg)")
    if kind == "line_load" and ld.get("part_id") and ld["part_id"] not in {p["id"] for p in _ensure_parts(project)}:
        raise HTTPException(422, "Unknown part")
    if kind == "ref_force" and ld.get("ref_id") not in _ref_ids(project):
        raise HTTPException(422, "Unknown reference point")
    if kind == "convection" and (ld.get("h") is None or ld.get("T_inf") is None):
        raise HTTPException(422, "Convection needs h and T_inf")
    if kind == "radiation" and (ld.get("emissivity") is None or ld.get("T_inf") is None):
        raise HTTPException(422, "Radiation needs emissivity and T_inf")
    ld["id"] = ld.get("id") or _new_id()
    project.loads = [*(project.loads or []), ld]
    return _commit(db, project, "Add load")


@app.delete("/api/projects/{project_id}/loads/{load_id}", response_model=schemas.ProjectOut)
def delete_load(load_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    project.loads = [l for l in (project.loads or []) if l.get("id") != load_id]
    return _commit(db, project, "Delete load")


@app.post("/api/projects/{project_id}/reference-points", response_model=schemas.ProjectOut)
def add_reference_point(payload: schemas.ReferencePoint, project: models.Project = Depends(owned_project),
                        db: Session = Depends(get_db)):
    rp = payload.model_dump()
    rp["id"] = rp.get("id") or _new_id()
    project.reference_points = [*(project.reference_points or []), rp]
    return _commit(db, project, "Add reference point")


@app.delete("/api/projects/{project_id}/reference-points/{rp_id}", response_model=schemas.ProjectOut)
def delete_reference_point(rp_id: str, project: models.Project = Depends(owned_project),
                           db: Session = Depends(get_db)):
    project.reference_points = [r for r in (project.reference_points or []) if r.get("id") != rp_id]
    project.constraints = [c for c in (project.constraints or []) if c.get("ref_id") != rp_id]
    project.boundary_conditions = [b for b in (project.boundary_conditions or []) if b.get("ref_id") != rp_id]
    project.loads = [l for l in (project.loads or []) if l.get("ref_id") != rp_id]
    return _commit(db, project, "Delete reference point")


@app.post("/api/projects/{project_id}/constraints", response_model=schemas.ProjectOut)
def add_constraint(payload: schemas.Constraint, project: models.Project = Depends(owned_project),
                   db: Session = Depends(get_db)):
    c = payload.model_dump()
    if c["type"] == "rigid":
        if c.get("ref_id") not in _ref_ids(project):
            raise HTTPException(422, "A rigid body needs a reference point")
        if not c.get("faces") and not c["node_ids"]:
            raise HTTPException(422, "Select the surface that moves with the reference point")
    elif c["type"] == "contact":
        pids = {p["id"] for p in _ensure_parts(project)}
        if c.get("master_part") not in pids or c.get("slave_part") not in pids:
            raise HTTPException(422, "Contact needs two existing parts")
        if c["master_part"] == c["slave_part"]:
            raise HTTPException(422, "Contact needs two different parts")
    elif c["type"] == "tie" and (c.get("master_part") or c.get("slave_part")):
        pids = {p["id"] for p in _ensure_parts(project)}
        if c.get("master_part") not in pids or c.get("slave_part") not in pids:
            raise HTTPException(422, "Choose two existing parts")
        if c["master_part"] == c["slave_part"]:
            raise HTTPException(422, "A tie needs two different parts")
    elif c["type"] == "tie":
        if not (c.get("slave_faces") or c["slave_node_ids"]) or not c.get("master_faces"):
            raise HTTPException(422, "A tie needs a slave selection and a master surface")
    else:
        raise HTTPException(422, "Constraint type must be 'rigid', 'tie' or 'contact'")
    c["id"] = c.get("id") or _new_id()
    project.constraints = [*(project.constraints or []), c]
    return _commit(db, project, "Add constraint")


@app.delete("/api/projects/{project_id}/constraints/{c_id}", response_model=schemas.ProjectOut)
def delete_constraint(c_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    project.constraints = [c for c in (project.constraints or []) if c.get("id") != c_id]
    return _commit(db, project, "Delete constraint")


@app.post("/api/projects/{project_id}/node-sets", response_model=schemas.ProjectOut)
def add_node_set(payload: schemas.NodeSet, project: models.Project = Depends(owned_project),
                 db: Session = Depends(get_db)):
    s = payload.model_dump()
    if s["kind"] not in ("nodes", "faces") or not s["ids"]:
        raise HTTPException(422, "A set needs a kind ('nodes' or 'faces') and at least one item")
    if len(project.node_sets or []) >= 50:
        raise HTTPException(422, "At most 50 named sets per project")
    s["id"] = s.get("id") or _new_id()
    project.node_sets = [*(project.node_sets or []), s]
    return _commit(db, project, "Save set")


@app.delete("/api/projects/{project_id}/node-sets/{set_id}", response_model=schemas.ProjectOut)
def delete_node_set(set_id: str, project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    project.node_sets = [s for s in (project.node_sets or []) if s.get("id") != set_id]
    return _commit(db, project, "Delete set")


# ----------------------------------------------------------------- AI agent
_agent_limiter = RateLimiter(settings.agent_requests_per_minute, 60)


@app.get("/api/agent/status")
def agent_status(user: models.User = Depends(current_user)):
    on = agent_mod.available()
    return {"available": on, "model": settings.agent_active_model if on else None,
            "provider": settings.agent_provider if on else None}


@app.post("/api/projects/{project_id}/agent/chat")
def agent_chat(payload: agent_mod.ChatRequest, project: models.Project = Depends(owned_project),
               user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """
    Chat with the engineering agent. It edits the project with tools and, when the model is complete, answers
    `run_simulation: true`; the browser then starts the analysis through the normal /solve path.
    """
    if payload.messages[-1].role != "user":
        raise HTTPException(422, "The last message must come from the user")
    if not agent_mod.available():
        raise HTTPException(503, agent_mod.NOT_CONFIGURED)
    if not _agent_limiter.hit(f"agent:{user.id}"):
        raise HTTPException(429, "You are sending messages too quickly. Wait a few seconds and try again.")
    out = agent_mod.run_agent(db, user, project, [m.model_dump() for m in payload.messages])
    db.refresh(project)
    return {**out, "project": schemas.ProjectOut.model_validate(project).model_dump()}


# --------------------------------------------------------------------- Jobs
def _expire_stale_jobs(db: Session, user: models.User) -> int:
    """Jobs left 'running' by a crashed worker must not block the user forever."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(minutes=STALE_JOB_MINUTES)
    rows = (db.query(models.Job).join(models.Project, models.Job.project_id == models.Project.id)
            .filter(models.Project.owner_id == user.id, models.Job.status.in_(("pending", "running"))).all())
    active = 0
    for j in rows:
        if j.created_at and j.created_at < cutoff:
            j.status, j.log, j.progress = "failed", "Timed out (the solver stopped responding).", 1.0
        else:
            active += 1
    db.commit()
    return active


@app.post("/api/projects/{project_id}/solve", response_model=schemas.JobOut)
def solve(project: models.Project = Depends(owned_project), user: models.User = Depends(current_user),
          db: Session = Depends(get_db)):
    if not project.elements:
        raise HTTPException(400, "Project has no mesh yet")
    require_pro_for(project.analysis_type or "static", user)  # a lapsed Pro user may hold a Pro-only project
    if _expire_stale_jobs(db, user) >= MAX_ACTIVE_JOBS:
        raise HTTPException(429, "You already have analyses running. Wait for them to finish.")

    layers.ensure(db, project)
    job = models.Job(project_id=project.id, layer_id=project.active_layer_id, status="pending", solver_used=jobs.SOLVER_NAME)
    snapshot = jobs.project_to_dict(project)       # taken now: the user may switch to another model while this runs
    db.add(job)
    db.commit()
    db.refresh(job)
    project_id, job_id = project.id, job.id

    def _run():
        from .database import SessionLocal
        session = SessionLocal()
        try:
            j = session.get(models.Job, job_id)
            p = session.get(models.Project, project_id)
            jobs.run_job(session, j, p, snapshot)
        finally:
            session.close()

    threading.Thread(target=_run, daemon=True).start()
    return job


@app.get("/api/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(models.Job, job_id)
    if not job or job.project is None or job.project.owner_id != user.id:
        raise HTTPException(404, "Job not found")
    return job


def _layer_jobs(db: Session, project: models.Project):
    q = db.query(models.Job).filter(models.Job.project_id == project.id)
    if project.active_layer_id:
        return q.filter(models.Job.layer_id == project.active_layer_id)
    return q.filter(models.Job.layer_id.is_(None))


@app.get("/api/projects/{project_id}/jobs/latest", response_model=schemas.JobOut)
def latest_job(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Most recent completed job, so results survive a page reload."""
    job = (_layer_jobs(db, project).filter(models.Job.status == "completed")
           .order_by(models.Job.created_at.desc()).first())
    if not job:
        raise HTTPException(404, "No completed job yet")
    return job


@app.get("/api/projects/{project_id}/jobs", response_model=List[schemas.JobOut])
def list_jobs(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Recent runs of the active model."""
    return _layer_jobs(db, project).order_by(models.Job.created_at.desc()).limit(20).all()


@app.get("/api/projects/{project_id}/jobs/summary", response_model=List[schemas.JobSummary])
def list_job_summaries(project: models.Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Recent runs of the active model without their result arrays."""
    rows = _layer_jobs(db, project).order_by(models.Job.created_at.desc()).limit(20).all()
    return [{"id": j.id, "layer_id": j.layer_id, "status": j.status, "progress": j.progress or 0.0,
             "analysis_type": (j.results or {}).get("analysis_type"),
             "created_at": j.created_at.isoformat() if j.created_at else None,
             "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j in rows]


# ------------------------------------------------------------- FEA / CFD / CAD exchange (bridge.py)
from . import bridge as _bridge  # noqa: E402  (imports this module's helpers lazily)

app.include_router(_bridge.router)
