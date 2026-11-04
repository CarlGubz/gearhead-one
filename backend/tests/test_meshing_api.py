"""
Meshing through the HTTP API: bricks for every primitive, the Gmsh mesher (primitives, CAD files, .msh files), the mid-side
table the pick cursor uses, and that all of it survives undo, save/open and the solver.
"""
import json
import math
import os

import numpy as np
import pytest

from app.fea import meshgen
from tests.conftest import register
from tests.test_simulation_api import face_nodes, face_tris, go_pro, solve

E = 210e9
SHAPES = list(meshgen.SHAPES)
QUAD = ("C3D10", "C3D20")


@pytest.fixture
def pro(client):
    register(client)
    go_pro(client)
    return client


def new_project(c, name="m"):
    return c.post("/api/projects", json={"name": name}).json()["id"]


def primitive(c, pid, shape, et="C3D20", div=2, **extra):
    return c.post(f"/api/projects/{pid}/mesh/primitive",
                  json={"shape": shape, "dims": meshgen.DEFAULT_DIMS.get(shape, {}), "divisions": div, "element_type": et, **extra})


def volume(p):
    from app.fea.model import FEMesh
    return FEMesh(p["nodes"], p["elements"], p["element_type"]).volumes().sum()


# --------------------------------------------------------------------- capabilities
def test_capabilities_describe_shapes_and_gmsh(client):
    caps = client.get("/api/capabilities").json()
    assert {"cone", "torus"} <= set(caps["shapes"]) and set(caps["shape_defaults"]) == set(caps["shapes"])
    g = caps["gmsh"]
    assert g["available"] is True and g["version"]
    assert {a["id"] for a in g["options"]["algorithms_3d"]} == {1, 4, 7, 9, 10}
    assert "box" in g["sweepable_shapes"] and ".brep" in caps["cad_import"]["formats"]


# ------------------------------------------------------------ bricks for every shape
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("et", ["C3D8", "C3D20"])
def test_every_primitive_can_be_meshed_with_bricks(pro, shape, et):
    pid = new_project(pro)
    r = primitive(pro, pid, shape, et)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == et and len(p["elements"][0]) == (8 if et == "C3D8" else 20)
    assert len(p["surface_parts"]) == len(p["surface_faces"])
    q = pro.get(f"/api/projects/{pid}/mesh/quality").json()
    assert q["inverted_elements"] == 0 and q["total_volume"] > 0


@pytest.mark.parametrize("shape", SHAPES)
def test_quadratic_meshes_carry_a_midside_table_aligned_with_the_triangles(pro, shape):
    for et in ("C3D4", "C3D8"):
        p = primitive(pro, new_project(pro), shape, et).json()
        assert p["surface_mid"] == []
    for et in QUAD:
        p = primitive(pro, new_project(pro), shape, et).json()
        mid = np.array(p["surface_mid"])
        tris = np.array(p["surface_faces"])
        assert mid.shape == tris.shape
        assert mid.max() < len(p["nodes"])
        assert (mid >= 0).sum() > 0
        # every mid-side node is a real mid-side node: not one of the corner nodes of any element
        nc = 4 if et == "C3D10" else 8
        corners = set(np.array(p["elements"])[:, :nc].ravel().tolist())
        assert not (set(mid[mid >= 0].tolist()) & corners)


def test_midside_table_follows_edits_and_history(pro):
    pid = new_project(pro)
    primitive(pro, pid, "box", "C3D20")
    p = primitive(pro, pid, "cylinder", "C3D10").json()
    assert np.array(p["surface_mid"]).shape == np.array(p["surface_faces"]).shape
    undone = pro.post(f"/api/projects/{pid}/undo").json()
    assert undone["element_type"] == "C3D20"
    assert np.array(undone["surface_mid"]).shape == np.array(undone["surface_faces"]).shape
    redone = pro.post(f"/api/projects/{pid}/redo").json()
    assert redone["element_type"] == "C3D10" and redone["surface_mid"] == p["surface_mid"]
    # switching to a linear mesh drops the table
    lin = primitive(pro, pid, "box", "C3D8").json()
    assert lin["surface_mid"] == []


def test_adding_and_deleting_bodies_keeps_the_table_consistent(pro):
    pid = new_project(pro)
    primitive(pro, pid, "cylinder", "C3D20")
    r = pro.post(f"/api/projects/{pid}/mesh/append", json={
        "shape": "sphere", "dims": {"radius": 0.3}, "divisions": 2, "element_type": "C3D8", "offset": [2, 0, 0], "name": "Ball"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == "C3D20" and len(p["parts"]) == 2                       # the new body takes the model's element type
    assert np.array(p["surface_mid"]).shape == np.array(p["surface_faces"]).shape
    d = pro.delete(f"/api/projects/{pid}/parts/{p['parts'][1]['id']}")
    assert d.status_code == 200
    q = d.json()
    assert np.array(q["surface_mid"]).shape == np.array(q["surface_faces"]).shape


def test_a_project_saved_before_the_table_existed_gets_it_rebuilt(pro):
    from app.database import SessionLocal
    from app import models
    pid = new_project(pro)
    primitive(pro, pid, "box", "C3D20")
    with SessionLocal() as db:
        row = db.get(models.Project, pid)
        row.surface_mid = []
        db.commit()
    p = pro.get(f"/api/projects/{pid}").json()
    assert np.array(p["surface_mid"]).shape == np.array(p["surface_faces"]).shape and (np.array(p["surface_mid"]) >= 0).any()


def test_saved_project_files_round_trip_with_bricks(pro):
    pid = new_project(pro)
    primitive(pro, pid, "torus", "C3D20")
    data = pro.get(f"/api/projects/{pid}/export/project").json()
    back = pro.post("/api/projects/import", json=data)
    assert back.status_code == 200, back.text
    p = back.json()
    assert p["element_type"] == "C3D20" and len(p["surface_mid"]) == len(p["surface_faces"])


def test_brick_cylinder_solves_like_the_closed_form_bar(pro):
    """Uniform tension through a curved brick mesh: the end displacement is sigma L / E (the isoparametric patch test)."""
    for et in ("C3D8", "C3D20"):
        pid = new_project(pro)
        p = primitive(pro, pid, "cylinder", et, div=2).json()
        assert pro.put(f"/api/projects/{pid}/material", json={"name": "S", "E": E, "nu": 0.3}).status_code == 200
        pts = np.array(p["nodes"])
        zmin = pts[:, 2].min()
        bottom = face_nodes(p, 2, "min")
        assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "z", "node_ids": bottom, "dofs": [2]}).status_code == 200
        centre = min(bottom, key=lambda i: np.hypot(pts[i, 0], pts[i, 1]))
        assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "xy", "node_ids": [centre], "dofs": [0, 1]}).status_code == 200
        on_x = [i for i in bottom if abs(pts[i, 1]) < 1e-9 and pts[i, 0] > 0.1][0]
        assert pro.post(f"/api/projects/{pid}/bcs", json={"name": "y", "node_ids": [on_x], "dofs": [1]}).status_code == 200
        r = pro.post(f"/api/projects/{pid}/loads", json={"name": "P", "load_type": "pressure", "faces": face_tris(p, 2, "max"),
                                                          "magnitude": -1e6})
        assert r.status_code == 200, r.text
        j = solve(pro, pid)
        assert j["status"] == "completed", j["log"]
        top = face_nodes(p, 2, "max")
        uz = np.array(j["results"]["displacements"])[top, 2]
        assert uz.mean() == pytest.approx(1e6 * 1.0 / E, rel=1e-6), et


# ----------------------------------------------------------------- Gmsh primitives
def test_gmsh_primitive_through_the_api(pro):
    pid = new_project(pro)
    r = primitive(pro, pid, "cylinder", "C3D10", div=3, mesher="gmsh", gmsh={"family": "hex", "order": 2})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == "C3D20" and p["parts"][0]["name"] == "Cylinder"
    assert volume(p) == pytest.approx(math.pi * 0.25, rel=0.005)
    assert len(p["surface_mid"]) == len(p["surface_faces"])
    # without family / order the element_type field decides
    q = primitive(pro, pid, "box", "C3D8", div=2, mesher="gmsh").json()
    assert q["element_type"] == "C3D8"


def test_gmsh_options_reach_the_mesher(pro):
    pid = new_project(pro)
    plain = primitive(pro, pid, "box", "C3D4", div=2, mesher="gmsh").json()
    ball = {"shape": "ball", "center": [0.5, 0, 0], "radius": 0.08, "size_in": 0.02}
    fine = primitive(pro, pid, "box", "C3D4", div=2, mesher="gmsh", gmsh={"regions": [ball]}).json()
    assert len(fine["elements"]) > 3 * len(plain["elements"])
    swept = primitive(pro, pid, "tube", "C3D8", div=3, mesher="gmsh", gmsh={"hex_method": "sweep"})
    assert swept.status_code == 200
    assert primitive(pro, pid, "sphere", "C3D8", mesher="gmsh", gmsh={"hex_method": "sweep"}).status_code == 400


def test_gmsh_errors_are_reported_and_leave_the_project_untouched(pro):
    pid = new_project(pro)
    primitive(pro, pid, "box", "C3D8")
    before = pro.get(f"/api/projects/{pid}").json()
    for bad in ({"algorithm_3d": 99}, {"family": "wedge"}, {"order": 3}, {"regions": [{"shape": "sphere"}]}, {"nonsense": True}):
        r = primitive(pro, pid, "box", "C3D4", mesher="gmsh", gmsh=bad)
        assert r.status_code == 400, (bad, r.text)
        assert "Mesh generation failed" in r.json()["detail"]
    too_fine = primitive(pro, pid, "box", "C3D4", mesher="gmsh", gmsh={"size": 1e-4})
    assert too_fine.status_code == 400 and "too small" in too_fine.json()["detail"]
    assert primitive(pro, pid, "teapot", "C3D4", mesher="gmsh").status_code == 400
    assert primitive(pro, pid, "box", "C3D4", mesher="magic").status_code == 422
    assert pro.get(f"/api/projects/{pid}").json()["elements"] == before["elements"]


def test_adding_a_gmsh_body_takes_the_models_element_type(pro):
    pid = new_project(pro)
    primitive(pro, pid, "box", "C3D8", div=2)
    r = pro.post(f"/api/projects/{pid}/mesh/append", json={
        "shape": "cylinder", "dims": {"radius": 0.1, "height": 0.2}, "divisions": 2, "element_type": "C3D4",
        "offset": [1.0, 0, 0], "mesher": "gmsh", "gmsh": {"family": "tet", "order": 2}})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == "C3D8" and len(p["parts"]) == 2 and len(p["elements"][0]) == 8
    assert volume(p) == pytest.approx(1 * 0.2 * 0.2 + math.pi * 0.1 ** 2 * 0.2, rel=0.01)       # default box + the new cylinder


# ------------------------------------------------------------------ CAD and .msh
@pytest.fixture(scope="module")
def cad_files(tmp_path_factory):
    gmsh = pytest.importorskip("gmsh")
    tmp = tmp_path_factory.mktemp("cadfiles")
    out = {}
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("c")
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 60, 12)
        gmsh.model.occ.synchronize()
        for name in ("cyl.step", "cyl.brep"):
            gmsh.write(str(tmp / name))
            out[name] = (tmp / name).read_bytes()
        gmsh.model.mesh.generate(3)
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.write(str(tmp / "cyl4.msh"))
        out["cyl4.msh"] = (tmp / "cyl4.msh").read_bytes()
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.write(str(tmp / "cyl2.msh"))
        out["cyl2.msh"] = (tmp / "cyl2.msh").read_bytes()
        # a quadratic brick mesh in the 2.2 ASCII dialect the built-in reader understands
        gmsh.clear()
        gmsh.model.add("h")
        gmsh.model.occ.addBox(0, 0, 0, 0.1, 0.02, 0.02)
        gmsh.model.occ.synchronize()
        gmsh.model.mesh.setTransfiniteAutomatic()
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.setOrder(2)
        gmsh.write(str(tmp / "hex20.msh"))
        out["hex20.msh"] = (tmp / "hex20.msh").read_bytes()
    finally:
        gmsh.finalize()
    return out


def upload_cad(c, pid, raw, name, **form):
    data = {"size": "0.006", "order": "2", "unit": "mm", **{k: str(v) for k, v in form.items()}}
    return c.post(f"/api/projects/{pid}/mesh/cad", files={"file": (name, raw)}, data=data)


def test_cad_file_as_quadratic_bricks(pro, cad_files):
    pid = new_project(pro)
    r = upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", family="hex")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == "C3D20" and len(p["parts"]) == 1
    assert volume(p) == pytest.approx(math.pi * 0.012 ** 2 * 0.06, rel=0.02)
    assert len(p["surface_mid"]) == len(p["surface_faces"])
    lin = upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", family="hex", order=1).json()
    assert lin["element_type"] == "C3D8" and lin["surface_mid"] == []
    brep = upload_cad(pro, pid, cad_files["cyl.brep"], "cyl.brep", family="hex", order=1)
    assert brep.status_code == 200 and brep.json()["element_type"] == "C3D8"


def test_cad_import_takes_any_gmsh_option_as_json(pro, cad_files):
    pid = new_project(pro)
    plain = upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", order=1).json()
    opts = {"regions": [{"shape": "ball", "center": [0.0, 0.0, 0.03], "radius": 0.01, "size_in": 0.002}]}
    fine = upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", order=1, options=json.dumps(opts))
    assert fine.status_code == 200, fine.text
    assert len(fine.json()["elements"]) > 2 * len(plain["elements"])
    assert upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", options="{not json").status_code == 422
    assert upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", options='["x"]').status_code == 422
    bad = upload_cad(pro, pid, cad_files["cyl.step"], "cyl.step", options='{"algorithm_3d": 2}')
    assert bad.status_code == 400 and "algorithm_3d" in bad.json()["detail"]


@pytest.mark.parametrize("name,etype", [("cyl2.msh", "C3D4"), ("cyl4.msh", "C3D4")])
def test_msh_files_of_any_version_import(pro, cad_files, name, etype):
    pid = new_project(pro)
    r = pro.post(f"/api/projects/{pid}/mesh/import", files={"file": (name, cad_files[name])})
    assert r.status_code == 200, r.text
    assert r.json()["element_type"] == etype and volume(r.json()) == pytest.approx(math.pi * 12 ** 2 * 60, rel=0.05)


def test_hex20_msh_imports(pro, cad_files):
    pid = new_project(pro)
    r = pro.post(f"/api/projects/{pid}/mesh/import", files={"file": ("hex20.msh", cad_files["hex20.msh"])})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["element_type"] == "C3D20" and volume(p) == pytest.approx(0.1 * 0.02 * 0.02, rel=1e-6)


def test_garbage_msh_is_rejected(pro):
    pid = new_project(pro)
    r = pro.post(f"/api/projects/{pid}/mesh/import", files={"file": ("junk.msh", b"hello")})
    assert r.status_code == 400 and "Could not read mesh" in r.json()["detail"]


# -------------------------------------------------------------------------- agent
def test_agent_tool_offers_the_new_shapes_and_the_gmsh_mesher():
    from app import agent
    schema = next(t for t in agent.TOOLS if t["name"] == "create_solid_mesh")["input_schema"]["properties"]
    assert {"cone", "torus"} <= set(schema["shape"]["enum"])
    assert schema["mesher"]["enum"] == ["builtin", "gmsh"] and "gmsh" in schema
