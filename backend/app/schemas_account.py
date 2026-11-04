"""Request/response schemas for accounts and billing."""
from __future__ import annotations

import datetime as dt
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    full_name: str = Field(default="", max_length=120)

    @field_validator("full_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class LimitsOut(BaseModel):
    max_projects: Optional[int]
    analysis_types: List[str]


class SubscriptionOut(BaseModel):
    tier: Literal["free", "pro"]
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    current_period_end: Optional[dt.datetime] = None
    days_left: int = 0
    limits: LimitsOut
    projects_used: int = 0


class MeOut(BaseModel):
    user: UserOut
    subscription: SubscriptionOut


class PlanOut(BaseModel):
    id: str
    name: str
    tagline: str
    amount: float
    amount_centavos: int
    days: int
    badge: Optional[str] = None


class PlansOut(BaseModel):
    currency: str
    plans: List[PlanOut]
    free_features: List[str]
    pro_features: List[str]
    payment_provider: str


class CheckoutIn(BaseModel):
    plan_id: str


class PaymentOut(BaseModel):
    id: str
    plan_id: str
    plan_name: str
    amount: float
    currency: str
    provider: str
    status: str
    created_at: dt.datetime
    paid_at: Optional[dt.datetime] = None


class CheckoutOut(BaseModel):
    payment: PaymentOut
    checkout_url: str


class VerifyOut(BaseModel):
    payment: PaymentOut
    subscription: SubscriptionOut


class MockCompleteIn(BaseModel):
    outcome: Literal["paid", "failed"]
