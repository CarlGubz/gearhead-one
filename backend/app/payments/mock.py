"""
Development-only provider. "Checkout" is an in-app page that pretends to be
the GCash approval screen; nothing is charged. Disabled in production by
`Settings.validate_for_production`.
"""
from __future__ import annotations

import uuid

from ..config import settings
from .base import CheckoutSession, ProviderStatus


class MockProvider:
    name = "mock"

    def create_checkout(self, *, payment_id, amount_centavos, **_) -> CheckoutSession:
        ref = f"mock_{uuid.uuid4().hex[:16]}"
        return CheckoutSession(provider_ref=ref, checkout_url=f"{settings.app_base_url}/pay/mock/{payment_id}")

    def fetch_status(self, provider_ref: str) -> ProviderStatus:
        # The mock has no remote state; completion is recorded by the
        # /billing/mock/{id}/complete endpoint, so the status poll never
        # flips a payment by itself.
        return ProviderStatus(paid=False)
