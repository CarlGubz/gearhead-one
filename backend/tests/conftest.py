import os
import tempfile

# Must be set before the app is imported: settings are read at import time.
_tmp = tempfile.mkdtemp()
# Set TEST_DATABASE_URL=postgresql://... to run the suite against PostgreSQL.
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["APP_ENV"] = "development"
os.environ["PAYMENT_PROVIDER"] = "mock"
os.environ["APP_BASE_URL"] = "http://localhost:5173"
# CFD / CAD workspaces: result files and uploads in the temp dir, fast scheduler ticks, and - unless
# CFD_TEST_EXTERNAL=1 - no external solvers, so the suite is deterministic and exercises the Python fallbacks.
os.environ["CFD_DATA_DIR"] = f"{_tmp}/cfd"
os.environ["CAD_DATA_DIR"] = f"{_tmp}/cad"
os.environ["CFD_SCHEDULER_INTERVAL"] = "0.2"
if os.environ.get("CFD_TEST_EXTERNAL") != "1":
    for _tool in ("OPENFOAM", "FENICSX", "PYFR"):
        os.environ[f"CFD_{_tool}_LAUNCHER"] = "off"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.routers import auth as auth_router, billing as billing_router  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_router.reset_limiters()
    billing_router.reset_limiters()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_client():
    """Factory for independent cookie jars (several users at once)."""
    clients = []

    def _make():
        c = TestClient(app)
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.close()


def register(c, email="ana@example.com", password=PASSWORD, name="Ana Reyes"):
    return c.post("/api/auth/register", json={"email": email, "password": password, "full_name": name})


class WorkspaceClient:
    """
    A signed-in Pro test client for the CFD / CAD suites, which were written against the stand-alone CFDFlow and
    CADWeb APIs. Their paths (`/api/projects`, `/api/runs/...`, `/api/parts/...`) are sent to the workspace's prefix
    (`/api/cfd/...`, `/api/cad/...`); `/api/health` and absolute workspace paths pass through unchanged.
    """

    def __init__(self, client, prefix):
        self._c, self._prefix = client, prefix

    def _url(self, url):
        if url.startswith("/api/") and not url.startswith(("/api/health", "/api/cfd/", "/api/cad/", "/api/auth/")):
            return self._prefix + url[len("/api"):]
        return url

    def get(self, url, *a, **k):
        return self._c.get(self._url(url), *a, **k)

    def post(self, url, *a, **k):
        return self._c.post(self._url(url), *a, **k)

    def put(self, url, *a, **k):
        return self._c.put(self._url(url), *a, **k)

    def patch(self, url, *a, **k):
        return self._c.patch(self._url(url), *a, **k)

    def delete(self, url, *a, **k):
        return self._c.delete(self._url(url), *a, **k)

    def websocket_connect(self, url, *a, **k):
        return self._c.websocket_connect(self._url(url), *a, **k)

    @property
    def raw(self):
        return self._c


def workspace_client(prefix, email):
    """Context manager: fresh tables, a registered Pro user, a WorkspaceClient bound to `prefix`."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        auth_router.reset_limiters()
        billing_router.reset_limiters()
        with TestClient(app) as c:
            assert register(c, email=email).status_code in (200, 201)
            pay = c.post("/api/billing/checkout", json={"plan_id": "pro_annual"}).json()["payment"]["id"]
            assert c.post(f"/api/billing/mock/{pay}/complete", json={"outcome": "paid"}).status_code == 200
            yield WorkspaceClient(c, prefix)

    return _cm()
