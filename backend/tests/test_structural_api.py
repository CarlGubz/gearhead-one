"""Truss / beam / shell / spring models through the HTTP API."""
import json

import numpy as np
import pytest

from tests.conftest import register
from tests.test_simulation_api import go_pro, solve

E, NU = 210e9, 0.3


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def new_project(c, name="S"):
    return c.post("/api/projects", json={"name": name}).json()["id"]


def steel(c, pid, nu=NU):
    assert c.put(f"/api/projects/{pid}/material", json={"name": "Steel", "E": E, "nu": nu, "density": 7850}).status_code == 200


def structural(c, pid, kind, element_type, dims=None, divisions=4, section=None):
    r = c.post(f"/api/projects/{pid}/mesh/structural",
               json={"kind": kind, "dims": dims or {}, "divisions": divisions, "element_type": element_type, "section": section})
    assert r.status_code == 200, r.text
    return r.json()


def bc(c, pid, ids, dofs, values=None):
    r = c.post(f"/api/projects/{pid}/bcs", json={"name": "bc", "node_ids": ids, "dofs": dofs, "values": values})
    assert r.status_code == 200, r.text


def load(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/loads", json={"name": "L", **body})
    assert r.status_code == 200, r.text
    return r.json()


RECT = {"type": "rect", "b": 0.05, "h": 0.1}
G = E / (2 * (1 + NU))
I_Y = 0.05 * 0.1 ** 3 / 12


def test_beam_cantilever_end_to_end(pro):
    pid = new_project(pro)
    steel(pro, pid)
    p = structural(pro, pid, "line", "B31", {"x1": 0, "x2": 1.5}, divisions=4, section=RECT)
    assert p["element_type"] == "B31" and len(p["elements"]) == 4 and p["surface_faces"] == []
    assert p["parts"][0]["section"]["type"] == "rect"
    bc(pro, pid, [0], [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="force", node_ids=[4], vector=[0, 0, -2000.0])
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    res = j["results"]
    assert res["model_class"] == "structural"
    expected = 2000 * 1.5 ** 3 / (3 * E * I_Y) + 2000 * 1.5 / ((5 / 6) * G * 0.005)
    assert -res["displacements"][4][2] == pytest.approx(expected, rel=1e-5)
    assert len(res["rotations"]) == 5 and len(res["element_forces"]) == 4
    assert max(res["fields"]["bending_moment"]) == pytest.approx(2000 * 1.5, rel=1e-5)
    assert res["reaction_forces"][0][2] == pytest.approx(2000, rel=1e-5)
    q = pro.get(f"/api/projects/{pid}/mesh/quality").json()
    assert q["model_class"] == "structural" and q["total_length"] == pytest.approx(1.5)
    assert pro.get(f"/api/projects/{pid}/export/inp").status_code == 400


def test_append_members_joins_nodes_and_gives_each_part_its_own_section(pro):
    pid = new_project(pro)
    steel(pro, pid)
    structural(pro, pid, "line", "B31", {"x1": 0, "x2": 1.0}, divisions=2, section=RECT)
    r = pro.post(f"/api/projects/{pid}/mesh/structural/append", json={
        "kind": "line", "dims": {"x1": 0, "y1": 0, "z1": 0, "x2": 0, "y2": 0, "z2": 1.0}, "divisions": 2,
        "element_type": "B31", "section": {"type": "circle", "d": 0.05}, "name": "Column"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert len(p["nodes"]) == 5 and len(p["elements"]) == 4              # shared root node merged: 3 + 3 - 1
    assert [q["name"] for q in p["parts"]] == ["Line", "Column"]
    assert p["parts"][1]["section"] == {"type": "circle", "d": 0.05}
    bad = pro.post(f"/api/projects/{pid}/mesh/structural/append", json={"kind": "line", "element_type": "T3D2"})
    assert bad.status_code == 422
    bc(pro, pid, [0], [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="force", node_ids=[2], vector=[0, 0, -1000.0])
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]


def test_section_edit_changes_the_answer_and_is_validated(pro):
    pid = new_project(pro)
    steel(pro, pid)
    p = structural(pro, pid, "line", "B31", {"x2": 1.0}, divisions=2, section=RECT)
    bc(pro, pid, [0], [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="force", node_ids=[2], vector=[0, 0, -1000.0])
    part = p["parts"][0]["id"]
    d1 = -solve(pro, pid)["results"]["displacements"][2][2]
    r = pro.put(f"/api/projects/{pid}/parts/{part}", json={"section": {"type": "rect", "b": 0.05, "h": 0.2}})
    assert r.status_code == 200 and r.json()["parts"][0]["section"]["h"] == 0.2
    d2 = -solve(pro, pid)["results"]["displacements"][2][2]
    assert d1 / d2 == pytest.approx(8.0, rel=0.05)                       # twice the depth -> ~8x stiffer in bending
    assert pro.put(f"/api/projects/{pid}/parts/{part}", json={"section": {"type": "rect", "b": -1, "h": 1}}).status_code == 422
    assert pro.put(f"/api/projects/{pid}/parts/{part}", json={"section": {"type": "wing"}}).status_code == 422


def test_solid_projects_reject_sections_and_structural_only_loads(pro):
    pid = new_project(pro)
    r = pro.post(f"/api/projects/{pid}/mesh/primitive", json={"shape": "box", "dims": {}, "divisions": 2, "element_type": "C3D8"})
    assert r.status_code == 200
    part = r.json()["parts"][0]["id"]
    assert pro.put(f"/api/projects/{pid}/parts/{part}", json={"section": {"area": 1}}).status_code == 422
    assert pro.post(f"/api/projects/{pid}/loads", json={"name": "q", "load_type": "line_load", "vector": [0, 0, -1]}).status_code == 422
    assert pro.post(f"/api/projects/{pid}/mesh/structural/append", json={"kind": "line", "element_type": "B31"}).status_code == 400
    assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "b", "node_ids": [0], "dofs": [4]}).status_code == 422   # solids: 0..2 only


def test_truss_from_custom_layout_member_forces(pro):
    pid = new_project(pro)
    steel(pro, pid)
    r = pro.post(f"/api/projects/{pid}/mesh/structural/custom", json={
        "nodes": [[-1, 0, 0], [1, 0, 0], [0, 1, 0]], "elements": [[0, 2], [1, 2]], "element_type": "T3D2", "section": {"area": 2e-4}})
    assert r.status_code == 200, r.text
    bc(pro, pid, [0, 1], [0, 1, 2])
    bc(pro, pid, [2], [2])
    load(pro, pid, load_type="force", node_ids=[2], vector=[0, -5000.0, 0])
    res = solve(pro, pid)["results"]
    assert -res["displacements"][2][1] == pytest.approx(5000 * np.sqrt(2) / (2 * E * 2e-4 * 0.5), rel=1e-5)
    assert abs(res["element_forces"][0][1]) == pytest.approx(5000 / (2 * np.sqrt(0.5)), rel=1e-5)
    bad = pro.post(f"/api/projects/{pid}/mesh/structural/custom", json={"nodes": [[0, 0, 0]], "elements": [[0, 3]], "element_type": "T3D2"})
    assert bad.status_code == 422
    assert pro.post(f"/api/projects/{pid}/mesh/structural/custom", json={"nodes": [[0, 0, 0], [1, 0, 0]], "elements": [[0, 1, 1]],
                                                                       "element_type": "T3D2"}).status_code == 422


def test_shell_plate_pressure_and_modal(pro):
    pid = new_project(pro)
    steel(pro, pid)
    p = structural(pro, pid, "plate", "S4", {"length": 1.0, "width": 1.0}, divisions=10, section={"thickness": 0.01})
    assert p["element_type"] == "S4" and len(p["surface_faces"]) == 2 * len(p["elements"]) and len(p["surface_parts"]) == len(p["surface_faces"])
    pts = np.array(p["nodes"])
    on = lambda ax, v: [i for i in range(len(pts)) if abs(pts[i, ax] - v) < 1e-9]    # noqa: E731
    bc(pro, pid, sorted(set(on(0, -0.5) + on(0, 0.5) + on(1, -0.5) + on(1, 0.5))), [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="pressure", faces=p["surface_faces"], magnitude=1000.0)
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    D = E * 0.01 ** 3 / (12 * (1 - NU ** 2))
    wmax = -min(j["results"]["fields"]["uz"])
    assert wmax == pytest.approx(0.00126 * 1000.0 / D, rel=0.03)               # clamped square plate, uniform pressure
    assert max(j["results"]["fields"]["von_mises"]) > 0
    assert pro.put(f"/api/projects/{pid}/analysis-type", json={"analysis_type": "modal", "n_modes": 4}).status_code == 200
    jm = solve(pro, pid)
    assert jm["status"] == "completed", jm["log"]
    # clamped square plate: omega = (35.99 / a^2) sqrt(D / (rho t))
    assert jm["results"]["frequencies_hz"][0] == pytest.approx(35.99 / (2 * np.pi) * np.sqrt(D / (7850 * 0.01)), rel=0.03)


def test_buckling_and_dynamic_on_a_beam(pro):
    pid = new_project(pro)
    steel(pro, pid)
    structural(pro, pid, "line", "B31", {"x2": 2.0}, divisions=8, section=RECT)
    bc(pro, pid, [0], [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="force", node_ids=[8], vector=[-1.0, 0, 0])
    assert pro.put(f"/api/projects/{pid}/analysis-type", json={"analysis_type": "buckling", "n_modes": 2}).status_code == 200
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    i_min = 0.1 * 0.05 ** 3 / 12
    assert j["results"]["load_factors"][0] == pytest.approx(np.pi ** 2 * E * i_min / (4 * 2.0 ** 2), rel=5e-3)
    pro.delete(f"/api/projects/{pid}/loads/{pro.get(f'/api/projects/{pid}').json()['loads'][0]['id']}")
    load(pro, pid, load_type="force", node_ids=[8], vector=[0, 0, -100.0])
    assert pro.put(f"/api/projects/{pid}/analysis-type", json={"analysis_type": "dynamic", "n_modes": 2}).status_code == 200
    assert pro.put(f"/api/projects/{pid}/settings", json={"t_end": 0.05, "n_steps": 200, "history_nodes": [8]}).status_code == 200
    jd = solve(pro, pid)
    assert jd["status"] == "completed", jd["log"]
    assert len(jd["results"]["history"]["node 8"]) == 201


def test_unsupported_features_fail_with_readable_messages(pro):
    pid = new_project(pro)
    steel(pro, pid)
    structural(pro, pid, "line", "B31", {"x2": 1.0}, divisions=2, section=RECT)
    bc(pro, pid, [0], [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="force", node_ids=[2], vector=[0, 0, -1.0])
    assert pro.put(f"/api/projects/{pid}/analysis-type", json={"analysis_type": "thermal", "n_modes": 2}).status_code == 200
    j = solve(pro, pid)
    assert j["status"] == "failed" and "not available for structural" in j["log"]
    assert pro.put(f"/api/projects/{pid}/analysis-type", json={"analysis_type": "static", "n_modes": 2}).status_code == 200
    assert pro.put(f"/api/projects/{pid}/settings", json={"nlgeom": True}).status_code == 200
    j = solve(pro, pid)
    assert j["status"] == "failed" and "NLGEOM" in j["log"]


def test_structural_project_file_round_trip_undo_and_part_delete(pro):
    pid = new_project(pro)
    steel(pro, pid)
    structural(pro, pid, "line", "B31", {"x2": 1.0}, divisions=2, section=RECT)
    r = pro.post(f"/api/projects/{pid}/mesh/structural/append", json={
        "kind": "line", "dims": {"x1": 1, "x2": 1, "z2": 1.0}, "divisions": 2, "element_type": "B31", "section": {"type": "circle", "d": 0.04}})
    assert r.status_code == 200, r.text
    bc(pro, pid, [0], [0, 1, 2, 3, 4, 5])
    load(pro, pid, load_type="force", node_ids=[4], vector=[0, 0, -500.0])
    first = solve(pro, pid)["results"]
    data = pro.get(f"/api/projects/{pid}/export/project").json()
    assert data["element_type"] == "B31" and data["parts"][1]["section"]["type"] == "circle"
    imp = pro.post("/api/projects/import", json=data)
    assert imp.status_code == 200, imp.text
    p2 = imp.json()
    assert p2["element_type"] == "B31" and p2["parts"][1]["section"] == {"type": "circle", "d": 0.04}
    again = solve(pro, p2["id"])["results"]
    assert np.allclose(again["displacements"], first["displacements"], rtol=1e-6)
    # undo the load, then the structural append
    assert pro.post(f"/api/projects/{pid}/undo").status_code == 200
    assert pro.get(f"/api/projects/{pid}").json()["loads"] == []
    # deleting a part compresses the model
    p = pro.get(f"/api/projects/{pid}").json()
    d = pro.delete(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}")
    assert d.status_code == 200, d.text
    assert len(d.json()["parts"]) == 1 and len(d.json()["elements"]) == 2
