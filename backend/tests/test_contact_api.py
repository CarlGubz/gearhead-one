"""Frictionless contact between parts through the HTTP API."""
import numpy as np
import pytest

from tests.conftest import register
from tests.test_simulation_api import face_nodes, fix_face, go_pro, make_project, set_type, solve, tip_pressure

P = 1e6


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def stacked(c, gap=0.0):
    """Base block z in [-0.2, 0.2] and a 0.2 m block on top of it, two parts, pressed from above."""
    p = make_project(c, dims={"length": 0.2, "width": 0.2, "height": 0.4}, div=2, et="C3D8")
    pid = p["id"]
    base = {"E": 100e9, "nu": 0.0, "density": 7800, "conductivity": 50, "specific_heat": 490}
    assert c.put(f"/api/projects/{pid}/material", json={"name": "Steel", **base}).status_code == 200
    r = c.post(f"/api/projects/{pid}/mesh/append", json={
        "shape": "box", "dims": {"length": 0.2, "width": 0.2, "height": 0.2}, "divisions": 2, "element_type": "C3D8",
        "offset": [0, 0, 0.3 + gap], "name": "Top"})
    assert r.status_code == 200, r.text
    p = r.json()
    pts = np.array(p["nodes"])
    top = [i for i in set(sum(p["elements"][p["parts"][1]["elem_start"]:], []))]
    fix_face(c, p, axis=2, which="min")
    xs = [i for i in top if abs(pts[i, 0] - pts[top, 0].min()) < 1e-9]
    ys = [i for i in top if abs(pts[i, 1] - pts[top, 1].min()) < 1e-9]
    assert c.post(f"/api/projects/{pid}/bcs", json={"name": "rx", "node_ids": xs, "dofs": [0]}).status_code == 200
    assert c.post(f"/api/projects/{pid}/bcs", json={"name": "ry", "node_ids": ys, "dofs": [1]}).status_code == 200
    p = c.get(f"/api/projects/{pid}").json()
    tip_pressure(c, p, P, axis=2)
    return c.get(f"/api/projects/{pid}").json()


def test_contact_end_to_end(pro):
    p = stacked(pro)
    pid, (a, b) = p["id"], [q["id"] for q in p["parts"]]
    r = pro.post(f"/api/projects/{pid}/constraints",
                 json={"name": "Seat", "type": "contact", "master_part": a, "slave_part": b, "penalty_factor": 10})
    assert r.status_code == 200, r.text
    assert r.json()["constraints"][0]["type"] == "contact"
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    res = j["results"]
    assert "Contact:" in j["log"]
    assert res["contact"]["total_force"] == pytest.approx(P * 0.04, rel=1e-2)
    assert max(res["fields"]["contact_pressure"]) > 0 and "contact_gap" in res["fields"]
    # series stiffness: 0.6 m of steel under P (+ a tiny penalty compliance)
    top = face_nodes(p, 2, "max")
    assert np.mean(np.array(res["fields"]["uz"])[top]) == pytest.approx(-P * 0.6 / 100e9, rel=2e-2)


def test_contact_constraint_validation(pro):
    p = stacked(pro)
    pid, (a, b) = p["id"], [q["id"] for q in p["parts"]]
    post = lambda body: pro.post(f"/api/projects/{pid}/constraints", json={"name": "c", "type": "contact", **body})  # noqa: E731
    assert post({"master_part": a, "slave_part": a}).status_code == 422
    assert post({"master_part": a, "slave_part": "ghost"}).status_code == 422
    assert post({"master_part": a}).status_code == 422
    assert post({"master_part": a, "slave_part": b, "penalty_factor": -1}).status_code == 422


def test_contact_is_limited_to_static_and_nonlinear(pro):
    p = stacked(pro)
    pid, (a, b) = p["id"], [q["id"] for q in p["parts"]]
    assert pro.post(f"/api/projects/{pid}/constraints",
                    json={"name": "c", "type": "contact", "master_part": a, "slave_part": b}).status_code == 200
    set_type(pro, pid, "modal")
    j = solve(pro, pid)
    assert j["status"] == "failed" and "static and nonlinear" in j["log"]
    set_type(pro, pid, "nonlinear", increments=3)
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    assert j["results"]["contact"]["active_nodes"] > 0
