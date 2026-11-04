"""
Request-scoped "who is asking" for the CFD (`app.cfd`) and CAD (`app.cad`) routers.

Those two packages came from stand-alone apps without accounts: every route looks its rows up through one small helper
(`cfd.api.projects._get`, `cad.routers.deps.get_or_404`, ...). Instead of threading a `user` argument through ~90 routes,
their routers declare `dependencies=[Depends(bind_user)]`; `bind_user` stores the signed-in user's id in a context
variable and the lookup helpers call `require_project_owner` with the Gearhead project the row belongs to.

`bind_user` must be an *async* dependency: FastAPI awaits it in the request's own task, so the context variable is
visible to the (sync) endpoint that FastAPI then runs in its thread pool. A sync dependency would set it in a worker
thread's private copy of the context.

Rows of other users answer 404 (not 403) so ids cannot be probed - the same rule as `deps.owned_project`.
"""
from __future__ import annotations

import contextvars
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from . import models
from .deps import current_user

_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("gearhead_user_id", default=None)


async def bind_user(user: models.User = Depends(current_user)) -> models.User:
    _user_id.set(user.id)
    return user


def current_user_id() -> Optional[str]:
    return _user_id.get()


def set_current_user_id(uid: Optional[str]) -> contextvars.Token:
    """For code that runs outside a request (tests, the WebSocket handler)."""
    return _user_id.set(uid)


def require_project_owner(db: Session, project_id: Optional[str], what: str = "Project") -> models.Project:
    """The Gearhead project `project_id`, if the current user owns it; otherwise 404."""
    uid = current_user_id()
    project = db.get(models.Project, project_id) if project_id else None
    if project is None or uid is None or project.owner_id != uid:
        raise HTTPException(404, f"{what} not found")
    return project


def owns_project(db: Session, project_id: Optional[str]) -> bool:
    uid = current_user_id()
    if not project_id or uid is None:
        return False
    project = db.get(models.Project, project_id)
    return project is not None and project.owner_id == uid
