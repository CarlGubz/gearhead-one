"""Account endpoints: register, login, logout, session info, passwords."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import mailer, models, schemas_account as sa
from ..billing_service import subscription_summary
from ..config import settings
from ..database import get_db
from ..deps import current_user, get_session
from ..models import utcnow
from ..ratelimit import RateLimiter, client_ip, limit_dependency
from ..security import (
    hash_password, hash_token, needs_rehash, new_token, password_problem, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_auth_limiter = RateLimiter(limit=20, window_seconds=60)
_reset_limiter = RateLimiter(limit=5, window_seconds=60)
auth_rate_limit = limit_dependency(_auth_limiter, "auth")
reset_rate_limit = limit_dependency(_reset_limiter, "reset")

GENERIC_LOGIN_ERROR = "Invalid email or password."


def reset_limiters() -> None:  # used by tests
    _auth_limiter.reset()
    _reset_limiter.reset()


def _me(user: models.User, db: Session) -> sa.MeOut:
    return sa.MeOut(user=sa.UserOut.model_validate(user), subscription=subscription_summary(user, db))


def _start_session(user: models.User, request: Request, response: Response, db: Session) -> None:
    token = new_token()
    now = utcnow()
    # Housekeeping: drop this user's expired sessions while we're here.
    db.query(models.UserSession).filter(
        models.UserSession.user_id == user.id, models.UserSession.expires_at <= now
    ).delete()
    db.add(models.UserSession(
        user_id=user.id, token_hash=hash_token(token), created_at=now, last_seen_at=now,
        expires_at=now + dt.timedelta(days=settings.session_days),
        user_agent=(request.headers.get("user-agent") or "")[:255], ip=client_ip(request),
    ))
    db.commit()
    response.set_cookie(
        settings.session_cookie_name, token, max_age=settings.session_days * 86400,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )


@router.post("/register", response_model=sa.MeOut, status_code=201, dependencies=[Depends(auth_rate_limit)])
def register(payload: sa.RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    problem = password_problem(payload.password, email)
    if problem:
        raise HTTPException(422, problem)
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(409, "An account with this email already exists. Try signing in.")
    user = models.User(email=email, password_hash=hash_password(payload.password),
                       full_name=payload.full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    _start_session(user, request, response, db)
    return _me(user, db)


@router.post("/login", response_model=sa.MeOut, dependencies=[Depends(auth_rate_limit)])
def login(payload: sa.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(429, "Too many failed attempts. Please try again later.")

    ok = verify_password(payload.password, user.password_hash if user else None)
    if not user or not ok or not user.is_active:
        if user:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= settings.max_failed_logins:
                user.locked_until = now + dt.timedelta(minutes=settings.lockout_minutes)
                user.failed_logins = 0
            db.commit()
        raise HTTPException(401, GENERIC_LOGIN_ERROR)

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    db.commit()
    _start_session(user, request, response, db)
    return _me(user, db)


@router.post("/logout", status_code=204)
def logout(sess: Optional[models.UserSession] = Depends(get_session), db: Session = Depends(get_db)):
    if sess:
        db.delete(sess)
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.get("/me", response_model=sa.MeOut)
def me(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return _me(user, db)


@router.post("/change-password", status_code=204)
def change_password(payload: sa.ChangePasswordIn, user: models.User = Depends(current_user),
                    sess: models.UserSession = Depends(get_session), db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    problem = password_problem(payload.new_password, user.email)
    if problem:
        raise HTTPException(422, problem)
    user.password_hash = hash_password(payload.new_password)
    # Sign out everywhere else.
    db.query(models.UserSession).filter(
        models.UserSession.user_id == user.id, models.UserSession.id != sess.id
    ).delete()
    db.commit()
    return Response(status_code=204)


@router.post("/forgot-password", status_code=202, dependencies=[Depends(reset_rate_limit)])
def forgot_password(payload: sa.ForgotIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Always answers 202 so the endpoint cannot be used to discover accounts."""
    user = db.query(models.User).filter(models.User.email == payload.email.lower()).first()
    if user and user.is_active:
        db.query(models.PasswordReset).filter(
            models.PasswordReset.user_id == user.id, models.PasswordReset.used_at.is_(None)
        ).delete()
        token = new_token()
        db.add(models.PasswordReset(
            user_id=user.id, token_hash=hash_token(token),
            expires_at=utcnow() + dt.timedelta(minutes=settings.reset_token_minutes),
        ))
        db.commit()
        subject, body = mailer.password_reset_email(
            user.email, f"{settings.app_base_url}/reset-password?token={token}")
        background.add_task(mailer.send_email, user.email, subject, body)
    return {"ok": True}


@router.post("/reset-password", status_code=204, dependencies=[Depends(reset_rate_limit)])
def reset_password(payload: sa.ResetIn, db: Session = Depends(get_db)):
    row = db.query(models.PasswordReset).filter(
        models.PasswordReset.token_hash == hash_token(payload.token)
    ).first()
    now = utcnow()
    if not row or row.used_at or row.expires_at <= now:
        raise HTTPException(400, "This reset link is invalid or has expired. Request a new one.")
    user = db.get(models.User, row.user_id)
    problem = password_problem(payload.password, user.email)
    if problem:
        raise HTTPException(422, problem)
    user.password_hash = hash_password(payload.password)
    user.failed_logins = 0
    user.locked_until = None
    row.used_at = now
    db.query(models.UserSession).filter(models.UserSession.user_id == user.id).delete()
    db.commit()
    return Response(status_code=204)
