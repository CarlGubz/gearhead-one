from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import models as core_models, workspaces
from ...scope import bind_user, current_user_id, owns_project, require_project_owner
from ..config import get_settings
from ..db import get_db
from ..models import Project, Run
from ..schemas import (
    CaseConfig,
    CheckResult,
    ProjectCreate,
    ProjectListItem,
    ProjectOut,
    ProjectUpdate,
)
from ..services.checks import check_case
from ..solver.backends import BACKENDS, list_backends
from ..templates import TEMPLATES, build_template, list_templates

# CFD cases ("projects" in the original CFDFlow API) belong to Gearhead projects; every route needs a signed-in user.
router = APIRouter(prefix="/api/cfd", tags=["cfd"], dependencies=[Depends(bind_user)])


def _get(db: Session, pid: str) -> Project:
    """A CFD case of the current user (404 for missing cases and for other users' cases)."""
    p = db.get(Project, pid)
    if not p or not owns_project(db, p.project_id):
        raise HTTPException(404, "Project not found")
    return p


@router.get("/templates")
def templates():
    return list_templates()


@router.get("/solvers")
def solvers():
    """Solver backends, their capabilities and whether they can run on this server."""
    return list_backends()


@router.post("/check", response_model=CheckResult)
def check(config: CaseConfig):
    """Validate an (unsaved) case definition - used for live feedback in the editor."""
    return check_case(config)


@router.get("/projects", response_model=list[ProjectListItem])
def list_projects(project_id: str | None = Query(None, description="only the cases of this Gearhead project"),
                  db: Session = Depends(get_db)):
    owned = select(core_models.Project.id).where(core_models.Project.owner_id == current_user_id())
    if project_id is not None:
        require_project_owner(db, project_id)
        owned = owned.where(core_models.Project.id == project_id)
    out = []
    for p in db.scalars(select(Project).where(Project.project_id.in_(owned)).order_by(Project.updated_at.desc())):
        cnt = db.scalar(select(func.count()).select_from(Run).where(Run.project_id == p.id)) or 0
        last = db.scalar(select(Run.status).where(Run.project_id == p.id).order_by(Run.created_at.desc()).limit(1))
        cfg = p.config
        out.append(
            ProjectListItem(
                id=p.id,
                project_id=p.project_id,
                name=p.name,
                description=p.description,
                template=p.template,
                updated_at=p.updated_at,
                created_at=p.created_at,
                nx=cfg["mesh"]["nx"],
                ny=cfg["mesh"]["ny"],
                nz=cfg["mesh"].get("nz", 1) if cfg.get("dimension") == "3d" else 1,
                dimension=cfg.get("dimension", "2d"),
                backend=cfg.get("solver", {}).get("backend", "native"),
                mode=cfg["run"]["mode"],
                energy=cfg["models"]["energy"],
                run_count=cnt,
                last_run_status=last,
            )
        )
    return out


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    desc = body.description
    if body.config is not None:
        cfg = body.config
    elif body.template:
        if body.template not in TEMPLATES:
            raise HTTPException(404, "Unknown template")
        _, tdesc, cfg = build_template(body.template)
        desc = desc or tdesc
    else:
        cfg = build_template("blank")[2]
    if body.project_id:
        owner = require_project_owner(db, body.project_id)
    else:  # stand-alone CFDFlow call: the case gets a Gearhead project of its own
        owner = workspaces.new_project(db, workspaces.user_by_id(db, current_user_id()), body.name, desc or "")
    p = Project(project_id=owner.id, name=body.name, description=desc, template=body.template,
                config=cfg.model_dump(mode="json"))
    db.add(p)
    db.commit()
    return p


@router.get("/projects/{pid}", response_model=ProjectOut)
def get_project(pid: str, db: Session = Depends(get_db)):
    return _get(db, pid)


@router.put("/projects/{pid}", response_model=ProjectOut)
def update_project(pid: str, body: ProjectUpdate, db: Session = Depends(get_db)):
    p = _get(db, pid)
    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.config is not None:
        p.config = body.config.model_dump(mode="json")
    db.commit()
    return p


@router.post("/projects/{pid}/duplicate", response_model=ProjectOut, status_code=201)
def duplicate_project(pid: str, db: Session = Depends(get_db)):
    p = _get(db, pid)
    q = Project(project_id=p.project_id, name=f"{p.name} (copy)", description=p.description, template=p.template,
                config=p.config)
    db.add(q)
    db.commit()
    return q


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: str, db: Session = Depends(get_db)):
    p = _get(db, pid)
    active = [r for r in p.runs if r.status in ("queued", "running")]
    if active:
        raise HTTPException(409, "Cancel the active runs before deleting this project.")
    settings = get_settings()
    for r in p.runs:
        shutil.rmtree(settings.runs_dir / r.id, ignore_errors=True)
    db.delete(p)
    db.commit()


@router.get("/projects/{pid}/export/{backend}")
def export_case(pid: str, backend: str, db: Session = Depends(get_db)):
    """Download the input deck for an external solver (OpenFOAM case, FEniCSx script, PyFR mesh + ini) as a zip."""
    p = _get(db, pid)
    b = BACKENDS.get(backend)
    if b is None or not b.exportable:
        raise HTTPException(404, f"No exportable case format '{backend}'")
    case = CaseConfig.model_validate(p.config)
    errors = [m.message for m in b.check(case) if m.level == "error"]
    if errors:
        raise HTTPException(422, detail={"message": f"The case cannot be expressed for {b.label}.", "errors": errors})
    tmpdir = Path(tempfile.mkdtemp(prefix="cfdflow-export-"))
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in p.name) or "case"
    folder = tmpdir / f"{stem}_{backend}"
    b.export_case(case, folder)
    zpath = tmpdir / f"{stem}_{backend}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                info = zipfile.ZipInfo(str(f.relative_to(tmpdir)).replace("\\", "/"))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o755 if f.suffix in (".sh", "") and f.name in ("Allrun", "run_pyfr.sh") else 0o644) << 16
                zf.writestr(info, f.read_bytes())
    return FileResponse(zpath, media_type="application/zip", filename=zpath.name,
                        background=BackgroundTask(lambda: shutil.rmtree(tmpdir, ignore_errors=True)))
