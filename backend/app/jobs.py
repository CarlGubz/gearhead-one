"""Job orchestration: runs the project's analysis on the built-in FE engine and stores the results."""
from __future__ import annotations

import datetime as dt
import logging
import traceback
from typing import Any, Dict

from sqlalchemy.orm import Session

from .fea import runner
from .fea.analyses import SolverError
from .models import Job, Project

log = logging.getLogger("gearhead.jobs")
SOLVER_NAME = "gearhead-fea"


def project_to_dict(project: Project) -> Dict[str, Any]:
    """Plain-dict snapshot of everything the solver needs (decouples the run from the DB session)."""
    return {
        "name": project.name,
        "nodes": project.nodes,
        "elements": project.elements,
        "element_type": project.element_type or "C3D4",
        "material": project.material or {},
        "materials": project.materials or [],
        "parts": project.parts or [],
        "boundary_conditions": project.boundary_conditions or [],
        "loads": project.loads or [],
        "analysis_type": project.analysis_type or "static",
        "n_modes": project.n_modes or 6,
        "gravity": project.gravity or {},
        "settings": project.settings or {},
        "reference_points": project.reference_points or [],
        "constraints": project.constraints or [],
    }


def run_job(db: Session, job: Job, project: Project, snapshot: Dict[str, Any] = None) -> None:
    """`snapshot` is the model captured when the solve was requested (the user may switch models while it runs)."""
    job.status = "running"
    job.progress = 0.05
    job.solver_used = SOLVER_NAME
    db.commit()
    if snapshot is None:
        snapshot = project_to_dict(project)
    messages = []
    last_commit = {"frac": 0.0}

    def progress(frac: float, msg: str) -> None:
        messages.append(msg)
        job.progress = max(job.progress or 0.0, min(float(frac), 0.99))
        job.log = "\n".join(messages[-30:])
        if frac - last_commit["frac"] >= 0.02:  # don't hammer the DB
            db.commit()
            last_commit["frac"] = frac

    try:
        out = runner.run(snapshot, progress)
        job.results = out["results"]
        job.log = out["log"]
        job.status = "completed"
    except (runner.ModelError, SolverError, ValueError) as exc:
        job.status = "failed"
        job.log = str(exc)
    except Exception as exc:  # noqa: BLE001 - never leave a job stuck in "running"
        log.exception("Job %s crashed", job.id)
        job.status = "failed"
        job.log = f"Unexpected solver error: {exc}\n\n{traceback.format_exc()[-2000:]}"
    job.progress = 1.0
    job.finished_at = dt.datetime.utcnow()
    db.commit()
