import base64
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient





def small_cavity(client):
    p = client.post("/api/projects", json={"name": "Cavity", "template": "lid_driven_cavity"}).json()
    cfg = p["config"]
    cfg["mesh"] = {"nx": 16, "ny": 16}
    cfg["run"]["tolerance"] = 1e-4
    r = client.put(f"/api/projects/{p['id']}", json={"config": cfg})
    assert r.status_code == 200
    return p["id"]


def wait(client, rid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/runs/{rid}").json()
        if r["status"] in ("completed", "failed", "cancelled"):
            return r
        time.sleep(0.3)
    raise AssertionError("run timed out")


def test_health_and_templates(client):
    assert client.get("/api/health").json()["status"] == "ok"
    ids = {t["id"] for t in client.get("/api/templates").json()}
    assert {"lid_driven_cavity", "cylinder_in_channel", "natural_convection", "electronics_cooling"} <= ids


def test_every_template_passes_case_check(client):
    for t in client.get("/api/templates").json():
        p = client.post("/api/projects", json={"name": t["id"], "template": t["id"]}).json()
        chk = client.post("/api/check", json=p["config"]).json()
        if not chk["stats"]["backend_available"] and p["config"]["models"]["turbulence"] != "laminar":
            # RANS needs OpenFOAM itself; the native fallback must refuse it with a clear message
            assert any("need the OpenFOAM backend" in i["message"] for i in chk["items"] if i["level"] == "error")
            continue
        assert chk["ok"], (t["id"], chk["items"])


def test_validation_errors(client):
    p = client.post("/api/projects", json={"name": "bad", "template": "blank"}).json()
    cfg = p["config"]
    cfg["mesh"]["nx"] = 2
    assert client.put(f"/api/projects/{p['id']}", json={"config": cfg}).status_code == 422
    cfg["mesh"]["nx"] = 32
    cfg["boundaries"]["right"]["type"] = "wall"  # inlet without outlet
    r = client.post("/api/check", json=cfg).json()
    assert not r["ok"] and any("outlet" in i["message"] for i in r["items"])
    client.put(f"/api/projects/{p['id']}", json={"config": cfg})
    assert client.post(f"/api/projects/{p['id']}/runs", json={}).status_code == 422


def test_project_crud_and_duplicate(client):
    p = client.post("/api/projects", json={"name": "A"}).json()
    q = client.post(f"/api/projects/{p['id']}/duplicate").json()
    assert q["name"] == "A (copy)"
    names = [x["name"] for x in client.get("/api/projects").json()]
    assert "A" in names and "A (copy)" in names
    assert client.delete(f"/api/projects/{q['id']}").status_code == 204
    assert client.get(f"/api/projects/{q['id']}").status_code == 404


def test_full_run_lifecycle(client):
    pid = small_cavity(client)
    run = client.post(f"/api/projects/{pid}/runs", json={"name": "t1"}).json()
    assert run["status"] in ("queued", "running")
    done = wait(client, run["id"])
    assert done["status"] == "completed", done
    assert done["converged"] and done["progress"] == 1.0
    mon = client.get(f"/api/runs/{run['id']}/monitors").json()
    assert mon["samples"] and "u" in mon["samples"][0]["residuals"]
    assert any(k.endswith(".u") for k in mon["samples"][-1]["values"])
    logs = client.get(f"/api/runs/{run['id']}/logs").json()
    assert any("Mesh" in l["message"] for l in logs)

    f = client.get(f"/api/runs/{run['id']}/fields").json()
    arr = np.frombuffer(base64.b64decode(f["fields"]["speed"]), dtype="<f4").reshape(f["ny"], f["nx"])
    assert arr.shape == (16, 16) and arr.max() > 0.3
    assert set(f["ranges"]) >= {"speed", "u", "v", "p", "T", "vorticity"}

    line = client.get(f"/api/runs/{run['id']}/probe-line", params=dict(x0=0.5, y0=0, x1=0.5, y1=1, n=20)).json()
    assert len(line["values"]["u"]) == 20
    vtu = client.get(f"/api/runs/{run['id']}/export/vtu")
    assert vtu.status_code == 200 and b"VTKFile" in vtu.content
    assert client.get(f"/api/runs/{run['id']}/export/csv").text.startswith("x,y,solid")
    assert client.get(f"/api/runs/{run['id']}/export/monitors").text.startswith("iteration,time")


def test_cancel_running_run(client):
    pid = small_cavity(client)
    p = client.get(f"/api/projects/{pid}").json()["config"]
    p["mesh"] = {"nx": 64, "ny": 64}
    p["run"]["tolerance"] = 1e-12
    p["run"]["max_iterations"] = 5_000_000
    client.put(f"/api/projects/{pid}", json={"config": p})
    run = client.post(f"/api/projects/{pid}/runs", json={}).json()
    for _ in range(60):
        if client.get(f"/api/runs/{run['id']}").json()["iteration"] > 50:
            break
        time.sleep(0.3)
    client.post(f"/api/runs/{run['id']}/cancel")
    done = wait(client, run["id"])
    assert done["status"] == "cancelled"
    assert client.get(f"/api/runs/{run['id']}/fields").status_code == 200  # partial result kept
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204


def test_websocket_stream(client):
    pid = small_cavity(client)
    run = client.post(f"/api/projects/{pid}/runs", json={}).json()
    last = None
    with client.websocket_connect(f"/api/runs/{run['id']}/stream") as ws:
        while True:
            try:
                last = ws.receive_json()
            except Exception:
                break
            if last["status"] == "completed" and not last["samples"]:
                break
    assert last["status"] == "completed" and last["summary"]["iterations"] > 0


# --------------------------------------------------------------------------- 3-D and solver backends


def make_project(client, cfg_patch: dict, template="lid_driven_cavity"):
    p = client.post("/api/projects", json={"name": "t", "template": template}).json()
    cfg = p["config"]
    for k, v in cfg_patch.items():
        if isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    r = client.put(f"/api/projects/{p['id']}", json={"config": cfg})
    assert r.status_code == 200, r.text
    return p["id"]


def test_solvers_endpoint(client):
    data = {s["id"]: s for s in client.get("/api/solvers").json()}
    assert {"native", "openfoam", "fenicsx", "pyfr"} <= set(data)
    assert data["openfoam"]["capabilities"]["turbulence"] and data["openfoam"]["fallback"] == "native"
    assert data["pyfr"]["capabilities"]["energy"] is False and data["native"]["available"]["ok"]


def test_3d_run_slices_and_exports(client):
    pid = make_project(client, {"dimension": "3d", "mesh": {"nx": 10, "ny": 10, "nz": 6}, "run": {"tolerance": 1e-3}})
    assert client.get("/api/projects").json()[0]["dimension"] == "3d"
    run = client.post(f"/api/projects/{pid}/runs", json={}).json()
    done = wait(client, run["id"], timeout=180)
    assert done["status"] == "completed", done
    assert done["summary"]["dimension"] == 3 and done["summary"]["cells"] == 600
    mon = client.get(f"/api/runs/{run['id']}/monitors").json()
    assert "w" in mon["samples"][-1]["residuals"] and "center.w" in mon["samples"][-1]["values"]
    f = client.get(f"/api/runs/{run['id']}/fields").json()
    assert f["dim"] == 3 and f["plane"]["axis"] == 2 and f["plane"]["count"] == 6 and (f["nx"], f["ny"]) == (10, 10)
    xz = client.get(f"/api/runs/{run['id']}/fields", params={"axis": 1, "index": 9}).json()
    assert (xz["nx"], xz["ny"]) == (10, 6) and xz["axes"] == ["x", "z"] and xz["vectors"] == ["u", "w"]
    arr = np.frombuffer(base64.b64decode(xz["fields"]["speed"]), dtype="<f4")
    assert arr.size == 60 and arr.max() > 0.1  # the slice next to the lid moves
    line = client.get(f"/api/runs/{run['id']}/probe-line", params=dict(x0=0, y0=0.5, x1=1, y1=0.5, n=12, axis=1, index=9)).json()
    assert len(line["values"]["u"]) == 12 and line["axes"] == ["x", "z"]
    vtu = client.get(f"/api/runs/{run['id']}/export/vtu")
    assert vtu.status_code == 200 and b"VTKFile" in vtu.content
    assert client.get(f"/api/runs/{run['id']}/export/csv").text.startswith("x,y,z,solid")


@pytest.mark.parametrize("backend,expected", [("openfoam", "native"), ("fenicsx", "python-fem"), ("pyfr", "python-ac")])
def test_external_backend_falls_back_to_python(client, backend, expected):
    pid = make_project(client, {"mesh": {"nx": 12, "ny": 12}, "run": {"tolerance": 1e-3}, "solver": {"backend": backend}})
    chk = client.post("/api/check", json=client.get(f"/api/projects/{pid}").json()["config"]).json()
    assert chk["ok"] and chk["stats"]["backend"] == expected
    run = client.post(f"/api/projects/{pid}/runs", json={}).json()
    done = wait(client, run["id"], timeout=180)
    assert done["status"] == "completed", done
    assert done["summary"]["backend"] == expected and done["summary"]["requested_backend"] == backend
    assert any("not available" in n for n in done["summary"]["notes"])
    assert client.get(f"/api/runs/{run['id']}/fields").status_code == 200


def test_fallback_disabled_blocks_run(client):
    pid = make_project(client, {"solver": {"backend": "openfoam", "allow_fallback": False}})
    r = client.post(f"/api/projects/{pid}/runs", json={})
    assert r.status_code == 422 and any("not available" in e for e in r.json()["detail"]["errors"])


@pytest.mark.parametrize("backend,member", [("openfoam", "Allrun"), ("fenicsx", "run_fenicsx.py"), ("pyfr", "config.ini")])
def test_case_export_zip(client, backend, member):
    import io
    import zipfile

    p = client.post("/api/projects", json={"name": "Cyl export", "template": "cylinder_in_channel"}).json()
    r = client.get(f"/api/projects/{p['id']}/export/{backend}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("/" + member) for n in names), names
    assert client.get(f"/api/projects/{p['id']}/export/native").status_code == 404
