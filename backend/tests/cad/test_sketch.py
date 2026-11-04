import math

from app.cad.services import sketch2d as s


def test_profile_area_with_hole_and_slot():
    ents = s.ensure_ids([
        {"type": "rect", "p1": [0, 0], "p2": [40, 20]},
        {"type": "circle", "c": [10, 10], "r": 4},
        {"type": "slot", "p1": [0, 40], "p2": [20, 40], "width": 6},
    ])
    a = s.analyse(ents)
    assert a["closed_loops"] == 3 and a["open_chains"] == 0
    assert math.isclose(a["area"], 800 - math.pi * 16 + 20 * 6 + math.pi * 9, rel_tol=2e-3)


def test_open_chain_reported():
    a = s.analyse(s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [10, 0]}, {"type": "line", "p1": [10, 0], "p2": [10, 10]}]))
    assert a["open_chains"] == 1 and not a["valid_profile"]


def test_lines_close_into_triangle():
    ents = s.ensure_ids([
        {"type": "line", "p1": [0, 0], "p2": [10, 0]}, {"type": "line", "p1": [10, 0], "p2": [5, 8]}, {"type": "line", "p1": [5, 8], "p2": [0, 0]},
    ])
    assert math.isclose(s.analyse(ents)["area"], 40.0, rel_tol=1e-6)


def test_solver_fully_constrained_and_conflict():
    ents = s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [10, 1]}, {"type": "line", "p1": [10, 1], "p2": [9, 12]}, {"type": "circle", "c": [3, 3], "r": 2}])
    cons = [
        {"type": "horizontal", "a": "e1"}, {"type": "distance", "a": "e1", "value": 25}, {"type": "perpendicular", "a": "e1", "b": "e2"},
        {"type": "coincident", "a": "e1.p2", "b": "e2.p1"}, {"type": "fixed", "a": "e1.p1", "value": [0, 0]},
        {"type": "distance", "a": "e2", "value": 12}, {"type": "radius", "a": "e3", "value": 3}, {"type": "fixed", "a": "e3.c", "value": [5, 5]},
    ]
    r = s.solve_constraints(ents, cons)
    assert r["status"] == "fully_constrained" and r["dof"] == 0
    e1, e2 = r["entities"][0], r["entities"][1]
    assert math.isclose(e1["p2"][0], 25, abs_tol=1e-6) and math.isclose(e2["p2"][1], 12, abs_tol=1e-6)
    assert s.solve_constraints(ents, cons + [{"type": "distance", "a": "e1", "value": 30}])["status"] == "conflict"


def test_dxf_roundtrip():
    ents = s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [10, 5]}, {"type": "circle", "c": [5, 2], "r": 1}, {"type": "arc", "c": [0, 0], "r": 3, "start": 0, "end": 90}])
    back = s.import_dxf(s.export_dxf(ents).encode())
    assert {e["type"] for e in back} >= {"polyline", "circle", "arc"}
    assert math.isclose(s.analyse(back)["area"], s.analyse(ents[:2])["area"], rel_tol=1e-3)


def test_transform_ops():
    ents = s.ensure_ids([{"type": "circle", "c": [10, 0], "r": 2}])
    assert len(s.transform_entities(ents, {"op": "circular_pattern", "count": 6, "center": [0, 0]})) == 5
    m = s.transform_entities(ents, {"op": "mirror", "p1": [0, 0], "p2": [0, 1]})
    assert math.isclose(m[0]["c"][0], -10, abs_tol=1e-9)
