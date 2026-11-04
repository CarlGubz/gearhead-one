"""Plans, GCash checkout, payment verification and the PayMongo webhook."""
from __future__ import annotations

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from .. import mailer, models, plans, schemas_account as sa
from ..billing_service import mark_failed, mark_paid, payment_out, subscription_summary
from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import current_user
from ..payments import ProviderError, get_provider
from ..payments.paymongo import is_live_key, verify_webhook_signature
from ..ratelimit import RateLimiter, limit_dependency

log = logging.getLogger("gearhead.billing")

router = APIRouter(prefix="/api/billing", tags=["billing"])
webhooks = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_checkout_limiter = RateLimiter(limit=10, window_seconds=60)
checkout_rate_limit = limit_dependency(_checkout_limiter, "checkout")


def reset_limiters() -> None:  # used by tests
    _checkout_limiter.reset()


def _owned_payment(payment_id: str, user: models.User, db: Session) -> models.Payment:
    payment = db.get(models.Payment, payment_id)
    if not payment or payment.user_id != user.id:
        raise HTTPException(404, "Payment not found")
    return payment


@router.get("/plans", response_model=sa.PlansOut)
def list_plans():
    return sa.PlansOut(
        currency=plans.CURRENCY,
        plans=[sa.PlanOut(id=p.id, name=p.name, tagline=p.tagline, amount=p.amount,
                          amount_centavos=p.amount_centavos, days=p.days, badge=p.badge)
               for p in plans.PLANS.values()],
        free_features=[f.format(n=settings.free_max_projects) for f in plans.FREE_FEATURES],
        pro_features=plans.PRO_FEATURES,
        payment_provider=settings.payment_provider,
    )


@router.get("/subscription", response_model=sa.SubscriptionOut)
def subscription(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return subscription_summary(user, db)


@router.post("/checkout", response_model=sa.CheckoutOut, dependencies=[Depends(checkout_rate_limit)])
def checkout(payload: sa.CheckoutIn, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    plan = plans.PLANS.get(payload.plan_id)
    if not plan:
        raise HTTPException(404, "Unknown plan")
    try:
        provider = get_provider()
    except ProviderError as exc:
        log.error("Payment provider misconfigured: %s", exc)
        raise HTTPException(503, "Payments are temporarily unavailable.")

    payment = models.Payment(
        user_id=user.id, plan_id=plan.id, amount_centavos=plan.amount_centavos,
        currency=plans.CURRENCY, provider=provider.name, status="pending",
    )
    db.add(payment)
    db.flush()  # assigns payment.id

    back = f"{settings.app_base_url}/billing/return?payment={payment.id}"
    try:
        session = provider.create_checkout(
            payment_id=payment.id, description=f"Gearhead FEA {plan.name}",
            amount_centavos=plan.amount_centavos, currency=plans.CURRENCY,
            success_url=f"{back}&result=success", cancel_url=f"{back}&result=cancel",
            customer_email=user.email, customer_name=user.full_name,
        )
    except ProviderError as exc:
        log.error("Checkout creation failed: %s", exc)
        payment.status = "failed"
        db.commit()
        raise HTTPException(502, "We couldn't reach the payment provider. Please try again.")

    payment.provider_ref = session.provider_ref
    payment.checkout_url = session.checkout_url
    db.commit()
    return sa.CheckoutOut(payment=payment_out(payment), checkout_url=session.checkout_url)


@router.get("/payments", response_model=List[sa.PaymentOut])
def payment_history(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (db.query(models.Payment).filter(models.Payment.user_id == user.id)
            .order_by(models.Payment.created_at.desc()).limit(50).all())
    return [payment_out(p) for p in rows]


@router.get("/payments/{payment_id}", response_model=sa.PaymentOut)
def get_payment(payment_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return payment_out(_owned_payment(payment_id, user, db))


@router.post("/payments/{payment_id}/verify", response_model=sa.VerifyOut)
def verify_payment(payment_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """
    Called by the return page. We never trust the redirect itself: the
    provider is asked directly whether this checkout session was paid.
    """
    payment = _owned_payment(payment_id, user, db)
    if payment.status == "pending" and payment.provider_ref:
        try:
            status = get_provider().fetch_status(payment.provider_ref)
        except ProviderError as exc:
            log.warning("Verification lookup failed for %s: %s", payment.id, exc)
            status = None
        if status and status.paid:
            _settle(db, payment.id, status.amount_centavos)
    db.refresh(payment)
    db.refresh(user)
    return sa.VerifyOut(payment=payment_out(payment), subscription=subscription_summary(user, db))


def _settle(db: Session, payment_id: str, amount_centavos) -> None:
    paid, newly = mark_paid(db, payment_id, amount_centavos)
    if paid and newly and paid.provider != "mock":
        _send_receipt(paid)


def _send_receipt(payment: models.Payment) -> None:
    try:
        with SessionLocal() as s:
            user = s.get(models.User, payment.user_id)
            sub = user.subscription
            plan = plans.PLANS[payment.plan_id]
            subject, body = mailer.receipt_email(
                plan.name, payment.amount_centavos / 100, payment.currency,
                sub.current_period_end.strftime("%B %d, %Y"), payment.id)
            mailer.send_email(user.email, subject, body)
    except Exception:  # noqa: BLE001
        log.exception("Receipt e-mail failed")


# --------------------------------------------------------- Mock GCash (dev only)
def _require_mock() -> None:
    if settings.payment_provider != "mock" or settings.is_production:
        raise HTTPException(404, "Not found")


@router.post("/mock/{payment_id}/complete", response_model=sa.VerifyOut, dependencies=[Depends(_require_mock)])
def mock_complete(payment_id: str, payload: sa.MockCompleteIn,
                  user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    payment = _owned_payment(payment_id, user, db)
    if payment.provider != "mock":
        raise HTTPException(404, "Not found")
    if payload.outcome == "paid":
        mark_paid(db, payment.id, None)
    else:
        mark_failed(db, payment)
    db.refresh(payment)
    db.refresh(user)
    return sa.VerifyOut(payment=payment_out(payment), subscription=subscription_summary(user, db))


# ------------------------------------------------------------- PayMongo webhook
@webhooks.post("/paymongo")
async def paymongo_webhook(request: Request):
    raw = await request.body()
    try:
        event = json.loads(raw)
        attrs = event["data"]["attributes"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Malformed event")

    live = bool(attrs.get("livemode"))
    if not verify_webhook_signature(raw, request.headers.get("paymongo-signature"),
                                    settings.paymongo_webhook_secret, live):
        raise HTTPException(400, "Invalid signature")
    if live != is_live_key() and settings.payment_provider == "paymongo":
        # A test-mode event must never settle a live-mode deployment (or vice versa).
        raise HTTPException(400, "Mode mismatch")

    if attrs.get("type") == "checkout_session.payment.paid":
        await run_in_threadpool(_handle_paid_event, attrs.get("data") or {})
    return {"received": True}


def _handle_paid_event(session_obj: dict) -> None:
    ref = session_obj.get("id")
    cs_attrs = session_obj.get("attributes") or {}
    amount = None
    for pay in cs_attrs.get("payments") or []:
        pa = pay.get("attributes", {})
        if pa.get("status") == "paid":
            amount = pa.get("amount")
            break
    with SessionLocal() as db:
        payment = db.query(models.Payment).filter(models.Payment.provider_ref == ref).first()
        if not payment:
            log.warning("Webhook for unknown checkout session %s", ref)
            return
        _settle(db, payment.id, amount)
