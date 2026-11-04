import math

import pytest

from app.cad.services import modeling3d as m
from app.cad.services import sketch2d as s


def F(i, t, **p):
    return {"id": i, "type": t, "params": {**m.default_params(t), **p}}


SK = {1: {"entities": s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [60, 40]}]), "plane": "XY", "offset": 0}}


def build(feats, params=None):
    return m.rebuild(feats, m.BuildContext(SK, params or {}))


def test_expression_parameters():
    assert m.eval_expr("w*2 + sin(30)", {"w": 5}) == pytest.approx(10.5)
    with pytest.raises(ValueError):
        m.eval_expr("__import__('os')", {})
    r = build([F("a", "extrude", sketch_id=1, distance="=h")], {"h": 7})
    assert r.shape.Volume() == pytest.approx(60 * 40 * 7)


def test_feature_tree_volume_and_status():
    r = build([
        F("a", "extrude", sketch_id=1, distance=20),
        F("b", "hole", face=">Z", diameter=8, depth=0, points=[[30, 20, 20]]),
        F("c", "fillet", radius=3, edges="|Z"),
        F("d", "shell", thickness=2, faces=">Z"),
    ])
    assert all(st["ok"] for st in r.statuses), r.statuses
    assert r.shape.isValid()
    assert r.shape.Volume() < 60 * 40 * 20


def test_failing_feature_does_not_break_others():
    r = build([F("a", "extrude", sketch_id=1, distance=10), F("b", "fillet", radius=500, edges="|Z"), F("c", "box", length=5, width=5, height=5, position=[100, 0, 0])])
    assert r.statuses[1]["ok"] is False and r.statuses[2]["ok"] is True
    assert r.shape.Volume() == pytest.approx(60 * 40 * 10 + 125)


def test_patterns_and_mirror():
    r = build([
        F("a", "box", length=10, width=10, height=10, position=[0, 0, 0]),
        F("b", "linear_pattern", source="a", count=3, spacing=[20, 0, 0]),
        F("c", "mirror", source="all", plane="XZ", offset=0),
    ])
    assert all(st["ok"] for st in r.statuses)
    assert r.shape.Volume() == pytest.approx(6 * 1000)


def test_near_selector_and_mass():
    r = build([F("a", "extrude", sketch_id=1, distance=10), F("b", "chamfer", length=1, edges="near:30,0,10")])
    assert r.statuses[1]["ok"]
    props = m.mass_properties(r.shape, "aluminum")
    assert props["mass_kg"] == pytest.approx(props["volume_mm3"] * 1e-9 * 2700)


def test_tessellation_normals_outward():
    r = build([F("a", "extrude", sketch_id=1, distance=10)])
    t = m.tessellate(r.shape)
    import numpy as np
    P = np.array(t["positions"]).reshape(-1, 3)
    I = np.array(t["indices"]).reshape(-1, 3)
    for f in t["faces"]:
        a, b, c = P[I[f["tri_start"]]]
        assert np.dot(np.cross(b - a, c - a), f["normal"]) > 0
