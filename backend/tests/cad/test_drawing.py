import pytest

from app.cad.services import drawing as dw
from app.cad.services import modeling3d as m


def F(i, t, **p):
    return {"id": i, "type": t, "params": {**m.default_params(t), **p}}


def build(feats):
    r = m.rebuild(feats, m.BuildContext({}, {}))
    return r.shape, r.prov


def plate(length=80, holes=((60, 25),)):
    return [F("a", "box", length=length, width=50, height=15)] + \
        [F("h", "hole", face=">Z", diameter=10, points=[[x, y, 15] for x, y in holes])]


def test_projection_and_snaps():
    shape, _ = build(plate())
    top = dw.project(shape, "top")
    assert top.bbox == pytest.approx((0, 0, 80, 50), abs=1e-6)
    assert any(abs(r - 5) < 1e-6 for _, _, r in top.circles)
    front = dw.project(shape, "front")
    assert front.bbox == pytest.approx((0, 0, 80, 15), abs=1e-6)
    assert front.hidden, "the through hole is hidden in the front view"
    assert any(s["end"] == "c" and s["r"] == pytest.approx(5) for s in top.snaps)


def test_standard_views_fit_and_auto_dims_measure_model():
    shape, prov = build(plate())
    views = dw.standard_views(shape, 1, dw.default_sheet())
    assert [v["kind"] for v in views] == ["front", "top", "right", "iso"]
    assert views[1]["y"] > views[0]["y"] and views[2]["x"] > views[0]["x"]  # third angle: top above, right to the right
    W, H = dw.sheet_size(dw.default_sheet())
    dims = dw.auto_dimensions(views, {1: (shape, prov)}, [])
    r = dw.render({"name": "t", "views": views, "dimensions": dims}, {1: (shape, prov)})
    vals = sorted(round(x["value"], 6) for x in r["dimensions"])
    assert vals == sorted([80, 15, 80, 50, 10, 50, 15])
    for v in r["views"]:
        x0, y0, x1, y1 = v["box"]
        assert 0 < x0 < x1 < W and 0 < y0 < y1 < H


def test_first_angle_layout():
    shape, _ = build(plate())
    views = dw.standard_views(shape, 1, {**dw.default_sheet(), "projection": "first"})
    front, top, right = views[:3]
    assert top["y"] < front["y"] and right["x"] < front["x"]


def test_dimensions_follow_model_changes():
    shape, prov = build(plate())
    views = dw.standard_views(shape, 1, dw.default_sheet())
    dims = dw.auto_dimensions(views, {1: (shape, prov)}, [])
    # the plate gets longer and the hole moves: the same dimension records now measure the new model
    shape2, prov2 = build(plate(length=100, holes=((70, 25),)))
    r = dw.render({"name": "t", "views": views, "dimensions": dims}, {1: (shape2, prov2)})
    by_id = {x["id"]: x for x in r["dimensions"]}
    assert all(x["ok"] for x in r["dimensions"])
    assert by_id[dims[0]["id"]]["value"] == pytest.approx(100)  # overall length in the front view
    dia = next(d for d in dims if d["type"] == "diameter")
    assert by_id[dia["id"]]["value"] == pytest.approx(10)


def test_svg_and_pdf_writers():
    shape, prov = build(plate())
    views = dw.standard_views(shape, 1, dw.default_sheet())
    r = dw.render({"name": "t", "views": views, "dimensions": dw.auto_dimensions(views, {1: (shape, prov)}, []),
                   "notes": [{"x": 20, "y": 40, "text": "1. Break edges 0.3 x 45°"}]}, {1: (shape, prov)})
    svg = dw.to_svg(r)
    assert svg.startswith("<svg") and "Ø10" in svg and "stroke-dasharray" in svg
    pdf = dw.to_pdf(r)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/BaseFont /Helvetica" in pdf and b"\xd810" in pdf  # Ø in WinAnsi
    xref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[xref:xref + 4] == b"xref"


def test_drawing_api(client, project):
    pid = client.post(f"/api/projects/{project}/parts", json={"name": "plate"}).json()["id"]
    client.post(f"/api/parts/{pid}/features", json={"type": "box", "params": {"length": 60, "width": 40, "height": 10}})
    d = client.post(f"/api/projects/{project}/drawings", json={"name": "Plate drawing", "part_id": pid}).json()
    assert len(d["views"]) == 4 and d["title"]["title"] == "plate"
    r = client.get(f"/api/drawings/{d['id']}/render").json()
    front = next(v for v in r["views"] if v["id"] == d["views"][0]["id"])
    snaps = [s for s in front["snaps"] if s["end"] != "c"]
    a = min(snaps, key=lambda s: (s["sx"], s["sy"]))
    b = max(snaps, key=lambda s: (s["sx"], -s["sy"]))
    res = client.post(f"/api/drawings/{d['id']}/dimensions", json={"view": front["id"], "type": "horizontal", "a": a, "b": b, "offset": -12})
    assert res.status_code == 201, res.text
    assert client.post(f"/api/drawings/{d['id']}/dimensions", json={"view": front["id"], "type": "diameter", "a": a}).status_code == 422
    r = client.get(f"/api/drawings/{d['id']}/render").json()
    assert r["dimensions"][0]["value"] == pytest.approx(60)
    auto = client.post(f"/api/drawings/{d['id']}/auto-dimensions").json()
    assert auto["added"] >= 4
    pdf = client.get(f"/api/drawings/{d['id']}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    assert client.get(f"/api/drawings/{d['id']}/export?format=svg").text.startswith("<svg")
    p = client.patch(f"/api/drawings/{d['id']}", json={"sheet": {**d["sheet"], "size": "A4"}, "title": {**d["title"], "drawn_by": "CG"}}).json()
    assert p["sheet"]["size"] == "A4" and p["title"]["drawn_by"] == "CG"
    assert client.patch(f"/api/drawings/{d['id']}", json={"sheet": {"size": "B7"}}).status_code == 422
    assert [x["id"] for x in client.get(f"/api/projects/{project}/drawings").json()] == [d["id"]]
