from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ... import models as core_models
from ...config import settings as core_settings
from ...scope import bind_user, owns_project
from ...security import hash_token
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import MonitorSample, Project, Run, RunLog
from ..schemas import CaseConfig, RunCreate, RunSummary
from ..services.checks import check_case
from ..solver import post

router = APIRouter(prefix="/api/cfd", tags=["cfd"], dependencies=[Depends(bind_user)])
# The live stream authenticates itself from the session cookie (HTTP dependencies do not apply to WebSockets).
ws_router = APIRouter(prefix="/api/cfd", tags=["cfd"])
TERMINAL = ("completed", "failed", "cancelled")


class RunDetail(RunSummary):
    config: dict


def _iso(ts: datetime) -> str:
    """ISO time in UTC (SQLite hands timezone-aware columns back naive)."""
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).isoformat()


def _case(db: Session, pid: str) -> Project:
    p = db.get(Project, pid)
    if not p or not owns_project(db, p.project_id):
        raise HTTPException(404, "Project not found")
    return p


def _run(db: Session, rid: str) -> Run:
    """A run of one of the current user's CFD cases (404 otherwise)."""
    r = db.get(Run, rid)
    if not r or not owns_project(db, r.project.project_id if r.project else None):
        raise HTTPException(404, "Run not found")
    return r


def _ws_user_id(ws: WebSocket) -> str | None:
    """User id of the session cookie sent with the WebSocket handshake (same check as deps.get_session)."""
    token = ws.cookies.get(core_settings.session_cookie_name)
    if not token:
        return None
    with SessionLocal() as db:
        sess = db.query(core_models.UserSession).filter(core_models.UserSession.token_hash == hash_token(token)).first()
        if not sess or sess.expires_at <= core_models.utcnow() or not sess.user.is_active:
            return None
        return sess.user_id


def _result_path(run: Run):
    d = get_settings().runs_dir / run.id
    final, latest = d / "result.npz", d / "latest.npz"
    if final.exists():
        return final
    if latest.exists():
        return latest
    raise HTTPException(404, "No results available yet")


@router.post("/projects/{pid}/runs", response_model=RunSummary, status_code=201)
def create_run(pid: str, body: RunCreate, db: Session = Depends(get_db)):
    p = _case(db, pid)
    case = CaseConfig.model_validate(p.config)
    chk = check_case(case)
    errors = [i.message for i in chk.items if i.level == "error"]
    if errors:
        raise HTTPException(422, detail={"message": "The case has errors that must be fixed before running.", "errors": errors})
    n = db.query(Run).filter(Run.project_id == pid).count() + 1
    run = Run(project_id=pid, name=body.name or f"Run {n}", config=p.config, status="queued")
    db.add(run)
    db.commit()
    return run


@router.get("/projects/{pid}/runs", response_model=list[RunSummary])
def list_runs(pid: str, db: Session = Depends(get_db)):
    _case(db, pid)
    return db.scalars(select(Run).where(Run.project_id == pid).order_by(Run.created_at.desc())).all()


@router.get("/runs/{rid}", response_model=RunDetail)
def get_run(rid: str, db: Session = Depends(get_db)):
    return _run(db, rid)


@router.post("/runs/{rid}/cancel", response_model=RunSummary)
def cancel_run(rid: str, db: Session = Depends(get_db)):
    r = _run(db, rid)
    if r.status == "queued":
        r.status = "cancelled"
        r.cancel_requested = True
        r.finished_at = datetime.now(timezone.utc)
    elif r.status == "running":
        r.cancel_requested = True
    db.commit()
    return r


@router.delete("/runs/{rid}", status_code=204)
def delete_run(rid: str, db: Session = Depends(get_db)):
    r = _run(db, rid)
    if r.status in ("queued", "running"):
        raise HTTPException(409, "Cancel the run before deleting it.")
    shutil.rmtree(get_settings().runs_dir / r.id, ignore_errors=True)
    db.delete(r)
    db.commit()


@router.get("/runs/{rid}/monitors")
def monitors(rid: str, since: int = -1, max_points: int = Query(1500, ge=10, le=20000), db: Session = Depends(get_db)):
    _run(db, rid)
    rows = db.scalars(
        select(MonitorSample).where(MonitorSample.run_id == rid, MonitorSample.iteration > since).order_by(MonitorSample.iteration)
    ).all()
    stride = max(1, len(rows) // max_points) if since < 0 else 1
    rows = rows[::stride] if stride > 1 else rows
    return {
        "samples": [{"iteration": r.iteration, "time": r.sim_time, "residuals": r.residuals, "values": r.values} for r in rows],
        "stride": stride,
    }


@router.get("/runs/{rid}/logs")
def logs(rid: str, after: int = 0, db: Session = Depends(get_db)):
    _run(db, rid)
    rows = db.scalars(select(RunLog).where(RunLog.run_id == rid, RunLog.id > after).order_by(RunLog.id).limit(500)).all()
    return [{"id": r.id, "ts": _iso(r.ts), "level": r.level, "message": r.message} for r in rows]


@router.get("/runs/{rid}/frames")
def frames(rid: str, db: Session = Depends(get_db)):
    r = _run(db, rid)
    path = _result_path(r)
    with np.load(path, allow_pickle=False) as z:
        return {"times": z["times"].tolist(), "final": path.name == "result.npz"}


@router.get("/runs/{rid}/fields")
def fields(rid: str, frame: int = -1, axis: int | None = Query(None, ge=0, le=2), index: int | None = Query(None, ge=0),
           db: Session = Depends(get_db)):
    """Cell-centred fields of one frame; 3-D results are returned as a slice normal to ``axis``."""
    r = _run(db, rid)
    res = post.load_result(_result_path(r))
    return post.fields_payload(res, frame, axis, index)


@router.get("/runs/{rid}/probe-line")
def probe_line(
    rid: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    n: int = Query(120, ge=2, le=2000),
    frame: int = -1,
    axis: int | None = Query(None, ge=0, le=2),
    index: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    r = _run(db, rid)
    res = post.load_result(_result_path(r))
    return post.probe_line(res, frame, (x0, y0), (x1, y1), n, axis, index)


def _cleanup(path):
    def _f():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return _f


@router.get("/runs/{rid}/export/vtu")
def export_vtu(rid: str, frame: int = -1, db: Session = Depends(get_db)):
    r = _run(db, rid)
    res = post.load_result(_result_path(r))
    from pathlib import Path

    tmp = Path(tempfile.mkstemp(suffix=".vtu")[1])
    post.export_vtu(res, frame, tmp)
    return FileResponse(tmp, media_type="application/xml", filename=f"{r.name or r.id}.vtu".replace(" ", "_"),
                        background=BackgroundTask(_cleanup(tmp)))


@router.get("/runs/{rid}/export/csv")
def export_csv(rid: str, frame: int = -1, db: Session = Depends(get_db)):
    r = _run(db, rid)
    res = post.load_result(_result_path(r))
    text = post.export_csv(res, frame)
    return PlainTextResponse(text, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{(r.name or r.id).replace(" ", "_")}_fields.csv"'})


@router.get("/runs/{rid}/export/monitors")
def export_monitors(rid: str, db: Session = Depends(get_db)):
    r = _run(db, rid)
    rows = db.scalars(select(MonitorSample).where(MonitorSample.run_id == rid).order_by(MonitorSample.iteration)).all()
    rkeys = sorted({k for s in rows for k in s.residuals})
    vkeys = sorted({k for s in rows for k in s.values})
    lines = [",".join(["iteration", "time"] + [f"res_{k}" for k in rkeys] + vkeys)]
    for s in rows:
        lines.append(",".join([str(s.iteration), f"{s.sim_time:.8g}"]
                              + [f"{s.residuals.get(k, '')}" for k in rkeys]
                              + [f"{s.values.get(k, '')}" for k in vkeys]))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{(r.name or r.id).replace(" ", "_")}_monitors.csv"'})


@ws_router.websocket("/runs/{rid}/stream")
async def stream(ws: WebSocket, rid: str, since: int = -1, after: int = 0):
    """Pushes status + new monitor samples + new log lines every ~0.5 s until the run ends."""
    uid = await run_in_threadpool(_ws_user_id, ws)

    def allowed() -> bool:
        with SessionLocal() as db:
            r = db.get(Run, rid)
            case = db.get(Project, r.project_id) if r else None
            owner = db.get(core_models.Project, case.project_id) if case and case.project_id else None
            return owner is not None and owner.owner_id == uid

    if uid is None or not await run_in_threadpool(allowed):
        await ws.close(code=4404)
        return
    await ws.accept()
    last_iter, last_log = since, after

    def poll():
        with SessionLocal() as db:
            r = db.get(Run, rid)
            if not r:
                return None
            samples = db.scalars(
                select(MonitorSample).where(MonitorSample.run_id == rid, MonitorSample.iteration > last_iter)
                .order_by(MonitorSample.iteration).limit(2000)
            ).all()
            lg = db.scalars(select(RunLog).where(RunLog.run_id == rid, RunLog.id > last_log).order_by(RunLog.id).limit(200)).all()
            return {
                "status": r.status,
                "progress": r.progress,
                "iteration": r.iteration,
                "sim_time": r.sim_time,
                "converged": r.converged,
                "error": r.error,
                "summary": r.summary if r.status in TERMINAL else None,
                "samples": [{"iteration": s.iteration, "time": s.sim_time, "residuals": s.residuals, "values": s.values} for s in samples],
                "logs": [{"id": l.id, "ts": _iso(l.ts), "level": l.level, "message": l.message} for l in lg],
            }

    try:
        while True:
            msg = await run_in_threadpool(poll)
            if msg is None:
                await ws.close(code=4404)
                return
            if msg["samples"]:
                last_iter = msg["samples"][-1]["iteration"]
            if msg["logs"]:
                last_log = msg["logs"][-1]["id"]
            await ws.send_json(msg)
            if msg["status"] in TERMINAL and not msg["samples"] and not msg["logs"]:
                await ws.close()
                return
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        return
