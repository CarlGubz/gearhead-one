import datetime as dt
import hashlib
import hmac
import json

import pytest

from app import models
from app.config import Settings, settings
from app.database import SessionLocal
from app.models import utcnow
from app.payments.base import ProviderStatus
from app.payments.paymongo import verify_webhook_signature
from tests.conftest import register


def new_project(c, name="P"):
    return c.post("/api/projects", json={"name": name})


def pay(client, plan_id="pro_monthly"):
    r = client.post("/api/billing/checkout", json={"plan_id": plan_id})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------ ownership
def test_users_cannot_see_or_touch_each_others_projects(client, make_client):
    register(client, "a@example.com")
    pid = new_project(client).json()["id"]
    other = make_client()
    register(other, "b@example.com")
    assert other.get("/api/projects").json() == []
    assert other.get(f"/api/projects/{pid}").status_code == 404
    assert other.delete(f"/api/projects/{pid}").status_code == 404
    assert other.post(f"/api/projects/{pid}/solve").status_code == 404
    assert other.get(f"/api/projects/{pid}/jobs").status_code == 404


# ---------------------------------------------------------- plan limits
def test_free_plan_project_limit_and_analysis_gating(client):
    register(client)
    for i in range(settings.free_max_projects):
        assert new_project(client, f"p{i}").status_code == 200
    assert new_project(client, "one too many").status_code == 402
    pid = client.get("/api/projects").json()[0]["id"]
    assert client.put(f"/api/projects/{pid}/analysis-type",
                      json={"analysis_type": "modal", "n_modes": 4}).status_code == 402
    assert client.put(f"/api/projects/{pid}/analysis-type",
                      json={"analysis_type": "static", "n_modes": 4}).status_code == 200


# ------------------------------------------------------- mock checkout
def test_mock_gcash_payment_unlocks_pro(client):
    register(client)
    out = pay(client)
    assert out["checkout_url"].endswith(f"/pay/mock/{out['payment']['id']}")
    assert out["payment"]["status"] == "pending"
    pid = out["payment"]["id"]

    r = client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"})
    assert r.status_code == 200
    body = r.json()
    assert body["payment"]["status"] == "paid"
    assert body["subscription"]["tier"] == "pro"
    assert body["subscription"]["limits"]["max_projects"] is None
    assert 29 <= body["subscription"]["days_left"] <= 31

    for i in range(settings.free_max_projects + 2):
        assert new_project(client, f"p{i}").status_code == 200
    pid2 = client.get("/api/projects").json()[0]["id"]
    assert client.put(f"/api/projects/{pid2}/analysis-type",
                      json={"analysis_type": "thermal", "n_modes": 4}).status_code == 200


def test_completing_twice_does_not_double_extend(client):
    register(client)
    pid = pay(client)["payment"]["id"]
    first = client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"}).json()
    second = client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"}).json()
    assert first["subscription"]["current_period_end"] == second["subscription"]["current_period_end"]


def test_early_renewal_stacks_time(client):
    register(client)
    p1 = pay(client)["payment"]["id"]
    client.post(f"/api/billing/mock/{p1}/complete", json={"outcome": "paid"})
    p2 = pay(client, "pro_annual")["payment"]["id"]
    sub = client.post(f"/api/billing/mock/{p2}/complete", json={"outcome": "paid"}).json()["subscription"]
    assert 390 <= sub["days_left"] <= 396  # ~30 + 365


def test_lapsed_subscription_falls_back_to_free(client):
    register(client)
    pid = pay(client)["payment"]["id"]
    client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"})
    with SessionLocal() as db:
        sub = db.query(models.Subscription).one()
        sub.current_period_end = utcnow() - dt.timedelta(days=1)
        db.commit()
    assert client.get("/api/billing/subscription").json()["tier"] == "free"


def test_failed_payment_grants_nothing(client):
    register(client)
    pid = pay(client)["payment"]["id"]
    r = client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "failed"}).json()
    assert r["payment"]["status"] == "failed"
    assert r["subscription"]["tier"] == "free"


def test_cannot_complete_someone_elses_payment(client, make_client):
    register(client, "a@example.com")
    pid = pay(client)["payment"]["id"]
    other = make_client()
    register(other, "b@example.com")
    assert other.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"}).status_code == 404
    assert other.get(f"/api/billing/payments/{pid}").status_code == 404


def test_unknown_plan_and_anonymous_checkout(client):
    assert client.post("/api/billing/checkout", json={"plan_id": "pro_monthly"}).status_code == 401
    register(client)
    assert client.post("/api/billing/checkout", json={"plan_id": "enterprise"}).status_code == 404


def test_mock_endpoints_disabled_when_provider_is_not_mock(client, monkeypatch):
    register(client)
    pid = pay(client)["payment"]["id"]
    monkeypatch.setattr(settings, "payment_provider", "paymongo")
    assert client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"}).status_code == 404


def test_production_refuses_mock_payments():
    s = Settings(app_env="production", database_url="postgresql://u:p@h/db",
                 app_base_url="https://example.com", payment_provider="mock")
    with pytest.raises(RuntimeError, match="mock"):
        s.validate_for_production()


def test_production_requires_postgres_and_https():
    s = Settings(app_env="production", database_url="sqlite:///x.db",
                 app_base_url="http://example.com", payment_provider="paymongo",
                 paymongo_secret_key="sk_live_x", paymongo_webhook_secret="whsk_x")
    with pytest.raises(RuntimeError) as err:
        s.validate_for_production()
    assert "PostgreSQL" in str(err.value) and "https" in str(err.value)


# --------------------------------------------------------- PayMongo webhook
SECRET = "whsk_test_secret"


def sign(body: bytes, live=False, ts="1700000000", secret=SECRET):
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},te={'' if live else sig},li={sig if live else ''}"


def paid_event(ref, amount, live=False):
    return json.dumps({"data": {"id": "evt_1", "attributes": {
        "type": "checkout_session.payment.paid", "livemode": live,
        "data": {"id": ref, "attributes": {"payments": [{"attributes": {"status": "paid", "amount": amount}}]}},
    }}}).encode()


def test_signature_verification_unit():
    body = b'{"x":1}'
    assert verify_webhook_signature(body, sign(body), SECRET, live=False)
    assert not verify_webhook_signature(body, sign(body), SECRET, live=True)  # test sig can't pass as live
    assert not verify_webhook_signature(body + b" ", sign(body), SECRET, live=False)  # tampered body
    assert not verify_webhook_signature(body, sign(body, secret="other"), SECRET, live=False)
    assert not verify_webhook_signature(body, None, SECRET, live=False)
    assert not verify_webhook_signature(body, sign(body), "", live=False)


def use_paymongo(monkeypatch, key="sk_test_abc"):
    monkeypatch.setattr(settings, "paymongo_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "paymongo_secret_key", key)
    monkeypatch.setattr(settings, "payment_provider", "paymongo")


def make_pending_paymongo_payment(client):
    """A pending payment as PayMongo mode would have created it."""
    register(client)
    out = pay(client)
    with SessionLocal() as db:
        p = db.get(models.Payment, out["payment"]["id"])
        p.provider, p.provider_ref = "paymongo", "cs_test_123"
        db.commit()
    return out["payment"]["id"]


def test_webhook_settles_once_and_rejects_bad_signature(client, monkeypatch):
    pid = make_pending_paymongo_payment(client)
    use_paymongo(monkeypatch)
    body = paid_event("cs_test_123", 49900)

    bad = client.post("/api/webhooks/paymongo", content=body,
                      headers={"Paymongo-Signature": sign(body, secret="x")})
    assert bad.status_code == 400
    assert client.get(f"/api/billing/payments/{pid}").json()["status"] == "pending"

    ok = client.post("/api/webhooks/paymongo", content=body, headers={"Paymongo-Signature": sign(body)})
    assert ok.status_code == 200
    assert client.get(f"/api/billing/payments/{pid}").json()["status"] == "paid"
    end1 = client.get("/api/billing/subscription").json()["current_period_end"]

    # Provider retries the same event: nothing changes.
    client.post("/api/webhooks/paymongo", content=body, headers={"Paymongo-Signature": sign(body)})
    assert client.get("/api/billing/subscription").json()["current_period_end"] == end1


def test_webhook_with_wrong_amount_is_ignored(client, monkeypatch):
    pid = make_pending_paymongo_payment(client)
    use_paymongo(monkeypatch)
    body = paid_event("cs_test_123", 100)  # paid 1 peso for a 499 peso plan
    client.post("/api/webhooks/paymongo", content=body, headers={"Paymongo-Signature": sign(body)})
    assert client.get(f"/api/billing/payments/{pid}").json()["status"] == "pending"
    assert client.get("/api/billing/subscription").json()["tier"] == "free"


def test_test_mode_event_cannot_settle_live_deployment(client, monkeypatch):
    pid = make_pending_paymongo_payment(client)
    use_paymongo(monkeypatch, key="sk_live_abc")
    body = paid_event("cs_test_123", 49900, live=False)
    r = client.post("/api/webhooks/paymongo", content=body, headers={"Paymongo-Signature": sign(body)})
    assert r.status_code == 400
    assert client.get(f"/api/billing/payments/{pid}").json()["status"] == "pending"


def test_verify_endpoint_settles_via_provider_lookup(client, monkeypatch):
    pid = make_pending_paymongo_payment(client)

    class Fake:
        name = "paymongo"

        def fetch_status(self, ref):
            assert ref == "cs_test_123"
            return ProviderStatus(paid=True, amount_centavos=49900)

    monkeypatch.setattr("app.routers.billing.get_provider", lambda: Fake())
    r = client.post(f"/api/billing/payments/{pid}/verify").json()
    assert r["payment"]["status"] == "paid" and r["subscription"]["tier"] == "pro"


def test_plans_endpoint_is_public(client):
    r = client.get("/api/billing/plans").json()
    assert r["currency"] == "PHP"
    assert {p["id"] for p in r["plans"]} == {"pro_monthly", "pro_annual"}
