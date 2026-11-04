"""CAM engine: 2.5D + 3-axis toolpath generation, linking, G-code and material-removal simulation.

Conventions
-----------
* Tool axis is +Z. Orient the model (e.g. with a *Move/rotate body* feature) so the face you
  want to machine points up.  Units are millimetres, feeds in mm/min.
* Toolpaths are computed on a tessellation of the part (see geometry_mesh.TriMesh).

Operations
----------
face       surface the stock top down to a final Z (zig-zag, outside entry)
profile    contour outside / inside / on the outline, helical ramp entry, climb or conventional
pocket     area clearing with offset rings; auto-recognises flat pocket floors, or use a sketch / section
drill      auto-detected (or sketched) holes: peck/standard cycles; large holes are helically milled
waterline  3-axis Z-level roughing around the part (handles overhangs by shadowing)
raster     3-axis parallel finishing over the surface with a ball / flat tool (drop-cutter on a height field)

Rest machining
--------------
profile / pocket / waterline / raster accept ``rest``: the in-process stock left by the earlier operations
is tracked on a height map, and the toolpath is clipped to where *this* tool would still remove more
than ``rest_threshold`` mm (e.g. corners a large roughing cutter could not reach).

Holder / shank collision checking
---------------------------------
Tools carry ``flute_length``, ``stickout`` (tip to holder face), ``shank_diameter`` and
``holder_diameter``. After linking, the program is replayed on an in-process stock model: wherever the
shank (above the flutes) or the holder would touch stock or part, and wherever a rapid move would pass
through material, a collision is reported with its location.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import sketch2d
from .geometry_mesh import TriMesh

RAPID_RATE = 6000.0  # mm/min used for time estimates
TOOL_CHANGE_S = 6.0

# --------------------------------------------------------------------------- schemas
def _p(name, label, typ="number", default=None, **kw):
    return {"name": name, "label": label, "type": typ, "default": default, **kw}


_COMMON = [
    _p("tool", "Tool", "tool", 1),
    _p("feed", "Feed override (mm/min, 0 = tool)", default=0),
    _p("plunge_feed", "Plunge override (0 = tool)", default=0),
    _p("rpm", "RPM override (0 = tool)", default=0),
]

_REST = [
    _p("rest", "Rest machining (only where earlier ops left material)", "bool", False),
    _p("rest_threshold", "Rest: ignore material thinner than (mm)", default=0.05),
]

OP_SCHEMAS: dict[str, dict] = {
    "face": {"label": "Face mill", "params": _COMMON + [
        _p("z_final", "Final Z (blank = part top)", default=None), _p("stepdown", "Stepdown", default=1.0),
        _p("stepover", "Stepover (fraction of dia)", default=0.7), _p("angle", "Direction", "select", 0, options=[0, 90])]},
    "profile": {"label": "Contour / profile", "params": _COMMON + [
        _p("source", "Outline source", "select", "silhouette", options=["silhouette", "section", "sketch"]),
        _p("sketch_id", "Sketch (XY plane)", "sketch", None), _p("section_z", "Section Z", default=None),
        _p("side", "Side", "select", "outside", options=["outside", "inside", "on", "both"]),
        _p("z_top", "Top Z (blank = stock top)", default=None), _p("z_bottom", "Bottom Z (blank = part bottom)", default=None),
        _p("stepdown", "Stepdown", default=2.0), _p("allowance", "Radial stock to leave", default=0.0),
        _p("finish", "Finish pass at 0 allowance", "bool", False),
        _p("climb", "Climb milling", "bool", True), _p("ramp_deg", "Ramp angle (deg)", default=3.0)] + _REST},
    "pocket": {"label": "Pocket / area clear", "params": _COMMON + [
        _p("source", "Region source", "select", "auto", options=["auto", "sketch", "section"]),
        _p("sketch_id", "Sketch (XY plane)", "sketch", None), _p("section_z", "Section Z", default=None),
        _p("z_floor", "Floor Z (sketch/section)", default=None), _p("z_top", "Top Z (blank = stock top)", default=None),
        _p("stepdown", "Stepdown", default=2.0), _p("stepover", "Stepover (fraction of dia)", default=0.45),
        _p("allowance", "Radial stock to leave", default=0.0), _p("floor_allowance", "Floor stock to leave", default=0.0),
        _p("finish_walls", "Wall finish pass", "bool", True), _p("climb", "Climb milling", "bool", True),
        _p("ramp_deg", "Ramp angle (deg)", default=3.0), _p("open_edges", "Clear open sides", "bool", True)] + _REST},
    "drill": {"label": "Drill / hole milling", "params": _COMMON + [
        _p("source", "Hole source", "select", "auto", options=["auto", "sketch", "points"]),
        _p("sketch_id", "Sketch circles (XY)", "sketch", None), _p("points", "Points [[x,y],..]", "points2", []),
        _p("z_top", "Top Z (blank = detected)", default=None), _p("depth", "Depth (blank = detected)", default=None),
        _p("peck", "Peck depth (0 = no peck)", default=0.0), _p("dwell", "Dwell (s)", default=0.0),
        _p("stepdown", "Helical pitch", default=1.0), _p("min_diameter", "Min hole dia", default=0.0),
        _p("max_diameter", "Max hole dia (0 = any)", default=0.0)]},
    "waterline": {"label": "3D roughing (waterline)", "params": _COMMON + [
        _p("z_top", "Top Z (blank = stock top)", default=None), _p("z_bottom", "Bottom Z (blank = part bottom)", default=None),
        _p("stepdown", "Stepdown", default=2.0), _p("stepover", "Stepover (fraction of dia)", default=0.5),
        _p("allowance", "Stock to leave", default=0.3), _p("climb", "Climb milling", "bool", True),
        _p("ramp_deg", "Ramp angle (deg)", default=3.0)] + _REST},
    "raster": {"label": "3D finishing (parallel)", "params": _COMMON + [
        _p("stepover", "Stepover (fraction of dia)", default=0.1), _p("angle", "Direction", "select", 0, options=[0, 90]),
        _p("allowance", "Stock to leave", default=0.0), _p("tolerance", "Path tolerance", default=0.02),
        _p("grid", "Height-field resolution (0 = auto)", default=0.0), _p("z_floor", "Lowest Z (blank = part bottom)", default=None)] + _REST},
}

DEFAULT_TOOLS = [
    {"id": 1, "name": "6 mm flat end mill", "type": "flat", "diameter": 6.0, "flutes": 3, "rpm": 12000, "feed": 1200, "plunge_feed": 400,
     "flute_length": 18.0, "stickout": 30.0, "shank_diameter": 6.0, "holder_diameter": 25.0},
    {"id": 2, "name": "6 mm ball end mill", "type": "ball", "diameter": 6.0, "flutes": 2, "rpm": 14000, "feed": 1500, "plunge_feed": 400,
     "flute_length": 18.0, "stickout": 30.0, "shank_diameter": 6.0, "holder_diameter": 25.0},
    {"id": 3, "name": "5 mm drill", "type": "drill", "diameter": 5.0, "flutes": 2, "rpm": 6000, "feed": 300, "plunge_feed": 300,
     "flute_length": 40.0, "stickout": 55.0, "shank_diameter": 5.0, "holder_diameter": 25.0},
    {"id": 4, "name": "8 mm drill", "type": "drill", "diameter": 8.0, "flutes": 2, "rpm": 4000, "feed": 250, "plunge_feed": 250,
     "flute_length": 50.0, "stickout": 65.0, "shank_diameter": 8.0, "holder_diameter": 25.0},
]


def tool_geometry(tool: dict) -> dict:
    """Cutter / shank / holder envelope (defaults when a tool does not define them)."""
    D = float(tool["diameter"])
    fl = float(tool.get("flute_length") or 3 * D)
    so = max(float(tool.get("stickout") or fl + 2 * D), fl)
    return {"flute_length": fl, "stickout": so, "shank_diameter": float(tool.get("shank_diameter") or D),
            "holder_diameter": float(tool.get("holder_diameter") or max(2.5 * D, D + 10))}


# ----------------------------------------------------------------------------- types
@dataclass
class Pass:
    pts: list  # [(x, y, z), ...]
    top: Optional[float] = None  # top of the layer being cut: used for a safe approach
    allowed: Any = None  # shapely geometry in which straight feed links are safe


@dataclass
class Drill:
    x: float
    y: float
    z_top: float
    z_bottom: float
    peck: float = 0.0
    dwell: float = 0.0


@dataclass
class Stock:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    @property
    def poly(self) -> Polygon:
        return box(self.xmin, self.ymin, self.xmax, self.ymax)

    def as_dict(self) -> dict:
        return {"min": [self.xmin, self.ymin, self.zmin], "max": [self.xmax, self.ymax, self.zmax]}


@dataclass
class Ctx:
    mesh: TriMesh
    stock: Stock
    faces: list
    holes: list
    sketches: dict
    clearance: float = 3.0
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------------------ geometry utils
def polys(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if hasattr(g, "geoms"):
        out = []
        for x in g.geoms:
            out += polys(x)
        return out
    return []


def lines(g) -> list[LineString]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, LineString):
        return [g]
    if isinstance(g, MultiLineString):
        return list(g.geoms)
    if hasattr(g, "geoms"):
        out = []
        for x in g.geoms:
            out += lines(x)
        return out
    return []


class RingPath:
    """A closed polyline addressed by arc length."""

    def __init__(self, coords):
        pts = np.array(coords, dtype=float)[:, :2]
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        self.pts = pts
        n = len(pts)
        nxt = np.roll(pts, -1, axis=0)
        self.L = np.hypot(*(nxt - pts).T)
        self.cum = np.concatenate([[0.0], np.cumsum(self.L)])
        self.perim = float(self.cum[-1])
        self.n = n

    def at(self, s: float) -> np.ndarray:
        s = s % self.perim if self.perim > 0 else 0.0
        i = int(np.searchsorted(self.cum, s, side="right") - 1)
        i = min(i, self.n - 1)
        t = (s - self.cum[i]) / self.L[i] if self.L[i] > 0 else 0.0
        return self.pts[i] + (self.pts[(i + 1) % self.n] - self.pts[i]) * t

    def walk(self, s0: float, length: float) -> list[tuple[float, float, float]]:
        """Vertices (x, y, s) from arc length s0 to s0+length (inclusive ends, wraps around)."""
        s_end = s0 + length
        laps = int(math.ceil(s_end / self.perim)) + 1
        marks = [s0]
        for lap in range(laps + 1):
            for c in self.cum[:-1]:
                v = c + lap * self.perim
                if s0 + 1e-9 < v < s_end - 1e-9:
                    marks.append(float(v))
        marks.append(s_end)
        marks = sorted(set(marks))
        return [(*self.at(m), m) for m in marks]


def helical_chain(ring, z_top: float, levels: list[float], slope_deg: float, max_laps: int = 12) -> list[tuple]:
    """One continuous path: ramp down to each level over part of a lap, then a full lap at depth."""
    rp = RingPath(ring)
    if rp.perim < 1e-6:
        return []
    tan = math.tan(math.radians(max(slope_deg, 0.2)))
    s, z, out = 0.0, z_top, []
    for L in levels:
        dz = z - L
        if dz > 1e-9:
            need = min(dz / tan, max_laps * rp.perim)
            for x, y, sm in rp.walk(s, need):
                zz = z - dz * (sm - s) / need
                if not out or (abs(out[-1][0] - x) > 1e-9 or abs(out[-1][1] - y) > 1e-9 or abs(out[-1][2] - zz) > 1e-9):
                    out.append((float(x), float(y), float(zz)))
            s += need
        for x, y, sm in rp.walk(s, rp.perim):
            if not out or (abs(out[-1][0] - x) > 1e-9 or abs(out[-1][1] - y) > 1e-9 or abs(out[-1][2] - L) > 1e-9):
                out.append((float(x), float(y), float(L)))
        s += rp.perim
        z = L
    return out


def level_list(z_top: float, z_bottom: float, stepdown: float, mandatory: Optional[list[float]] = None) -> list[float]:
    """Descending cut levels (bottom of each layer) from z_top down to z_bottom."""
    total = z_top - z_bottom
    if total <= 1e-9:
        return []
    stepdown = max(stepdown, 0.05)
    n = max(1, math.ceil(total / stepdown - 1e-9))
    lv = {round(z_top - total * (i + 1) / n, 6) for i in range(n)}
    for m in mandatory or []:
        if z_bottom - 1e-9 <= m < z_top - 1e-9:
            lv.add(round(m, 6))
    levels = sorted(lv, reverse=True)
    # re-split any gap that became larger than the stepdown
    out, prev = [], z_top
    for L in levels:
        gap = prev - L
        k = max(1, math.ceil(gap / stepdown - 1e-9))
        for i in range(1, k + 1):
            out.append(round(prev - gap * i / k, 6))
        prev = L
    return out


def orient_region(geom, climb: bool):
    """Orient polygons so exteriors run CW / islands CCW for climb milling (material to the left)."""
    return [orient(p, sign=-1.0 if climb else 1.0) for p in polys(geom)]


def nearest_start(coords: list, pos: Optional[tuple]) -> list:
    """Rotate a closed ring so it starts at the vertex closest to ``pos``."""
    if pos is None or len(coords) < 4:
        return coords
    pts = coords[:-1] if coords[0] == coords[-1] else coords
    arr = np.array(pts)
    i = int(np.argmin(np.hypot(arr[:, 0] - pos[0], arr[:, 1] - pos[1])))
    rot = pts[i:] + pts[:i]
    return rot + [rot[0]]


# --------------------------------------------------------------------------- stock
def compute_stock(setup_stock: dict, mesh: TriMesh) -> Stock:
    s = setup_stock or {}
    if s.get("min") and s.get("max"):
        return Stock(*s["min"], *s["max"])
    m = s.get("margin", [2.0, 2.0])
    top_extra = float(s.get("top_extra", 1.0))
    return Stock(
        mesh.xmin - m[0], mesh.ymin - m[1], mesh.zmin,
        mesh.xmax + m[0], mesh.ymax + m[1], mesh.zmax + top_extra,
    )


# ---------------------------------------------------------------------------- ops
def _zigzag(poly_bounds, y_step, x_ext, z, angle, top, allowed):
    xmin, ymin, xmax, ymax = poly_bounds
    out = []
    ys = list(np.arange(ymin, ymax, y_step)) + [ymax]
    for k, y in enumerate(ys):
        a, b = (xmin - x_ext, xmax + x_ext)
        seq = [(a, y), (b, y)] if k % 2 == 0 else [(b, y), (a, y)]
        if angle == 90:
            seq = [(yy, xx) for xx, yy in seq]  # transpose: lines run along Y
        out.append(Pass([(px, py, z) for px, py in seq], top=top, allowed=allowed))
    return out


def op_face(op, tool, ctx: Ctx) -> list[Pass]:
    p = op["params"]
    r, D = tool["diameter"] / 2, tool["diameter"]
    st = ctx.stock
    z_final = p.get("z_final")
    z_final = ctx.mesh.zmax if z_final in (None, "") else float(z_final)
    if z_final >= st.zmax - 1e-6:
        ctx.warnings.append(f"{op['id']}: nothing to face (final Z is at/above stock top)")
        return []
    levels = level_list(st.zmax, z_final, float(p.get("stepdown", 1.0)))
    step = float(p.get("stepover", 0.7)) * D
    angle = int(p.get("angle", 0))
    outer = box(st.xmin - r - 3, st.ymin - r - 3, st.xmax + r + 3, st.ymax + r + 3)
    allowed = outer.difference(st.poly.buffer(0.01))
    b = (st.xmin, st.ymin, st.xmax, st.ymax) if angle == 0 else (st.ymin, st.xmin, st.ymax, st.xmax)
    passes: list[Pass] = []
    for z in levels:
        passes += _zigzag(b, step, r + 1.0, z, angle, z, allowed)
    return passes


def _region_from_source(p, ctx: Ctx):
    src = p.get("source", "silhouette")
    if src == "sketch":
        sk = ctx.sketches.get(int(p["sketch_id"])) if p.get("sketch_id") not in (None, "") else None
        if not sk:
            raise ValueError("sketch not found")
        if sk.get("plane", "XY") != "XY":
            raise ValueError("CAM sketches must be on the XY plane")
        return sketch2d.sketch_region(sk["entities"])
    if src == "section":
        z = p.get("section_z")
        z = (ctx.mesh.zmin + ctx.mesh.zmax) / 2 if z in (None, "") else float(z)
        return ctx.mesh.section(z)
    return ctx.mesh.silhouette()


def op_profile(op, tool, ctx: Ctx) -> list[Pass]:
    p = op["params"]
    r = tool["diameter"] / 2
    region = _region_from_source(p, ctx)
    if region.is_empty:
        raise ValueError("profile region is empty")
    climb = bool(p.get("climb", True))
    allow = float(p.get("allowance", 0) or 0)
    st = ctx.stock
    z_top = st.zmax if p.get("z_top") in (None, "") else float(p["z_top"])
    z_bot = ctx.mesh.zmin if p.get("z_bottom") in (None, "") else float(p["z_bottom"])
    levels = level_list(z_top, z_bot, float(p.get("stepdown", 2.0)))
    if not levels:
        return []
    side = p.get("side", "outside")
    slope = float(p.get("ramp_deg", 3.0))

    def rings_for(extra: float):
        out = []
        if side in ("outside", "both"):
            for pg in polys(region):
                grown = Polygon(pg.exterior).buffer(r + extra)
                for q in orient_region(grown, climb=False):  # CCW: part on the left -> climb
                    out.append(list(q.exterior.coords))
        if side in ("inside", "both"):
            for pg in polys(region):
                for hole in pg.interiors:
                    shrunk = Polygon(hole).buffer(-(r + extra))
                    for q in orient_region(shrunk, climb=True):  # CW: material outside on the left
                        out.append(list(q.exterior.coords))
        if side == "on":
            for pg in polys(region):
                q = orient(pg, sign=1.0)
                out.append(list(q.exterior.coords))
                out += [list(i.coords) for i in q.interiors]
        if not climb:
            out = [list(reversed(c)) for c in out]
        return out

    passes: list[Pass] = []
    pos = None
    for ring in rings_for(allow):
        ring = nearest_start(ring, pos)
        pts = helical_chain(ring, z_top, levels, slope)
        if pts:
            passes.append(Pass(pts, top=z_top))
            pos = pts[-1][:2]
    if allow > 0 and p.get("finish"):
        for ring in rings_for(0.0):
            ring = nearest_start(ring, pos)
            pts = helical_chain(ring, z_top, [levels[-1]], slope)
            if pts:
                passes.append(Pass(pts, top=z_top))
                pos = pts[-1][:2]
    return passes


# ------------------------------------------------------------------ area clearing
def _pocket_floors(op, tool, ctx: Ctx, z_top: float) -> list[tuple[Any, float]]:
    p = op["params"]
    src = p.get("source", "auto")
    r = tool["diameter"] / 2
    out: list[tuple[Any, float]] = []
    if src == "auto":
        for f in ctx.faces:
            if f["type"] != "PLANE" or f["normal"][2] < 0.98:
                continue
            zf = f["center"][2]
            if zf >= z_top - 0.01 or zf <= ctx.mesh.zmin + 0.01:
                continue
            region = ctx.mesh.triangles_region(f["tri_start"], f["tri_count"])
            if region.is_empty or region.area < 1e-3:
                continue
            # holes in the floor face are either bosses (material above the floor: keep as islands) or holes
            # going down through the floor (empty above it): those must be cleared too, or a column of stock is left
            above = ctx.mesh.section(zf + 0.05)
            filled = []
            for pg in polys(region):
                islands = [ring for ring in pg.interiors
                           if not above.is_empty and Polygon(ring).intersection(above).area > 0.5 * Polygon(ring).area]
                filled.append(Polygon(pg.exterior, islands))
            out.append((unary_union(filled), zf))
    else:
        region = _region_from_source(p, ctx)
        zf = p.get("z_floor")
        if zf in (None, ""):
            raise ValueError("set a floor Z for sketch/section pockets")
        out.append((region, float(zf)))
    if out and p.get("open_edges", True):
        stock_poly = ctx.stock.poly
        ext = []
        for region, zf in out:
            band = region.buffer(2 * r + 1).difference(stock_poly)
            ext.append((unary_union([region, band]) if not band.is_empty else region, zf))
        out = ext
    return out


def clear_levels(regions: list[tuple[Any, float]], tool, z_top: float, p: dict, ctx: Ctx, op_id: str) -> list[Pass]:
    """Offset-ring clearing of (region, floor_z) pairs; shared by pocket and milled holes."""
    r, D = tool["diameter"] / 2, tool["diameter"]
    step = max(float(p.get("stepover", 0.45)) * D, 0.05)
    allow = float(p.get("allowance", 0) or 0)
    fl_allow = float(p.get("floor_allowance", 0) or 0)
    climb = bool(p.get("climb", True))
    slope = float(p.get("ramp_deg", 3.0))
    floors = [(reg, zf + fl_allow) for reg, zf in regions]
    z_min = min(zf for _, zf in floors)
    levels = level_list(z_top, z_min, float(p.get("stepdown", 2.0)), [zf for _, zf in floors])
    passes: list[Pass] = []
    prev = z_top
    pos = None
    for L in levels:
        active = [reg for reg, zf in floors if zf <= L + 1e-6]
        top = prev
        prev = L
        if not active:
            continue
        region_L = unary_union(active)
        A = region_L.buffer(-(r + allow))
        if A.is_empty:
            ctx.warnings.append(f"{op_id}: region narrower than the tool at Z={L:.2f}")
            continue
        free = A.buffer(1e-3)
        cur, k = A, 0
        while not cur.is_empty and k < 400:
            for pg in orient_region(cur, climb):
                for ring in [list(pg.exterior.coords)] + [list(i.coords) for i in pg.interiors]:
                    ring = nearest_start(ring, pos)
                    if k == 0:
                        pts = helical_chain(ring, top, [L], slope) if len(ring) > 3 else []
                        if len(ring) > 3 and RingPath(ring).perim < 3.0:  # tiny ring: plunge instead of ramp
                            pts = [(x, y, top) for x, y in ring[:1]] + [(x, y, L) for x, y in ring]
                        pass_ = Pass(pts, top=top, allowed=free)
                    else:
                        pass_ = Pass([(x, y, L) for x, y in ring], top=L, allowed=free)
                    if pass_.pts:
                        passes.append(pass_)
                        pos = pass_.pts[-1][:2]
            k += 1
            cur = A.buffer(-k * step)
        if allow > 0 and p.get("finish_walls", True):
            W = region_L.buffer(-r)
            for pg in orient_region(W, climb):
                for ring in [list(pg.exterior.coords)] + [list(i.coords) for i in pg.interiors]:
                    ring = nearest_start(ring, pos)
                    pts = helical_chain(ring, top, [L], slope)
                    if pts:
                        passes.append(Pass(pts, top=top, allowed=W.buffer(1e-3)))
                        pos = pts[-1][:2]
    return passes


def op_pocket(op, tool, ctx: Ctx) -> list[Pass]:
    p = op["params"]
    z_top = ctx.stock.zmax if p.get("z_top") in (None, "") else float(p["z_top"])
    regions = _pocket_floors(op, tool, ctx, z_top)
    if not regions:
        ctx.warnings.append(f"{op['id']}: no pocket floors found (flat upward faces below the stock top)")
        return []
    p = {**p, "allowance": p.get("allowance", 0) or 0}
    if not p["allowance"]:
        p["finish_walls"] = False
    return clear_levels(regions, tool, z_top, p, ctx, op["id"])


# ---------------------------------------------------------------------- drilling
def detect_holes(shape) -> list[dict]:
    """Vertical cylindrical holes (axis ∥ Z, concave) of a cadquery shape."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    holes: dict[tuple, dict] = {}
    for face in shape.Faces():
        if face.geomType() != "CYLINDER":
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        ax = cyl.Axis()
        d, loc = ax.Direction(), ax.Location()
        if abs(abs(d.Z()) - 1.0) > 1e-6:
            continue
        v = face.Vertices()[0].Center()
        n = face.normalAt(v)
        rad = (v.x - loc.X(), v.y - loc.Y())
        if n.x * rad[0] + n.y * rad[1] >= 0:  # normal points away from axis -> boss, not hole
            continue
        bb = face.BoundingBox()
        key = (round(loc.X(), 3), round(loc.Y(), 3), round(cyl.Radius(), 3))
        h = holes.setdefault(key, {"x": loc.X(), "y": loc.Y(), "diameter": 2 * cyl.Radius(), "z_top": bb.zmax, "z_bottom": bb.zmin})
        h["z_top"] = max(h["z_top"], bb.zmax)
        h["z_bottom"] = min(h["z_bottom"], bb.zmin)
    return sorted(holes.values(), key=lambda h: (round(h["y"], 1), round(h["x"], 1)))


def op_drill(op, tool, ctx: Ctx) -> list:
    p = op["params"]
    D = tool["diameter"]
    src = p.get("source", "auto")
    cand: list[dict] = []
    if src == "auto":
        cand = [dict(h) for h in ctx.holes]
    elif src == "sketch":
        sk = ctx.sketches.get(int(p["sketch_id"])) if p.get("sketch_id") not in (None, "") else None
        if not sk:
            raise ValueError("sketch not found")
        for e in sk["entities"]:
            if e["type"] == "circle":
                cand.append({"x": e["c"][0], "y": e["c"][1], "diameter": 2 * e["r"], "z_top": ctx.stock.zmax, "z_bottom": ctx.mesh.zmin})
            elif e["type"] == "point":
                cand.append({"x": e["p"][0], "y": e["p"][1], "diameter": D, "z_top": ctx.stock.zmax, "z_bottom": ctx.mesh.zmin})
    else:
        for x, y in p.get("points") or []:
            cand.append({"x": float(x), "y": float(y), "diameter": D, "z_top": ctx.stock.zmax, "z_bottom": ctx.mesh.zmin})
    lo, hi = float(p.get("min_diameter") or 0), float(p.get("max_diameter") or 0)
    cand = [h for h in cand if h["diameter"] >= lo and (hi <= 0 or h["diameter"] <= hi)]
    if not cand:
        ctx.warnings.append(f"{op['id']}: no holes found")
        return []
    out: list = []
    tip = 0.3 * D if tool["type"] == "drill" else 0.0
    zt_override = p.get("z_top")
    for h in cand:
        z_top = float(zt_override) if zt_override not in (None, "") else max(h["z_top"], ctx.stock.zmax if h["z_top"] >= ctx.mesh.zmax - 1e-6 else h["z_top"])
        depth = p.get("depth")
        z_bot = z_top - float(depth) if depth not in (None, "") else h["z_bottom"]
        through = z_bot <= ctx.mesh.zmin + 1e-6
        if through and tool["type"] == "drill":
            z_bot -= tip
        if tool["type"] == "drill":
            if h["diameter"] > D * 1.05:
                ctx.warnings.append(f"{op['id']}: hole Ø{h['diameter']:.2f} is larger than drill Ø{D:.2f} (skipped; use a milling tool)")
                continue
            out.append(Drill(h["x"], h["y"], z_top, z_bot, float(p.get("peck") or 0), float(p.get("dwell") or 0)))
        else:
            if h["diameter"] < D * 1.02:
                out.append(Drill(h["x"], h["y"], z_top, z_bot, float(p.get("peck") or 0), float(p.get("dwell") or 0)))
                continue
            circle = Point(h["x"], h["y"]).buffer(h["diameter"] / 2, quad_segs=12)
            sub = {"stepdown": p.get("stepdown", 1.0), "stepover": 0.45, "climb": True, "ramp_deg": 3.0, "allowance": 0, "finish_walls": False}
            out += clear_levels([(circle, z_bot)], tool, z_top, sub, ctx, op["id"])
    return out


# --------------------------------------------------------------- 3-axis roughing
def op_waterline(op, tool, ctx: Ctx) -> list[Pass]:
    p = op["params"]
    r, D = tool["diameter"] / 2, tool["diameter"]
    st, mesh = ctx.stock, ctx.mesh
    z_top = st.zmax if p.get("z_top") in (None, "") else float(p["z_top"])
    z_bot = mesh.zmin if p.get("z_bottom") in (None, "") else float(p["z_bottom"])
    z_bot += float(p.get("floor_allowance", 0) or 0)
    sd = float(p.get("stepdown", 2.0))
    step = max(float(p.get("stepover", 0.5)) * D, 0.05)
    allow = float(p.get("allowance", 0.3) or 0)
    climb = bool(p.get("climb", True))
    slope = float(p.get("ramp_deg", 3.0))
    levels = level_list(z_top, z_bot, sd)
    sp = st.poly
    clip = sp.buffer(0.02)
    outer = box(st.xmin - r - 3, st.ymin - r - 3, st.xmax + r + 3, st.ymax + r + 3)
    air = outer.difference(sp.buffer(0.01))
    passes: list[Pass] = []
    shadow = Polygon()
    prev = z_top
    pos = None
    for L in levels:
        top = prev
        prev = L
        secs = [mesh.section(L + 1e-3), mesh.section(L + (top - L) / 2), mesh.section(top - 1e-3)]
        shadow = unary_union([shadow] + [s for s in secs if not s.is_empty])
        if shadow.is_empty:  # nothing of the part at this height: plain facing of the layer
            passes += _zigzag((st.xmin, st.ymin, st.xmax, st.ymax), step, r + 1.0, L, 0, L, air)
            continue
        free_area = sp.buffer(r).difference(shadow.buffer(r + allow - 1e-3))
        k = 0
        while k < 500:
            d = r + allow + k * step
            grown = shadow.buffer(d)
            if not lines(grown.boundary.intersection(clip)):
                break  # rings have left the stock: layer is fully covered
            oriented = []
            for pg in orient_region(grown, climb):  # exteriors CW / islands CCW => climb
                oriented.append(pg.exterior)
                oriented += list(pg.interiors)
            for ln in oriented:
                if ln.within(clip):  # closed ring fully inside the stock -> ramp into material
                    coords = nearest_start(list(ln.coords), pos)
                    pts = helical_chain(coords, top, [L], slope)
                    if pts:
                        passes.append(Pass(pts, top=top, allowed=free_area.buffer(1e-3)))
                        pos = pts[-1][:2]
                    continue
                segs = [list(sg.coords) for sg in lines(ln.intersection(clip)) if len(sg.coords) >= 2]
                while segs:  # greedy: next segment whose START is nearest (direction is kept for climb milling)
                    if pos is None:
                        j = 0
                    else:
                        j = min(range(len(segs)), key=lambda q: math.hypot(segs[q][0][0] - pos[0], segs[q][0][1] - pos[1]))
                    c = segs.pop(j)
                    dx, dy = c[0][0] - c[1][0], c[0][1] - c[1][1]
                    nrm = math.hypot(dx, dy) or 1.0
                    entry = (c[0][0] + dx / nrm * (r + 1.0), c[0][1] + dy / nrm * (r + 1.0))
                    pts = [(entry[0], entry[1], L)] + [(x, y, L) for x, y in c]
                    passes.append(Pass(pts, top=L, allowed=free_area.buffer(1e-3)))
                    pos = pts[-1][:2]
            k += 1
    return passes


# ------------------------------------------------------------ 3-axis finishing
def op_raster(op, tool, ctx: Ctx) -> list[Pass]:
    p = op["params"]
    r, D = tool["diameter"] / 2, tool["diameter"]
    mesh = ctx.mesh
    step = max(float(p.get("stepover", 0.1)) * D, 0.02)
    allow = float(p.get("allowance", 0) or 0)
    tol = float(p.get("tolerance", 0.02))
    angle = int(p.get("angle", 0))
    floor = mesh.zmin if p.get("z_floor") in (None, "") else float(p["z_floor"])
    res = float(p.get("grid") or 0) or max(0.1, min(step / 3, 0.25))
    x0, y0 = mesh.xmin - r - 1, mesh.ymin - r - 1
    w, h = mesh.xmax - mesh.xmin + 2 * r + 2, mesh.ymax - mesh.ymin + 2 * r + 2
    while (w / res) * (h / res) > 2.5e6:
        res *= 1.5
    nx, ny = int(math.ceil(w / res)), int(math.ceil(h / res))
    H = mesh.heightfield(x0, y0, nx, ny, res)
    covered = np.isfinite(H)
    Hf = np.where(covered, H, floor)
    k = int(math.ceil(r / res))
    yy, xx = np.mgrid[-k : k + 1, -k : k + 1]
    dist = np.hypot(xx, yy) * res
    inside = dist <= r + 1e-9
    if tool["type"] == "ball":
        prof = r - np.sqrt(np.maximum(r * r - dist * dist, 0.0))
    else:
        prof = np.zeros_like(dist)
    Z = ndimage.grey_dilation(Hf, footprint=inside, structure=np.where(inside, -prof, 0.0))
    Z = np.maximum(Z + allow, floor)
    mask = ndimage.binary_dilation(covered, structure=inside)
    if angle == 90:
        Z, mask = Z.T, mask.T
        nx, ny = ny, nx
        xo, yo = y0, x0
    else:
        xo, yo = x0, y0
    stride = max(1, int(round(step / res)))
    sil = mesh.silhouette().buffer(r + res)
    allowed = box(mesh.xmin - r - 3, mesh.ymin - r - 3, mesh.xmax + r + 3, mesh.ymax + r + 3).difference(sil)
    passes: list[Pass] = []
    flip = False
    for j in range(0, ny, stride):
        row_m, row_z = mask[j], Z[j]
        idx = np.nonzero(row_m)[0]
        if idx.size == 0:
            continue
        runs = np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1)
        for run in runs:
            i0, i1 = max(run[0] - 1, 0), min(run[-1] + 1, nx - 1)
            ii = np.arange(i0, i1 + 1)
            s = (ii + 0.5) * res
            zz = row_z[ii]
            ls = LineString(np.column_stack([s, zz])).simplify(tol, preserve_topology=False)
            sc = np.array(ls.coords)
            ycoord = yo + (j + 0.5) * res
            if angle == 90:
                pts = [(ycoord, xo + sv, zv) for sv, zv in sc]
            else:
                pts = [(xo + sv, ycoord, zv) for sv, zv in sc]
            if flip:
                pts = pts[::-1]
            passes.append(Pass(pts, top=None, allowed=allowed))
        flip = not flip
    return passes


OP_FUNCS: dict[str, Callable] = {
    "face": op_face, "profile": op_profile, "pocket": op_pocket, "drill": op_drill,
    "waterline": op_waterline, "raster": op_raster,
}


# ------------------------------------------------------------------------- linking
class Linker:
    def __init__(self, z_clear: float, safe_z: float, link_max: float):
        self.ev: list[dict] = []
        self.z_clear, self.safe_z, self.link_max = z_clear, safe_z, link_max
        self.pos: Optional[tuple] = None
        self.op = ""
        self.tool_no = 0

    def _m(self, kind: str, x: float, y: float, z: float):
        self.ev.append({"t": "move", "k": kind, "x": x, "y": y, "z": z, "op": self.op, "tool": self.tool_no})
        self.pos = (x, y, z)

    def tool_change(self, tool: dict, op_id: str):
        if self.pos is not None:
            self._m("rapid", self.pos[0], self.pos[1], self.safe_z)
        self.ev.append({"t": "tool", "tool": tool, "op": op_id})
        self.tool_no = int(tool["id"])
        self.op = op_id
        if self.pos is not None:
            self.pos = (self.pos[0], self.pos[1], self.safe_z)

    def retract(self):
        if self.pos and self.pos[2] < self.z_clear - 1e-9:
            self._m("rapid", self.pos[0], self.pos[1], self.z_clear)

    def start_pass(self, ps: Pass):
        s = ps.pts[0]
        if self.pos is None:
            self._m("rapid", s[0], s[1], self.safe_z)
        else:
            dist = math.hypot(s[0] - self.pos[0], s[1] - self.pos[1])
            cur_z = self.pos[2]
            can_link = False
            if ps.allowed is not None and dist <= self.link_max and cur_z < self.z_clear - 1e-6:
                try:
                    can_link = LineString([self.pos[:2], s[:2]]).within(ps.allowed) if dist > 1e-9 else True
                except Exception:
                    can_link = False
            if can_link:
                if s[2] > cur_z + 1e-9:
                    self._m("cut", self.pos[0], self.pos[1], s[2])
                self._m("cut", s[0], s[1], max(cur_z, s[2]))
                if s[2] < cur_z - 1e-9:
                    self._m("plunge", s[0], s[1], s[2])
                return
            self.retract()
            self._m("rapid", s[0], s[1], max(self.pos[2], self.z_clear))
        approach = min((ps.top if ps.top is not None else s[2]) + 0.5, self.z_clear)
        if approach > s[2] + 1e-9:
            self._m("rapid", s[0], s[1], approach)
            self._m("plunge", s[0], s[1], s[2])
        else:
            self._m("rapid", s[0], s[1], s[2])

    def do_pass(self, ps: Pass):
        if len(ps.pts) < 1:
            return
        self.start_pass(ps)
        for x, y, z in ps.pts[1:]:
            self._m("cut", x, y, z)

    def do_drill(self, d: Drill):
        self.ev.append({"t": "drill", "x": d.x, "y": d.y, "z_top": d.z_top, "z_bottom": d.z_bottom,
                        "peck": d.peck, "dwell": d.dwell, "op": self.op, "tool": self.tool_no, "z_retract": self.z_clear})
        self.pos = (d.x, d.y, self.z_clear)

    def finish(self):
        if self.pos:
            self._m("rapid", self.pos[0], self.pos[1], self.safe_z)


# ---------------------------------------------------------------- statistics / view
def _feed_of(tool: dict, op: dict, kind: str) -> float:
    p = op.get("params", {}) if op else {}
    if kind == "plunge":
        return float(p.get("plunge_feed") or 0) or float(tool.get("plunge_feed") or tool.get("feed") or 300)
    return float(p.get("feed") or 0) or float(tool.get("feed") or 600)


def build_view(events: list[dict], max_points: int) -> tuple[list[dict], int]:
    """Group moves into polylines per (op, kind)."""
    out: list[dict] = []
    cur = None
    last = None
    total = 0
    for e in events:
        if e["t"] == "drill":
            out.append({"op": e["op"], "kind": "plunge", "pts": [e["x"], e["y"], e["z_top"], e["x"], e["y"], e["z_bottom"]]})
            last = (e["x"], e["y"], e["z_top"])
            cur = None
            continue
        if e["t"] != "move":
            continue
        kind = "rapid" if e["k"] == "rapid" else ("plunge" if e["k"] == "plunge" else "cut")
        pt = (e["x"], e["y"], e["z"])
        if last is None:
            last = pt
            continue
        if cur is None or cur["kind"] != kind or cur["op"] != e["op"]:
            cur = {"op": e["op"], "kind": kind, "pts": list(last)}
            out.append(cur)
        cur["pts"].extend(pt)
        total += 1
        last = pt
        if total > max_points:
            raise ValueError("toolpath too large to display; increase stepover or reduce the region")
    return out, total


def statistics(events: list[dict], ops_by_id: dict, tools: dict) -> dict:
    per: dict[str, dict] = {}
    pos = None
    cut_len = rapid_len = 0.0
    time_s = 0.0
    changes = 0
    for e in events:
        if e["t"] == "tool":
            changes += 1
            time_s += TOOL_CHANGE_S
            continue
        op_id = e.get("op", "")
        row = per.setdefault(op_id, {"op": op_id, "cut_mm": 0.0, "rapid_mm": 0.0, "time_s": 0.0})
        tool = tools.get(e.get("tool"), {"feed": 600})
        if e["t"] == "drill":
            depth = e["z_top"] - e["z_bottom"]
            f = _feed_of(tool, ops_by_id.get(op_id), "plunge")
            t = depth / f * 60 + e["dwell"] + (depth / max(e["peck"], 1e-6) * 0.4 if e["peck"] else 0)
            row["cut_mm"] += depth
            row["time_s"] += t
            cut_len += depth
            time_s += t
            pos = (e["x"], e["y"], e["z_top"])
            continue
        p = (e["x"], e["y"], e["z"])
        if pos is not None:
            d = math.dist(pos, p)
            if e["k"] == "rapid":
                t = d / RAPID_RATE * 60
                row["rapid_mm"] += d
                rapid_len += d
            else:
                f = _feed_of(tool, ops_by_id.get(op_id), e["k"])
                t = d / f * 60
                row["cut_mm"] += d
                cut_len += d
            row["time_s"] += t
            time_s += t
        pos = p
    return {
        "cut_length_mm": cut_len, "rapid_length_mm": rapid_len, "estimated_time_s": time_s,
        "tool_changes": changes, "operations": list(per.values()),
    }


# ----------------------------------------------------------------------- stock model
class StockGrid:
    """Height map of the in-process stock (top surface per XY cell) with cutter stamping."""

    def __init__(self, stock: Stock, res: float = 0.0, max_cells: int = 600_000):
        self.stock = stock
        span_x, span_y = stock.xmax - stock.xmin, stock.ymax - stock.ymin
        res = res or max(0.25, max(span_x, span_y) / 400)
        while (span_x / res) * (span_y / res) > max_cells:
            res *= 1.3
        self.res = res
        self.nx, self.ny = int(math.ceil(span_x / res)), int(math.ceil(span_y / res))
        self.S = np.full((self.ny, self.nx), stock.zmax)
        self._foot: dict[tuple, tuple] = {}
        self._disk: dict[float, tuple] = {}

    def footprint(self, tool: dict) -> tuple[int, np.ndarray]:
        """(half width k, profile) of the cutting end: height of the cutter surface above its tip; inf outside."""
        key = (tool["diameter"], tool["type"])
        if key not in self._foot:
            r = tool["diameter"] / 2
            k = int(math.ceil(r / self.res))
            yy, xx = np.mgrid[-k : k + 1, -k : k + 1]
            d = np.hypot(xx, yy) * self.res
            self._foot[key] = (k, np.where(d <= r + 1e-9, (r - np.sqrt(np.maximum(r * r - d * d, 0.0))) if tool["type"] == "ball" else 0.0, np.inf))
        return self._foot[key]

    def disk(self, radius: float) -> tuple[int, np.ndarray]:
        if radius not in self._disk:
            k = int(math.ceil(radius / self.res))
            yy, xx = np.mgrid[-k : k + 1, -k : k + 1]
            self._disk[radius] = (k, np.hypot(xx, yy) * self.res <= radius + 1e-9)
        return self._disk[radius]

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return int((x - self.stock.xmin) / self.res), int((y - self.stock.ymin) / self.res)

    def _window(self, x, y, k):
        ci, cj = self.cell(x, y)
        i0, i1, j0, j1 = ci - k, ci + k + 1, cj - k, cj + k + 1
        pi0, pj0 = max(0, -i0), max(0, -j0)
        i0c, j0c, i1c, j1c = max(i0, 0), max(j0, 0), min(i1, self.nx), min(j1, self.ny)
        if i1c <= i0c or j1c <= j0c:
            return None
        return (slice(j0c, j1c), slice(i0c, i1c)), (slice(pj0, pj0 + (j1c - j0c)), slice(pi0, pi0 + (i1c - i0c)))

    def stamp(self, x, y, z, tool: dict):
        k, prof = self.footprint(tool)
        w = self._window(x, y, k)
        if w is None:
            return
        win = self.S[w[0]]
        np.minimum(win, z + prof[w[1]], out=win)

    def excess(self, x, y, z, tool: dict) -> float:
        """How far stock rises above the cutter end placed at (x, y, z) (<= 0: the cutter touches nothing)."""
        k, prof = self.footprint(tool)
        w = self._window(x, y, k)
        if w is None:
            return -np.inf
        return float(np.max(self.S[w[0]] - (z + prof[w[1]])))

    def above_disk(self, x, y, radius: float) -> float:
        """Highest stock within `radius` of (x, y)."""
        k, m = self.disk(radius)
        w = self._window(x, y, k)
        if w is None:
            return -np.inf
        win, msk = self.S[w[0]], m[w[1]]
        return float(win[msk].max()) if msk.any() else -np.inf

    def useful_map(self, tool: dict) -> np.ndarray:
        """Per cell: highest (stock - cutter profile) under a cutter centred there (grey dilation)."""
        k, prof = self.footprint(tool)
        inside = np.isfinite(prof)
        return ndimage.grey_dilation(self.S, footprint=inside, structure=np.where(inside, -prof, 0.0), mode="nearest")

    def samples(self, events: list[dict], spacing: float):
        """Yield (event, x, y, z, tool_id) along every feed move, plus one sample per drill hole."""
        prev = None
        for e in events:
            if e["t"] == "drill":
                yield e, e["x"], e["y"], e["z_bottom"]
                prev = (e["x"], e["y"], e.get("z_retract", e["z_top"]))  # canned cycles retract to the clearance plane
            elif e["t"] == "move":
                p = (e["x"], e["y"], e["z"])
                if prev is not None:
                    n = max(1, int(math.dist(prev, p) / spacing))
                    for t in np.linspace(1.0 / n, 1, n):
                        yield e, prev[0] + (p[0] - prev[0]) * t, prev[1] + (p[1] - prev[1]) * t, prev[2] + (p[2] - prev[2]) * t
                prev = p

    def stamp_events(self, events: list[dict], tools: dict, spacing: Optional[float] = None):
        spacing = spacing or self.res * 0.7
        for e, x, y, z in self.samples(events, spacing):
            tool = tools.get(e.get("tool"))
            if tool and not (e["t"] == "move" and e["k"] == "rapid"):
                self.stamp(x, y, z, tool)


def _cut_length(events: list[dict]) -> float:
    total, prev = 0.0, None
    for e in events:
        if e["t"] == "move":
            p = (e["x"], e["y"], e["z"])
            if prev is not None and e["k"] != "rapid":
                total += math.dist(prev, p)
            prev = p
        elif e["t"] == "drill":
            total += e["z_top"] - e["z_bottom"]
            prev = (e["x"], e["y"], e["z_top"])
    return total


# ------------------------------------------------------------------ rest machining
def rest_clip(passes: list, tool: dict, grid: StockGrid, threshold: float) -> tuple[list, int]:
    """Keep only the parts of `passes` where the tool would remove more than `threshold` of stock."""
    M = grid.useful_map(tool)
    res = grid.res
    out: list = []
    kept_pts = 0
    for item in passes:
        if isinstance(item, Drill):
            if grid.excess(item.x, item.y, item.z_bottom, tool) > threshold:
                out.append(item)
            continue
        pts = item.pts
        dense: list[tuple] = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(1, int(math.dist(a[:2], b[:2]) / res))
            dense += [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t) for t in np.linspace(1.0 / n, 1, n)]
        keep = []
        for x, y, z in dense:
            i, j = grid.cell(x, y)
            keep.append(0 <= i < grid.nx and 0 <= j < grid.ny and M[j, i] - z > threshold)
        keep = np.array(keep)
        if not keep.any():
            continue
        keep = keep | np.roll(keep, 1) | np.roll(keep, -1)  # one sample of run-in / run-out
        idx = np.nonzero(keep)[0]
        for run in np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1):
            if len(run) >= 2:
                seg = [dense[i] for i in run]
                out.append(Pass(seg, top=item.top if item.top is not None else max(p[2] for p in seg), allowed=item.allowed))
                kept_pts += len(seg)
    return out, kept_pts


# ------------------------------------------------------------- holder collisions
def check_collisions(events: list[dict], tools: dict, stock: Stock, res: float = 0.0, max_samples: int = 120_000,
                     tol: float = 0.05, limit: int = 200) -> dict:
    """Replay the program on an in-process stock model and report shank / holder / rapid collisions."""
    grid = StockGrid(stock, res or 0.0, max_cells=250_000)
    spacing = max(grid.res * 0.7, _cut_length(events) / max_samples)
    hits: list[dict] = []
    seen: set = set()
    counts = {"holder": 0, "shank": 0, "rapid": 0}
    worst = {"holder": 0.0, "shank": 0.0, "rapid": 0.0}
    # clearance envelopes are shrunk by ~1 grid cell: a cutter running tangent to a wall (how walls are cut)
    # would otherwise "touch" the wall cells the height map quantises into its footprint
    shrink = 1.5 * grid.res
    for e, x, y, z in grid.samples(events, spacing):
        tool = tools.get(e.get("tool"))
        if not tool:
            continue
        g = tool_geometry(tool)
        found = []
        if e["t"] == "move" and e["k"] == "rapid":
            depth = grid.excess(x, y, z, {**tool, "diameter": max(tool["diameter"] - 2 * shrink, grid.res)})
            if depth > tol:
                found.append(("rapid", depth))
        else:
            if g["stickout"] > g["flute_length"] + 1e-9:
                depth = grid.above_disk(x, y, max(g["shank_diameter"] / 2 - shrink, grid.res)) - (z + g["flute_length"])
                if depth > tol:
                    found.append(("shank", depth))
            depth = grid.above_disk(x, y, max(g["holder_diameter"] / 2 - shrink, grid.res)) - (z + g["stickout"])
            if depth > tol:
                found.append(("holder", depth))
        for what, depth in found:
            counts[what] += 1
            worst[what] = max(worst[what], depth)
            key = (what, e.get("op"), round(x / 2), round(y / 2), round(z / 2))
            if key not in seen and len(hits) < limit:
                seen.add(key)
                hits.append({"kind": what, "op": e.get("op"), "tool": e.get("tool"), "x": round(x, 3), "y": round(y, 3), "z": round(z, 3),
                             "depth_mm": round(depth, 3)})
        if not (e["t"] == "move" and e["k"] == "rapid"):
            grid.stamp(x, y, z, tool)
    by_op: dict[str, int] = {}
    for h in hits:
        by_op[h["op"]] = by_op.get(h["op"], 0) + 1
    return {"count": sum(counts.values()), "samples": counts, "worst_mm": worst, "collisions": hits, "by_op": by_op,
            "resolution_mm": grid.res}


# ----------------------------------------------------------------------- simulation
def simulate(events: list[dict], tools: dict, stock: Stock, mesh: TriMesh, res: float = 0.0, max_samples: int = 150_000) -> dict:
    """Height-map (dexel) stock removal simulation compared with the part surface."""
    grid = StockGrid(stock, res)
    res, nx, ny = grid.res, grid.nx, grid.ny
    grid.stamp_events(events, tools, max(res * 0.7, _cut_length(events) / max_samples))
    S = grid.S
    P = mesh.heightfield(stock.xmin, stock.ymin, nx, ny, res)
    covered = np.isfinite(P)
    cell = res * res
    removed = float(((stock.zmax - S).sum()) * cell)
    err = S - np.where(covered, P, stock.zmin)
    tol = 0.02
    # Thin rims along vertical walls are grid-quantisation noise (<= 1 cell); erode them away so only real
    # areas of gouging / leftover material are reported.
    g_mask = ndimage.binary_erosion(covered & (err < -tol), iterations=2, border_value=0)
    l_mask = ndimage.binary_erosion(covered & (err > tol), iterations=2, border_value=0)
    gouge = float(np.where(g_mask, -err, 0).sum() * cell)
    left = float(np.where(l_mask, err, 0).sum() * cell)
    # coarse grid for display
    ds = max(1, int(math.ceil(max(nx, ny) / 90)))
    Sd = S[::ds, ::ds]
    Ed = np.where(covered, err, 0.0)[::ds, ::ds]
    return {
        "removed_volume_mm3": removed,
        "leftover_volume_mm3": left,
        "gouge_volume_mm3": gouge,
        "grid": {
            "nx": int(Sd.shape[1]), "ny": int(Sd.shape[0]), "x0": stock.xmin + 0.5 * res, "y0": stock.ymin + 0.5 * res,
            "dx": res * ds, "dy": res * ds,
            "heights": [round(float(v), 3) for v in Sd.ravel()],
            "error": [round(float(v), 3) for v in Ed.ravel()],
        },
        "resolution_mm": res,
    }


# ------------------------------------------------------------------------ pipeline
def generate(setup: dict, mesh: TriMesh, faces: list, holes: list, sketches: dict, max_points: int = 400_000) -> dict:
    from . import gcode

    stock = compute_stock(setup.get("stock") or {}, mesh)
    post = {**gcode.DEFAULT_POST, **(setup.get("post") or {})}
    ctx = Ctx(mesh, stock, faces, holes, sketches, clearance=float(post.get("clearance", 3.0)))
    tools = {int(t["id"]): t for t in (setup.get("tools") or DEFAULT_TOOLS)}
    lk = Linker(stock.zmax + ctx.clearance, stock.zmax + float(post.get("safe_height", 25.0)), link_max=60.0)
    ops = [o for o in setup.get("operations", []) if o.get("enabled", True)]
    ops_by_id = {o["id"]: o for o in ops}
    errors: list[str] = []
    cur_tool: Optional[int] = None
    rest_grid: Optional[StockGrid] = StockGrid(stock, max_cells=250_000) if any(o["params"].get("rest") for o in ops) else None
    stamped = 0
    for op in ops:
        try:
            t = tools.get(int(op["params"].get("tool", op.get("tool", 1))))
            if t is None:
                raise ValueError("tool not found")
            if op["type"] not in OP_FUNCS:
                raise ValueError(f"unknown operation type {op['type']}")
            tool = dict(t)
            for k in ("rpm", "feed", "plunge_feed"):
                if op["params"].get(k):
                    tool[k] = op["params"][k]
            result = OP_FUNCS[op["type"]](op, tool, ctx)
            if rest_grid is not None and op["params"].get("rest") and result:
                rest_grid.stamp_events(lk.ev[stamped:], tools)  # stock left by all earlier operations
                stamped = len(lk.ev)
                result, n = rest_clip(result, tool, rest_grid, float(op["params"].get("rest_threshold") or 0.05))
                if not result:
                    ctx.warnings.append(f"{op['id']}: rest machining found no material left for this tool")
        except Exception as exc:
            errors.append(f"{op.get('id')}: {type(exc).__name__}: {exc}")
            continue
        if not result:
            continue
        if cur_tool != int(t["id"]):
            lk.tool_change(tool, op["id"])
            cur_tool = int(t["id"])
        lk.op = op["id"]
        for item in result:
            if isinstance(item, Drill):
                lk.do_drill(item)
            else:
                lk.do_pass(item)
    lk.finish()
    stats = statistics(lk.ev, ops_by_id, tools)
    view, npts = build_view(lk.ev, max_points)
    code = gcode.post_process(lk.ev, tools, ops_by_id, post, stock)
    collisions = check_collisions(lk.ev, tools, stock) if setup.get("check_collisions", True) else None
    if collisions and collisions["count"]:
        parts = [f"{n} {k}" for k, n in collisions["samples"].items() if n]
        ctx.warnings.append(f"COLLISION: {', '.join(parts)} sample(s) - see the collision list (worst "
                            f"{max(collisions['worst_mm'].values()):.2f} mm); use a longer stickout or a slimmer holder")
    stats["collisions"] = collisions["count"] if collisions else None
    return {
        "gcode": code, "summary": stats, "toolpaths": view, "warnings": ctx.warnings, "errors": errors,
        "stock": stock.as_dict(), "events": lk.ev, "point_count": npts, "collisions": collisions,
    }
