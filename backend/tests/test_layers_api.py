"""Model stack: several models in one project, each with its own setup, undo history and results."""
import pytest

from tests.conftest import register
from tests.test_simulation_api import face_nodes, go_pro, make_project, solve, tip_pressure

BOX = {"length": 1.0, "width": 0.1, "height": 0.1}


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def stack(p):
    return [(l["name"], l["active"]) for l in p["layer_stack"]]


def active_id(p):
    return next(l["id"] for l in p["layer_stack"] if l["active"])


def test_new_project_has_one_model_with_live_summary(pro):
    p = make_project(pro, et="C3D8")
    assert stack(p) == [("Model 1", True)]
    top = p["layer_stack"][0]
    assert p["active_layer_id"] == top["id"] and top["process"] == "cae"
    assert top["summary"]["n_elements"] == len(p["elements"]) and top["summary"]["element_type"] == "C3D8"


def test_switching_models_keeps_each_mesh_and_setup(pro):
    p = make_project(pro, et="C3D8")
    pid, first = p["id"], active_id(p)
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "fix", "node_ids": face_nodes(p, 0, "min"), "dofs": [0, 1, 2]})

    q = pro.post(f"/api/projects/{pid}/layers", json={"name": "Bracket", "process": "cad"}).json()
    assert stack(q) == [("Bracket", True), ("Model 1", False)]
    assert q["nodes"] == [] and q["boundary_conditions"] == [] and q["layer_stack"][0]["process"] == "cad"
    q = pro.post(f"/api/projects/{pid}/mesh/primitive", json={"shape": "cylinder", "dims": {"radius": 0.2, "height": 1},
                                                             "divisions": 2, "element_type": "C3D4"}).json()
    second = active_id(q)
    assert q["layer_stack"][1]["summary"]["n_elements"] == len(p["elements"])   # stashed numbers of the inactive model

    back = pro.post(f"/api/projects/{pid}/layers/{first}/activate").json()
    assert back["nodes"] == p["nodes"] and back["element_type"] == "C3D8" and len(back["boundary_conditions"]) == 1
    again = pro.post(f"/api/projects/{pid}/layers/{second}/activate").json()
    assert again["nodes"] == q["nodes"] and again["element_type"] == "C3D4" and again["boundary_conditions"] == []


def test_undo_history_is_per_model(pro):
    p = make_project(pro, et="C3D8")
    pid, first = p["id"], active_id(p)
    pro.put(f"/api/projects/{pid}/material", json={"name": "Alu", "E": 69e9, "nu": 0.33})
    q = pro.post(f"/api/projects/{pid}/layers", json={"name": "B"}).json()
    assert q["history_pos"] == -1 and q["history_labels"] == []
    assert pro.post(f"/api/projects/{pid}/undo").status_code == 409            # nothing to undo in the new model
    pro.put(f"/api/projects/{pid}/material", json={"name": "Copper", "E": 110e9, "nu": 0.34})

    r = pro.post(f"/api/projects/{pid}/layers/{first}/activate").json()
    assert r["history_labels"][-1] == "Edit default material" and r["material"]["name"] == "Alu"
    r = pro.post(f"/api/projects/{pid}/undo").json()
    assert r["material"]["name"] == "Steel" and len(r["nodes"]) == len(p["nodes"])   # undid model 1's edit only
    other = next(l["id"] for l in r["layer_stack"] if not l["active"])
    assert pro.post(f"/api/projects/{pid}/layers/{other}/activate").json()["material"]["name"] == "Copper"


def test_history_goto_jumps_several_steps(pro):
    p = make_project(pro, et="C3D8")
    pid = p["id"]
    for n in ("A", "B", "C"):
        pro.put(f"/api/projects/{pid}/material", json={"name": n, "E": 1e9, "nu": 0.3})
    r = pro.post(f"/api/projects/{pid}/history/1").json()
    assert r["history_pos"] == 1 and r["material"]["name"] == "Steel" and r["nodes"] == p["nodes"]
    assert pro.post(f"/api/projects/{pid}/history/3").json()["material"]["name"] == "B"
    assert pro.post(f"/api/projects/{pid}/history/99").status_code == 409


def test_duplicate_rename_move_hide_delete(pro):
    p = make_project(pro, et="C3D8")
    pid, first = p["id"], active_id(p)
    d = pro.post(f"/api/projects/{pid}/layers", json={"name": "Copy", "duplicate_of": first}).json()
    assert d["nodes"] == p["nodes"] and d["history_labels"] == [] and stack(d) == [("Copy", True), ("Model 1", False)]
    copy = active_id(d)

    r = pro.put(f"/api/projects/{pid}/layers/{first}", json={"name": "Original", "visible": False, "process": "cam"}).json()
    row = next(l for l in r["layer_stack"] if l["id"] == first)
    assert row["name"] == "Original" and row["visible"] is False and row["process"] == "cam"
    assert pro.put(f"/api/projects/{pid}/layers/{copy}", json={"visible": False}).json()["layer_stack"][0]["visible"] is True

    r = pro.post(f"/api/projects/{pid}/layers/{first}/move", json={"direction": "up"}).json()
    assert stack(r) == [("Original", False), ("Copy", True)]
    assert pro.post(f"/api/projects/{pid}/layers/{first}/move", json={"direction": "up"}).status_code == 422

    prev = pro.get(f"/api/projects/{pid}/layers/{first}/preview").json()
    assert prev["nodes"] == p["nodes"] and prev["surface_faces"] == p["surface_faces"] and prev["segments"] == []

    r = pro.delete(f"/api/projects/{pid}/layers/{copy}").json()       # deleting the active model activates its neighbour
    assert stack(r) == [("Original", True)] and r["nodes"] == p["nodes"]
    assert pro.delete(f"/api/projects/{pid}/layers/{first}").status_code == 422
    assert pro.post(f"/api/projects/{pid}/layers/nope/activate").status_code == 404


def test_results_belong_to_their_model(pro):
    p = make_project(pro, et="C3D8")
    pid, first = p["id"], active_id(p)
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "fix", "node_ids": face_nodes(p, 0, "min"), "dofs": [0, 1, 2]})
    tip_pressure(pro, p, 1e6)
    job = solve(pro, pid)
    assert job["status"] == "completed" and job["layer_id"] == first
    runs = pro.get(f"/api/projects/{pid}/jobs/summary").json()
    assert [r["id"] for r in runs] == [job["id"]] and runs[0]["analysis_type"] == "static" and "results" not in runs[0]

    pro.post(f"/api/projects/{pid}/layers", json={"name": "Empty"})
    assert pro.get(f"/api/projects/{pid}/jobs/latest").status_code == 404
    assert pro.get(f"/api/projects/{pid}/jobs").json() == [] and pro.get(f"/api/projects/{pid}/jobs/summary").json() == []
    pro.post(f"/api/projects/{pid}/layers/{first}/activate")
    assert pro.get(f"/api/projects/{pid}/jobs/latest").json()["id"] == job["id"]


def test_project_file_round_trips_the_whole_stack(pro):
    p = make_project(pro, et="C3D8")
    pid = p["id"]
    pro.post(f"/api/projects/{pid}/layers", json={"name": "Tube", "process": "cad"})
    t = pro.post(f"/api/projects/{pid}/mesh/primitive", json={"shape": "tube", "dims": {"outer_radius": 0.3, "inner_radius": 0.2,
                                                             "height": 0.5}, "divisions": 2, "element_type": "C3D4"}).json()
    pro.put(f"/api/projects/{pid}/layers/{active_id(p)}", json={"visible": False})
    data = pro.get(f"/api/projects/{pid}/export/project").json()
    assert data["format"] == "gearhead-project" and data["version"] == 3
    assert [l["name"] for l in data["layers"]] == ["Model 1", "Tube"]
    assert data["nodes"] == t["nodes"]                                  # top level = active model (version-1 readers)

    r = pro.post("/api/projects/import", json=data).json()
    assert stack(r) == [("Tube", True), ("Model 1", False)] and r["nodes"] == t["nodes"]
    assert r["layer_stack"][0]["process"] == "cad" and r["layer_stack"][1]["visible"] is False
    other = r["layer_stack"][1]["id"]
    assert pro.post(f"/api/projects/{r['id']}/layers/{other}/activate").json()["nodes"] == p["nodes"]

    v1 = {k: v for k, v in data.items() if k != "layers"}             # old single-model files still open
    assert stack(pro.post("/api/projects/import", json=v1).json()) == [("Model 1", True)]


def test_parts_remember_their_input_geometry(pro):
    p = make_project(pro, et="C3D8")
    src = p["parts"][0]["source"]
    assert src["kind"] == "primitive" and src["shape"] == "box" and src["dims"] == BOX
    q = pro.post(f"/api/projects/{p['id']}/mesh/append", json={"shape": "sphere", "dims": {"radius": 0.1}, "divisions": 2,
                                                               "offset": [2, 0, 0]}).json()
    assert q["parts"][0]["source"]["shape"] == "box"
    assert q["parts"][1]["source"]["shape"] == "sphere" and q["parts"][1]["source"]["offset"] == [2, 0, 0]


def test_legacy_project_is_adopted_into_a_stack(pro):
    from app import models as m
    from app.database import SessionLocal
    p = make_project(pro, et="C3D8")
    pro.put(f"/api/projects/{p['id']}/material", json={"name": "Alu", "E": 69e9, "nu": 0.33})
    with SessionLocal() as db:                                      # as if created before the model stack existed
        db.query(m.ModelLayer).filter(m.ModelLayer.project_id == p["id"]).delete()
        db.query(m.ProjectHistory).filter(m.ProjectHistory.project_id == p["id"]).update({m.ProjectHistory.layer_id: ""})
        db.get(m.Project, p["id"]).active_layer_id = None
        db.commit()
    r = pro.get(f"/api/projects/{p['id']}").json()
    assert stack(r) == [("Model 1", True)] and r["active_layer_id"]
    assert pro.post(f"/api/projects/{p['id']}/undo").json()["material"]["name"] == "Steel"   # old history kept
