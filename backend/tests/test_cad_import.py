"""STEP / IGES / STL import through the API. Test files are generated with Gmsh itself, so the volumes are known exactly."""
import os

import numpy as np
import pytest

gmsh = pytest.importorskip("gmsh")

from tests.conftest import register
from tests.test_simulation_api import face_nodes, face_tris, go_pro, solve

E = 210e9


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def make_files(tmp_path):
    """Boxes, a cylinder and a two-solid assembly, written as STEP, IGES and STL (all dimensions in mm)."""
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    out = {}
    try:
        def build(name, fn, formats=("step", "iges", "stl")):
            gmsh.clear()
            gmsh.model.add(name)
            fn(gmsh.model.occ)
            gmsh.model.occ.synchronize()
            for ext in formats:
                path = str(tmp_path / f"{name}.{ext}")
                if ext == "stl":
                    gmsh.option.setNumber("Mesh.MeshSizeMax", 2.0)         # fine enough that facets follow the circle closely
                    gmsh.model.mesh.generate(2)
                gmsh.write(path)
                out[f"{name}.{ext}"] = path

        build("box", lambda o: o.addBox(0, 0, 0, 100, 20, 10))
        build("cyl", lambda o: o.addCylinder(0, 0, 0, 0, 0, 60, 12), formats=("step", "stl"))
        build("pair", lambda o: (o.addBox(0, 0, 0, 40, 20, 10), o.addBox(60, 0, 0, 40, 20, 10)), formats=("step",))
    finally:
        gmsh.finalize()
    return out


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return make_files(tmp_path_factory.mktemp("cad"))


def upload(c, pid, path, name=None, **form):
    data = {"size": "0.006", "order": "2", "unit": "mm", **{k: str(v) for k, v in form.items()}}
    with open(path, "rb") as f:
        return c.post(f"/api/projects/{pid}/mesh/cad", files={"file": (name or os.path.basename(path), f.read())}, data=data)


def new_project(c):
    return c.post("/api/projects", json={"name": "cad"}).json()["id"]


def volume(p):
    from app.fea.model import FEMesh
    return FEMesh(p["nodes"], p["elements"], p["element_type"]).volumes().sum()


def test_capabilities_report_cad_support(client):
    caps = client.get("/api/capabilities").json()["cad_import"]
    assert caps["available"] is True and ".step" in caps["formats"] and "mm" in caps["units"]


@pytest.mark.parametrize("name,rel", [("box.step", 1e-9), ("box.iges", 1e-9), ("box.stl", 1e-9)])
def test_box_volume_in_every_format(pro, files, name, rel):
    pid = new_project(pro)
    r = upload(pro, pid, files[name])
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == "C3D10" and len(p["parts"]) == 1 and p["parts"][0]["name"] == "box"
    assert volume(p) == pytest.approx(100e-3 * 20e-3 * 10e-3, rel=1e-6)          # mm -> m
    assert len(p["surface_faces"]) > 100 and len(p["surface_parts"]) == len(p["surface_faces"])
    lo = np.min(np.array(p["nodes"]), axis=0)
    assert np.allclose(np.ptp(np.array(p["nodes"]), axis=0), [0.1, 0.02, 0.01])
    q = pro.get(f"/api/projects/{pid}/mesh/quality").json()
    assert q["inverted_elements"] == 0


def test_linear_tetra_order_and_units(pro, files):
    pid = new_project(pro)
    p = upload(pro, pid, files["box.step"], order=1, unit="cm", size=0.02).json()
    assert p["element_type"] == "C3D4"
    assert volume(p) == pytest.approx(100e-2 * 20e-2 * 10e-2, rel=1e-6)           # the file is read as centimetres


def test_imported_cad_part_solves_like_the_closed_form_bar(pro, files):
    pid = new_project(pro)
    assert pro.put(f"/api/projects/{pid}/material", json={"name": "S", "E": E, "nu": 0.0}).status_code == 200
    p = upload(pro, pid, files["box.step"]).json()
    ids = face_nodes(p, 0, "min")
    assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "x", "node_ids": ids, "dofs": [0]}).status_code == 200
    one = [i for i in ids if abs(np.array(p["nodes"])[i][1] - np.array(p["nodes"])[:, 1].min()) < 1e-9
           and abs(np.array(p["nodes"])[i][2] - np.array(p["nodes"])[:, 2].min()) < 1e-9][0]
    assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "yz", "node_ids": [one], "dofs": [1, 2]}).status_code == 200
    far = [i for i in face_nodes(p, 0, "min") if abs(np.array(p["nodes"])[i][1] - np.array(p["nodes"])[:, 1].max()) < 1e-9
           and abs(np.array(p["nodes"])[i][2] - np.array(p["nodes"])[:, 2].min()) < 1e-9][0]
    assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "z", "node_ids": [far], "dofs": [2]}).status_code == 200
    r = pro.post(f"/api/projects/{pid}/loads", json={"name": "P", "load_type": "pressure", "faces": face_tris(p, 0, "max"), "magnitude": -1e6})
    assert r.status_code == 200, r.text
    j = solve(pro, pid)
    assert j["status"] == "completed", j["log"]
    tip = face_nodes(p, 0, "max")
    ux = np.mean(np.array(j["results"]["displacements"])[tip, 0])
    assert ux == pytest.approx(1e6 * 0.1 / E, rel=2e-3)                           # sigma L / E


def test_cylinder_volume_and_curved_faces(pro, files):
    pid = new_project(pro)
    p = upload(pro, pid, files["cyl.step"], size=0.005).json()
    assert volume(p) == pytest.approx(np.pi * 0.012 ** 2 * 0.06, rel=0.01)
    s = upload(pro, new_project(pro), files["cyl.stl"], size=0.003)
    assert s.status_code == 200, s.text
    # facets cut the circle a little (the STL itself holds 99.7 % of the volume, and the volume mesh converges to that as the size drops)
    assert volume(s.json()) == pytest.approx(np.pi * 0.012 ** 2 * 0.06, rel=0.02)


def test_assembly_gives_one_part_per_solid(pro, files):
    pid = new_project(pro)
    p = upload(pro, pid, files["pair.step"], size=0.008).json()
    assert [q["name"] for q in p["parts"]] == ["pair 1", "pair 2"]
    assert p["parts"][0]["elem_end"] == p["parts"][1]["elem_start"] and p["parts"][1]["elem_end"] == len(p["elements"])
    assert volume(p) == pytest.approx(2 * 40e-3 * 20e-3 * 10e-3, rel=1e-6)


def test_import_can_be_undone(pro, files):
    pid = new_project(pro)
    upload(pro, pid, files["box.step"])
    assert pro.post(f"/api/projects/{pid}/undo").status_code == 200
    assert pro.get(f"/api/projects/{pid}").json()["elements"] == []


def test_bad_files_and_parameters_are_rejected_with_readable_messages(pro, files, tmp_path):
    pid = new_project(pro)
    junk = tmp_path / "junk.step"
    junk.write_text("this is not a STEP file\n" * 20)
    r = upload(pro, pid, str(junk))
    assert r.status_code == 400 and "CAD" in r.json()["detail"]
    empty = tmp_path / "empty.stl"
    empty.write_bytes(b"")
    assert upload(pro, pid, str(empty)).status_code == 400
    # an STL with a hole (one triangle removed) is not watertight
    ascii_stl = tmp_path / "open.stl"
    ascii_stl.write_text("solid x\n" + "".join(
        f"facet normal 0 0 1\nouter loop\nvertex {a}\nvertex {b}\nvertex {c}\nendloop\nendfacet\n"
        for a, b, c in [("0 0 0", "1 0 0", "0 1 0"), ("1 0 0", "1 1 0", "0 1 0")]) + "endsolid x\n")
    bad = upload(pro, pid, str(ascii_stl))
    assert bad.status_code == 400
    assert upload(pro, pid, files["box.step"], name="model.obj").status_code == 400
    assert upload(pro, pid, files["box.step"], unit="furlong").status_code == 400
    assert upload(pro, pid, files["box.step"], order=3).status_code == 400
    tiny = upload(pro, pid, files["box.step"], size=0.00001)
    assert tiny.status_code == 400 and "too small" in tiny.json()["detail"]
    assert pro.get(f"/api/projects/{pid}").json()["elements"] == []              # nothing was installed by the failures


def test_other_users_cannot_import_into_a_project(client, make_client, files):
    register(client)
    pid = client.post("/api/projects", json={"name": "mine"}).json()["id"]
    other = make_client()
    register(other, email="bob@example.com")
    assert upload(other, pid, files["box.step"]).status_code == 404
