"""End-to-end: build a model over HTTP, solve it, and check results against closed-form values."""
import time

import numpy as np
import pytest

from tests.conftest import register


def go_pro(client):
    pid = client.post("/api/billing/checkout", json={"plan_id": "pro_monthly"}).json()["payment"]["id"]
    assert client.post(f"/api/billing/mock/{pid}/complete", json={"outcome": "paid"}).status_code == 200


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def make_project(c, shape="box", dims=None, div=2, et="C3D20", name="T"):
    p = c.post("/api/projects", json={"name": name}).json()
    r = c.post(f"/api/projects/{p['id']}/mesh/primitive",
               json={"shape": shape, "dims": {"length": 1.0, "width": 0.1, "height": 0.1} if dims is None else dims,
                     "divisions": div, "element_type": et})
    assert r.status_code == 200, r.text
    return r.json()


def corner_ids(p):
    nc = {"C3D4": 4, "C3D10": 4, "C3D8": 8, "C3D20": 8}[p["element_type"]]
    return sorted({n for e in p["elements"] for n in e[:nc]})


def face_nodes(p, axis, which):
    pts = np.array(p["nodes"])
    v = pts[:, axis].min() if which == "min" else pts[:, axis].max()
    return [i for i in corner_ids(p) if abs(pts[i, axis] - v) < 1e-9]


def face_tris(p, axis, which):
    pts = np.array(p["nodes"])
    v = pts[:, axis].min() if which == "min" else pts[:, axis].max()
    return [t for t in p["surface_faces"] if all(abs(pts[n, axis] - v) < 1e-9 for n in t)]


def fix_face(c, p, axis=0, which="min", dofs=(0, 1, 2)):
    r = c.post(f"/api/projects/{p['id']}/bcs",
               json={"name": "Fixed", "node_ids": face_nodes(p, axis, which), "dofs": list(dofs)})
    assert r.status_code == 200, r.text


def solve(c, pid, timeout=90):
    job = c.post(f"/api/projects/{pid}/solve")
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(0.2)
    raise AssertionError("solver timed out")


def set_type(c, pid, kind, **settings):
    assert c.put(f"/api/projects/{pid}/analysis-type", json={"analysis_type": kind, "n_modes": 4}).status_code == 200
    if settings:
        assert c.put(f"/api/projects/{pid}/settings", json=settings).status_code == 200


def tip_pressure(c, p, mag, axis=0):
    r = c.post(f"/api/projects/{p['id']}/loads",
               json={"name": "P", "load_type": "pressure", "faces": face_tris(p, axis, "max"), "magnitude": mag})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ analyses
def test_static_tension_bar_through_the_api(pro):
    p = make_project(pro, dims={"length": 2.0, "width": 0.2, "height": 0.2}, et="C3D10")
    # symmetry planes (rollers) so the state is uniform
    for ax in range(3):
        pro.post(f"/api/projects/{p['id']}/bcs",
                 json={"name": f"sym{ax}", "node_ids": face_nodes(p, ax, "min"), "dofs": [ax]})
    tip_pressure(pro, p, -1e6)
    j = solve(pro, p["id"])
    assert j["status"] == "completed", j["log"]
    r = j["results"]
    assert max(r["fields"]["ux"]) == pytest.approx(1e6 * 2.0 / 210e9, rel=1e-6)
    assert max(r["fields"]["von_mises"]) == pytest.approx(1e6, rel=1e-5)
    assert j["solver_used"] == "gearhead-fea"
    assert r["reaction_total"][0] == pytest.approx(-1e6 * 0.04, rel=1e-6)


def test_quadratic_midside_nodes_are_constrained_with_the_face(pro):
    p = make_project(pro, et="C3D10")
    fix_face(pro, p)  # only CORNER nodes are sent, like the UI does
    tip_pressure(pro, p, 1e5)
    j = solve(pro, p["id"])
    assert j["status"] == "completed", j["log"]
    u = np.array(j["results"]["displacements"])
    x = np.array(p["nodes"])[:, 0]
    assert np.abs(u[np.isclose(x, x.min())]).max() == 0.0  # mid-side nodes included


def test_modal_buckling_and_nonlinear(pro):
    p = make_project(pro, dims={"length": 1.0, "width": 0.05, "height": 0.05})
    fix_face(pro, p)
    tip_pressure(pro, p, 1000 / 0.05 ** 2)  # 1 kN compression
    set_type(pro, p["id"], "modal")
    m = solve(pro, p["id"])
    assert m["status"] == "completed", m["log"]
    f1 = (1.875104 ** 2 / (2 * np.pi)) * np.sqrt(210e9 * (0.05 ** 4 / 12) / (7850 * 0.05 ** 2))
    assert m["results"]["frequencies_hz"][0] == pytest.approx(f1, rel=0.02)

    set_type(pro, p["id"], "buckling")
    b = solve(pro, p["id"])
    assert b["status"] == "completed", b["log"]
    pcr = np.pi ** 2 * 210e9 * (0.05 ** 4 / 12) / 4
    assert b["results"]["load_factors"][0] * 1000 == pytest.approx(pcr, rel=0.02)

    assert pro.put(f"/api/projects/{p['id']}/material", json={
        "E": 200e9, "nu": 0.3, "density": 7800, "yield_stress": 250e6, "hardening": 2e9}).status_code == 200
    pro.delete(f"/api/projects/{p['id']}/loads/" + pro.get(f"/api/projects/{p['id']}").json()["loads"][0]["id"])
    tip_pressure(pro, pro.get(f"/api/projects/{p['id']}").json(), -300e6)  # tension past yield
    set_type(pro, p["id"], "nonlinear", increments=8)
    n = solve(pro, p["id"])
    assert n["status"] == "completed", n["log"]
    assert n["results"]["max_plastic_strain"] > 0.01


def test_thermal_steady_transient_and_thermomech(pro):
    p = make_project(pro, dims={"length": 1.0, "width": 0.1, "height": 0.1})
    pid = p["id"]
    set_type(pro, pid, "thermal")
    for which, T in (("min", 100.0), ("max", 0.0)):
        pro.post(f"/api/projects/{pid}/bcs", json={"name": f"T{which}", "bc_type": "temperature",
                                                   "node_ids": face_nodes(p, 0, which), "dofs": [0], "values": [T]})
    s = solve(pro, pid)
    assert s["status"] == "completed", s["log"]
    xs = np.array(p["nodes"])[:, 0]
    expect = 100 * (1 - (xs - xs.min()) / (xs.max() - xs.min()))
    assert np.allclose(s["results"]["temperatures"], expect, atol=1e-4)

    set_type(pro, pid, "thermal_transient", t_end=2000.0, n_steps=40, initial_temperature=0.0)
    tr = solve(pro, pid)
    assert tr["status"] == "completed", tr["log"]
    assert len(tr["results"]["frames"]) > 5 and tr["results"]["history"]["max_temperature"][-1] <= 100.0 + 1e-6

    # thermo-mechanical: temperature BCs (typed) + a clamp at x-min; bar expands against the clamp
    set_type(pro, pid, "thermomech", reference_temperature=0.0, thermal_mode="steady")
    fix_face(pro, p)
    kept = pro.get(f"/api/projects/{pid}").json()["boundary_conditions"]
    assert {b.get("bc_type") for b in kept} >= {"temperature", None}
    tm = solve(pro, pid)
    assert tm["status"] == "completed", tm["log"]
    assert tm["results"]["max_temperature"] == pytest.approx(100.0, abs=1e-6)
    assert tm["results"]["max_displacement"] > 0


def test_dynamic_response_and_history(pro):
    p = make_project(pro, dims={"length": 1.0, "width": 0.05, "height": 0.05})
    fix_face(pro, p)
    pro.post(f"/api/projects/{p['id']}/loads", json={"name": "P", "load_type": "pressure",
                                                      "faces": face_tris(p, 0, "max"), "magnitude": 1e4})
    set_type(pro, p["id"], "dynamic", t_end=0.05, n_steps=100, amplitude={"type": "step"},
             history_nodes=[face_nodes(p, 0, "max")[0]])
    j = solve(pro, p["id"])
    assert j["status"] == "completed", j["log"]
    r = j["results"]
    assert len(r["frames"]) >= 10 and len(r["frame_times"]) == len(r["frames"])
    assert any(k.startswith("node") for k in r["history"])


def test_rigid_body_and_reference_point_load(pro):
    p = make_project(pro, dims={"length": 1.0, "width": 0.1, "height": 0.1})
    pid = p["id"]
    fix_face(pro, p)
    rp = pro.post(f"/api/projects/{pid}/reference-points", json={"name": "RP1", "coords": [0.5, 0, 0]})
    rid = rp.json()["reference_points"][0]["id"]
    c = pro.post(f"/api/projects/{pid}/constraints", json={
        "name": "Rigid", "type": "rigid", "ref_id": rid, "faces": face_tris(p, 0, "max")})
    assert c.status_code == 200, c.text
    pro.post(f"/api/projects/{pid}/loads", json={"name": "F", "load_type": "ref_force", "ref_id": rid,
                                                  "vector": [0, 0, -1000]})
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    tip = abs(j["results"]["reference_points"][rid][2])
    assert tip == pytest.approx(1000 * 1.0 / (3 * 210e9 * 0.1 ** 4 / 12), rel=0.05)


# --------------------------------------------------------------- failures
def test_missing_supports_gives_a_readable_error(pro):
    p = make_project(pro, et="C3D4")
    tip_pressure(pro, p, 1e5)
    j = solve(pro, p["id"])
    assert j["status"] == "failed"
    assert "support" in j["log"].lower() and "Traceback" not in j["log"]


def test_underconstrained_model_is_reported_not_crashed(pro):
    p = make_project(pro, et="C3D10")
    pro.post(f"/api/projects/{p['id']}/bcs", json={"name": "one node", "node_ids": [face_nodes(p, 0, "min")[0]], "dofs": [0, 1, 2]})
    tip_pressure(pro, p, 1e5)
    j = solve(pro, p["id"])
    assert j["status"] == "failed"
    assert "constrain" in j["log"].lower() or "singular" in j["log"].lower()


def test_free_plan_blocks_pro_analyses_and_allows_all_element_types(client):
    register(client)
    p = make_project(client, et="C3D20")  # element order is not gated
    assert p["element_type"] == "C3D20"
    for kind in ("nonlinear", "buckling", "dynamic", "thermal_transient", "thermomech"):
        r = client.put(f"/api/projects/{p['id']}/analysis-type", json={"analysis_type": kind, "n_modes": 4})
        assert r.status_code == 402, kind


# --------------------------------------------------------------- file I/O
def test_inp_export_and_reimport_round_trip(pro):
    p = make_project(pro, et="C3D10", name="Roundtrip")
    fix_face(pro, p)
    tip_pressure(pro, p, 1e5)
    r = pro.get(f"/api/projects/{p['id']}/export/inp")
    assert r.status_code == 200
    text = r.text
    assert "*ELEMENT, TYPE=C3D10" in text and "*BOUNDARY" in text and "*DLOAD" in text and "ccx" not in text.lower()
    q = pro.post("/api/projects", json={"name": "Copy"}).json()
    up = pro.post(f"/api/projects/{q['id']}/mesh/import", files={"file": ("model.inp", text.encode(), "text/plain")})
    assert up.status_code == 200, up.text
    got = up.json()
    assert got["element_type"] == "C3D10" and len(got["nodes"]) == len(p["nodes"]) and len(got["elements"]) == len(p["elements"])
    assert np.allclose(got["nodes"], p["nodes"], atol=1e-8)


def test_project_file_round_trip(pro):
    p = make_project(pro, et="C3D8", name="SaveMe")
    fix_face(pro, p)
    tip_pressure(pro, p, 1e5)
    pro.put(f"/api/projects/{p['id']}/material", json={"E": 70e9, "nu": 0.33, "density": 2700, "alpha": 23e-6})
    f = pro.get(f"/api/projects/{p['id']}/export/project")
    assert f.status_code == 200 and "attachment" in f.headers["content-disposition"]
    imp = pro.post("/api/projects/import", json=f.json())
    assert imp.status_code == 200, imp.text
    q = imp.json()
    assert q["name"] == "SaveMe" and q["element_type"] == "C3D8"
    assert q["material"]["E"] == 70e9 and len(q["boundary_conditions"]) == 1 and len(q["loads"]) == 1
    assert pro.post("/api/projects/import", json={"format": "nope"}).status_code == 422


def test_mesh_import_rejects_unsupported_files(pro):
    p = pro.post("/api/projects", json={"name": "x"}).json()
    r = pro.post(f"/api/projects/{p['id']}/mesh/import", files={"file": ("a.stl", b"solid", "text/plain")})
    assert r.status_code == 415
    r = pro.post(f"/api/projects/{p['id']}/mesh/import", files={"file": ("a.inp", b"*NODE\n1,0,0,0\n", "text/plain")})
    assert r.status_code == 400


def test_mesh_quality_append_part_and_new_shapes(pro):
    for shape, dims in [("tube", {"outer_radius": 0.5, "inner_radius": 0.3, "height": 1.0}),
                        ("plate_hole", {"length": 1.0, "width": 0.5, "thickness": 0.1, "hole_radius": 0.1}), ("lbracket", {"length": 1.0, "height": 1.0, "thickness": 0.25, "depth": 0.25}), ("cylinder", {"radius": 0.5, "height": 1})]:
        p = make_project(pro, shape=shape, dims=dims, et="C3D10" if shape != "lbracket" else "C3D8")
        q = pro.get(f"/api/projects/{p['id']}/mesh/quality").json()
        assert q["inverted_elements"] == 0 and q["total_volume"] > 0
    p = make_project(pro, et="C3D8")
    fix_face(pro, p)
    r = pro.post(f"/api/projects/{p['id']}/mesh/append", json={"shape": "box", "dims": {"length": 1, "width": 0.1, "height": 0.1},
                                                               "divisions": 2, "offset": [2.0, 0, 0]})
    assert r.status_code == 200
    q = r.json()
    assert len(q["nodes"]) > len(p["nodes"]) and len(q["boundary_conditions"]) == 1


def test_other_users_cannot_use_new_endpoints(client, make_client):
    register(client, "a@example.com")
    go_pro(client)
    p = make_project(client)
    other = make_client()
    register(other, "b@example.com")
    for method, path in [("get", f"/api/projects/{p['id']}/export/inp"),
                         ("get", f"/api/projects/{p['id']}/export/project"),
                         ("get", f"/api/projects/{p['id']}/mesh/quality")]:
        assert getattr(other, method)(path).status_code == 404


def test_capabilities_and_validation(pro):
    caps = pro.get("/api/capabilities").json()
    assert "nonlinear" in caps["analysis_types"] and {e["id"] for e in caps["element_types"]} == {"C3D4", "C3D10", "C3D8", "C3D20"}
    p = make_project(pro)
    assert pro.put(f"/api/projects/{p['id']}/settings", json={"bogus": 1}).status_code == 422
    assert pro.put(f"/api/projects/{p['id']}/material", json={"E": -1}).status_code == 422
    # curved shapes used to be tetrahedra-only; bricks are now available for every primitive
    r = pro.post(f"/api/projects/{p['id']}/mesh/primitive", json={"shape": "cylinder", "dims": {}, "element_type": "C3D8"})
    assert r.status_code == 200 and r.json()["element_type"] == "C3D8"
    r = pro.post(f"/api/projects/{p['id']}/mesh/primitive",
                 json={"shape": "tube", "dims": {"outer_radius": 0.2, "inner_radius": 0.3}, "element_type": "C3D20"})
    assert r.status_code == 400 and "inner radius" in r.json()["detail"]
    assert pro.post(f"/api/projects/{p['id']}/bcs", json={"name": "x", "node_ids": [10 ** 9], "dofs": [0]}).status_code == 422
