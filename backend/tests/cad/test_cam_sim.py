import math

import pytest

from app.cad.services import cam, modeling3d as m, simulation as sim, sketch2d as s
from app.cad.services.geometry_mesh import TriMesh


def F(i, t, **p):
    return {"id": i, "type": t, "params": {**m.default_params(t), **p}}


@pytest.fixture(scope="module")
def plate():
    sk = {1: {"entities": s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [80, 50]}]), "plane": "XY", "offset": 0}}
    feats = [
        F("a", "extrude", sketch_id=1, distance=15),
        F("p", "box", length=30, width=20, height=6, position=[10, 10, 9], op="cut"),
        F("h", "hole", face=">Z", diameter=5, depth=0, points=[[70, 10, 15], [70, 40, 15]]),
    ]
    shape = m.rebuild(feats, m.BuildContext(sk, {})).shape
    md = m.tessellate(shape, 0.02)
    return shape, md, TriMesh(md["positions"], md["indices"]), sk


def O(i, t, tool, **p):
    return {"id": i, "type": t, "enabled": True, "params": {**{k["name"]: k["default"] for k in cam.OP_SCHEMAS[t]["params"]}, "tool": tool, **p}}


def test_hole_detection(plate):
    shape, *_ = plate
    holes = cam.detect_holes(shape)
    assert len(holes) == 2 and all(h["diameter"] == pytest.approx(5.0) for h in holes)


def test_cam_pipeline_and_simulation_has_no_gouge(plate):
    shape, md, mesh, sk = plate
    holes = cam.detect_holes(shape)
    ops = [O("op1", "face", 1, z_final=15.0), O("op2", "pocket", 1, allowance=0.2), O("op3", "drill", 3, peck=3.0), O("op4", "profile", 1, stepdown=5)]
    setup = {"tools": cam.DEFAULT_TOOLS, "operations": ops, "post": {"dialect": "fanuc", "line_numbers": True}}
    res = cam.generate(setup, mesh, md["faces"], holes, sk)
    assert not res["errors"], res["errors"]
    g = res["gcode"]
    assert g.startswith("%") and "M30" in g and "G83" in g and "T3 M6" in g
    assert res["summary"]["estimated_time_s"] > 0 and res["point_count"] > 100
    stock = cam.compute_stock({}, mesh)
    out = cam.simulate(res["events"], {t["id"]: t for t in cam.DEFAULT_TOOLS}, stock, mesh)
    assert out["gouge_volume_mm3"] < 1.0
    assert out["removed_volume_mm3"] > 10000


def test_grbl_has_no_canned_cycles(plate):
    shape, md, mesh, sk = plate
    setup = {"tools": cam.DEFAULT_TOOLS, "operations": [O("op1", "drill", 3, peck=2.0)], "post": {"dialect": "grbl"}}
    g = cam.generate(setup, mesh, md["faces"], cam.detect_holes(shape), sk)["gcode"]
    assert "G83" not in g and "G81" not in g and "M0" in g


def test_fea_cantilever_matches_beam_theory():
    sk = {1: {"entities": s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [100, 10]}]), "plane": "XY", "offset": 0}}
    shape = m.rebuild([F("a", "extrude", sketch_id=1, distance=10)], m.BuildContext(sk, {})).shape
    r = sim.run_simulation(shape, "steel", "static", {"mesh_size": 2.5, "fixed": [{"face": "<X"}], "forces": [{"face": ">X", "vector": [0, 0, -100]}]}, 120000)
    delta = 100 * 100**3 / (3 * 200000 * (10 * 10**3 / 12))  # Euler-Bernoulli tip deflection
    assert r["summary"]["max_displacement_mm"] == pytest.approx(delta, rel=0.04)
    assert r["summary"]["equilibrium_error_n"] < 1e-3
    modal = sim.run_simulation(shape, "steel", "modal", {"mesh_size": 2.5, "fixed": [{"face": "<X"}], "modes": 2}, 120000)
    f1 = (1.875**2 / (2 * math.pi)) * math.sqrt(200e9 * (0.01 * 0.01**3 / 12) / (7850 * 1e-4 * 0.1**4))
    assert modal["summary"]["frequencies_hz"][0] == pytest.approx(f1, rel=0.03)
    th = sim.run_simulation(shape, "steel", "thermal", {"mesh_size": 3, "fixed_temps": [{"face": "<X", "temperature": 100}], "convection": [{"face": ">X", "h": 25, "ambient": 20}]}, 120000)
    assert 90 < th["summary"]["min_temperature_c"] < 100


# ------------------------------------------------------------------ rest machining / collisions
SMALL = {"id": 5, "name": "2 mm flat end mill", "type": "flat", "diameter": 2.0, "flutes": 2, "rpm": 18000, "feed": 600, "plunge_feed": 200,
         "flute_length": 8.0, "stickout": 25.0, "shank_diameter": 3.0, "holder_diameter": 20.0}


def test_rest_machining_only_cuts_leftovers(plate):
    shape, md, mesh, sk = plate
    tools = cam.DEFAULT_TOOLS + [SMALL]
    by_id = {t["id"]: t for t in tools}
    stock = cam.compute_stock({}, mesh)
    big = [O("op1", "face", 1, z_final=15.0), O("op2", "pocket", 1)]
    full = cam.generate({"tools": tools, "operations": big + [O("op3", "pocket", 5)]}, mesh, md["faces"], [], sk)
    rest = cam.generate({"tools": tools, "operations": big + [O("op3", "pocket", 5, rest=True)]}, mesh, md["faces"], [], sk)
    assert not rest["errors"], rest["errors"]
    cut = lambda r: next(o["cut_mm"] for o in r["summary"]["operations"] if o["op"] == "op3")  # noqa: E731
    assert 0 < cut(rest) < 0.3 * cut(full)  # only the corners the 6 mm cutter could not reach
    only_big = cam.generate({"tools": tools, "operations": big}, mesh, md["faces"], [], sk)
    left_big = cam.simulate(only_big["events"], by_id, stock, mesh, 0.25)["leftover_volume_mm3"]
    sim_rest = cam.simulate(rest["events"], by_id, stock, mesh, 0.25)
    assert sim_rest["leftover_volume_mm3"] < 0.6 * left_big
    assert sim_rest["gouge_volume_mm3"] < 1.0


def test_holder_collision_detected(plate):
    shape, md, mesh, sk = plate
    stubby = {**cam.DEFAULT_TOOLS[0], "id": 9, "stickout": 4.0, "flute_length": 4.0, "holder_diameter": 25.0}
    ops = [O("op1", "face", 1, z_final=15.0), O("op2", "pocket", 9)]
    bad = cam.generate({"tools": cam.DEFAULT_TOOLS + [stubby], "operations": ops}, mesh, md["faces"], [], sk)
    col = bad["collisions"]
    assert col["count"] > 0 and col["samples"]["holder"] > 0 and col["by_op"].get("op2")
    assert all(c["op"] == "op2" for c in col["collisions"])
    assert any("COLLISION" in w for w in bad["warnings"])
    ok = cam.generate({"tools": cam.DEFAULT_TOOLS, "operations": [O("op1", "face", 1, z_final=15.0), O("op2", "pocket", 1)]}, mesh, md["faces"], [], sk)
    assert ok["collisions"]["count"] == 0  # no false positives from material removed earlier in the same op


def test_rapid_through_stock_detected(plate):
    shape, md, mesh, sk = plate
    stock = cam.compute_stock({}, mesh)
    t = cam.DEFAULT_TOOLS[0]
    ev = [{"t": "move", "k": "rapid", "x": stock.xmin - 10, "y": 25, "z": 5, "op": "x", "tool": 1},
          {"t": "move", "k": "rapid", "x": stock.xmax + 10, "y": 25, "z": 5, "op": "x", "tool": 1}]
    col = cam.check_collisions(ev, {1: t}, stock)
    assert col["samples"]["rapid"] > 0 and col["worst_mm"]["rapid"] == pytest.approx(stock.zmax - 5, abs=0.3)


def test_demo_program_has_no_false_collisions(client):
    """Drill retracts and tangent wall moves (bore milling) must not be reported as collisions."""
    pid = client.post("/api/projects/demo").json()["id"]
    plate = next(p for p in client.get(f"/api/projects/{pid}/parts").json() if "plate" in p["name"].lower())
    cam_id = client.get(f"/api/parts/{plate['id']}/cam").json()[0]["id"]
    g = client.post(f"/api/cam/{cam_id}/generate").json()
    assert g["collisions"]["count"] == 0, g["collisions"]["collisions"][:3]
    client.delete(f"/api/projects/{pid}")
