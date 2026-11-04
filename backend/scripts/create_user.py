"""
Create (or update) a user from the command line - handy for dev/admin accounts.

    cd backend
    python -m scripts.create_user you@example.com --name "Your Name" --pro-days 365
    python -m scripts.create_user you@example.com --password "my long passphrase"

Uses DATABASE_URL like the app does (default: ./webfea.db). With no
--password a strong random one is generated and printed once.
An existing user's password is reset and Pro time is added on top.
"""
from __future__ import annotations

import argparse
import datetime as dt
import secrets

from app import models  # noqa: F401  (registers tables)
from app.database import Base, SessionLocal, engine
from app.models import Subscription, User, utcnow
from app.security import hash_password, password_problem


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email")
    ap.add_argument("--name", default="")
    ap.add_argument("--password", help="omit to generate a random one")
    ap.add_argument("--pro-days", type=int, default=0, help="grant this many days of Pro (no payment)")
    args = ap.parse_args()

    email = args.email.strip().lower()
    password = args.password or secrets.token_urlsafe(12)
    problem = password_problem(password, email)
    if problem:
        raise SystemExit(problem)

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        created = user is None
        if created:
            user = User(email=email, password_hash=hash_password(password), full_name=args.name)
            db.add(user)
        else:
            user.password_hash = hash_password(password)
            user.failed_logins, user.locked_until = 0, None
            if args.name:
                user.full_name = args.name
        db.flush()

        if args.pro_days > 0:
            now = utcnow()
            sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
            start = sub.current_period_end if sub and sub.current_period_end > now else now
            end = start + dt.timedelta(days=args.pro_days)
            if sub:
                sub.plan_id, sub.current_period_start, sub.current_period_end = "pro_annual", start, end
            else:
                db.add(Subscription(user_id=user.id, plan_id="pro_annual",
                                    current_period_start=start, current_period_end=end))
        db.commit()

    print(f"{'Created' if created else 'Updated'} {email}")
    print(f"Password: {password}")
    if args.pro_days:
        print(f"Pro granted for {args.pro_days} days")


if __name__ == "__main__":
    main()
