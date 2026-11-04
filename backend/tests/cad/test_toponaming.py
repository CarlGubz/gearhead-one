"""Persistent references must survive upstream edits that renumber faces / edges."""
import pytest

from app.cad.services import modeling3d as m
from app.cad.services import sketch2d as s
from app.cad.services import toponaming as tn


def F(i, t, **p):
    return {"id": i, "type": t, "params": {**m.default_params(t), **p}}


def build(feats, sketches=None, params=None):
    return m.rebuild(feats, m.BuildContext(sketches or {}, params or {}))


def _face_idx(r, pred):
    return next(i for i, f in enumerate(r.shape.Faces()) if pred(f))


def _top(f):
    return f.geomType() == "PLANE" and f.normalAt(f.Center()).z > 0.99


def test_face_ref_survives_upstream_insert_and_resize():
    base = [F("a", "box", length=40, width=30, height=10)]
    r = build(base)
    top = _face_idx(r, lambda f: _top(f) and abs(f.Center().z - 10) < 1e-6)
    ref = tn.make_reference(r.shape, "face", [top], r.prov)
    hole = F("h", "hole", face=ref, diameter=6, positions=[[0, 0]])
    old_idx = F("h", "hole", face=f"idx:{top}", diameter=6, positions=[[0, 0]])

    # upstream: a boss on the side (renumbers faces) and a taller box
    edited = [F("a", "box", length=40, width=30, height=16), F("b", "box", length=10, width=10, height=5, position=[-10, 0, 0])]
    ok = build(edited + [hole])
    assert ok.statuses[-1]["ok"], ok.statuses
    # the hole still went through the top face: a 6 mm through-hole along Z removes pi*9*16
    assert ok.shape.Volume() == pytest.approx(40 * 30 * 16 + 500 - 3.14159265 * 9 * 16, rel=1e-4)
    # whereas the raw index now points at some other face
    bad = build(edited + [old_idx])
    assert bad.shape.Volume() != pytest.approx(ok.shape.Volume(), rel=1e-6)


def test_edge_ref_survives_sketch_change():
    sk = {1: {"entities": s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [60, 40]}]), "plane": "XY", "offset": 0}}
    feats = [F("a", "extrude", sketch_id=1, distance=10)]
    r = build(feats, sk)
    edges = r.shape.Edges()
    corner = next(i for i, e in enumerate(edges) if e.geomType() == "LINE" and abs(e.Center().x - 60) < 1e-6 and abs(e.Center().y - 40) < 1e-6)
    ref = tn.make_reference(r.shape, "edge", [corner], r.prov)
    # add a hole to the sketch and make the plate longer: topology and coordinates change
    sk2 = {1: {"entities": s.ensure_ids([{"type": "rect", "p1": [0, 0], "p2": [80, 40]}, {"type": "circle", "c": [20, 20], "r": 5}]),
               "plane": "XY", "offset": 0}}
    out = build(feats + [F("f", "fillet", radius=4, edges=ref)], sk2)
    assert out.statuses[-1]["ok"], out.statuses
    cyl = [f for f in out.shape.Faces() if f.geomType() == "CYLINDER"]
    # one fillet face at the (80, 40) corner + the sketch hole
    fillet_faces = [f for f in cyl if f.Center().x > 70 and f.Center().y > 30]
    assert len(fillet_faces) == 1 and len(cyl) == 2


def test_hole_on_face_created_by_feature_is_tracked():
    feats = [F("a", "box", length=40, width=40, height=10), F("b", "box", length=10, width=10, height=10, position=[15, 15, 10])]
    r = build(feats)
    boss_top = _face_idx(r, lambda f: _top(f) and abs(f.Center().z - 20) < 1e-6)
    assert r.prov[boss_top][0] == "b"
    ref = tn.make_reference(r.shape, "face", [boss_top], r.prov)
    moved = [feats[0], F("b", "box", length=10, width=10, height=14, position=[5, 5, 10])]
    out = build(moved + [F("h", "hole", face=ref, diameter=4, depth=3, positions=[[0, 0]])])
    assert out.statuses[-1]["ok"]
    assert out.shape.Volume() == pytest.approx(16000 + 1400 - 3.14159265 * 4 * 3, rel=1e-4)


def test_lost_reference_reports_error():
    feats = [F("a", "box", length=20, width=20, height=10), F("b", "cylinder", radius=3, height=10, position=[30, 0, 0])]
    r = build(feats)
    cyl = _face_idx(r, lambda f: f.geomType() == "CYLINDER")
    ref = tn.make_reference(r.shape, "face", [cyl], r.prov)
    out = build([feats[0], F("s", "shell", thickness=1, faces=ref)])
    assert not out.statuses[-1]["ok"] and "lost" in out.statuses[-1]["error"]


def test_geometric_fallback_warns():
    feats = [F("a", "box", length=20, width=20, height=10)]
    r = build(feats)
    top = _face_idx(r, _top)
    ref = tn.make_reference(r.shape, "face", [top], r.prov)
    # same geometry, rebuilt by a feature with a different id -> origin gone, geometry matches
    out = build([F("z", "box", length=20, width=20, height=10), F("h", "hole", face=ref, diameter=4, positions=[[0, 0]])])
    assert out.statuses[-1]["ok"] and out.statuses[-1].get("warnings")


def test_reference_and_stabilize_api(client, project):
    pid = client.post(f"/api/projects/{project}/parts", json={"name": "refs"}).json()["id"]
    client.post(f"/api/parts/{pid}/features", json={"type": "box", "params": {"length": 30, "width": 20, "height": 10}})
    mesh = client.get(f"/api/parts/{pid}/mesh").json()["mesh"]
    top = next(f["index"] for f in mesh["faces"] if f["normal"][2] > 0.99)
    sel = client.get(f"/api/parts/{pid}/reference", params={"kind": "face", "indices": str(top)}).json()["selector"]
    assert sel.startswith("ref:")
    p = client.post(f"/api/parts/{pid}/features", json={"type": "hole", "params": {"face": f"idx:{top}", "diameter": 4}}).json()
    assert all(s["ok"] for s in p["statuses"])
    st = client.post(f"/api/parts/{pid}/stabilize-references").json()
    assert st["changed"] == 1 and st["features"][1]["params"]["face"].startswith("ref:")
    assert all(s["ok"] for s in st["statuses"])
