"""Parametric 3D solid-modelling kernel (OpenCascade through CadQuery).

A *part* is an ordered list of features.  Rebuilding a part replays the feature tree
from the top and returns the resulting B-rep plus a per-feature status, so a failing
feature never destroys the rest of the model.

Numeric parameters may be expressions: any string that starts with ``=`` is evaluated
against the part's named parameters, e.g. ``"=width*2 + 3"``.

Selectors used by features that reference faces/edges:

* any CadQuery selector string (``">Z"``, ``"|Z"``, ``"%CIRCLE"``, ``"#Z"`` ...)
* ``all``
* ``idx:1,4,7``                 explicit indices into ``body.Faces()`` / ``body.Edges()``
* ``near:x,y,z[;x,y,z...]``     entity nearest to each point (what the 3D viewer sends on click)
* ``faceedges:<face selector>`` every edge belonging to the selected faces
* ``ref:[{...}]``               persistent reference (provenance + geometric signature, see toponaming.py);
                                survives renumbering when upstream features change. Viewport picks produce these.
"""
from __future__ import annotations

import ast
import math
import operator
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import cadquery as cq
import numpy as np

from . import sketch2d, toponaming

# ----------------------------------------------------------------- materials
# density kg/m3, E GPa, nu, yield MPa, k W/(m K), specific heat J/(kg K), cte 1/K
MATERIALS: dict[str, dict[str, Any]] = {
    "steel": dict(label="Structural steel", density=7850, E=200, nu=0.29, yield_strength=250, k=50, cp=470, color="#8a96a8"),
    "stainless": dict(label="Stainless 304", density=8000, E=193, nu=0.30, yield_strength=215, k=16, cp=500, color="#b9c0c8"),
    "aluminum": dict(label="Aluminium 6061-T6", density=2700, E=69, nu=0.33, yield_strength=276, k=167, cp=896, color="#c7ccd4"),
    "titanium": dict(label="Titanium Ti-6Al-4V", density=4430, E=114, nu=0.34, yield_strength=880, k=6.7, cp=526, color="#9aa3ad"),
    "brass": dict(label="Brass C360", density=8500, E=100, nu=0.34, yield_strength=200, k=115, cp=380, color="#d6b35a"),
    "copper": dict(label="Copper C110", density=8960, E=117, nu=0.34, yield_strength=70, k=390, cp=385, color="#c8774a"),
    "cast_iron": dict(label="Cast iron", density=7200, E=110, nu=0.28, yield_strength=200, k=52, cp=460, color="#6f7479"),
    "abs": dict(label="ABS plastic", density=1050, E=2.3, nu=0.35, yield_strength=40, k=0.17, cp=1400, color="#e8e4d8"),
    "nylon": dict(label="Nylon PA6", density=1150, E=2.7, nu=0.39, yield_strength=70, k=0.25, cp=1700, color="#efe9d2"),
    "pla": dict(label="PLA", density=1240, E=3.5, nu=0.36, yield_strength=50, k=0.13, cp=1800, color="#d5e3d0"),
}


def material(name: str) -> dict[str, Any]:
    return MATERIALS.get(name, MATERIALS["steel"])


# ----------------------------------------------------- safe expression evaluator
_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS: dict[str, Callable] = {
    "sin": lambda a: math.sin(math.radians(a)), "cos": lambda a: math.cos(math.radians(a)),
    "tan": lambda a: math.tan(math.radians(a)), "asin": lambda a: math.degrees(math.asin(a)),
    "acos": lambda a: math.degrees(math.acos(a)), "atan": lambda a: math.degrees(math.atan(a)),
    "sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "round": round,
    "floor": math.floor, "ceil": math.ceil, "radians": math.radians, "degrees": math.degrees,
}
_CONSTS = {"pi": math.pi, "e": math.e}


def eval_expr(expr: str, params: dict[str, Any]) -> float:
    """Evaluate an arithmetic expression (trig functions use degrees). No attribute access, no calls other than whitelisted ones."""

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id in params:
                return float(resolve_value(params[n.id], params, _depth=1))
            if n.id in _CONSTS:
                return _CONSTS[n.id]
            raise ValueError(f"unknown parameter '{n.id}'")
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError("unsupported expression")

    return ev(ast.parse(expr.strip(), mode="eval"))


def resolve_value(v: Any, params: dict[str, Any], _depth: int = 0) -> Any:
    if _depth > 8:
        raise ValueError("parameter expressions are nested too deeply (cycle?)")
    if isinstance(v, str) and v.startswith("="):
        return eval_expr(v[1:], params)
    if isinstance(v, dict):
        return {k: resolve_value(x, params, _depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [resolve_value(x, params, _depth + 1) for x in v]
    return v


# ----------------------------------------------------------- feature catalogue
def _p(name, label, typ="number", default=None, **kw):
    return {"name": name, "label": label, "type": typ, "default": default, **kw}


OPS = ["add", "cut", "intersect"]
_OP = _p("op", "Operation", "select", "add", options=OPS)
_POS = _p("position", "Position [x,y,z]", "vec3", [0, 0, 0])

FEATURE_SCHEMAS: dict[str, dict[str, Any]] = {
    "box": {"label": "Box", "group": "Primitive", "params": [
        _p("length", "Length (X)", default=50), _p("width", "Width (Y)", default=30), _p("height", "Height (Z)", default=20),
        _POS, _p("centered", "Centered", "bool", False), _OP]},
    "cylinder": {"label": "Cylinder", "group": "Primitive", "params": [
        _p("radius", "Radius", default=10), _p("height", "Height", default=30),
        _p("axis", "Axis", "select", "Z", options=["X", "Y", "Z"]), _POS, _p("centered", "Centered", "bool", False), _OP]},
    "sphere": {"label": "Sphere", "group": "Primitive", "params": [_p("radius", "Radius", default=15), _POS, _OP]},
    "cone": {"label": "Cone", "group": "Primitive", "params": [
        _p("radius1", "Bottom radius", default=15), _p("radius2", "Top radius", default=5), _p("height", "Height", default=25),
        _p("axis", "Axis", "select", "Z", options=["X", "Y", "Z"]), _POS, _OP]},
    "extrude": {"label": "Extrude", "group": "Sketch", "params": [
        _p("sketch_id", "Sketch", "sketch"), _p("distance", "Distance", default=10),
        _p("direction", "Direction", "select", "normal", options=["normal", "reverse", "symmetric"]),
        _p("taper", "Taper angle (deg)", default=0), _p("through_all", "Through all", "bool", False), _OP]},
    "revolve": {"label": "Revolve", "group": "Sketch", "params": [
        _p("sketch_id", "Sketch", "sketch"), _p("angle", "Angle (deg)", default=360),
        _p("axis_start", "Axis start [u,v]", "vec2", [0, 0]), _p("axis_end", "Axis end [u,v]", "vec2", [0, 1]), _OP]},
    "sweep": {"label": "Sweep", "group": "Sketch", "params": [
        _p("sketch_id", "Profile sketch", "sketch"), _p("path_sketch_id", "Path sketch", "sketch"),
        _p("frenet", "Frenet frame", "bool", False), _OP]},
    "loft": {"label": "Loft", "group": "Sketch", "params": [
        _p("sketch_ids", "Profile sketches (ordered)", "sketchlist", []), _p("ruled", "Ruled", "bool", False), _OP]},
    "hole": {"label": "Hole", "group": "Feature", "params": [
        _p("face", "Face selector", "selector", ">Z"), _p("points", "World points [[x,y,z]..]", "points", []),
        _p("positions", "Face offsets [[u,v]..]", "points2", [[0, 0]]), _p("diameter", "Diameter", default=6),
        _p("depth", "Depth (0 = through)", default=0),
        _p("kind", "Kind", "select", "simple", options=["simple", "counterbore", "countersink"]),
        _p("cbore_diameter", "Counterbore diameter", default=11), _p("cbore_depth", "Counterbore depth", default=5),
        _p("csk_diameter", "Countersink diameter", default=12), _p("csk_angle", "Countersink angle", default=90)]},
    "fillet": {"label": "Fillet", "group": "Feature", "params": [
        _p("radius", "Radius", default=2), _p("edges", "Edge selector", "selector", "|Z")]},
    "chamfer": {"label": "Chamfer", "group": "Feature", "params": [
        _p("length", "Length", default=1.5), _p("length2", "Length 2 (0 = symmetric)", default=0),
        _p("edges", "Edge selector", "selector", ">Z")]},
    "shell": {"label": "Shell", "group": "Feature", "params": [
        _p("thickness", "Wall thickness", default=2), _p("faces", "Faces to remove", "selector", ">Z"),
        _p("direction", "Wall", "select", "inside", options=["inside", "outside"])]},
    "linear_pattern": {"label": "Linear pattern", "group": "Pattern", "params": [
        _p("source", "Source feature", "feature"), _p("count", "Count", "int", 3),
        _p("spacing", "Spacing [dx,dy,dz]", "vec3", [20, 0, 0]),
        _p("count2", "Count (2nd dir)", "int", 1), _p("spacing2", "Spacing 2 [dx,dy,dz]", "vec3", [0, 20, 0])]},
    "circular_pattern": {"label": "Circular pattern", "group": "Pattern", "params": [
        _p("source", "Source feature", "feature"), _p("count", "Count", "int", 6),
        _p("angle", "Total angle (deg)", default=360), _p("axis_point", "Axis point", "vec3", [0, 0, 0]),
        _p("axis_dir", "Axis direction", "vec3", [0, 0, 1])]},
    "mirror": {"label": "Mirror", "group": "Pattern", "params": [
        _p("source", "Source feature (all = whole body)", "feature", "all"),
        _p("plane", "Mirror plane", "select", "YZ", options=["XY", "XZ", "YZ"]), _p("offset", "Plane offset", default=0)]},
    "transform": {"label": "Move / rotate body", "group": "Body", "params": [
        _p("translate", "Translate [x,y,z]", "vec3", [0, 0, 0]), _p("rotate_axis", "Rotation axis", "vec3", [0, 0, 1]),
        _p("rotate_angle", "Rotation (deg)", default=0)]},
    "combine": {"label": "Combine with part", "group": "Body", "params": [
        _p("part_id", "Part", "part"), _p("position", "Position [x,y,z]", "vec3", [0, 0, 0]),
        _p("rotate_axis", "Rotation axis", "vec3", [0, 0, 1]), _p("rotate_angle", "Rotation (deg)", default=0), _OP]},
    "import_step": {"label": "Imported STEP", "group": "Body", "params": [_p("file", "Stored file", "string", "")]},
}

TOOL_TYPES = {"box", "cylinder", "sphere", "cone", "extrude", "revolve", "sweep", "loft", "hole", "combine"}


# ---------------------------------------------------------------- build context
@dataclass
class BuildContext:
    sketches: dict[int, dict]
    parameters: dict[str, Any] = field(default_factory=dict)
    part_lookup: Optional[Callable[[int], Optional[dict]]] = None
    uploads_dir: Optional[Path] = None
    depth: int = 0
    tracker: Optional[toponaming.Tracker] = None


@dataclass
class BuildResult:
    shape: Optional[cq.Shape]
    statuses: list[dict]
    prov: list = field(default_factory=list)  # provenance tag per face of `shape`
    upgraded: Optional[list[dict]] = None  # features with idx:/near: selectors rewritten as ref: (upgrade mode)


# feature params that hold face / edge selectors
SELECTOR_PARAMS: dict[str, list[tuple[str, str]]] = {
    "hole": [("face", "face")], "fillet": [("edges", "edge")], "chamfer": [("edges", "edge")], "shell": [("faces", "face")],
}


def _vec(v) -> cq.Vector:
    return cq.Vector(float(v[0]), float(v[1]), float(v[2]))


def _axis_vec(a: str) -> cq.Vector:
    return {"X": cq.Vector(1, 0, 0), "Y": cq.Vector(0, 1, 0), "Z": cq.Vector(0, 0, 1)}[str(a).upper()]


def _simplify(shape: cq.Shape) -> cq.Shape:
    solids = shape.Solids()
    if len(solids) == 1:
        return solids[0]
    if len(solids) > 1:
        return cq.Compound.makeCompound(solids)
    raise ValueError("operation produced an empty result")


def _combine(body: Optional[cq.Shape], tool: cq.Shape, op: str) -> cq.Shape:
    if body is None:
        if op != "add":
            raise ValueError("the first feature must add material")
        return _simplify(tool)
    if op == "add":
        r = body.fuse(tool)
    elif op == "cut":
        r = body.cut(tool)
    elif op == "intersect":
        r = body.intersect(tool)
    else:
        raise ValueError(f"unknown operation '{op}'")
    try:
        r = r.clean()
    except Exception:
        pass
    return _simplify(r)


# ------------------------------------------------------------------ selectors
def _points(spec: str) -> list[cq.Vector]:
    return [cq.Vector(*[float(c) for c in chunk.split(",")]) for chunk in spec.split(";") if chunk.strip()]


def _dist_point_shape(pt: cq.Vector, shape: cq.Shape) -> float:
    return cq.Vertex.makeVertex(pt.x, pt.y, pt.z).distance(shape)


def _resolve(body: cq.Shape, sel: str, kind: str, prov=None, warnings=None) -> list[cq.Shape]:
    sel = str(sel).strip()
    if sel.startswith("ref:"):
        return toponaming.resolve_reference(body, sel[4:], kind, prov, warnings)
    entities = body.Faces() if kind == "face" else body.Edges()
    if sel in ("all", "*"):
        return list(entities)
    if sel.startswith("idx:"):
        out = []
        for tok in sel[4:].split(","):
            i = int(tok)
            if not 0 <= i < len(entities):
                raise ValueError(f"{kind} index {i} out of range (0..{len(entities) - 1})")
            out.append(entities[i])
        return out
    if sel.startswith("near:"):
        out = []
        for pt in _points(sel[5:]):
            best = min(entities, key=lambda e: _dist_point_shape(pt, e))
            if not any(best.isSame(o) for o in out):
                out.append(best)
        return out
    if sel.startswith("faceedges:"):
        faces = _resolve(body, sel[len("faceedges:"):], "face", prov, warnings)
        out: list[cq.Shape] = []
        for f in faces:
            for e in f.Edges():
                if not any(e.isSame(o) for o in out):
                    out.append(e)
        return out
    wp = cq.Workplane(obj=body)
    res = (wp.faces(sel) if kind == "face" else wp.edges(sel)).vals()
    if not res:
        raise ValueError(f"selector '{sel}' matched no {kind}s")
    return list(res)


def resolve_faces(body, sel, prov=None, warnings=None):
    return _resolve(body, sel, "face", prov, warnings)


def resolve_edges(body, sel, prov=None, warnings=None):
    return _resolve(body, sel, "edge", prov, warnings)


def _ctx_resolve(ctx: "BuildContext", body, sel, kind):
    tr = ctx.tracker
    return _resolve(body, sel, kind, tr.prov if tr else None, tr.warnings if tr else None)


def indices_of(body: cq.Shape, shapes: list[cq.Shape], kind: str) -> list[int]:
    items = body.Faces() if kind == "face" else body.Edges()
    out = []
    for s in shapes:
        i = next((i for i, x in enumerate(items) if x.isSame(s)), None)
        if i is not None and i not in out:
            out.append(i)
    return out


# ---------------------------------------------------------------- tool builders
def _sketch(ctx: BuildContext, sid) -> dict:
    sk = ctx.sketches.get(int(sid))
    if not sk:
        raise ValueError(f"sketch {sid} not found")
    return sk


def _faces_of(ctx: BuildContext, sid) -> tuple[list[cq.Face], cq.Plane]:
    sk = _sketch(ctx, sid)
    faces = sketch2d.profile_faces(sk["entities"], sk.get("plane", "XY"), sk.get("offset", 0.0))
    return faces, sketch2d.plane_of(sk.get("plane", "XY"), sk.get("offset", 0.0))


def _fuse_all(shapes: list[cq.Shape]) -> cq.Shape:
    out = shapes[0]
    for s in shapes[1:]:
        out = out.fuse(s)
    return out


def _big(body: Optional[cq.Shape]) -> float:
    return (body.BoundingBox().DiagonalLength * 2 + 100) if body is not None else 1000.0


def make_tool(f: dict, ctx: BuildContext, body: Optional[cq.Shape]) -> cq.Shape:
    t, p = f["type"], f["params"]
    if t == "box":
        l, w, h = float(p["length"]), float(p["width"]), float(p["height"])
        pos = p.get("position", [0, 0, 0])
        org = [pos[0] - l / 2, pos[1] - w / 2, pos[2] - h / 2] if p.get("centered") else pos
        return cq.Solid.makeBox(l, w, h, _vec(org), cq.Vector(0, 0, 1))
    if t == "cylinder":
        h = float(p["height"])
        d = _axis_vec(p.get("axis", "Z"))
        pos = _vec(p.get("position", [0, 0, 0]))
        if p.get("centered"):
            pos = pos - d * (h / 2)
        return cq.Solid.makeCylinder(float(p["radius"]), h, pos, d)
    if t == "sphere":
        s = cq.Workplane("XY").sphere(float(p["radius"])).val()
        return s.translate(_vec(p.get("position", [0, 0, 0])))
    if t == "cone":
        d = _axis_vec(p.get("axis", "Z"))
        return cq.Solid.makeCone(float(p["radius1"]), float(p["radius2"]), float(p["height"]), _vec(p.get("position", [0, 0, 0])), d)
    if t == "extrude":
        faces, plane = _faces_of(ctx, p["sketch_id"])
        n = plane.zDir
        dist = float(p["distance"])
        direction = p.get("direction", "normal")
        if p.get("through_all"):
            dist, direction = _big(body), "symmetric"
        if dist <= 0:
            raise ValueError("extrude distance must be positive")
        solids = []
        for fc in faces:
            if direction == "symmetric":
                fc = fc.translate(n * (-dist / 2))
                vec = n * dist
            elif direction == "reverse":
                vec = n * (-dist)
            else:
                vec = n * dist
            taper = float(p.get("taper", 0) or 0)
            solids.append(cq.Solid.extrudeLinear(fc, vec, taper) if taper else cq.Solid.extrudeLinear(fc, vec))
        return _fuse_all(solids)
    if t == "revolve":
        faces, plane = _faces_of(ctx, p["sketch_id"])
        a0 = plane.toWorldCoords(tuple(p.get("axis_start", [0, 0])))
        a1 = plane.toWorldCoords(tuple(p.get("axis_end", [0, 1])))
        solids = [cq.Solid.revolve(fc, float(p.get("angle", 360)), a0, a1) for fc in faces]
        return _fuse_all(solids)
    if t == "sweep":
        faces, _ = _faces_of(ctx, p["sketch_id"])
        psk = _sketch(ctx, p["path_sketch_id"])
        path = sketch2d.path_wire(psk["entities"], psk.get("plane", "XY"), psk.get("offset", 0.0))
        solids = []
        for fc in faces:
            try:
                s = cq.Solid.sweep(fc, path, True, bool(p.get("frenet", False)))
            except Exception:
                s = cq.Solid.sweep(fc.outerWire(), list(fc.innerWires()), path, True, bool(p.get("frenet", False)))
            solids.append(s)
        return _fuse_all(solids)
    if t == "loft":
        ids = p.get("sketch_ids", [])
        if len(ids) < 2:
            raise ValueError("loft needs at least two profile sketches")
        wires = []
        for sid in ids:
            faces, _ = _faces_of(ctx, sid)
            wires.append(faces[0].outerWire())
        return cq.Solid.makeLoft(wires, bool(p.get("ruled", False)))
    if t == "hole":
        if body is None:
            raise ValueError("hole needs an existing body")
        face = _ctx_resolve(ctx, body, p["face"], "face")[0]
        n = face.normalAt(face.Center())
        ref = cq.Vector(0, 0, 1) if abs(n.z) < 0.9 else cq.Vector(1, 0, 0)
        xd = ref.cross(n).normalized()
        plane = cq.Plane(origin=face.Center(), xDir=xd, normal=n)
        centers: list[cq.Vector] = []
        pts = p.get("points") or []
        if pts:
            for q in pts:
                v = _vec(q)
                centers.append(v - n * ((v - plane.origin).dot(n)))  # project onto the face plane
        else:
            for uv in p.get("positions") or [[0, 0]]:
                centers.append(plane.toWorldCoords((float(uv[0]), float(uv[1]))))
        depth = float(p.get("depth") or 0) or _big(body)
        dia = float(p["diameter"])
        down = n * -1
        cutters = []
        for c in centers:
            cutters.append(cq.Solid.makeCylinder(dia / 2, depth, c + n * 0.0, down))
            kind = p.get("kind", "simple")
            if kind == "counterbore":
                cutters.append(cq.Solid.makeCylinder(float(p["cbore_diameter"]) / 2, float(p["cbore_depth"]), c, down))
            elif kind == "countersink":
                big_d = float(p["csk_diameter"])
                half = math.radians(float(p.get("csk_angle", 90))) / 2
                h = (big_d - dia) / 2 / math.tan(half)
                cutters.append(cq.Solid.makeCone(big_d / 2, dia / 2, h, c, down))
        return _fuse_all(cutters)
    if t == "combine":
        if ctx.part_lookup is None or ctx.depth > 3:
            raise ValueError("cannot combine parts here (depth limit)")
        other = ctx.part_lookup(int(p["part_id"]))
        if not other:
            raise ValueError(f"part {p['part_id']} not found")
        sub = rebuild(
            other["features"],
            BuildContext(ctx.sketches, other.get("parameters", {}), ctx.part_lookup, ctx.uploads_dir, ctx.depth + 1),
        )
        if sub.shape is None:
            raise ValueError("the part to combine is empty")
        shp = sub.shape
        ang = float(p.get("rotate_angle", 0) or 0)
        if ang:
            ax = _vec(p.get("rotate_axis", [0, 0, 1]))
            shp = shp.rotate(cq.Vector(0, 0, 0), ax, ang)
        return shp.translate(_vec(p.get("position", [0, 0, 0])))
    raise ValueError(f"feature '{t}' does not create a tool")


# ---------------------------------------------------------------------- rebuild
def _apply_pattern(body, f, tools, ctx):
    p = f["params"]
    src = tools.get(p.get("source"))
    if src is None:
        raise ValueError("pattern source feature not found (patterns work on primitive, sketch and hole features)")
    tool, op = src
    instances: list[cq.Shape] = []
    if f["type"] == "linear_pattern":
        s1, s2 = _vec(p["spacing"]), _vec(p.get("spacing2", [0, 0, 0]))
        for i in range(int(p["count"])):
            for j in range(int(p.get("count2", 1) or 1)):
                if i == 0 and j == 0:
                    continue
                instances.append(tool.translate(s1 * i + s2 * j))
    else:
        count = int(p["count"])
        total = float(p.get("angle", 360))
        step = total / count if abs(total - 360) < 1e-9 else total / max(count - 1, 1)
        ap = _vec(p.get("axis_point", [0, 0, 0]))
        ad = _vec(p.get("axis_dir", [0, 0, 1]))
        for i in range(1, count):
            instances.append(tool.rotate(ap, ap + ad, step * i))
    out = body
    for inst in instances:
        out = _combine(out, inst, op)
    return out, instances


def _upgrade_selectors(f: dict, params: dict, body: Optional[cq.Shape], tracker: toponaming.Tracker) -> dict:
    """Rewrite idx:/near: selectors of feature `f` as ref: selectors against the current body."""
    if body is None or f["type"] not in SELECTOR_PARAMS:
        return f
    new_params = dict(f.get("params", {}))
    for name, kind in SELECTOR_PARAMS[f["type"]]:
        sel = params.get(name)
        if isinstance(sel, str) and (sel.startswith("idx:") or sel.startswith("near:")):
            shapes = _resolve(body, sel, kind, tracker.prov)
            new_params[name] = toponaming.make_reference(body, kind, indices_of(body, shapes, kind), tracker.prov)
    return {**f, "params": new_params}


def rebuild(features: list[dict], ctx: BuildContext, upgrade_refs: bool = False) -> BuildResult:
    body: Optional[cq.Shape] = None
    tools: dict[str, tuple[cq.Shape, str]] = {}
    statuses: list[dict] = []
    tracker = toponaming.Tracker()
    ctx.tracker = tracker
    upgraded: list[dict] = []
    for f in features:
        fid = f.get("id", "")
        st = {"id": fid, "ok": True, "error": None}
        if f.get("suppressed"):
            st["suppressed"] = True
            statuses.append(st)
            upgraded.append(f)
            continue
        tracker.warnings = []
        prev = body
        try:
            t = f["type"]
            if t not in FEATURE_SCHEMAS:
                raise ValueError(f"unknown feature type '{t}'")
            params = resolve_value(f.get("params", {}), ctx.parameters)
            if upgrade_refs:
                upgraded.append(_upgrade_selectors(f, params, body, tracker))
            ff = {**f, "params": params}
            new_tools: list[cq.Shape] = []
            if t in TOOL_TYPES:
                tool = make_tool(ff, ctx, body)
                op = params.get("op", "cut" if t == "hole" else "add")
                body = _combine(body, tool, op)
                tools[fid] = (tool, op)
                new_tools = [tool]
            elif t == "fillet":
                edges = _ctx_resolve(ctx, _need(body), params["edges"], "edge")
                body = _simplify(body.fillet(float(params["radius"]), edges))
            elif t == "chamfer":
                edges = _ctx_resolve(ctx, _need(body), params["edges"], "edge")
                l2 = float(params.get("length2") or 0) or None
                body = _simplify(body.chamfer(float(params["length"]), l2, edges))
            elif t == "shell":
                th = float(params["thickness"])
                faces = _ctx_resolve(ctx, _need(body), params["faces"], "face") if params.get("faces") else []
                th = -abs(th) if params.get("direction", "inside") == "inside" else abs(th)
                body = _simplify(body.hollow(faces, th))
            elif t in ("linear_pattern", "circular_pattern"):
                body, new_tools = _apply_pattern(_need(body), ff, tools, ctx)
            elif t == "mirror":
                _need(body)
                plane = params.get("plane", "YZ")
                off = float(params.get("offset", 0) or 0)
                base = {"YZ": (off, 0, 0), "XZ": (0, off, 0), "XY": (0, 0, off)}[plane]
                src = params.get("source", "all")
                mirrored = body.mirror(plane, base) if src in ("all", "", None) else tools[src][0].mirror(plane, base)
                body = _combine(body, mirrored, "add" if src in ("all", "", None) else tools[src][1])
                new_tools = [mirrored]
            elif t == "transform":
                _need(body)
                ang = float(params.get("rotate_angle", 0) or 0)
                if ang:
                    body = body.rotate(cq.Vector(0, 0, 0), _vec(params.get("rotate_axis", [0, 0, 1])), ang)
                body = body.translate(_vec(params.get("translate", [0, 0, 0])))
            elif t == "import_step":
                if ctx.uploads_dir is None:
                    raise ValueError("uploads directory not configured")
                path = ctx.uploads_dir / os.path.basename(params["file"])
                body = _simplify(cq.importers.importStep(str(path)).val())
            if body is not None and body is not prev:
                if t == "transform":
                    pass  # same topology, same face order: tags carry over
                elif t == "import_step":
                    tracker.reset(body, fid)
                else:
                    tracker.update(prev, body, fid, new_tools)
            st["volume"] = round(body.Volume(), 4) if body is not None else 0.0
            if tracker.warnings:
                st["warnings"] = list(dict.fromkeys(tracker.warnings))
        except Exception as exc:  # keep going; report on this feature only
            st["ok"] = False
            st["error"] = f"{type(exc).__name__}: {exc}"
            body = prev
            if upgrade_refs and len(upgraded) < len(statuses) + 1:
                upgraded.append(f)
        statuses.append(st)
    toponaming.register(body, tracker.prov)
    return BuildResult(body, statuses, list(tracker.prov), upgraded if upgrade_refs else None)


def _need(body):
    if body is None:
        raise ValueError("this feature needs an existing body")
    return body


# ------------------------------------------------------------ analysis / export
def mass_properties(shape: cq.Shape, material_name: str = "steel") -> dict:
    m = material(material_name)
    bb = shape.BoundingBox()
    vol = shape.Volume()
    com = shape.Center()
    props = {
        "volume_mm3": vol,
        "area_mm2": shape.Area(),
        "mass_kg": vol * 1e-9 * m["density"],
        "density_kg_m3": m["density"],
        "center_of_mass": [com.x, com.y, com.z],
        "bbox_min": [bb.xmin, bb.ymin, bb.zmin],
        "bbox_max": [bb.xmax, bb.ymax, bb.zmax],
        "bbox_size": [bb.xlen, bb.ylen, bb.zlen],
        "faces": len(shape.Faces()),
        "edges": len(shape.Edges()),
        "solids": len(shape.Solids()),
    }
    try:
        mi = cq.Shape.matrixOfInertia(shape)
        props["inertia_tensor_mm5"] = [[float(mi[i][j]) for j in range(3)] for i in range(3)]
    except Exception:
        pass
    return props


def tessellate(shape: cq.Shape, tolerance: Optional[float] = None, angular: float = 0.25) -> dict:
    """Flat arrays for WebGL: per-face smooth normals, face metadata for picking, edge polylines."""
    diag = shape.BoundingBox().DiagonalLength or 1.0
    tol = tolerance or max(diag * 0.0008, 0.005)
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    faces_meta = []
    base = 0
    for fi, face in enumerate(shape.Faces()):
        verts, tris = face.tessellate(tol, angular)
        if not tris:
            continue
        P = np.array([[v.x, v.y, v.z] for v in verts], dtype=float)
        T = np.array(tris, dtype=np.int64)
        e1, e2 = P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]
        fn = np.cross(e1, e2)  # area weighted triangle normals
        N = np.zeros_like(P)
        for k in range(3):
            np.add.at(N, T[:, k], fn)
        ln = np.linalg.norm(N, axis=1, keepdims=True)
        N = np.divide(N, ln, out=np.zeros_like(N), where=ln > 1e-14)
        gt = face.geomType()
        c = face.Center()
        try:
            nc = face.normalAt(c)
            normal = [nc.x, nc.y, nc.z]
        except Exception:
            normal = [0, 0, 1]
        if gt == "PLANE":
            N[:] = normal
        faces_meta.append(
            {
                "index": fi,
                "type": gt,
                "area": face.Area(),
                "center": [c.x, c.y, c.z],
                "normal": normal,
                "tri_start": len(indices) // 3,
                "tri_count": len(T),
            }
        )
        positions.extend(P.ravel().tolist())
        normals.extend(N.ravel().tolist())
        indices.extend((T + base).ravel().tolist())
        base += len(P)
    edges = []
    for ei, ed in enumerate(shape.Edges()):
        n = 2 if ed.geomType() == "LINE" else max(8, min(120, int(ed.Length() / max(tol * 20, 0.3))))
        pts = []
        for i in range(n + 1):
            q = ed.positionAt(i / n)
            pts.extend([q.x, q.y, q.z])
        edges.append({"index": ei, "type": ed.geomType(), "length": ed.Length(), "points": pts})
    return {"positions": positions, "normals": normals, "indices": indices, "faces": faces_meta, "edges": edges}


def export_shape(shape: cq.Shape, fmt: str) -> tuple[bytes, str]:
    fmt = fmt.lower()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, f"out.{fmt}")
        if fmt in ("step", "stp"):
            shape.exportStep(path)
            mime = "application/step"
        elif fmt == "stl":
            shape.exportStl(path, tolerance=max(shape.BoundingBox().DiagonalLength * 0.0005, 0.005), angularTolerance=0.1)
            mime = "model/stl"
        elif fmt in ("brep", "brp"):
            shape.exportBrep(path)
            mime = "application/octet-stream"
        else:
            raise ValueError(f"unsupported export format '{fmt}' (step, stl, brep)")
        with open(path, "rb") as fh:
            return fh.read(), mime


def new_feature_id(features: list[dict]) -> str:
    used = {f.get("id") for f in features}
    n = len(features) + 1
    while f"f{n}" in used:
        n += 1
    return f"f{n}"


def default_params(ftype: str) -> dict:
    return {p["name"]: p["default"] for p in FEATURE_SCHEMAS[ftype]["params"]}
