from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from ...scope import bind_user

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import SessionLocal, get_db
from ..schemas import SimulationIn
from ..services import assembly as asmsvc
from ..services import calculix
from ..services import simulation as simsvc
from ..services import store
from .deps import get_or_404

router = APIRouter(prefix="/api/cad", tags=["cad-simulation"], dependencies=[Depends(bind_user)])


def job_out(j: models.SimulationJob, with_result: bool = True) -> dict:
    out = {
        "id": j.id, "part_id": j.part_id, "assembly_id": j.assembly_id, "kind": j.kind, "config": j.config or {}, "status": j.status, "error": j.error or "",
        "created_at": j.created_at.isoformat(), "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "summary": (j.result or {}).get("summary"), "mesh": (j.result or {}).get("mesh"),
    }
    if with_result:
        out["result"] = j.result or {}
    return out


def _assembly_bodies(db: Session, a: models.Assembly) -> list[dict]:
    bodies = []
    for c in a.components:
        part = db.get(models.Part, c.part_id)
        shape = store.part_shape_or_none(db, part) if part else None
        if shape is None:
            raise ValueError(f"component '{c.name}' has no geometry")
        bodies.append({"name": c.name, "shape": shape, "material": part.material, "component": c.id,
                       "matrix": asmsvc.euler_matrix(c.position, c.rotation)})
    if len(bodies) < 2:
        raise ValueError("a contact analysis needs at least two components")
    return bodies


def _run_job(job_id: int):
    """Runs in a worker thread (FastAPI background task) with its own DB session."""
    db = SessionLocal()
    try:
        job = db.get(models.SimulationJob, job_id)
        job.status = "running"
        db.commit()
        try:
            cfg = job.config or {}
            if job.assembly_id is not None:
                a = db.get(models.Assembly, job.assembly_id)
                job.result = calculix.run(job.kind, _assembly_bodies(db, a), cfg, settings.max_fea_elements, settings.ccx_timeout)
            else:
                part = db.get(models.Part, job.part_id)
                shape = store.part_shape_or_none(db, part)
                if shape is None:
                    raise ValueError("part has no geometry")
                if job.kind in calculix.KINDS:
                    body = [{"name": part.name, "shape": shape, "material": part.material}]
                    job.result = calculix.run(job.kind, body, cfg, settings.max_fea_elements, settings.ccx_timeout)
                else:
                    job.result = simsvc.run_simulation(shape, part.material, job.kind, cfg, settings.max_fea_elements)
            job.status = "done"
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    finally:
        db.close()


@router.post("/parts/{part_id}/simulations", status_code=202)
def start_simulation(part_id: int, body: SimulationIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    get_or_404(db, models.Part, part_id, "part")
    part_kinds = ["static", "thermal", "modal"] + [k for k in calculix.KINDS if k not in calculix.ASSEMBLY_KINDS]
    if body.kind not in part_kinds:
        raise HTTPException(422, f"kind must be one of: {', '.join(part_kinds)}")
    _require_ccx(body.kind)
    job = models.SimulationJob(part_id=part_id, kind=body.kind, config=dict(body.config), status="queued")
    db.add(job)
    db.commit()
    bg.add_task(_run_job, job.id)
    return job_out(job, with_result=False)


def _require_ccx(kind: str):
    if kind in calculix.KINDS:
        cmd, how = calculix.solver_command()
        if cmd is None:
            raise HTTPException(422, how)


@router.post("/assemblies/{aid}/simulations", status_code=202)
def start_assembly_simulation(aid: int, body: SimulationIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    get_or_404(db, models.Assembly, aid, "assembly")
    if body.kind not in calculix.ASSEMBLY_KINDS:
        raise HTTPException(422, f"assembly analyses: {', '.join(sorted(calculix.ASSEMBLY_KINDS))}")
    _require_ccx(body.kind)
    job = models.SimulationJob(assembly_id=aid, kind=body.kind, config=dict(body.config), status="queued")
    db.add(job)
    db.commit()
    bg.add_task(_run_job, job.id)
    return job_out(job, with_result=False)


@router.get("/assemblies/{aid}/simulations")
def list_assembly_simulations(aid: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Assembly, aid, "assembly")
    rows = db.query(models.SimulationJob).filter(models.SimulationJob.assembly_id == aid).order_by(models.SimulationJob.id.desc()).all()
    return [job_out(j, with_result=False) for j in rows]


@router.get("/calculix")
def calculix_status(refresh: bool = False):
    """Is the CalculiX solver available, and how is it run?"""
    calculix.solver_command(refresh=refresh)
    return calculix.status()


@router.get("/parts/{part_id}/simulations")
def list_simulations(part_id: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Part, part_id, "part")
    rows = db.query(models.SimulationJob).filter(models.SimulationJob.part_id == part_id).order_by(models.SimulationJob.id.desc()).all()
    return [job_out(j, with_result=False) for j in rows]


@router.get("/simulations/{jid}")
def get_simulation(jid: int, db: Session = Depends(get_db)):
    return job_out(get_or_404(db, models.SimulationJob, jid, "simulation"))


@router.delete("/simulations/{jid}", status_code=204)
def delete_simulation(jid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.SimulationJob, jid, "simulation"))
    db.commit()
