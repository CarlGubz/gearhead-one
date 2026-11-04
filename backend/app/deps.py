"""FastAPI dependencies: current user, subscription tier, ownership checks."""
from __future__ import annotations

import datetime as dt
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, WebSocketException
from starlette.requests import HTTPConnection
from sqlalchemy.orm import Session

from . import models, plans
from .config import settings
from .database import get_db
from .models import utcnow
from .security import hash_token


def get_session(request: Request, db: Session = Depends(get_db)) -> Optional[models.UserSession]:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    sess = db.query(models.UserSession).filter(models.UserSession.token_hash == hash_token(token)).first()
    if not sess:
        return None
    now = utcnow()
    if sess.expires_at <= now:
        db.delete(sess)
        db.commit()
        return None
    # Sliding expiry, written at most hourly to avoid a write per request.
    if now - sess.last_seen_at > dt.timedelta(hours=1):
        sess.last_seen_at = now
        sess.expires_at = now + dt.timedelta(days=settings.session_days)
        db.commit()
    return sess


def optional_user(sess: Optional[models.UserSession] = Depends(get_session)) -> Optional[models.User]:
    if sess and sess.user.is_active:
        return sess.user
    return None


def current_user(user: Optional[models.User] = Depends(optional_user)) -> models.User:
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def tier_of(user: models.User) -> str:
    sub = user.subscription
    return "pro" if sub and sub.current_period_end > utcnow() else "free"


def owned_project(project_id: str, user: models.User = Depends(current_user),
                  db: Session = Depends(get_db)) -> models.Project:
    """404 (not 403) for other people's projects so ids can't be probed."""
    project = db.get(models.Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(404, "Project not found")
    return project


def require_pro_for(analysis_type: str, user: models.User) -> None:
    if analysis_type not in plans.limits_for(tier_of(user)).analysis_types:
        raise HTTPException(
            402, f"{analysis_type.capitalize()} analysis is part of the Pro plan. Upgrade to unlock it."
        )


def verify_origin(request: HTTPConnection) -> None:
    """
    CSRF defence in depth on top of SameSite=Lax: state-changing requests that
    carry an Origin header must come from this site. Requests without Origin
    (curl, server-to-server such as the PayMongo webhook) are not browser
    cross-site requests and are allowed.

    WebSocket handshakes (the CFD live run stream) are always checked: browsers
    send cookies with cross-site WebSocket connections, so the Origin is the
    only protection against cross-site WebSocket hijacking.
    """
    is_ws = request.scope.get("type") == "websocket"
    if not is_ws and request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    if origin.rstrip("/") in settings.allowed_origins:
        return
    host = request.headers.get("host", "")
    if urlparse(origin).netloc == host:
        return
    if is_ws:
        raise WebSocketException(code=1008, reason="Cross-site request blocked")
    raise HTTPException(403, "Cross-site request blocked")
