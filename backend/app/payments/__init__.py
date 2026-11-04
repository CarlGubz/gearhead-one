from __future__ import annotations

from ..config import settings
from .base import PaymentProvider, ProviderError  # noqa: F401
from .mock import MockProvider
from .paymongo import PayMongoProvider


def get_provider() -> PaymentProvider:
    if settings.payment_provider == "paymongo":
        if not settings.paymongo_secret_key:
            raise ProviderError("PAYMONGO_SECRET_KEY is not configured")
        return PayMongoProvider(settings.paymongo_secret_key)
    if settings.payment_provider == "mock":
        return MockProvider()
    raise ProviderError(f"Unknown PAYMENT_PROVIDER '{settings.payment_provider}'")
