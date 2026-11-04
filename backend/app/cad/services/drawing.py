"""2D drawing sheets: projected views (hidden-line removal), associative dimensions, title block, SVG / PDF.

A drawing is stored as definitions only (sheet, views, dimensions, notes, title block); geometry is projected
from the current part every time the sheet is rendered, so views follow model changes.

Views        front / back / top / bottom / left / right (orthographic) and iso, via OpenCascade HLR:
             visible edges (thick), tangent edges (thin) and hidden edges (dashed), centre marks on holes.
Dimensions   horizontal / vertical / aligned between two anchors, radius / diameter on circles.
             Anchors are persistent edge references (``ref:``, see toponaming.py) + which end (or the arc centre),
             so the measured value is always taken from the current model.
Output       one list of primitives (polylines, filled polygons, text in sheet millimetres, y up), written to
             SVG for the browser and to a vector PDF by a small built-in writer (no extra dependency).
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepLib import BRepLib
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from . import modeling3d, toponaming

SHEETS: dict[str, tuple[float, float]] = {  # landscape width, height (mm)
    "A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841),
    "Letter": (279.4, 215.9), "Tabloid": (431.8, 279.4),
}
SCALES = [50, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01]  # ISO 5455 preferred scales
_S3 = 1 / math.sqrt(3)
_S2 = 1 / math.sqrt(2)
VIEW_DIRS: dict[str, tuple[tuple, tuple]] = {  # (towards the viewer, sheet x axis) in model coordinates
    "front": ((0, -1, 0), (1, 0, 0)), "back": ((0, 1, 0), (-1, 0, 0)),
    "top": ((0, 0, 1), (1, 0, 0)), "bottom": ((0, 0, -1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, -1, 0)),
    "iso": ((_S3, -_S3, _S3), (_S2, _S2, 0)),
}
DIM_TYPES = ("horizontal", "vertical", "aligned", "radius", "diameter")
BORDER = 10.0
TITLE_W, TITLE_H = 180.0, 40.0
TEXT = 3.5  # ISO 3098 lettering height for dimensions
W_VIS, W_THIN, W_HID = 0.5, 0.25, 0.3
ARROW_L, ARROW_W = 3.0, 1.0


def default_sheet() -> dict:
    return {"size": "A3", "projection": "third", "decimals": 2, "units": "mm"}


def default_title(name: str = "") -> dict:
    return {"title": name, "number": "", "material": "", "drawn_by": "", "date": date.today().isoformat(),
            "company": "Gearhead CAD", "revision": "A", "tolerance": "ISO 2768-m"}


def sheet_size(sheet: dict) -> tuple[float, float]:
    w, h = SHEETS.get(sheet.get("size", "A3"), SHEETS["A3"])
    return (h, w) if sheet.get("orientation") == "portrait" else (w, h)


# ------------------------------------------------------------------ projection
@dataclass
class ViewGeo:
    visible: list[np.ndarray] = field(default_factory=list)
    tangent: list[np.ndarray] = field(default_factory=list)
    hidden: list[np.ndarray] = field(default_factory=list)
    circles: list[tuple[float, float, float]] = field(default_factory=list)  # (cx, cy, r), view coords
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    X: np.ndarray = None
    Y: np.ndarray = None
    Z: np.ndarray = None
    snaps: list[dict] = field(default_factory=list)  # {x, y, edge, end}  end: 0 | 1 | "c"


def _axes(kind: str):
    if kind not in VIEW_DIRS:
        raise ValueError(f"unknown view '{kind}' ({', '.join(VIEW_DIRS)})")
    z, x = (np.array(v, dtype=float) for v in VIEW_DIRS[kind])
    z, x = z / np.linalg.norm(z), x / np.linalg.norm(x)
    return x, np.cross(z, x), z


def _edges_of(comp) -> list[np.ndarray]:
    out = []
    if comp is None or comp.IsNull():
        return out
    BRepLib.BuildCurves3d_s(comp)
    ex = TopExp_Explorer(comp, TopAbs_EDGE)
    while ex.More():
        try:
            a = BRepAdaptor_Curve(TopoDS.Edge_s(ex.Current()))
            t0, t1 = a.FirstParameter(), a.LastParameter()
            if a.GetType() == GeomAbs_Line:
                ts = [t0, t1]
            elif a.GetType() == GeomAbs_Circle:
                n = max(8, int(abs(t1 - t0) / (2 * math.pi) * 72))
                ts = np.linspace(t0, t1, n + 1)
            else:
                ts = np.linspace(t0, t1, 41)
            pts = np.array([[a.Value(t).X(), a.Value(t).Y()] for t in ts])
            if len(pts) >= 2 and np.ptp(pts, axis=0).max() > 1e-9:
                out.append(pts)
        except Exception:
            pass
        ex.Next()
    return out


def _circles_of(comp) -> list[tuple[float, float, float]]:
    out = []
    if comp is None or comp.IsNull():
        return out
    ex = TopExp_Explorer(comp, TopAbs_EDGE)
    while ex.More():
        try:
            a = BRepAdaptor_Curve(TopoDS.Edge_s(ex.Current()))
            # centre marks on full circles (holes, bosses) only, not on fillet arcs
            if a.GetType() == GeomAbs_Circle and abs(a.LastParameter() - a.FirstParameter()) > 2 * math.pi - 1e-6:
                c = a.Circle()
                key = (round(c.Location().X(), 4), round(c.Location().Y(), 4), round(c.Radius(), 4))
                if key not in out:
                    out.append(key)
        except Exception:
            pass
        ex.Next()
    return out


_VIEW_CACHE: "OrderedDict[tuple, ViewGeo]" = OrderedDict()


def project(shape: cq.Shape, kind: str, hidden: bool = True) -> ViewGeo:
    key = (id(shape), kind, hidden)
    if key in _VIEW_CACHE:
        _VIEW_CACHE.move_to_end(key)
        return _VIEW_CACHE[key]
    X, Y, Z = _axes(kind)
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*Z), gp_Dir(*X))))
    algo.Update()
    algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    g = ViewGeo(X=X, Y=Y, Z=Z)
    vis_c = h.VCompound()
    g.visible = _edges_of(vis_c) + _edges_of(h.OutLineVCompound())
    g.tangent = _edges_of(h.Rg1LineVCompound())
    if hidden:
        g.hidden = _edges_of(h.HCompound()) + _edges_of(h.OutLineHCompound())
    g.circles = _circles_of(vis_c) if kind != "iso" else []
    allp = np.concatenate(g.visible + g.tangent + g.hidden) if (g.visible or g.tangent or g.hidden) else np.zeros((1, 2))
    g.bbox = (float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max()))
    # snap points from the model edges (vertex ends and face-on circle centres)
    seen: set = set()
    for i, e in enumerate(shape.Edges()):
        cands = [(0, e.startPoint()), (1, e.endPoint())]
        if e.geomType() == "CIRCLE":
            c = BRepAdaptor_Curve(e.wrapped).Circle()
            ax = c.Axis().Direction()
            if abs(ax.X() * Z[0] + ax.Y() * Z[1] + ax.Z() * Z[2]) > 0.999:
                loc = c.Location()
                cands.append(("c", cq.Vector(loc.X(), loc.Y(), loc.Z())))
        for end, p in cands:
            v = np.array([p.x, p.y, p.z])
            x, y = float(v @ X), float(v @ Y)
            k = (round(x, 3), round(y, 3), end == "c")
            if k in seen:
                continue
            seen.add(k)
            s = {"x": x, "y": y, "edge": i, "end": end, "p": [round(float(c), 6) for c in v]}
            if end == "c":
                s["r"] = BRepAdaptor_Curve(e.wrapped).Circle().Radius()
                s["closed"] = bool(e.IsClosed())
            g.snaps.append(s)
    _VIEW_CACHE[key] = g
    while len(_VIEW_CACHE) > 64:
        _VIEW_CACHE.popitem(last=False)
    return g


# ------------------------------------------------------------------ view / anchor maths
def to_sheet(view: dict, x: float, y: float) -> tuple[float, float]:
    cu, cv = view.get("center", [0, 0])
    s = float(view.get("scale", 1))
    return view["x"] + (x - cu) * s, view["y"] + (y - cv) * s


def make_anchor(shape: cq.Shape, prov, snap: dict) -> dict:
    """Persistent anchor from a snap point ({edge index, end, p})."""
    return {"edge": toponaming.make_reference(shape, "edge", [int(snap["edge"])], prov), "end": snap["end"],
            "p": snap.get("p")}


def resolve_anchor(shape: cq.Shape, kind: str, anchor: dict) -> tuple[Optional[tuple], Optional[float], bool]:
    """((x, y) in view coords, circle radius or None, ok). Falls back to the stored point when the edge is gone."""
    X, Y, _ = _axes(kind)
    p3, r, ok = None, None, True
    try:
        e = modeling3d.resolve_edges(shape, anchor["edge"])[0]
        if anchor.get("end") == "c":
            c = BRepAdaptor_Curve(e.wrapped).Circle()
            loc = c.Location()
            p3, r = np.array([loc.X(), loc.Y(), loc.Z()]), c.Radius()
        else:
            ends = [np.array(e.startPoint().toTuple()), np.array(e.endPoint().toTuple())]
            if anchor.get("p") is not None:  # edge orientation can flip on rebuild: take the end nearest the original
                ref = np.array(anchor["p"], dtype=float)
                p3 = min(ends, key=lambda q: float(np.linalg.norm(q - ref)))
            else:
                p3 = ends[1 if anchor.get("end") == 1 else 0]
    except Exception:
        ok = False
        if anchor.get("p") is not None:
            p3 = np.array(anchor["p"], dtype=float)
    if p3 is None:
        return None, None, False
    return (float(p3 @ X), float(p3 @ Y)), r, ok


# ------------------------------------------------------------------ primitives
class Canvas:
    def __init__(self, w: float, h: float):
        self.w, self.h = w, h
        self.items: list[dict] = []

    def line(self, pts, w=W_THIN, dash=None, color="#000000", layer="line"):
        self.items.append({"t": "line", "pts": [[round(float(x), 3), round(float(y), 3)] for x, y in pts], "w": w,
                           "dash": dash, "c": color, "layer": layer})

    def fill(self, pts, color="#000000", layer="line"):
        self.items.append({"t": "fill", "pts": [[round(float(x), 3), round(float(y), 3)] for x, y in pts], "c": color, "layer": layer})

    def text(self, x, y, s, size=TEXT, anchor="middle", rot=0.0, color="#000000", bold=False, layer="text"):
        self.items.append({"t": "text", "x": round(float(x), 3), "y": round(float(y), 3), "text": str(s), "s": size,
                           "anchor": anchor, "rot": round(float(rot), 3), "c": color, "bold": bold, "layer": layer})

    def rect(self, x0, y0, x1, y1, w=W_THIN, layer="frame"):
        self.line([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], w=w, layer=layer)


_HELV = {**{c: 556 for c in "0123456789"}, ".": 278, ",": 278, " ": 278, "-": 333, "±": 584, "Ø": 778, "°": 400,
         "R": 722, "x": 500, ":": 278, "/": 278, "(": 333, ")": 333, "I": 278, "i": 222, "l": 222, "M": 833, "W": 944, "m": 833}


def text_width(s: str, size: float) -> float:
    return sum(_HELV.get(c, 667 if c.isupper() else 556) for c in s) / 1000.0 * size


def fmt_value(v: float, decimals: int) -> str:
    t = f"{v:.{max(0, int(decimals))}f}"
    return t.rstrip("0").rstrip(".") if "." in t else t


def _arrow(cv: Canvas, tip, direction, color="#000000"):
    d = np.array(direction, dtype=float)
    d = d / (np.linalg.norm(d) or 1)
    n = np.array([-d[1], d[0]])
    tip = np.array(tip, dtype=float)
    base = tip - d * ARROW_L
    cv.fill([tip, base + n * ARROW_W / 2, base - n * ARROW_W / 2], color=color, layer="dimension")


def _dim_text(cv: Canvas, mid, text, angle_deg, color):
    """ISO placement: centred on the dimension line, just above it in the reading direction, never upside down."""
    a = (angle_deg + 180) % 360 - 180
    if a > 90 or a <= -90:
        a = a + 180 if a <= -90 else a - 180
    up = np.array([-math.sin(math.radians(a)), math.cos(math.radians(a))])
    pos = np.array(mid, dtype=float) + up * 0.9
    cv.text(pos[0], pos[1], text, TEXT, "middle", a, color, layer="dimension")


def draw_linear(cv: Canvas, A, B, kind: str, offset: float, text: str, color="#000000"):
    A, B = np.array(A, dtype=float), np.array(B, dtype=float)
    if kind == "horizontal":
        u, n = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    elif kind == "vertical":
        u, n = np.array([0.0, 1.0]), np.array([-1.0, 0.0])
    else:
        d = B - A
        u = d / (np.linalg.norm(d) or 1)
        n = np.array([-u[1], u[0]])
    base = A + n * offset  # the dimension line passes through here, parallel to u
    pa = base + u * float(np.dot(A - base, u))
    pb = base + u * float(np.dot(B - base, u))
    gap, over = 1.0, 2.0
    for P, Q in ((A, pa), (B, pb)):  # extension lines: small gap at the object, 2 mm past the dimension line
        v = Q - P
        L = np.linalg.norm(v)
        if L > gap:
            v = v / L
            cv.line([P + v * gap, Q + v * over], w=W_THIN, color=color, layer="dimension")
    L = float(np.linalg.norm(pb - pa))
    if L < 1e-9:
        return
    dirv = (pb - pa) / L
    cv.line([pa, pb], w=W_THIN, color=color, layer="dimension")
    if L > 2.5 * ARROW_L:
        _arrow(cv, pa, -dirv, color)
        _arrow(cv, pb, dirv, color)
    else:  # arrows outside for short dimensions
        _arrow(cv, pa, dirv, color)
        _arrow(cv, pb, -dirv, color)
        cv.line([pa - dirv * (ARROW_L + 2), pa], w=W_THIN, color=color, layer="dimension")
        cv.line([pb, pb + dirv * (ARROW_L + 2)], w=W_THIN, color=color, layer="dimension")
    _dim_text(cv, (pa + pb) / 2, text, math.degrees(math.atan2(dirv[1], dirv[0])), color)


def draw_radial(cv: Canvas, C, R: float, kind: str, angle_deg: float, text: str, color="#000000"):
    C = np.array(C, dtype=float)
    d = np.array([math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))])
    tip = C + d * R
    out = tip + d * max(6.0, 0.6 * R)
    if kind == "diameter":
        far = C - d * R
        cv.line([far, out], w=W_THIN, color=color, layer="dimension")
        _arrow(cv, tip, d, color)
        _arrow(cv, far, -d, color)
    else:
        cv.line([C, out], w=W_THIN, color=color, layer="dimension")
        _arrow(cv, tip, d, color)
    shelf = np.array([1.0 if d[0] >= 0 else -1.0, 0.0])
    tw = text_width(text, TEXT) + 2
    end = out + shelf * tw
    cv.line([out, end], w=W_THIN, color=color, layer="dimension")
    cv.text((out[0] + end[0]) / 2, out[1] + 0.9, text, TEXT, "middle", 0, color, layer="dimension")


def _centre_mark(cv: Canvas, cx, cy, r):
    e = r + 2.0
    for a, b in (((cx - e, cy), (cx + e, cy)), ((cx, cy - e), (cx, cy + e))):
        cv.line([a, b], w=0.18, dash=[6, 1.5, 1, 1.5], color="#000000", layer="centre")


def _title_block(cv: Canvas, title: dict, sheet: dict, scale_text: str):
    W, H = cv.w, cv.h
    x1, y0 = W - BORDER, BORDER
    x0, y1 = x1 - TITLE_W, y0 + TITLE_H
    cv.rect(x0, y0, x1, y1, w=W_VIS)
    rows = [y0, y0 + 10, y0 + 20, y0 + 30, y1]
    for y in rows[1:-1]:
        cv.line([(x0, y), (x1, y)], w=W_THIN, layer="frame")
    cols = [x0, x0 + 60, x0 + 100, x0 + 140, x1]
    for x in cols[1:-1]:
        cv.line([(x, y0), (x, y0 + 20)], w=W_THIN, layer="frame")
    cv.line([(x0 + 120, y0 + 20), (x0 + 120, y1)], w=W_THIN, layer="frame")

    def cell(xa, ya, label, value, size=3.0, bold=False):
        cv.text(xa + 1.5, ya + 7.2, label.upper(), 1.8, "start", 0, "#555555", layer="frame")
        cv.text(xa + 1.5, ya + 2.2, value, size, "start", 0, "#000000", bold, layer="frame")

    cell(x0, y0 + 30, "Company", title.get("company", ""), 3.5, True)
    cell(x0 + 120, y0 + 30, "Revision", title.get("revision", ""))
    cell(x0, y0 + 20, "Title", title.get("title", ""), 4.0, True)
    cell(x0 + 120, y0 + 20, "Drawing no.", title.get("number", ""))
    cell(x0, y0 + 10, "Material", title.get("material", ""))
    cell(x0 + 60, y0 + 10, "Scale", scale_text)
    cell(x0 + 100, y0 + 10, "Units", sheet.get("units", "mm"))
    cell(x0 + 140, y0 + 10, "Sheet", f"{sheet.get('size', 'A3')}")
    cell(x0, y0, "Drawn by", title.get("drawn_by", ""))
    cell(x0 + 60, y0, "Date", title.get("date", ""))
    cell(x0 + 100, y0, "Tolerance", title.get("tolerance", ""))
    cell(x0 + 140, y0, "Projection", "3rd angle" if sheet.get("projection", "third") == "third" else "1st angle")


def scale_label(s: float) -> str:
    if s >= 1:
        return f"{fmt_value(s, 2)}:1"
    return f"1:{fmt_value(1 / s, 2)}"


# ------------------------------------------------------------------ layout
def standard_views(shape: cq.Shape, part_id: int, sheet: dict, kinds=("front", "top", "right", "iso")) -> list[dict]:
    """Front / top / right (+ iso) laid out in third- or first-angle projection at a standard scale."""
    W, H = sheet_size(sheet)
    g = {k: project(shape, k) for k in kinds}
    size = {k: (g[k].bbox[2] - g[k].bbox[0], g[k].bbox[3] - g[k].bbox[1]) for k in kinds}
    gap = 22.0
    ux0, uy0, ux1, uy1 = BORDER + gap, BORDER + TITLE_H + gap * 0.6, W - BORDER - gap, H - BORDER - gap
    fw, fh = size.get("front", (1, 1))
    tw, th = size.get("top", (0, 0))
    rw, rh = size.get("right", (0, 0))
    iw, ih = size.get("iso", (0, 0))
    first = sheet.get("projection") == "first"

    def layout(scale):
        fx = ux0 + scale * fw / 2 if not first else ux0 + scale * rw + gap + scale * fw / 2
        fy = uy0 + scale * fh / 2 if not first else uy0 + scale * th + gap + scale * fh / 2
        pos = {"front": (fx, fy)}
        if "top" in kinds:
            pos["top"] = (fx, fy + (scale * (fh + th) / 2 + gap) * (-1 if first else 1))
        if "right" in kinds:
            pos["right"] = (fx + (scale * (fw + rw) / 2 + gap) * (-1 if first else 1), fy)
        vs = {k: scale for k in kinds}
        if "iso" in kinds:  # pictorial view in the free corner, at the largest standard scale that fits there
            right_edge = max(pos[k][0] + scale * size[k][0] / 2 for k in pos)
            top_edge = max(pos[k][1] + scale * size[k][1] / 2 for k in pos)
            areas = [(ux1 - right_edge - gap, uy1 - uy0), (ux1 - ux0, uy1 - top_edge - gap)]  # beside / above the block
            best = max((next((s for s in SCALES if s <= scale and s * iw <= aw and s * ih <= ah), 0.0) for aw, ah in areas))
            if best < scale / 2:
                return None
            vs["iso"] = best
            pos["iso"] = (ux1 - best * iw / 2, uy1 - best * ih / 2)
        return pos, vs

    for scale in SCALES:  # the orthographic block (front + right across, front + top up) sets the sheet scale
        if scale * (fw + rw) + gap <= ux1 - ux0 and scale * (fh + th) + gap <= uy1 - uy0:
            got = layout(scale)
            if got:
                pos, vscale = got
                break
    else:
        scale = SCALES[-1]
        pos, vscale = layout(scale) or ({k: (W / 2, H / 2) for k in kinds}, {k: scale for k in kinds})
    views = []
    for i, k in enumerate(kinds, 1):
        b = g[k].bbox
        views.append({"id": f"v{i}", "part_id": part_id, "kind": k, "scale": vscale[k], "x": round(pos[k][0], 3), "y": round(pos[k][1], 3),
                      "center": [round((b[0] + b[2]) / 2, 6), round((b[1] + b[3]) / 2, 6)], "hidden": k != "iso",
                      # orthographic views in standard projection are not labelled; the pictorial view is
                      "label": "ISOMETRIC VIEW" if k == "iso" else "", "show_scale": k == "iso" and vscale[k] != scale})
    return views


def new_view(shape: cq.Shape, part_id: int, kind: str, scale: float, x: float, y: float, used_ids: set) -> dict:
    b = project(shape, kind).bbox
    n = 1
    while f"v{n}" in used_ids:
        n += 1
    return {"id": f"v{n}", "part_id": part_id, "kind": kind, "scale": scale, "x": x, "y": y,
            "center": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], "hidden": kind != "iso", "label": f"VIEW {kind.upper()}"}


def auto_dimensions(views: list[dict], shapes: dict[int, tuple], existing: list[dict]) -> list[dict]:
    """Overall width / height on the orthographic views and diameters of face-on circles."""
    out = []
    used = {d.get("id") for d in existing}
    n = [1]

    def nid():
        while f"d{n[0]}" in used:
            n[0] += 1
        used.add(f"d{n[0]}")
        return f"d{n[0]}"

    done_d: set = set()
    for v in views:
        if v["kind"] == "iso" or v["part_id"] not in shapes:
            continue
        shape, prov = shapes[v["part_id"]]
        g = project(shape, v["kind"])
        pts = [s for s in g.snaps if s["end"] != "c"]
        if len(pts) < 2:
            continue
        x0, y0, x1, y1 = g.bbox
        lo_x = min(pts, key=lambda s: (s["x"], s["y"]))
        hi_x = max(pts, key=lambda s: (s["x"], -s["y"]))
        lo_y = min(pts, key=lambda s: (s["y"], -s["x"]))
        hi_y = max(pts, key=lambda s: (s["y"], s["x"]))
        s = float(v["scale"])
        if hi_x["x"] - lo_x["x"] > 1e-6:
            off = -((lo_x["y"] - y0) * s + 10)  # below the view
            out.append({"id": nid(), "view": v["id"], "type": "horizontal", "a": make_anchor(shape, prov, lo_x),
                        "b": make_anchor(shape, prov, hi_x), "offset": round(off, 3)})
        if hi_y["y"] - lo_y["y"] > 1e-6:
            off = (x1 - lo_y["x"]) * s + 10  # right of the view (offset is along -x for vertical dims)
            out.append({"id": nid(), "view": v["id"], "type": "vertical", "a": make_anchor(shape, prov, lo_y),
                        "b": make_anchor(shape, prov, hi_y), "offset": round(-off, 3)})
        # one dimension per distinct hole / arc size in this view, with a count ("4x R4", "2x Ø10")
        groups: dict[tuple, list] = {}
        for c in [s_ for s_ in g.snaps if s_["end"] == "c"]:
            loc = (round(c["x"], 3), round(c["y"], 3))
            key = (bool(c.get("closed")), round(c["r"], 4))
            if loc not in [q[0] for q in groups.get(key, [])]:
                groups.setdefault(key, []).append((loc, c))
        for (closed, r), items in groups.items():
            gkey = (v["part_id"], closed, r)
            if gkey in done_d:
                continue
            done_d.add(gkey)
            c = items[0][1]
            dim = {"id": nid(), "view": v["id"], "type": "diameter" if closed else "radius", "a": make_anchor(shape, prov, c), "angle": 45.0}
            if len(items) > 1:
                dim["prefix"] = f"{len(items)}x "
            out.append(dim)
    return out


# ------------------------------------------------------------------ render
def render(drawing: dict, shapes: dict[int, tuple]) -> dict:
    """shapes: part_id -> (shape, provenance). Returns {sheet, primitives, views, dimensions}."""
    sheet = {**default_sheet(), **(drawing.get("sheet") or {})}
    W, H = sheet_size(sheet)
    cv = Canvas(W, H)
    cv.rect(BORDER, BORDER, W - BORDER, H - BORDER, w=0.7)
    views_out, dims_out = [], []
    vmap = {v["id"]: v for v in drawing.get("views", [])}
    for v in drawing.get("views", []):
        if v.get("part_id") not in shapes:
            views_out.append({"id": v["id"], "missing": True})
            continue
        shape, _ = shapes[v["part_id"]]
        g = project(shape, v["kind"], bool(v.get("hidden", True)))
        T = lambda pts, v=v: [to_sheet(v, x, y) for x, y in pts]  # noqa: E731
        for pl in g.hidden:
            cv.line(T(pl), w=W_HID, dash=[2.0, 1.0], color="#333333", layer="hidden")
        for pl in g.tangent:
            cv.line(T(pl), w=0.18, color="#000000", layer="tangent")
        for pl in g.visible:
            cv.line(T(pl), w=W_VIS, color="#000000", layer="visible")
        for cx, cy, r in g.circles:
            sx, sy = to_sheet(v, cx, cy)
            _centre_mark(cv, sx, sy, r * float(v["scale"]))
        bx0, by0 = to_sheet(v, g.bbox[0], g.bbox[1])
        bx1, by1 = to_sheet(v, g.bbox[2], g.bbox[3])
        if v.get("label"):
            cv.text((bx0 + bx1) / 2, by0 - 8, v["label"] + (f"  {scale_label(float(v['scale']))}" if v.get("show_scale") else ""),
                    3.0, "middle", 0, "#333333", layer="label")
        views_out.append({"id": v["id"], "box": [bx0, by0, bx1, by1],
                          "snaps": [{**{k: s[k] for k in ("edge", "end", "p")}, **({"r": s["r"]} if "r" in s else {}),
                                     "sx": round(to_sheet(v, s["x"], s["y"])[0], 3), "sy": round(to_sheet(v, s["x"], s["y"])[1], 3)} for s in g.snaps]})
    dec = int(sheet.get("decimals", 2))
    for d in drawing.get("dimensions", []):
        v = vmap.get(d.get("view"))
        if not v or v.get("part_id") not in shapes:
            dims_out.append({"id": d.get("id"), "ok": False, "value": None})
            continue
        shape, _ = shapes[v["part_id"]]
        s = float(v["scale"])
        ok = True
        try:
            pa, ra, oka = resolve_anchor(shape, v["kind"], d["a"])
            ok &= oka
            color = "#000000"
            if d["type"] in ("radius", "diameter"):
                if ra is None:
                    raise ValueError("not a circle")
                val = ra * (2 if d["type"] == "diameter" else 1)
                text = d.get("text") or (d.get("prefix", "") + ("Ø" if d["type"] == "diameter" else "R") + fmt_value(val, dec))
                C = to_sheet(v, *pa)
                draw_radial(cv, C, ra * s, d["type"], float(d.get("angle", 45)), text, "#000000" if ok else "#c8361b")
            else:
                pb, _, okb = resolve_anchor(shape, v["kind"], d["b"])
                ok &= okb
                A, B = np.array(to_sheet(v, *pa)), np.array(to_sheet(v, *pb))
                if d["type"] == "horizontal":
                    val = abs(pb[0] - pa[0])
                elif d["type"] == "vertical":
                    val = abs(pb[1] - pa[1])
                else:
                    val = math.dist(pa, pb)
                text = d.get("text") or fmt_value(val, dec)
                if d.get("tolerance"):
                    text += f" ±{fmt_value(float(d['tolerance']), dec)}"
                draw_linear(cv, A, B, d["type"], float(d.get("offset", 10)), text, color if ok else "#c8361b")
            dims_out.append({"id": d.get("id"), "ok": bool(ok), "value": val})
        except Exception as exc:
            dims_out.append({"id": d.get("id"), "ok": False, "value": None, "error": str(exc)})
    for nt in drawing.get("notes", []):
        lines = str(nt.get("text", "")).split("\n")
        for i, ln in enumerate(lines):
            cv.text(nt.get("x", 20), nt.get("y", 60) - i * float(nt.get("size", 3.5)) * 1.5, ln, float(nt.get("size", 3.5)), "start", 0, layer="note")
    scales = sorted({float(v["scale"]) for v in drawing.get("views", []) if v.get("kind") != "iso"}) or [1.0]
    _title_block(cv, {**default_title(drawing.get("name", "")), **(drawing.get("title") or {})}, sheet, " / ".join(scale_label(s) for s in scales))
    return {"sheet": {"w": W, "h": H, **sheet}, "primitives": cv.items, "views": views_out, "dimensions": dims_out}


# ------------------------------------------------------------------ writers
def _esc_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def to_svg(r: dict) -> str:
    W, H = r["sheet"]["w"], r["sheet"]["h"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>']
    for p in r["primitives"]:
        if p["t"] in ("line", "fill"):
            pts = " ".join(f"{x:.3f},{H - y:.3f}" for x, y in p["pts"])
            if p["t"] == "line":
                dash = f' stroke-dasharray="{" ".join(str(d) for d in p["dash"])}"' if p.get("dash") else ""
                out.append(f'<polyline points="{pts}" fill="none" stroke="{p["c"]}" stroke-width="{p["w"]}" '
                           f'stroke-linecap="round" stroke-linejoin="round"{dash}/>')
            else:
                out.append(f'<polygon points="{pts}" fill="{p["c"]}"/>')
        else:
            anchor = {"start": "start", "middle": "middle", "end": "end"}[p["anchor"]]
            rot = f' transform="rotate({-p["rot"]:.3f} {p["x"]:.3f} {H - p["y"]:.3f})"' if p.get("rot") else ""
            weight = ' font-weight="bold"' if p.get("bold") else ""
            out.append(f'<text x="{p["x"]:.3f}" y="{H - p["y"]:.3f}" font-family="Helvetica, Arial, sans-serif" font-size="{p["s"]}" '
                       f'fill="{p["c"]}" text-anchor="{anchor}"{weight}{rot}>{_esc_xml(p["text"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _pdf_text(s: str) -> bytes:
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _rgb(c: str) -> str:
    c = c.lstrip("#")
    return " ".join(f"{int(c[i:i + 2], 16) / 255:.3f}" for i in (0, 2, 4))


def to_pdf(r: dict) -> bytes:
    """Single-page vector PDF (standard Helvetica fonts, WinAnsi encoding)."""
    k = 72.0 / 25.4
    W, H = r["sheet"]["w"], r["sheet"]["h"]
    ops: list[bytes] = [b"1 J 1 j"]
    for p in r["primitives"]:
        if p["t"] == "line":
            pts = p["pts"]
            dash = "[" + " ".join(f"{d * k:.3f}" for d in p["dash"]) + "] 0 d" if p.get("dash") else "[] 0 d"
            path = f"{pts[0][0] * k:.3f} {pts[0][1] * k:.3f} m " + " ".join(f"{x * k:.3f} {y * k:.3f} l" for x, y in pts[1:])
            ops.append(f"{_rgb(p['c'])} RG {p['w'] * k:.3f} w {dash} {path} S".encode())
        elif p["t"] == "fill":
            pts = p["pts"]
            path = f"{pts[0][0] * k:.3f} {pts[0][1] * k:.3f} m " + " ".join(f"{x * k:.3f} {y * k:.3f} l" for x, y in pts[1:])
            ops.append(f"{_rgb(p['c'])} rg {path} h f".encode())
        else:
            size = p["s"] / 0.72  # cap height ~ 0.72 em: lettering height = cap height
            w = text_width(p["text"], size)
            dx = {"start": 0.0, "middle": -w / 2, "end": -w}[p["anchor"]]
            a = math.radians(p.get("rot") or 0)
            ca, sa = math.cos(a), math.sin(a)
            x = (p["x"] + dx * ca) * k
            y = (p["y"] + dx * sa) * k
            font = b"/F2" if p.get("bold") else b"/F1"
            ops.append(b"BT " + f"{_rgb(p['c'])} rg ".encode() + font + f" {size * k:.3f} Tf {ca:.5f} {sa:.5f} {-sa:.5f} {ca:.5f} {x:.3f} {y:.3f} Tm (".encode()
                       + _pdf_text(p["text"]) + b") Tj ET")
    stream = b"\n".join(ops)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W * k:.3f} {H * k:.3f}] /Contents 4 0 R "
        f"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>".encode(),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info << /Producer (Gearhead CAD) >> >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
