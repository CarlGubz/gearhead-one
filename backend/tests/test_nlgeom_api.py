"""Large-deformation analysis through the HTTP API."""
import numpy as np
import pytest

from tests.conftest import register
from tests.test_simulation_api import face_nodes, face_tris, fix_face, go_pro, make_project, set_type, solve


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def cantilever(c, alpha=1.0):
    L, w, E = 1.0, 0.1, 210e9
    p = make_project(c, dims={"length": L, "width": w, "height": w}, div=2, et="C3D20")
    pid = p["id"]
    assert c.put(f"/api/projects/{pid}/material", json={"name": "S", "E": E, "nu": 0.0}).status_code == 200
    fix_face(c, p)
    P = alpha * E * w ** 4 / 12.0 / L ** 2
    r = c.post(f"/api/projects/{pid}/loads", json={"name": "T", "load_type": "traction", "faces": face_tris(p, 0, "max"),
                                                    "vector": [0.0, 0.0, -P / w ** 2]})
    assert r.status_code == 200, r.text
    return p


def tip_drop(p, j):
    z = np.array(j["results"]["displacements"])[face_nodes(p, 0, "max"), 2]
    return -z.mean()


def test_nlgeom_setting_is_validated_and_changes_the_answer(pro):
    p = cantilever(pro)
    pid = p["id"]
    assert pro.put(f"/api/projects/{pid}/settings", json={"finite_strain_model": "bogus"}).status_code == 422
    set_type(pro, pid, "static")
    lin = solve(pro, pid)
    assert lin["status"] == "completed", lin["log"]
    assert pro.put(f"/api/projects/{pid}/settings", json={"nlgeom": True, "increments": 8}).status_code == 200
    nl = solve(pro, pid)
    assert nl["status"] == "completed", nl["log"]
    assert "large deformation" in nl["log"]
    L = 1.0
    assert tip_drop(p, lin) / L == pytest.approx(1.0 / 3.0, rel=0.02)         # small-strain: PL^3/3EI
    assert tip_drop(p, nl) / L == pytest.approx(0.3017, rel=0.02)             # elastica
    assert pro.put(f"/api/projects/{pid}/settings", json={"finite_strain_model": "neohookean"}).status_code == 200
    nh = solve(pro, pid)
    assert nh["status"] == "completed", nh["log"]
    assert tip_drop(p, nh) / L == pytest.approx(0.3017, rel=0.03)


def test_nlgeom_is_rejected_for_other_analyses(pro):
    p = cantilever(pro)
    pid = p["id"]
    assert pro.put(f"/api/projects/{pid}/settings", json={"nlgeom": True}).status_code == 200
    set_type(pro, pid, "modal")
    j = solve(pro, pid)
    assert j["status"] == "failed" and "static and nonlinear" in j["log"]
