"""Undo / redo of project edits."""
import numpy as np
import pytest

from tests.conftest import register
from tests.test_parts_api import two_part_project
from tests.test_simulation_api import face_nodes, go_pro, make_project


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def hist(c, pid):
    return c.get(f"/api/projects/{pid}/history").json()


def test_undo_redo_walks_through_every_edit_kind(pro):
    p = pro.post("/api/projects", json={"name": "H"}).json()
    pid = p["id"]
    assert hist(pro, pid)["can_undo"] is False
    mesh = pro.post(f"/api/projects/{pid}/mesh/primitive", json={"shape": "box", "dims": {"length": 1, "width": 0.2, "height": 0.2},
                                                                 "divisions": 2, "element_type": "C3D8"}).json()
    ids = face_nodes(mesh, 0, "min")
    after_bc = pro.post(f"/api/projects/{pid}/bcs", json={"name": "fix", "node_ids": ids, "dofs": [0, 1, 2]}).json()
    after_mat = pro.put(f"/api/projects/{pid}/material", json={"name": "Alu", "E": 69e9, "nu": 0.33}).json()
    after_set = pro.put(f"/api/projects/{pid}/settings", json={"increments": 7}).json()
    h = hist(pro, pid)
    assert h["position"] == 4 and h["count"] == 5 and h["undo_label"] == "Change analysis settings" and not h["can_redo"]

    r = pro.post(f"/api/projects/{pid}/undo").json()
    assert r["settings"].get("increments") is None and r["material"]["name"] == "Alu"
    r = pro.post(f"/api/projects/{pid}/undo").json()
    assert r["material"]["name"] != "Alu" and len(r["boundary_conditions"]) == 1
    r = pro.post(f"/api/projects/{pid}/undo").json()
    assert r["boundary_conditions"] == [] and len(r["nodes"]) == len(mesh["nodes"])
    r = pro.post(f"/api/projects/{pid}/undo").json()          # back before the mesh existed
    assert r["nodes"] == [] and r["elements"] == [] and r["surface_faces"] == []
    assert pro.post(f"/api/projects/{pid}/undo").status_code == 409
    h = hist(pro, pid)
    assert h["position"] == 0 and h["can_redo"] and h["redo_label"] == "Generate mesh"

    # redo everything: identical to the states we saw going forward
    assert pro.post(f"/api/projects/{pid}/redo").json()["nodes"] == mesh["nodes"]
    assert pro.post(f"/api/projects/{pid}/redo").json()["boundary_conditions"] == after_bc["boundary_conditions"]
    assert pro.post(f"/api/projects/{pid}/redo").json()["material"] == after_mat["material"]
    last = pro.post(f"/api/projects/{pid}/redo").json()
    assert last["settings"] == after_set["settings"] and last["parts"] == after_set["parts"]
    assert pro.post(f"/api/projects/{pid}/redo").status_code == 409


def test_new_edit_after_undo_discards_the_redo_branch(pro):
    p = make_project(pro, et="C3D8")
    pid = p["id"]
    ids = face_nodes(p, 0, "min")
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "a", "node_ids": ids, "dofs": [0]})
    pro.post(f"/api/projects/{pid}/undo")
    assert hist(pro, pid)["can_redo"]
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "b", "node_ids": ids, "dofs": [1]})
    h = hist(pro, pid)
    assert not h["can_redo"] and h["undo_label"] == "Add support"
    assert pro.post(f"/api/projects/{pid}/redo").status_code == 409
    assert [b["name"] for b in pro.get(f"/api/projects/{pid}").json()["boundary_conditions"]] == ["b"]


def test_mesh_changing_edits_restore_mesh_parts_and_faces_exactly(pro):
    p, mid = two_part_project(pro)
    pid = p["id"]
    before = pro.get(f"/api/projects/{pid}").json()
    deleted = pro.delete(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}").json()
    assert len(deleted["parts"]) == 1
    restored = pro.post(f"/api/projects/{pid}/undo").json()
    for key in ("nodes", "elements", "parts", "surface_faces", "surface_parts", "materials", "material"):
        assert restored[key] == before[key], key
    moved = pro.post(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}/translate", json={"offset": [0, 0, 1.0]}).json()
    assert not np.allclose(moved["nodes"], before["nodes"])
    assert pro.post(f"/api/projects/{pid}/undo").json()["nodes"] == before["nodes"]


def test_history_is_capped_and_labels_stay_aligned(pro):
    p = make_project(pro, et="C3D8")
    pid = p["id"]
    ids = face_nodes(p, 0, "min")
    for i in range(25):
        assert pro.post(f"/api/projects/{pid}/bcs", json={"name": f"bc{i}", "node_ids": ids, "dofs": [0]}).status_code == 200
    h = hist(pro, pid)
    assert h["count"] == 20 and h["position"] == 19
    for k in range(19):
        assert pro.post(f"/api/projects/{pid}/undo").status_code == 200
    got = pro.get(f"/api/projects/{pid}").json()
    assert len(got["boundary_conditions"]) == 25 - 19          # oldest 6 edits were forgotten, state 0 = 6 BCs in
    assert pro.post(f"/api/projects/{pid}/undo").status_code == 409


def test_project_responses_carry_history_and_other_users_are_blocked(client, make_client):
    register(client, "a@example.com")
    go_pro(client)
    p = make_project(client, et="C3D8")
    got = client.get(f"/api/projects/{p['id']}").json()
    assert got["history_pos"] >= 0 and got["history_labels"][-1] == "Generate mesh"
    other = make_client()
    register(other, "b@example.com")
    for method, path in [("get", "history"), ("post", "undo"), ("post", "redo")]:
        assert getattr(other, method)(f"/api/projects/{p['id']}/{path}").status_code == 404


def test_project_without_history_columns_starts_cleanly(pro):
    from app import models as m
    from app.database import SessionLocal
    p = make_project(pro, et="C3D8")
    with SessionLocal() as db:
        row = db.get(m.Project, p["id"])
        row.history_pos, row.history_labels = None, None     # as if created before undo existed
        db.query(m.ProjectHistory).filter(m.ProjectHistory.project_id == p["id"]).delete()
        db.commit()
    pid = p["id"]
    assert hist(pro, pid)["can_undo"] is False
    ids = face_nodes(p, 0, "min")
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "x", "node_ids": ids, "dofs": [0]})
    assert hist(pro, pid)["can_undo"] is True
    assert pro.post(f"/api/projects/{pid}/undo").json()["boundary_conditions"] == []


def test_deleting_a_project_removes_its_history(pro):
    from app import models as m
    from app.database import SessionLocal
    p = make_project(pro, et="C3D8")
    assert pro.delete(f"/api/projects/{p['id']}").status_code == 200
    with SessionLocal() as db:
        assert db.query(m.ProjectHistory).filter(m.ProjectHistory.project_id == p["id"]).count() == 0
