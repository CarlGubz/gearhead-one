"""Mounts the CFD workbench (formerly the stand-alone CFDFlow API) into the Gearhead app.

Routes live under ``/api/cfd`` and require a signed-in user (see `app.scope`). The run scheduler (which spawns one
solver worker process per run) is started and stopped by the platform's lifespan in `app.main`.
"""
from __future__ import annotations

import logging
import threading

from fastapi import FastAPI

from .api import projects, runs
from .services.run_manager import manager

log = logging.getLogger("gearhead.cfd")


def mount(app: FastAPI) -> None:
    app.include_router(projects.router)
    app.include_router(runs.router)
    app.include_router(runs.ws_router)


_users = 0
_lock = threading.Lock()


def start() -> None:
    """Start the scheduler once, however many app lifespans are open (e.g. several test clients)."""
    global _users
    with _lock:
        _users += 1
        if _users == 1:
            manager.start()


def stop() -> None:
    global _users
    with _lock:
        _users = max(0, _users - 1)
        if _users == 0:
            manager.stop()
