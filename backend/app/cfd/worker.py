"""Solver worker process: ``python -m app.cfd.worker <run_id>``.

One OS process per run so that runs are isolated, can use their own core, and can be
terminated safely. All state is exchanged through PostgreSQL and the run directory.
The worker picks the solver backend of the case (native, OpenFOAM, FEniCSx, PyFR or a Python
fallback, see :mod:`app.solver.backends`) and stores the result in the common format.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import sys
import traceback
from datetime import datetime, timezone

from sqlalchemy import select, update

from .config import get_settings
from .db import SessionLocal
from .models import MonitorSample, Run, RunLog
from .schemas import CaseConfig
from .solver.backends import BackendFailed, BackendUnavailable, resolve
from .solver.ns2d import SolverDiverged
from .solver.post import save_frames
from .solver.runner import Hooks


def _now():
    return datetime.now(timezone.utc)


def main(run_id: str) -> int:
    settings = get_settings()
    run_dir = settings.run_dir(run_id)
    db = SessionLocal()
    run = db.get(Run, run_id)
    if run is None:
        return 2
    if run.status == "cancelled":
        return 0
    case = CaseConfig.model_validate(run.config)
    run.status = "running"
    run.started_at = _now()
    run.pid = os.getpid()
    db.commit()

    buffer: list[MonitorSample] = []

    def flush():
        if buffer:
            db.add_all(buffer)
            buffer.clear()
        db.commit()

    def log(level: str, msg: str):
        db.add(RunLog(run_id=run_id, level=level, message=msg))
        db.commit()

    def on_monitor(it, t, residuals, values):
        buffer.append(MonitorSample(run_id=run_id, iteration=it, sim_time=t, residuals=residuals, values=values))

    def on_progress(frac, it, t):
        flush()
        db.execute(update(Run).where(Run.id == run_id).values(progress=frac, iteration=it, sim_time=t))
        db.commit()

    def on_snapshot(s):
        meta = s.result_meta(case.run.mode)
        meta["backend"] = backend.id
        save_frames(run_dir / "latest.npz", meta, s.S, [s.time], [s.state_arrays()])

    def save_latest(meta, solid, t, frame):
        save_frames(run_dir / "latest.npz", meta, solid, [t], [frame])

    def should_stop() -> bool:
        with SessionLocal() as sess:
            return bool(sess.scalar(select(Run.cancel_requested).where(Run.id == run_id)))

    def fail(msg: str) -> int:
        flush()
        db.execute(update(Run).where(Run.id == run_id).values(status="failed", error=msg, finished_at=_now()))
        db.commit()
        return 1

    hooks = Hooks(log=log, on_monitor=on_monitor, on_progress=on_progress, on_snapshot=on_snapshot,
                  should_stop=should_stop, save_latest=save_latest)
    try:
        backend, notes = resolve(case)
        dim = "3-D" if case.ndim == 3 else "2-D"
        log("info", f"Solver: {backend.label} ({dim}, {case.run.mode})")
        for n in notes:
            log("warning", n)
        result = backend.run(case, hooks, run_dir)
        flush()
        if notes:
            result.summary.setdefault("notes", [])
            result.summary["notes"] = notes + list(result.summary["notes"])
        result.summary["requested_backend"] = case.solver.backend
        save_frames(run_dir / "result.npz", result.meta, result.solid, result.times, result.frames)
        db.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(
                status=result.status,
                progress=1.0 if result.status == "completed" else Run.progress,
                iteration=result.iterations,
                sim_time=result.sim_time,
                converged=result.converged,
                summary=result.summary,
                finished_at=_now(),
            )
        )
        db.commit()
        return 0
    except SolverDiverged as exc:
        return fail(str(exc))
    except (BackendFailed, BackendUnavailable) as exc:
        log("error", str(exc))
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        log("error", f"{type(exc).__name__}: {exc}")
        print(tb, file=sys.stderr)
        return fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
