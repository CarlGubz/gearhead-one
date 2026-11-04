"""Linear-solver choice through the API."""
import pytest

from tests.conftest import register
from tests.test_simulation_api import face_nodes, fix_face, go_pro, make_project, solve, tip_pressure


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def prepared(c):
    p = make_project(c, dims={"length": 1.0, "width": 0.2, "height": 0.2}, div=6, et="C3D8")
    fix_face(c, p)
    tip_pressure(c, p, 1e6)
    return p


def test_linear_solver_setting_is_validated_and_used(pro):
    p = prepared(pro)
    pid = p["id"]
    assert pro.put(f"/api/projects/{pid}/settings", json={"linear_solver": "bogus"}).status_code == 422
    assert pro.put(f"/api/projects/{pid}/settings", json={"solver_tolerance": 0.5}).status_code == 422

    direct = solve(pro, pid)
    assert direct["status"] == "completed" and "sparse LU" in direct["log"]

    assert pro.put(f"/api/projects/{pid}/settings", json={"linear_solver": "iterative"}).status_code == 200
    it = solve(pro, pid)
    assert it["status"] == "completed", it["log"]
    assert "Schwarz PCG" in it["log"]
    import numpy as np
    assert np.allclose(it["results"]["fields"]["ux"], direct["results"]["fields"]["ux"], rtol=1e-5, atol=1e-12)

    assert pro.put(f"/api/projects/{pid}/settings", json={"linear_solver": "auto", "auto_threshold": 1000}).status_code == 200
    auto = solve(pro, pid)
    assert "Schwarz PCG" in auto["log"]
