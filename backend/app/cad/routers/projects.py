from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ... import models as core_models, workspaces
from ...scope import bind_user, current_user_id, require_project_owner
from .. import models
from ..database import get_db
from ..schemas import ProjectIn, ProjectPatch
from ..services import demo
from .deps import apply_patch, get_or_404

# A CAD project always belongs to a Gearhead project (one each); see app/workspaces.py.
router = APIRouter(prefix="/api/cad/projects", tags=["cad-projects"], dependencies=[Depends(bind_user)])


def project_out(p: models.Project) -> dict:
    return {
        "id": p.id, "project_id": p.project_id, "name": p.name, "description": p.description,
        "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "counts": {"sketches": len(p.sketches), "parts": len(p.parts), "assemblies": len(p.assemblies)},
    }


def _owner_for(db: Session, project_id: str | None, name: str, description: str = "") -> core_models.Project:
    """The Gearhead project a new CAD project attaches to (a new one when none is named)."""
    if project_id:
        owner = require_project_owner(db, project_id)
        if workspaces.cad_project_for(db, owner, create=False) is not None:
            raise HTTPException(409, "That project already has a CAD project")
        return owner
    return workspaces.new_project(db, workspaces.user_by_id(db, current_user_id()), name, description)


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    owned = db.query(core_models.Project.id).filter(core_models.Project.owner_id == current_user_id())
    rows = (db.query(models.Project).filter(models.Project.project_id.in_(owned.scalar_subquery()))
            .order_by(models.Project.id.desc()).all())
    return [project_out(p) for p in rows]


@router.post("", status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    owner = _owner_for(db, body.project_id, body.name, body.description)
    p = models.Project(project_id=owner.id, name=body.name, description=body.description)
    db.add(p)
    db.commit()
    return project_out(p)


@router.post("/demo", status_code=201)
def create_demo_project(db: Session = Depends(get_db)):
    """Creates a ready-made project: parametric bracket, pin, assembly with mates, CAM setup."""
    owner = _owner_for(db, None, demo.DEMO_NAME, demo.DEMO_DESCRIPTION)
    p = demo.create_demo(db, project_id=owner.id)
    return project_out(p)


@router.post("/{pid}/demo", status_code=201)
def load_demo_into(pid: int, db: Session = Depends(get_db)):
    """Adds the demo content (plate, pins, assembly with mates, CAM setup) to an existing CAD project."""
    p = get_or_404(db, models.Project, pid, "project")
    demo.create_demo(db, proj=p)
    db.refresh(p)
    return project_out(p)


@router.get("/{pid}")
def get_project(pid: int, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Project, pid, "project")
    out = project_out(p)
    out["sketches"] = [{"id": s.id, "name": s.name, "plane": s.plane} for s in p.sketches]
    out["parts"] = [{"id": x.id, "name": x.name, "material": x.material, "features": len(x.features or [])} for x in p.parts]
    out["assemblies"] = [{"id": a.id, "name": a.name, "components": len(a.components)} for a in p.assemblies]
    return out


@router.patch("/{pid}")
def patch_project(pid: int, body: ProjectPatch, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Project, pid, "project")
    apply_patch(p, body, ["name", "description"])
    db.commit()
    return project_out(p)


@router.delete("/{pid}", status_code=204)
def delete_project(pid: int, db: Session = Depends(get_db)):
    """Deletes the CAD data of a project (the Gearhead project with its FEA models and CFD cases stays)."""
    p = get_or_404(db, models.Project, pid, "project")
    workspaces.delete_cad_project(db, p)
    db.commit()
