"""Parts and material library through the HTTP API."""
import numpy as np
import pytest

from tests.conftest import register
from tests.test_simulation_api import go_pro, make_project, solve, tip_pressure


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def two_part_project(c, et="C3D8", E2=40e9):
    """Bar of 1 m (E = 100 GPa, nu 0) + a 0.5 m bar added as a separate body with its own material."""
    p = make_project(c, dims={"length": 1.0, "width": 0.2, "height": 0.2}, div=2, et=et)
    base = {"E": 100e9, "nu": 0.0, "density": 7800, "conductivity": 50, "specific_heat": 490}
    assert c.put(f"/api/projects/{p['id']}/material", json={"name": "Base", **base}).status_code == 200
    r = c.post(f"/api/projects/{p['id']}/materials", json={"name": "Soft", **{**base, "E": E2}})
    assert r.status_code == 200, r.text
    mid = r.json()["materials"][0]["id"]
    r = c.post(f"/api/projects/{p['id']}/mesh/append", json={
        "shape": "box", "dims": {"length": 0.5, "width": 0.2, "height": 0.2}, "divisions": 2, "element_type": et,
        "offset": [0.75, 0, 0], "name": "Cap", "material_id": mid})
    assert r.status_code == 200, r.text
    return r.json(), mid


def n_first_part_nodes(p):
    return len(np.unique(np.array(p["elements"])[: p["parts"][1]["elem_start"]]))


def test_parts_materials_and_tie_end_to_end(pro):
    p, mid = two_part_project(pro)
    assert [q["name"] for q in p["parts"]] == ["Box", "Cap"]
    assert p["parts"][1]["material_id"] == mid and len(p["surface_parts"]) == len(p["surface_faces"])
    assert set(p["surface_parts"]) == {0, 1}
    pid = p["id"]
    pts = np.array(p["nodes"])
    for ax in range(3):
        ids = [i for i in range(len(pts)) if abs(pts[i, ax] - pts[:, ax].min()) < 1e-9]
        pro.post(f"/api/projects/{pid}/bcs", json={"name": f"s{ax}", "node_ids": ids, "dofs": [ax]})
    ptag = [q["id"] for q in p["parts"]]
    tie = {"name": "Tie", "type": "tie", "master_part": ptag[0], "slave_part": ptag[1]}
    assert pro.post(f"/api/projects/{pid}/constraints", json=tie).status_code == 200
    same = {"name": "x", "type": "tie", "master_part": ptag[0], "slave_part": ptag[0]}
    assert pro.post(f"/api/projects/{pid}/constraints", json=same).status_code == 422
    ghost = {"name": "x", "type": "tie", "master_part": ptag[0], "slave_part": "ghost"}
    assert pro.post(f"/api/projects/{pid}/constraints", json=ghost).status_code == 422
    tip_pressure(pro, pro.get(f"/api/projects/{pid}").json(), -1e6)
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    assert "2 part(s), 2 material(s)" in j["log"]
    assert max(j["results"]["fields"]["ux"]) == pytest.approx(1e6 * (1.0 / 100e9 + 0.5 / 40e9), rel=1e-6)
    # re-assigning the cap to the base material changes the answer
    assert pro.put(f"/api/projects/{pid}/parts/{ptag[1]}", json={"material_id": "default"}).status_code == 200
    j2 = solve(pro, pid)
    assert max(j2["results"]["fields"]["ux"]) == pytest.approx(1e6 * 1.5 / 100e9, rel=1e-6)


def test_material_library_crud_and_part_rename(pro):
    p, mid = two_part_project(pro)
    pid = p["id"]
    r = pro.put(f"/api/projects/{pid}/materials/{mid}", json={"name": "Softer", "E": 5e9, "nu": 0.2})
    assert r.status_code == 200 and r.json()["materials"][0]["E"] == 5e9
    assert pro.put(f"/api/projects/{pid}/materials/nope", json={"name": "x"}).status_code == 404
    assert pro.delete(f"/api/projects/{pid}/materials/default").status_code == 400
    assert pro.put(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}", json={"material_id": "ghost"}).status_code == 422
    r = pro.put(f"/api/projects/{pid}/parts/{p['parts'][0]['id']}", json={"name": "Beam"})
    assert r.json()["parts"][0]["name"] == "Beam"
    r = pro.delete(f"/api/projects/{pid}/materials/{mid}")
    assert r.status_code == 200 and r.json()["materials"] == []
    assert all(q["material_id"] == "default" for q in r.json()["parts"])


def test_translate_part_moves_only_that_body(pro):
    p, _ = two_part_project(pro)
    pid = p["id"]
    cap = p["parts"][1]
    before = np.array(p["nodes"])
    r = pro.post(f"/api/projects/{pid}/parts/{cap['id']}/translate", json={"offset": [0.0, 0.0, 1.0]})
    assert r.status_code == 200, r.text
    after = np.array(r.json()["nodes"])
    n_base = n_first_part_nodes(p)
    assert np.allclose(after[:n_base], before[:n_base])
    assert np.allclose(after[n_base:] - before[n_base:], [0, 0, 1.0])
    assert pro.post(f"/api/projects/{pid}/parts/ghost/translate", json={"offset": [0, 0, 1]}).status_code == 404


def test_delete_part_compresses_mesh_and_remaps_setup(pro):
    p, _ = two_part_project(pro)
    pid = p["id"]
    n_base = n_first_part_nodes(p)
    pts = np.array(p["nodes"])
    on_base = [i for i in range(n_base) if abs(pts[i, 0] - pts[:n_base, 0].min()) < 1e-9]
    on_cap = [i for i in range(n_base, len(pts)) if abs(pts[i, 0] - pts[n_base:, 0].max()) < 1e-9]
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "base", "node_ids": on_base, "dofs": [0, 1, 2]})
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "cap", "node_ids": on_cap, "dofs": [0]})
    pro.post(f"/api/projects/{pid}/node-sets", json={"name": "capnodes", "kind": "nodes", "ids": on_cap})
    q = pro.get(f"/api/projects/{pid}").json()
    tris = [t for t in q["surface_faces"] if all(n >= n_base for n in t)]
    pro.post(f"/api/projects/{pid}/loads", json={"name": "cap load", "load_type": "pressure", "faces": tris[:2], "magnitude": 1e3})
    r = pro.delete(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["nodes"]) == n_base and len(d["parts"]) == 1 and d["parts"][0]["elem_end"] == len(d["elements"])
    assert [b["name"] for b in d["boundary_conditions"]] == ["base"]   # cap BC dropped, base BC kept
    assert d["boundary_conditions"][0]["node_ids"] == on_base
    assert d["loads"] == [] and d["node_sets"] == []                    # selections that became empty are removed
    assert len(d["surface_parts"]) == len(d["surface_faces"]) and set(d["surface_parts"]) == {0}
    assert pro.delete(f"/api/projects/{pid}/parts/{d['parts'][0]['id']}").status_code == 400  # last part stays


def test_delete_first_part_shifts_remaining_ids(pro):
    p, _ = two_part_project(pro)
    pid = p["id"]
    n_base = n_first_part_nodes(p)
    pts = np.array(p["nodes"])
    cap_ids = [i for i in range(n_base, len(pts)) if abs(pts[i, 0] - pts[n_base:, 0].max()) < 1e-9]
    pro.post(f"/api/projects/{pid}/bcs", json={"name": "cap", "node_ids": cap_ids, "dofs": [0, 1, 2]})
    d = pro.delete(f"/api/projects/{pid}/parts/{p['parts'][0]['id']}").json()
    assert len(d["nodes"]) == len(pts) - n_base
    got = np.array(d["nodes"])[d["boundary_conditions"][0]["node_ids"]]
    assert np.allclose(got[:, 0], pts[cap_ids][:, 0])        # ids were remapped to the same physical nodes
    assert d["parts"][0]["elem_start"] == 0


def test_parts_survive_project_file_and_inp_round_trips(pro):
    p, mid = two_part_project(pro, et="C3D10")
    pid = p["id"]
    f = pro.get(f"/api/projects/{pid}/export/project").json()
    q = pro.post("/api/projects/import", json=f).json()
    assert [x["name"] for x in q["parts"]] == ["Box", "Cap"] and q["materials"][0]["name"] == "Soft"
    assert q["parts"][1]["material_id"] == q["materials"][0]["id"] and len(q["surface_parts"]) == len(q["surface_faces"])

    inp_text = pro.get(f"/api/projects/{pid}/export/inp").text
    assert inp_text.count("*MATERIAL") == 2 and inp_text.count("*SOLID SECTION") == 2 and "PART2_Cap" in inp_text
    n = pro.post("/api/projects", json={"name": "from inp"}).json()
    got = pro.post(f"/api/projects/{n['id']}/mesh/import", files={"file": ("m.inp", inp_text.encode(), "text/plain")})
    assert got.status_code == 200, got.text
    g = got.json()
    assert len(g["parts"]) == 2 and g["parts"][0]["elem_end"] == g["parts"][1]["elem_start"]
    assert g["material"]["E"] == 100e9 and g["materials"][0]["E"] == 40e9
    assert g["parts"][1]["material_id"] == g["materials"][0]["id"] and g["parts"][0]["material_id"] == "default"


def test_project_without_parts_is_served_as_one_part(pro):
    from app import models as m
    from app.database import SessionLocal
    p = make_project(pro, et="C3D4")
    with SessionLocal() as db:
        row = db.get(m.Project, p["id"])
        row.parts, row.materials, row.surface_parts = None, None, None   # as if created before parts existed
        db.commit()
    g = pro.get(f"/api/projects/{p['id']}").json()
    assert len(g["parts"]) == 1 and g["parts"][0]["material_id"] == "default" and g["materials"] == []
    assert len(g["surface_parts"]) == len(g["surface_faces"])
    r = pro.put(f"/api/projects/{p['id']}/parts/{g['parts'][0]['id']}", json={"name": "Renamed"})
    assert r.status_code == 200 and r.json()["parts"][0]["name"] == "Renamed"


def test_other_users_cannot_touch_parts_or_materials(client, make_client):
    register(client, "a@example.com")
    go_pro(client)
    p, _ = two_part_project(client)
    other = make_client()
    register(other, "b@example.com")
    pid = p["id"]
    assert other.put(f"/api/projects/{pid}/parts/{p['parts'][0]['id']}", json={"name": "hacked"}).status_code == 404
    assert other.delete(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}").status_code == 404
    assert other.post(f"/api/projects/{pid}/materials", json={"name": "x"}).status_code == 404
