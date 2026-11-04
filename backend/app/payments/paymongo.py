"""
PayMongo Checkout integration, GCash only.

Docs: https://developers.paymongo.com/docs/checkout-api
Flow: create a Checkout Session restricted to `gcash` -> redirect the user to
`checkout_url` -> user approves in the GCash app -> PayMongo redirects back to
`success_url` and sends a `checkout_session.payment.paid` webhook.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional

import httpx

from ..config import settings
from .base import CheckoutSession, ProviderError, ProviderStatus

API = "https://api.paymongo.com/v1"


class PayMongoProvider:
    name = "paymongo"

    def __init__(self, secret_key: str):
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def create_checkout(self, *, payment_id, description, amount_centavos, currency,
                        success_url, cancel_url, customer_email, customer_name) -> CheckoutSession:
        body = {"data": {"attributes": {
            "billing": {"name": customer_name or customer_email, "email": customer_email},
            "line_items": [{
                "currency": currency, "amount": amount_centavos,
                "name": description, "quantity": 1,
            }],
            "payment_method_types": ["gcash"],
            "description": description,
            "reference_number": payment_id,
            "metadata": {"payment_id": payment_id},
            "send_email_receipt": True,
            "show_description": True,
            "show_line_items": True,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }}}
        try:
            r = httpx.post(f"{API}/checkout_sessions", json=body, headers=self._headers, timeout=20)
            r.raise_for_status()
            data = r.json()["data"]
            return CheckoutSession(provider_ref=data["id"], checkout_url=data["attributes"]["checkout_url"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProviderError(f"PayMongo checkout creation failed: {exc}") from exc

    def fetch_status(self, provider_ref: str) -> ProviderStatus:
        try:
            r = httpx.get(f"{API}/checkout_sessions/{provider_ref}", headers=self._headers, timeout=20)
            r.raise_for_status()
            attrs = r.json()["data"]["attributes"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProviderError(f"PayMongo status lookup failed: {exc}") from exc
        for pay in attrs.get("payments") or []:
            pa = pay.get("attributes", {})
            if pa.get("status") == "paid":
                return ProviderStatus(paid=True, amount_centavos=pa.get("amount"))
        intent = (attrs.get("payment_intent") or {}).get("attributes", {})
        if intent.get("status") == "succeeded":
            return ProviderStatus(paid=True, amount_centavos=intent.get("amount"))
        return ProviderStatus(paid=False)


def verify_webhook_signature(raw_body: bytes, header: Optional[str], secret: str, live: bool) -> bool:
    """
    PayMongo signs `"{timestamp}.{raw body}"` with HMAC-SHA256 and sends
    `Paymongo-Signature: t=<ts>,te=<test sig>,li=<live sig>`. Live-mode
    events must match `li`, test-mode events `te`.
    """
    if not header or not secret:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    timestamp = parts.get("t")
    expected = parts.get("li" if live else "te")
    if not timestamp or not expected:
        return False
    computed = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected)


def is_live_key() -> bool:
    return settings.paymongo_secret_key.startswith("sk_live_")
