"""CAD / CAM workbench tests (formerly the CADWeb suite), run against the integrated Gearhead app."""
import pytest

from tests.conftest import workspace_client


@pytest.fixture(autouse=True)
def clean_db():
    """Overrides the platform's per-test table reset: these tests share one signed-in user per module."""
    yield


@pytest.fixture(scope="module")
def client():
    with workspace_client("/api/cad", "cad-tests@example.com") as c:
        yield c


@pytest.fixture()
def project(client):
    r = client.post("/api/projects", json={"name": "pytest project"})
    assert r.status_code == 201
    pid = r.json()["id"]
    yield pid
    client.delete(f"/api/projects/{pid}")
