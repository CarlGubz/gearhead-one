"""
The integrated suite: one project holding FEA, CFD and CAD; ownership across the three APIs; the project file that
carries all of them; and the geometry / load exchange between the workspaces (bridge.py).
"""
import time

import numpy as np
import pytest

from tests.conftest import register
from tests.test_simulation_api import go_pro

BOX = {"length": 0.2, "width": 0.05, "height": 0.05}


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def project_with_box(c, name="Suite"):
    pid = c.post("/api/projects", json={"name": name}).json()["id"]
    r = c.post(f"/api/projects/{pid}/mesh/primitive", json={"shape": "box", "dims": BOX, "divisions": 4, "element_type": "C3D4"})
    assert r.status_code == 200
    return pid


def cad_id(c, pid):
    return c.get(f"/api/projects/{pid}/workspaces").json()["cad"]["cad_project_id"]


def wait_run(c, rid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/cfd/runs/{rid}").json()
        if r["status"] in ("completed", "failed", "cancelled"):
            return r
        time.sleep(0.3)
    raise AssertionError("CFD run timed out")


# ------------------------------------------------------------------------------------------- workspaces & ownership
def test_project_holds_three_workspaces(pro):
    pid = project_with_box(pro)
    ws = pro.get(f"/api/projects/{pid}/workspaces").json()
    assert ws["fea"]["models"] == 1 and ws["fea"]["n_elements"] > 0
    assert ws["cfd"]["cases"] == [] and ws["cad"]["cad_project_id"]
    case = pro.post("/api/cfd/projects", json={"name": "Cavity", "template": "lid_driven_cavity", "project_id": pid})
    assert case.status_code == 201 and case.json()["project_id"] == pid
    assert [c["name"] for c in pro.get(f"/api/cfd/projects?project_id={pid}").json()] == ["Cavity"]
    assert pro.get(f"/api/projects/{pid}/workspaces").json()["cfd"]["cases"][0]["name"] == "Cavity"


def test_cfd_and_cad_require_sign_in_and_hide_other_users_rows(make_client):
    ana, eve = make_client(), make_client()
    register(ana)
    register(eve, email="eve@example.com")
    assert make_client().get("/api/cfd/templates").status_code == 401
    assert make_client().get("/api/cad/meta").status_code == 401
    pid = ana.post("/api/projects", json={"name": "Mine"}).json()["id"]
    case = ana.post("/api/cfd/projects", json={"name": "c", "project_id": pid}).json()
    cad = cad_id(ana, pid)
    part = ana.post(f"/api/cad/projects/{cad}/parts", json={"name": "P"}).json()
    # eve sees none of it (404, not 403) and cannot attach her rows to ana's project
    assert eve.get(f"/api/cfd/projects/{case['id']}").status_code == 404
    assert eve.get(f"/api/cad/parts/{part['id']}").status_code == 404
    assert eve.get(f"/api/cad/projects/{cad}").status_code == 404
    assert eve.post("/api/cfd/projects", json={"name": "x", "project_id": pid}).status_code == 404
    assert eve.post(f"/api/projects/{pid}/bridge/to-cad", json={"source": "fea_model"}).status_code == 404
    assert eve.get("/api/cfd/projects").json() == [] and eve.get("/api/cad/projects").json() == []


def test_free_plan_limit_counts_projects_created_by_cfd_and_cad(client):
    register(client)
    for k in range(3):   # FREE_MAX_PROJECTS = 3: stand-alone CFD / CAD "create project" calls make Gearhead projects
        url, body = ("/api/cfd/projects", {"name": f"c{k}"}) if k % 2 else ("/api/cad/projects", {"name": f"d{k}"})
        assert client.post(url, json=body).status_code == 201
    assert client.post("/api/cfd/projects", json={"name": "one too many"}).status_code == 402


def test_deleting_a_project_removes_its_cfd_and_cad_data(pro):
    pid = project_with_box(pro)
    case = pro.post("/api/cfd/projects", json={"name": "c", "project_id": pid}).json()
    part = pro.post(f"/api/cad/projects/{cad_id(pro, pid)}/parts", json={"name": "P"}).json()
    assert pro.delete(f"/api/projects/{pid}").status_code == 200
    assert pro.get(f"/api/cfd/projects/{case['id']}").status_code == 404
    assert pro.get(f"/api/cad/parts/{part['id']}").status_code == 404


def test_cfd_timestamps_are_utc(pro):
    case = pro.post("/api/cfd/projects", json={"name": "t"}).json()
    assert case["created_at"].endswith("+00:00") or case["created_at"].endswith("Z")


# ------------------------------------------------------------------------------------------- project file v3
def test_project_file_carries_fea_cfd_and_cad(pro):
    pid = project_with_box(pro)
    pro.post("/api/cfd/projects", json={"name": "Channel", "template": "channel_flow", "project_id": pid})
    cad = cad_id(pro, pid)
    sk = pro.post(f"/api/cad/projects/{cad}/sketches", json={"name": "s", "entities": [{"type": "rect", "p1": [0, 0], "p2": [40, 20]}]}).json()
    part = pro.post(f"/api/cad/projects/{cad}/parts", json={"name": "Plate"}).json()
    pro.post(f"/api/cad/parts/{part['id']}/features", json={"type": "extrude", "params": {"sketch_id": sk["id"], "distance": 5}})
    data = pro.get(f"/api/projects/{pid}/export/project").json()
    assert data["format"] == "gearhead-project" and data["version"] == 3
    assert len(data["layers"]) == 1 and len(data["cfd"]["cases"]) == 1 and len(data["cad"]["parts"]) == 1

    new = pro.post("/api/projects/import", json=data).json()
    ws = pro.get(f"/api/projects/{new['id']}/workspaces").json()
    assert ws["fea"]["n_elements"] > 0 and [c["name"] for c in ws["cfd"]["cases"]] == ["Channel"]
    parts = pro.get(f"/api/cad/projects/{ws['cad']['cad_project_id']}/parts").json()
    # the extrude's sketch reference was remapped to the imported sketch: the part rebuilds to the same volume
    vol = pro.get(f"/api/cad/parts/{parts[0]['id']}/mesh").json()["properties"]["volume_mm3"]
    assert vol == pytest.approx(40 * 20 * 5)

    merged = pro.post(f"/api/projects/{pid}/merge-file", json=data).json()
    assert merged["added"]["fea_models"] == 1 and merged["added"]["cfd_cases"] == 1 and merged["added"]["cad_parts"] == 1
    assert merged["workspaces"]["fea"]["models"] == 2


def test_version_2_fea_files_still_open(pro):
    pid = project_with_box(pro)
    data = pro.get(f"/api/projects/{pid}/export/project").json()
    data.update(format="gearhead-fea-project", version=2)
    data.pop("cfd")
    data.pop("cad")
    assert pro.post("/api/projects/import", json=data).status_code == 200


# ------------------------------------------------------------------------------------------- the bridge
def test_fea_primitives_become_exact_cad_features(pro):
    pid = project_with_box(pro)
    pro.post(f"/api/projects/{pid}/mesh/append", json={"shape": "torus", "dims": {"major_radius": 0.05, "minor_radius": 0.015},
                                                     "divisions": 4, "element_type": "C3D4", "offset": [0.3, 0, 0], "name": "Ring"})
    r = pro.post(f"/api/projects/{pid}/bridge/to-cad", json={"source": "fea_model"}).json()
    assert [p["features"][0]["type"] for p in r["parts"]] == ["box", "import_step"]
    vols = [pro.get(f"/api/cad/parts/{p['id']}/mesh").json()["properties"]["volume_mm3"] for p in r["parts"]]
    assert vols[0] == pytest.approx(200 * 50 * 50)
    assert vols[1] == pytest.approx(2 * np.pi**2 * 50 * 15**2, rel=1e-6)


def test_cad_part_meshes_into_a_new_fea_model_with_its_material(pro):
    pid = project_with_box(pro)
    part = pro.post(f"/api/cad/projects/{cad_id(pro, pid)}/parts", json={"name": "Block", "material": "aluminum"}).json()
    pro.post(f"/api/cad/parts/{part['id']}/features", json={"type": "box", "params": {"length": 60, "width": 20, "height": 10}})
    p = pro.post(f"/api/projects/{pid}/bridge/to-fea", json={"source": "cad_part", "id": str(part["id"]), "size": 0.005}).json()
    assert len(p["layer_stack"]) == 2 and p["element_type"] == "C3D10" and p["parts"][0]["name"] == "Block"
    assert p["material"]["E"] == pytest.approx(69e9, rel=0.1)
    span = np.ptp(np.asarray(p["nodes"]), axis=0)
    assert span == pytest.approx([0.06, 0.02, 0.01], rel=1e-6)          # mm -> m


def test_fea_model_to_cfd_and_pressure_back_onto_fea(pro):
    pid = project_with_box(pro)
    r = pro.post(f"/api/projects/{pid}/bridge/to-cfd", json={"source": "fea_model", "dimension": "2d"}).json()
    case = pro.get(f"/api/cfd/projects/{r['case_id']}").json()
    ob = case["config"]["obstacles"][0]
    assert ob["shape"] == "mesh" and ob["source"]["kind"] == "fea_model"
    assert pro.post("/api/cfd/check", json=case["config"]).json()["ok"]
    cfg = case["config"]
    cfg["mesh"] = {"nx": 60, "ny": 30, "nz": 16}
    cfg["boundaries"]["left"]["velocity"] = cfg["initial"]["velocity_x"] = 0.02
    cfg["run"]["tolerance"] = 1e-4
    assert pro.put(f"/api/cfd/projects/{case['id']}", json={"config": cfg}).status_code == 200
    run = pro.post(f"/api/cfd/projects/{case['id']}/runs", json={"name": "r"}).json()
    assert wait_run(pro, run["id"])["status"] == "completed"
    out = pro.post(f"/api/projects/{pid}/bridge/cfd-to-fea", json={"run_id": run["id"], "obstacle_id": ob["id"], "bins": 4}).json()
    loads = [l for l in out["project"]["loads"] if l["load_type"] == "pressure"]
    assert out["summary"]["faces_mapped"] > 0 and 1 <= len(loads) <= 4
    lo, hi = out["summary"]["pressure_range_pa"]
    assert hi > 0 > lo        # stagnation pressure in front, suction behind / on the sides


def test_cfd_obstacles_back_to_cad_and_fea(pro):
    pid = pro.post("/api/projects", json={"name": "Obs"}).json()["id"]
    case = pro.post("/api/cfd/projects", json={"name": "Cyl", "template": "cylinder_in_channel", "project_id": pid}).json()
    r = pro.post(f"/api/projects/{pid}/bridge/to-cad", json={"source": "cfd_obstacles", "id": case["id"]}).json()
    assert r["parts"][0]["features"][0]["type"] == "cylinder"
    p = pro.post(f"/api/projects/{pid}/bridge/to-fea", json={"source": "cfd_obstacles", "id": case["id"], "size": 0.01, "order": 1}).json()
    assert p["element_type"] == "C3D4" and len(p["elements"]) > 0
