"""
Subscription plans and what each tier is allowed to do.

Prices are in centavos (integer PHP * 100) to avoid float rounding in
payment amounts. This file is the single source of truth: the API, the
pricing page and the enforcement checks all read from here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .config import settings
from .fea.runner import ANALYSIS_TYPES

CURRENCY = "PHP"


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    tagline: str
    amount_centavos: int
    days: int  # length of one paid period
    badge: Optional[str] = None

    @property
    def amount(self) -> float:
        return self.amount_centavos / 100


PLANS: Dict[str, Plan] = {
    "pro_monthly": Plan(
        id="pro_monthly",
        name="Pro · Monthly",
        tagline="Billed every 30 days. Pay again whenever you like.",
        amount_centavos=49900,
        days=30,
    ),
    "pro_annual": Plan(
        id="pro_annual",
        name="Pro · Annual",
        tagline="365 days of Pro. Two months free.",
        amount_centavos=499000,
        days=365,
        badge="Best value",
    ),
}


@dataclass(frozen=True)
class Limits:
    tier: str
    max_projects: Optional[int]  # None = unlimited
    analysis_types: tuple


def limits_for(tier: str) -> Limits:
    if tier == "pro":
        return Limits("pro", None, tuple(ANALYSIS_TYPES))
    return Limits("free", settings.free_max_projects, ("static",))


FREE_FEATURES = [
    "Up to {n} projects",
    "Static structural analysis",
    "Primitive geometry & meshing",
    "Contour post-processing",
]
PRO_FEATURES = [
    "Unlimited projects",
    "All 8 analysis types: nonlinear plasticity, buckling, dynamics, transient & coupled heat",
    "Rigid bodies, ties and quadratic elements",
    "Everything in Free",
]
