"""
Payment-provider interface.

Routes only talk to `PaymentProvider`; adding Xendit, Maya, Dragonpay, ...
means writing one more class with these three methods and registering it in
`get_provider`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


class ProviderError(Exception):
    """The provider rejected the call or could not be reached."""


@dataclass
class CheckoutSession:
    provider_ref: str
    checkout_url: str


@dataclass
class ProviderStatus:
    paid: bool
    amount_centavos: Optional[int] = None  # as reported by the provider, for tamper checks
    failed: bool = False


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, *, payment_id: str, description: str, amount_centavos: int,
                        currency: str, success_url: str, cancel_url: str,
                        customer_email: str, customer_name: str) -> CheckoutSession: ...

    def fetch_status(self, provider_ref: str) -> ProviderStatus: ...
