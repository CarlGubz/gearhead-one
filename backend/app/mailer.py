"""
Outgoing e-mail. Uses SMTP when SMTP_HOST is set; otherwise the message is
written to the log so development works with no mail server.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("gearhead.mail")


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        log.warning("MAIL (SMTP not configured) to=%s subject=%s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except Exception:  # noqa: BLE001 - mail failure must never break a request
        log.exception("Failed to send e-mail to %s", to)


def password_reset_email(to: str, link: str) -> tuple[str, str]:
    return (
        "Reset your Gearhead FEA password",
        f"Someone asked to reset the password for this account.\n\n"
        f"Open this link within {settings.reset_token_minutes} minutes to choose a new one:\n{link}\n\n"
        f"If that wasn't you, ignore this message - your password has not changed.\n",
    )


def receipt_email(plan_name: str, amount: float, currency: str, until: str, ref: str) -> tuple[str, str]:
    return (
        "Your Gearhead FEA Pro payment",
        f"Thanks! We received your GCash payment.\n\n"
        f"Plan:      {plan_name}\nAmount:    {currency} {amount:,.2f}\n"
        f"Reference: {ref}\nPro until: {until}\n\n"
        f"Manage your subscription: {settings.app_base_url}/account\n",
    )
