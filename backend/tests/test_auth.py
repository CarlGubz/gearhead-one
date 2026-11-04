import pytest

from app.config import settings
from tests.conftest import PASSWORD, register


def test_register_sets_httponly_session_and_me_works(client):
    r = register(client)
    assert r.status_code == 201
    assert r.json()["user"]["email"] == "ana@example.com"
    assert r.json()["subscription"]["tier"] == "free"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").status_code == 200


def test_password_hash_is_argon2_and_session_token_is_hashed(client):
    from app.database import SessionLocal
    from app import models
    register(client)
    token = client.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        assert db.query(models.User).one().password_hash.startswith("$argon2id$")
        assert db.query(models.UserSession).one().token_hash != token


@pytest.mark.parametrize("pw", ["short", "password123", "aaaaaaaaaaaa"])
def test_weak_passwords_rejected(client, pw):
    assert register(client, password=pw).status_code == 422


def test_duplicate_email_case_insensitive(client):
    register(client)
    assert register(client, email="ANA@example.com").status_code == 409


def test_login_wrong_password_and_unknown_user_are_indistinguishable(client):
    register(client)
    client.post("/api/auth/logout")
    a = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "nope-nope-nope"})
    b = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_and_logout_cycle(client):
    register(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "Ana@Example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_lockout_after_repeated_failures(client):
    register(client)
    client.post("/api/auth/logout")
    for _ in range(settings.max_failed_logins):
        r = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "wrong-wrong-1"})
        assert r.status_code == 401
    # Even the right password is refused while locked.
    r = client.post("/api/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_ip_rate_limit(client):
    codes = [
        client.post("/api/auth/login", json={"email": f"x{i}@example.com", "password": "whatever-1234"}).status_code
        for i in range(25)
    ]
    assert 429 in codes


def test_change_password_revokes_other_sessions(client, make_client):
    register(client)
    other = make_client()
    other.post("/api/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert other.get("/api/auth/me").status_code == 200
    r = client.post("/api/auth/change-password",
                    json={"current_password": PASSWORD, "new_password": "a brand new passphrase"})
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_password_reset_flow(client, monkeypatch):
    sent = {}
    monkeypatch.setattr("app.mailer.send_email", lambda to, subj, body: sent.update(to=to, body=body))
    register(client)
    client.post("/api/auth/logout")
    assert client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"}).status_code == 202
    assert sent == {}  # unknown address: same response, no mail
    assert client.post("/api/auth/forgot-password", json={"email": "ana@example.com"}).status_code == 202
    token = sent["body"].split("token=")[1].split()[0]
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "fresh passphrase 42"})
    assert r.status_code == 204
    # single use
    again = client.post("/api/auth/reset-password", json={"token": token, "password": "another passphrase 7"})
    assert again.status_code == 400
    ok = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "fresh passphrase 42"})
    assert ok.status_code == 200


def test_cross_site_origin_blocked(client):
    r = client.post("/api/auth/login", json={"email": "a@example.com", "password": "x"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/auth/login", json={"email": "a@example.com", "password": "x"},
                    headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 401


def test_unauthenticated_projects_rejected(client):
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json={"name": "x"}).status_code == 401
