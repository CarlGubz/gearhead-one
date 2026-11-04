"""
One Gearhead project, three workspaces.

A Gearhead project (`models.Project`, owned by a user, limited by the plan) holds:

* **FEA** - its model stack (the project columns + `model_layers`, see layers.py);
* **CFD** - any number of CFD cases (`cfd_cases.project_id`), each with its runs and result files;
* **CAD** - one CAD project (`cad_projects.project_id`, created on first use) with sketches, parts, assemblies,
  drawings, CAM setups and CAD simulation jobs.

This module holds the glue that does not belong to one workspace: creating a Gearhead project on behalf of the CFD /
CAD APIs (their original "create project" calls), finding or creating the CAD project of a Gearhead project, the
workspace summary the UI shows, and deleting a project's CFD / CAD data (run directories and uploads live on disk).
"""
from __future__ import annotations

import re
import shutil
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import layers, models, plans
from .deps import tier_of

_UPLOAD_NAME = re.compile(r"[0-9a-f]{32}\.step")


def check_project_limit(db: Session, user: models.User) -> None:
    limit = plans.limits_for(tier_of(user)).max_projects
    if limit is not None:
        used = db.query(models.Project).filter(models.Project.owner_id == user.id).count()
        if used >= limit:
            raise HTTPException(
                402, f"The Free plan includes {limit} projects. Upgrade to Pro for unlimited projects.")


def new_project(db: Session, user: models.User, name: str, description: str = "") -> models.Project:
    """A new, empty Gearhead project (one empty FEA model). Counts against the plan's project limit."""
    check_project_limit(db, user)
    project = models.Project(owner_id=user.id, name=(name or "Project").strip()[:120] or "Project",
                             description=description or "", **layers.blank_model())
    db.add(project)
    layers.ensure(db, project)
    db.flush()
    return project


def user_by_id(db: Session, uid: Optional[str]) -> models.User:
    user = db.get(models.User, uid) if uid else None
    if user is None:
        raise HTTPException(401, "Not signed in")
    return user


# ------------------------------------------------------------------------------------------------ CAD
def cad_project_for(db: Session, project: models.Project, create: bool = True):
    """The CAD project of a Gearhead project (created empty on first use)."""
    from .cad import models as cad_models

    cp = db.query(cad_models.CadProject).filter(cad_models.CadProject.project_id == project.id).first()
    if cp is None and create:
        cp = cad_models.CadProject(project_id=project.id, name=project.name, description=project.description or "")
        db.add(cp)
        db.flush()
    return cp


# ------------------------------------------------------------------------------------------------ CFD
def cfd_cases_of(db: Session, project: models.Project) -> list:
    from .cfd.models import CfdCase

    return (db.query(CfdCase).filter(CfdCase.project_id == project.id)
            .order_by(CfdCase.updated_at.desc()).all())


def summary(db: Session, project: models.Project) -> dict:
    """What each workspace of a project holds (the workspace switcher and the project list show it)."""
    from .cad import models as cad_models
    from .cfd.models import CfdRun

    cases = cfd_cases_of(db, project)
    cp = cad_project_for(db, project, create=False)
    cad = {"cad_project_id": cp.id if cp else None, "sketches": 0, "parts": 0, "assemblies": 0, "drawings": 0}
    if cp is not None:
        cad.update(sketches=len(cp.sketches), parts=len(cp.parts), assemblies=len(cp.assemblies),
                   drawings=db.query(cad_models.Drawing).filter(cad_models.Drawing.project_id == cp.id).count())
    return {
        "project_id": project.id,
        "fea": {"models": len(project.layers or []), "n_elements": len(project.elements or []),
                "element_type": project.element_type, "analysis_type": project.analysis_type},
        "cfd": {"cases": [{"id": c.id, "name": c.name, "dimension": (c.config or {}).get("dimension", "2d"),
                           "runs": db.query(CfdRun).filter(CfdRun.project_id == c.id).count(),
                           "updated_at": c.updated_at.isoformat() if c.updated_at else None} for c in cases]},
        "cad": cad,
    }


def delete_project_data(db: Session, project: models.Project) -> None:
    """Remove the CFD cases (with their result files) and the CAD project of a Gearhead project that is being deleted.

    Done explicitly (not only by `ON DELETE CASCADE`): SQLite does not enforce foreign keys by default, and run
    directories / CAD uploads are files. Running CFD runs are asked to stop; the scheduler kills them.
    """
    from .cfd.config import get_settings as cfd_settings
    from .cfd.models import CfdCase, CfdRun

    runs_dir = cfd_settings().runs_dir
    for case in db.query(CfdCase).filter(CfdCase.project_id == project.id).all():
        for run in db.query(CfdRun).filter(CfdRun.project_id == case.id).all():
            run.cancel_requested = True
            shutil.rmtree(runs_dir / run.id, ignore_errors=True)
        db.delete(case)
    cp = cad_project_for(db, project, create=False)
    if cp is not None:
        delete_cad_project(db, cp)
    db.flush()


def delete_cad_project(db: Session, cp) -> None:
    """Delete a CAD project with everything in it, including uploaded STEP files.

    CAM setups and CAD simulation jobs hang off parts / assemblies without an ORM relationship (only `ON DELETE
    CASCADE`, which SQLite does not enforce by default), so they are removed explicitly.
    """
    from .cad import models as cad_models
    from .cad.config import settings as cad_settings

    part_ids = [p.id for p in cp.parts]
    asm_ids = [a.id for a in cp.assemblies]
    if part_ids:
        db.query(cad_models.CamSetup).filter(cad_models.CamSetup.part_id.in_(part_ids)).delete(synchronize_session=False)
        db.query(cad_models.SimulationJob).filter(cad_models.SimulationJob.part_id.in_(part_ids)).delete(synchronize_session=False)
    if asm_ids:
        db.query(cad_models.SimulationJob).filter(cad_models.SimulationJob.assembly_id.in_(asm_ids)).delete(synchronize_session=False)
    files = set()
    for part in cp.parts:
        for feat in part.features or []:
            name = str((feat.get("params") or {}).get("file") or "") if feat.get("type") == "import_step" else ""
            if _UPLOAD_NAME.fullmatch(name):  # only server-generated names (the param is user-editable)
                files.add(name)
    db.delete(cp)
    db.flush()
    for name in files:
        (cad_settings.uploads_dir / name).unlink(missing_ok=True)
