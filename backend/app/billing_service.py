"""Subscription state and idempotent payment fulfilment."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy.orm import Session

from . import models, plans, schemas_account as sa
from .deps import tier_of
from .models import utcnow

log = logging.getLogger("gearhead.billing")


def subscription_summary(user: models.User, db: Session) -> sa.SubscriptionOut:
    tier = tier_of(user)
    lim = plans.limits_for(tier)
    sub = user.subscription
    used = db.query(models.Project).filter(models.Project.owner_id == user.id).count()
    out = sa.SubscriptionOut(
        tier=tier,
        limits=sa.LimitsOut(max_projects=lim.max_projects, analysis_types=list(lim.analysis_types)),
        projects_used=used,
    )
    if sub:
        plan = plans.PLANS.get(sub.plan_id)
        out.plan_id = sub.plan_id
        out.plan_name = plan.name if plan else sub.plan_id
        out.current_period_end = sub.current_period_end
        if tier == "pro":
            out.days_left = max(0, (sub.current_period_end - utcnow()).days + 1)
    return out


def payment_out(p: models.Payment) -> sa.PaymentOut:
    plan = plans.PLANS.get(p.plan_id)
    return sa.PaymentOut(
        id=p.id, plan_id=p.plan_id, plan_name=plan.name if plan else p.plan_id,
        amount=p.amount_centavos / 100, currency=p.currency, provider=p.provider,
        status=p.status, created_at=p.created_at, paid_at=p.paid_at,
    )


def mark_paid(db: Session, payment_id: str, reported_amount_centavos: Optional[int]):
    """
    Settle a payment exactly once and extend the buyer's subscription.

    Called from both the webhook and the return-URL verification; whichever
    arrives second finds `status == "paid"` and does nothing. The row is
    locked (`SELECT ... FOR UPDATE` on PostgreSQL) so two simultaneous calls
    cannot both extend the period.

    Returns `(payment | None, newly_settled)`; `newly_settled` is True only
    for the call that actually flipped the payment to paid.
    """
    payment = (
        db.query(models.Payment)
        .filter(models.Payment.id == payment_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if payment is None:
        return None, False
    if payment.status == "paid":
        return payment, False
    if reported_amount_centavos is not None and reported_amount_centavos != payment.amount_centavos:
        log.error("Amount mismatch for payment %s: expected %s, provider says %s",
                  payment.id, payment.amount_centavos, reported_amount_centavos)
        db.rollback()
        return None, False
    plan = plans.PLANS.get(payment.plan_id)
    if plan is None:
        log.error("Payment %s references unknown plan %s", payment.id, payment.plan_id)
        db.rollback()
        return None, False

    now = utcnow()
    sub = db.query(models.Subscription).filter(models.Subscription.user_id == payment.user_id).one_or_none()
    if sub and sub.current_period_end > now:
        start = sub.current_period_end  # renew early: stack onto the remaining time
    else:
        start = now
    end = start + dt.timedelta(days=plan.days)
    if sub:
        sub.plan_id = plan.id
        sub.current_period_start = start
        sub.current_period_end = end
    else:
        db.add(models.Subscription(user_id=payment.user_id, plan_id=plan.id,
                                   current_period_start=start, current_period_end=end))
    payment.status = "paid"
    payment.paid_at = now
    db.commit()
    return payment, True


def mark_failed(db: Session, payment: models.Payment) -> None:
    if payment.status == "pending":
        payment.status = "failed"
        db.commit()
