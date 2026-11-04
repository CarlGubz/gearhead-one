"""Schedules solver worker processes (bounded concurrency) and reaps finished / crashed ones."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Run, RunLog

log = logging.getLogger("gearhead.cfd.runs")
BACKEND_ROOT = Path(__file__).resolve().parents[3]  # backend/ (app/cfd/services/run_manager.py)
KILL_GRACE_SECONDS = 20.0


def _now():
    return datetime.now(timezone.utc)


class RunManager:
    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen] = {}
        self._cancel_seen: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._stop.clear()  # restartable: the platform may stop and start it again (e.g. one app lifespan per test)
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="run-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()

    def recover(self) -> None:
        """Runs left 'running' by a previous API process can no longer be tracked."""
        with SessionLocal() as db:
            for run in db.scalars(select(Run).where(Run.status == "running")):
                alive = False
                if run.pid:
                    try:
                        os.kill(run.pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if not alive:
                    run.status = "failed"
                    run.error = "Interrupted: the server restarted while this run was in progress."
                    run.finished_at = _now()
            db.commit()

    # ------------------------------------------------------------ internals
    def _spawn(self, run: Run) -> None:
        settings = get_settings()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        run_dir = settings.run_dir(run.id)
        logf = open(run_dir / "worker.log", "ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.cfd.worker", run.id],
            cwd=str(BACKEND_ROOT),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        self.procs[run.id] = proc
        log.info("started worker pid=%s for run %s", proc.pid, run.id)

    def tick(self) -> None:
        settings = get_settings()
        with SessionLocal() as db:
            # reap finished workers
            for rid, proc in list(self.procs.items()):
                rc = proc.poll()
                run = db.get(Run, rid)
                if rc is not None:
                    del self.procs[rid]
                    self._cancel_seen.pop(rid, None)
                    if run and run.status in ("queued", "running"):
                        run.status = "cancelled" if run.cancel_requested else "failed"
                        run.error = None if run.cancel_requested else f"Solver process exited unexpectedly (code {rc}). See worker.log."
                        run.finished_at = _now()
                        db.add(RunLog(run_id=rid, level="error", message=f"Worker exited with code {rc}"))
                        db.commit()
                elif run and run.cancel_requested:
                    first = self._cancel_seen.setdefault(rid, time.time())
                    if time.time() - first > KILL_GRACE_SECONDS:
                        proc.kill()
            # start queued runs
            free = settings.max_concurrent_runs - len(self.procs)
            if free > 0:
                queued = db.scalars(
                    select(Run).where(Run.status == "queued", Run.cancel_requested.is_(False)).order_by(Run.created_at)
                ).all()
                for run in queued:
                    if free <= 0:
                        break
                    if run.id in self.procs:
                        continue
                    self._spawn(run)
                    free -= 1

    def _loop(self) -> None:
        interval = get_settings().scheduler_interval
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self._stop.wait(interval)


manager = RunManager()
