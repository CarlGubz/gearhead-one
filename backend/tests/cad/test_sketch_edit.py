import math

from app.cad.services import sketch2d as s
from app.cad.services import sketch_edit as se


def _by(ents, i):
    return next(e for e in ents if e["id"] == i)


def test_trim_line_middle_splits_between_crossings():
    ents = s.ensure_ids([
        {"type": "line", "p1": [0, 0], "p2": [30, 0]},
        {"type": "line", "p1": [10, -5], "p2": [10, 5]},
        {"type": "line", "p1": [20, -5], "p2": [20, 5]},
    ])
    out, _ = se.trim(ents, [], "e1", [15, 0])
    horiz = sorted((e for e in out if e["type"] == "line" and e["p1"][1] == e["p2"][1] == 0), key=lambda e: e["p1"][0])
    assert len(horiz) == 2
    assert math.isclose(horiz[0]["p2"][0], 10) and math.isclose(horiz[1]["p1"][0], 20)


def test_trim_line_end_and_isolated_delete():
    ents = s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [30, 0]}, {"type": "circle", "c": [10, 0], "r": 5}])
    out, _ = se.trim(ents, [], "e1", [25, 0])
    assert math.isclose(_by(out, "e1")["p2"][0], 15)
    out, _ = se.trim(s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [1, 0]}]), [], "e1", [0.5, 0])
    assert out == []


def test_trim_circle_becomes_arc():
    ents = s.ensure_ids([{"type": "circle", "c": [0, 0], "r": 10}, {"type": "line", "p1": [-20, 0], "p2": [20, 0]}])
    out, _ = se.trim(ents, [], "e1", [0, 10])  # remove the upper half
    arc = _by(out, "e1")
    assert arc["type"] == "arc"
    assert math.isclose(se._arc_span(arc["start"], arc["end"]), 180, abs_tol=1e-6)
    mid = math.radians(arc["start"] + 90)
    assert math.sin(mid) < 0  # the kept half is below the line


def test_trim_rect_side_explodes():
    ents = s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [20, 10]}, {"type": "line", "p1": [10, -5], "p2": [10, 15]}])
    out, _ = se.trim(ents, [], "e1", [15, 0])
    assert all(e["type"] == "line" for e in out)
    assert len(out) == 5  # 4 rect sides (bottom shortened) + the cutter


def test_extend_line_to_boundary():
    ents = s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [5, 0]}, {"type": "line", "p1": [12, -5], "p2": [12, 5]}])
    out, _ = se.extend(ents, [], "e1", [5, 0])
    assert math.isclose(_by(out, "e1")["p2"][0], 12)


def test_fillet_rect_corner_is_tangent_and_closed():
    ents = s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [20, 10]}])
    out, cons = se.fillet(ents, [], 3, at=[20, 10])
    arc = next(e for e in out if e["type"] == "arc")
    assert math.isclose(arc["r"], 3)
    assert math.isclose(arc["c"][0], 17) and math.isclose(arc["c"][1], 7)
    a = s.analyse(out)
    assert a["valid_profile"] and math.isclose(a["area"], 200 - (9 - math.pi * 9 / 4), rel_tol=1e-3)
    r = s.solve_constraints(out, cons)
    assert r["status"] in ("solved", "fully_constrained") and r["max_residual"] < 1e-6


def test_fillet_too_large_rejected():
    ents = s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [2, 0]}, {"type": "line", "p1": [0, 0], "p2": [0, 2]}])
    try:
        se.fillet(ents, [], 5, a="e1", b="e2")
        raise AssertionError("expected failure")
    except ValueError as exc:
        assert "too large" in str(exc)


def test_infer_constraints():
    ents = s.ensure_ids([
        {"type": "line", "p1": [0, 0], "p2": [10, 0]},
        {"type": "line", "p1": [10, 0], "p2": [10, 8]},
        {"type": "line", "p1": [5, -3], "p2": [5, 0]},   # ends on e1 mid-span
        {"type": "arc", "c": [10, 12], "r": 4, "start": 180, "end": 270},  # e2 meets it at (10, 8)
    ])
    new = se.infer_constraints(ents, [])
    kinds = {(c["type"], c.get("a"), c.get("b")) for c in new}
    assert ("horizontal", "e1", None) in kinds and ("vertical", "e2", None) in kinds
    assert any(c["type"] == "coincident" and {c["a"], c["b"]} == {"e1.p2", "e2.p1"} for c in new)
    assert ("point_on_line", "e3.p2", "e1") in kinds
    assert not any(c["type"] == "tangent" for c in new)  # e2 is radial to the arc, not tangent
    assert se.infer_constraints(ents, new) == []  # idempotent
    assert s.solve_constraints(ents, new)["status"] in ("solved", "fully_constrained")


def test_infer_tangent_line_from_arc():
    ents = s.ensure_ids([{"type": "arc", "c": [0, 0], "r": 5, "start": 0, "end": 90}, {"type": "line", "p1": [0, 5], "p2": [-10, 5]}])
    assert any(c["type"] == "tangent" for c in se.infer_constraints(ents, [], ["e2"]))


def test_layers_dxf_roundtrip():
    ents = s.ensure_ids([{"type": "line", "p1": [0, 0], "p2": [1, 0], "layer": "CUT"}, {"type": "circle", "c": [0, 0], "r": 1}])
    layers = [{"name": "CUT", "color": "#ff0000", "visible": True, "locked": False}]
    back, back_layers = s.import_dxf(s.export_dxf(ents, layers).encode(), with_layers=True)
    assert next(e for e in back if e["type"] == "line")["layer"] == "CUT"
    assert "layer" not in next(e for e in back if e["type"] == "circle")
    cut = next(l for l in back_layers if l["name"] == "CUT")
    assert cut["color"] == "#ff0000"


def test_edit_api(client, project):
    sk = client.post(f"/api/projects/{project}/sketches", json={"name": "t", "entities": [{"type": "rect", "p1": [0, 0], "p2": [20, 10]}],
                                                                 "layers": [{"name": "A", "color": "#00ff00"}]}).json()
    assert [l["name"] for l in sk["layers"]] == ["0", "A"]
    r = client.post(f"/api/sketches/{sk['id']}/edit", json={"op": "fillet", "params": {"at": [0, 0], "radius": 2}})
    assert r.status_code == 200, r.text
    assert r.json()["solve"]["status"] in ("solved", "fully_constrained")
    assert sum(e["type"] == "arc" for e in r.json()["entities"]) == 1
    bad = client.post(f"/api/sketches/{sk['id']}/edit", json={"op": "fillet", "params": {"at": [50, 50], "radius": 2}})
    assert bad.status_code == 422
    inf = client.post("/api/sketch-infer", json={"entities": [{"id": "e1", "type": "line", "p1": [0, 0], "p2": [5, 0]}], "ids": ["e1"]})
    assert inf.json()["constraints"] == [{"type": "horizontal", "a": "e1"}]
