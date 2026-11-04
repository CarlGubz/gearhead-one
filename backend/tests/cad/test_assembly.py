import math

import numpy as np
import pytest

from app.cad.services import assembly as a
from app.cad.services import modeling3d as m
from app.cad.services import sketch2d as s


def F(i, t, **p):
    return {"id": i, "type": t, "params": {**m.default_params(t), **p}}


def test_concentric_and_flush_mates():
    sk = {1: {"entities": s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [60, 40]}]), "plane": "XY", "offset": 0}}
    plate = m.rebuild([F("a", "extrude", sketch_id=1, distance=10), F("h", "hole", face=">Z", diameter=8, depth=0, points=[[30, 20, 10]])], m.BuildContext(sk, {})).shape
    pin = m.rebuild([F("c", "cylinder", radius=4, height=30)], m.BuildContext({}, {})).shape
    shapes = {1: plate, 2: pin}
    comps = [
        {"id": 1, "part_id": 1, "name": "plate", "position": [0, 0, 0], "rotation": [0, 0, 0], "fixed": True},
        {"id": 2, "part_id": 2, "name": "pin", "position": [100, 50, 70], "rotation": [30, 60, 10], "fixed": False},
    ]
    mates = [
        {"id": 1, "type": "concentric", "comp_a": 1, "comp_b": 2, "params": {"a_face": "%CYLINDER", "b_face": "%CYLINDER"}},
        {"id": 2, "type": "flush", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">Z", "b_face": "<Z"}},
    ]
    res = a.solve(comps, mates, shapes)
    assert set(res["mate_status"].values()) == {"ok"}
    for c in comps:
        c.update({k: res["components"][c["id"]][k] for k in ("position", "rotation")})
    bb = a.world_shapes(comps, shapes)[2].BoundingBox()
    assert (bb.xmin + bb.xmax) / 2 == pytest.approx(30, abs=1e-6)
    assert (bb.ymin + bb.ymax) / 2 == pytest.approx(20, abs=1e-6)
    assert bb.zmin == pytest.approx(10, abs=1e-6)
    # push the pin into the plate -> interference is reported
    comps[1]["position"], comps[1]["rotation"] = [10, 10, 2], [0, 0, 0]
    assert a.interference(comps, shapes)
    assert a.bill_of_materials(comps, {1: {"name": "plate"}, 2: {"name": "pin"}}, shapes)[0]["quantity"] == 1


# ------------------------------------------------------------ simultaneous solver
def _box(l, w, h, **kw):
    return m.rebuild([F("a", "box", length=l, width=w, height=h, **kw)], m.BuildContext({}, {})).shape


def _place(comps, res):
    for c in comps:
        c.update({k: res["components"][c["id"]][k] for k in ("position", "rotation")})


def test_simultaneous_mates_conflicting_for_sequential():
    """A block sits in an L: two flush mates on different walls + floor. Sequential solvers fight; this must not."""
    base = m.rebuild([F("a", "box", length=40, width=40, height=5), F("b", "box", length=5, width=40, height=20, position=[0, 0, 5]),
                      F("c", "box", length=40, width=5, height=20, position=[0, 0, 5])], m.BuildContext({}, {})).shape
    blk = _box(10, 10, 10)
    shapes = {1: base, 2: blk}
    comps = [{"id": 1, "part_id": 1, "name": "L", "position": [0, 0, 0], "rotation": [0, 0, 0], "fixed": True},
             {"id": 2, "part_id": 2, "name": "blk", "position": [20, 22, 30], "rotation": [10, -15, 25], "fixed": False}]
    mates = [
        {"id": 1, "type": "flush", "comp_a": 1, "comp_b": 2, "params": {"a_face": "near:20,20,5", "b_face": "<Z"}},
        {"id": 2, "type": "flush", "comp_a": 1, "comp_b": 2, "params": {"a_face": "near:5,20,15", "b_face": "<X"}},
        {"id": 3, "type": "flush", "comp_a": 1, "comp_b": 2, "params": {"a_face": "near:20,5,15", "b_face": "<Y"}},
    ]
    res = a.solve(comps, mates, shapes)
    assert set(res["mate_status"].values()) == {"ok"}, res["mate_status"]
    assert res["status"] == "fully_constrained" and res["dof"] == 0
    _place(comps, res)
    bb = a.world_shapes(comps, shapes)[2].BoundingBox()
    assert (bb.xmin, bb.ymin, bb.zmin) == pytest.approx((5, 5, 5), abs=1e-6)


def test_distance_angle_parallel_and_dof():
    shapes = {1: _box(50, 50, 5), 2: _box(20, 10, 4)}
    comps = [{"id": 1, "part_id": 1, "name": "base", "position": [0, 0, 0], "rotation": [0, 0, 0], "fixed": True},
             {"id": 2, "part_id": 2, "name": "plate", "position": [5, 5, 20], "rotation": [5, 0, 30], "fixed": False}]
    mates = [{"id": 1, "type": "distance", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">Z", "b_face": "<Z", "value": 7}},
             {"id": 2, "type": "parallel", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">Z", "b_face": "<Z"}}]
    res = a.solve(comps, mates, shapes)
    assert res["status"] == "under_constrained" and res["dof"] == 3  # slide in X, Y and spin about Z remain
    _place(comps, res)
    assert a.world_shapes(comps, shapes)[2].BoundingBox().zmin == pytest.approx(12, abs=1e-6)
    mates.append({"id": 3, "type": "angle", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">X", "b_face": ">X", "value": 45}})
    res = a.solve(comps, mates, shapes)
    assert res["mate_status"][3] == "ok" and res["dof"] == 2
    _place(comps, res)
    R = a.euler_matrix(comps[1]["position"], comps[1]["rotation"])[:3, :3]
    assert abs(R[:, 0] @ [1, 0, 0]) == pytest.approx(math.cos(math.radians(45)), abs=1e-6)


def test_conflict_reported():
    shapes = {1: _box(50, 50, 5), 2: _box(20, 10, 4)}
    comps = [{"id": 1, "part_id": 1, "name": "base", "position": [0, 0, 0], "rotation": [0, 0, 0], "fixed": True},
             {"id": 2, "part_id": 2, "name": "plate", "position": [0, 0, 10], "rotation": [0, 0, 0], "fixed": False}]
    mates = [{"id": 1, "type": "distance", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">Z", "b_face": "<Z", "value": 3}},
             {"id": 2, "type": "distance", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">Z", "b_face": "<Z", "value": 9}}]
    res = a.solve(comps, mates, shapes)
    assert res["status"] == "conflict"
    assert any(s.startswith("not satisfied") for s in res["mate_status"].values())


def test_tangent_and_lock_and_chain():
    shapes = {1: _box(60, 20, 5), 2: m.rebuild([F("c", "cylinder", radius=5, height=20, axis="Y")], m.BuildContext({}, {})).shape, 3: _box(4, 4, 4)}
    comps = [{"id": 1, "part_id": 1, "name": "rail", "position": [0, 0, 0], "rotation": [0, 0, 0], "fixed": True},
             {"id": 2, "part_id": 2, "name": "roller", "position": [20, 0, 30], "rotation": [20, 0, 0], "fixed": False},
             {"id": 3, "part_id": 3, "name": "tag", "position": [20, -6, 30], "rotation": [0, 0, 0], "fixed": False}]
    mates = [
        {"id": 1, "type": "tangent", "comp_a": 1, "comp_b": 2, "params": {"a_face": ">Z", "b_face": "%CYLINDER"}},
        {"id": 2, "type": "lock", "comp_a": 2, "comp_b": 3, "params": {}},
    ]
    rel0 = np.linalg.inv(a.euler_matrix([20, 0, 30], [20, 0, 0])) @ a.euler_matrix([20, -6, 30], [0, 0, 0])
    res = a.solve(comps, mates, shapes)
    assert set(res["mate_status"].values()) == {"ok"}, res["mate_status"]
    _place(comps, res)
    roller_bb = a.world_shapes(comps, shapes)[2].BoundingBox()
    assert roller_bb.zmin == pytest.approx(5, abs=1e-5)  # resting on top of the rail
    rel = np.linalg.inv(a.euler_matrix(comps[1]["position"], comps[1]["rotation"])) @ a.euler_matrix(comps[2]["position"], comps[2]["rotation"])
    assert np.allclose(rel, rel0, atol=1e-6)  # the tag moved with the roller


def test_mate_types_api(client, project):
    meta = client.get("/api/meta").json()
    assert {t["type"] for t in meta["mate_types"]} >= {"flush", "distance", "angle", "parallel", "perpendicular", "concentric", "tangent", "point", "lock"}
