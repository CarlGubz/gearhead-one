import time


def test_health_and_meta(client):
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert "extrude" in meta["features"] and "pocket" in meta["cam_operations"] and "aluminum" in meta["materials"]


def test_sketch_part_workflow(client, project):
    sk = client.post(f"/api/projects/{project}/sketches", json={"name": "base", "entities": [{"type": "rect", "p1": [0, 0], "p2": [50, 30]}]}).json()
    assert sk["entities"][0]["id"] == "e1"
    assert client.get(f"/api/sketches/{sk['id']}/analysis").json()["area"] == 1500
    assert client.get(f"/api/sketches/{sk['id']}/export?format=dxf").status_code == 200
    assert client.get(f"/api/sketches/{sk['id']}/export?format=svg").text.startswith("<svg")
    part = client.post(f"/api/projects/{project}/parts", json={"name": "Block", "material": "aluminum", "parameters": {"h": 12}}).json()
    r = client.post(f"/api/parts/{part['id']}/features", json={"type": "extrude", "params": {"sketch_id": sk["id"], "distance": "=h"}})
    assert r.status_code == 201 and r.json()["statuses"][0]["ok"]
    fid = client.post(f"/api/parts/{part['id']}/features", json={"type": "fillet", "params": {"radius": 2, "edges": "|Z"}}).json()["features"][1]["id"]
    mesh = client.get(f"/api/parts/{part['id']}/mesh").json()
    assert mesh["properties"]["volume_mm3"] < 1500 * 12 and len(mesh["mesh"]["indices"]) > 0
    # suppress, move, delete
    assert client.patch(f"/api/parts/{part['id']}/features/{fid}", json={"suppressed": True}).json()["features"][1]["suppressed"]
    assert client.get(f"/api/parts/{part['id']}/mesh").json()["properties"]["volume_mm3"] == 1500 * 12
    for fmt in ("step", "stl"):
        assert client.get(f"/api/parts/{part['id']}/export?format={fmt}").status_code == 200
    assert client.get(f"/api/parts/{part['id']}/export?format=obj").status_code == 422


def test_sketch_solver_endpoint(client, project):
    sk = client.post(f"/api/projects/{project}/sketches", json={
        "entities": [{"type": "line", "p1": [0, 0], "p2": [9, 1]}],
        "constraints": [{"type": "horizontal", "a": "e1"}, {"type": "distance", "a": "e1", "value": 20}, {"type": "fixed", "a": "e1.p1", "value": [0, 0]}],
    }).json()
    r = client.post(f"/api/sketches/{sk['id']}/solve").json()
    assert r["status"] == "fully_constrained" and abs(r["sketch"]["entities"][0]["p2"][0] - 20) < 1e-6


def test_demo_project_end_to_end(client):
    proj = client.post("/api/projects/demo").json()
    detail = client.get(f"/api/projects/{proj['id']}").json()
    plate = next(p for p in detail["parts"] if p["name"] == "Mounting plate")
    mesh = client.get(f"/api/parts/{plate['id']}/mesh").json()
    assert all(s["ok"] for s in mesh["part"]["statuses"]), mesh["part"]["statuses"]
    # assembly: mates must seat both pins
    aid = detail["assemblies"][0]["id"]
    solved = client.post(f"/api/assemblies/{aid}/solve").json()
    assert set(solved["mate_status"].values()) == {"ok"}
    assert client.get(f"/api/assemblies/{aid}/interference").json()["count"] == 0
    bom = client.get(f"/api/assemblies/{aid}/bom").json()
    assert bom["total_parts"] == 3
    amesh = client.get(f"/api/assemblies/{aid}/mesh").json()
    assert len(amesh["components"]) == 3 and len(amesh["meshes"]) == 2
    assert client.get(f"/api/assemblies/{aid}/export?format=step").status_code == 200
    # CAM
    setups = client.get(f"/api/parts/{plate['id']}/cam").json()
    gen = client.post(f"/api/cam/{setups[0]['id']}/generate").json()
    assert not gen["errors"], gen["errors"]
    assert "M30" in gen["gcode"] and gen["summary"]["estimated_time_s"] > 0
    assert client.get(f"/api/cam/{setups[0]['id']}/gcode").status_code == 200
    auto = client.post(f"/api/cam/{setups[0]['id']}/auto-operations").json()
    assert {o["type"] for o in auto["operations"]} >= {"face", "pocket", "drill", "profile"}
    sim = client.post(f"/api/cam/{setups[0]['id']}/simulate").json()
    assert sim["gouge_volume_mm3"] < 5
    # simulation job (background task runs before TestClient returns)
    job = client.post(f"/api/parts/{plate['id']}/simulations", json={"kind": "static", "config": {
        "mesh_size": 5, "fixed": [{"face": "<Z"}], "pressures": [{"face": ">Z", "value": 0.5}]}}).json()
    for _ in range(60):
        j = client.get(f"/api/simulations/{job['id']}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert j["status"] == "done", j["error"]
    assert j["summary"]["max_von_mises_mpa"] > 0 and j["result"]["surface"]["indices"]


def test_validation_errors(client, project):
    assert client.get("/api/parts/99999").status_code == 404
    part = client.post(f"/api/projects/{project}/parts", json={"name": "p"}).json()
    assert client.post(f"/api/parts/{part['id']}/features", json={"type": "nope"}).status_code == 422
    assert client.get(f"/api/parts/{part['id']}/export?format=step").status_code == 422  # no geometry
    assert client.post(f"/api/projects/{project}/sketches", json={"plane": "AB"}).status_code == 422
