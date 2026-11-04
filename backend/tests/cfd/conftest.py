"""CFD workbench tests (formerly the CFDFlow suite), run against the integrated Gearhead app."""
import pytest

from tests.conftest import workspace_client


@pytest.fixture(autouse=True)
def clean_db():
    """Overrides the platform's per-test table reset: these tests share one signed-in user per module."""
    yield


@pytest.fixture(scope="module")
def client():
    with workspace_client("/api/cfd", "cfd-tests@example.com") as c:
        yield c
