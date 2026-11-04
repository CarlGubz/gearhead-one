"""Interactive sketch editing: trim / extend / fillet / explode and constraint inference.

All functions are pure: they take the entity + constraint lists of a sketch and return new
lists. Geometry is handled analytically for lines, arcs and circles; every other entity type
(rect, polyline, polygon, slot, ellipse, spline) acts as a cutting boundary through its
line / arc pieces or a sampled polyline.

Constraint hygiene: when an entity is cut or moved, constraints that reference its points or
dimensions are dropped (they would be stale); purely orientational ones (horizontal,
parallel, radius, concentric, ...) on the whole entity are kept.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import sketch2d

EPS = 1e-7
TOL = 1e-6  # coincidence tolerance (mm) for inference / corner finding

# ----------------------------------------------------------------- primitives


@dataclass
class Seg:
    p: tuple[float, float]
    q: tuple[float, float]


@dataclass
class Circ:
    c: tuple[float, float]
    r: float
    a0: float | None = None  # radians, CCW span; None -> full circle
    a1: float | None = None

    def contains_angle(self, a: float) -> bool:
        if self.a0 is None:
            return True
        span = (self.a1 - self.a0) % (2 * math.pi) or 2 * math.pi
        return (a - self.a0) % (2 * math.pi) <= span + 1e-9


def _arc_span(start_deg: float, end_deg: float) -> float:
    s = (end_deg - start_deg) % 360.0
    return s if s > 1e-9 else 360.0


def _pieces(e: dict) -> list:
    """Decompose an entity into Seg / Circ pieces usable as cutting boundaries."""
    t = e["type"]
    if t == "line":
        return [Seg(tuple(e["p1"]), tuple(e["p2"]))]
    if t == "circle":
        return [Circ(tuple(e["c"]), float(e["r"]))]
    if t == "arc":
        return [Circ(tuple(e["c"]), float(e["r"]), math.radians(e["start"]), math.radians(e["end"]))]
    if t in ("rect", "polyline", "polygon", "slot"):
        out = []
        for x in explode_entity(e):
            out.extend(_pieces(x))
        return out
    if t in ("ellipse", "spline"):
        pts = []
        for ed in sketch2d.entity_edges(e):
            pts.extend(sketch2d._sample_edge(ed))
        return [Seg(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    return []


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _line_line(p, d, q, e):
    """Parameters (t, u) of the intersection p+t*d == q+u*e, or None when parallel."""
    den = _cross(d[0], d[1], e[0], e[1])
    if abs(den) < 1e-12:
        return None
    w = (q[0] - p[0], q[1] - p[1])
    return _cross(w[0], w[1], e[0], e[1]) / den, _cross(w[0], w[1], d[0], d[1]) / den


def _line_circle(p, d, c, r):
    """Parameters t where p+t*d meets the circle (c, r)."""
    fx, fy = p[0] - c[0], p[1] - c[1]
    a = d[0] ** 2 + d[1] ** 2
    b = 2 * (fx * d[0] + fy * d[1])
    cc = fx * fx + fy * fy - r * r
    disc = b * b - 4 * a * cc
    if a < 1e-18 or disc < -1e-9:
        return []
    disc = math.sqrt(max(disc, 0.0))
    ts = {(-b - disc) / (2 * a), (-b + disc) / (2 * a)}
    return sorted(ts)


def _circle_circle(c1, r1, c2, r2):
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = math.hypot(dx, dy)
    if d < 1e-12 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    mx, my = c1[0] + a * dx / d, c1[1] + a * dy / d
    pts = [(mx + h * dy / d, my - h * dx / d), (mx - h * dy / d, my + h * dx / d)]
    return pts[:1] if h < 1e-12 else pts


def _ang(c, p):
    return math.atan2(p[1] - c[1], p[0] - c[0])


# --------------------------------------------------- intersections with a curve
def _hits_on_line(p1, p2, others: list, ray: bool = False) -> list[float]:
    """Line parameters t (0..1 along p1->p2; any t>0 when ray=True) where others cross it."""
    d = (p2[0] - p1[0], p2[1] - p1[1])
    out = []
    for o in others:
        if isinstance(o, Seg):
            e = (o.q[0] - o.p[0], o.q[1] - o.p[1])
            r = _line_line(p1, d, o.p, e)
            if r and -1e-9 <= r[1] <= 1 + 1e-9:
                out.append(r[0])
        else:
            for t in _line_circle(p1, d, o.c, o.r):
                pt = (p1[0] + t * d[0], p1[1] + t * d[1])
                if o.contains_angle(_ang(o.c, pt)):
                    out.append(t)
    return out


def _hits_on_circle(c, r, others: list) -> list[float]:
    """Angles (radians) where others cross the full circle (c, r)."""
    out = []
    for o in others:
        if isinstance(o, Seg):
            d = (o.q[0] - o.p[0], o.q[1] - o.p[1])
            for t in _line_circle(o.p, d, c, r):
                if -1e-9 <= t <= 1 + 1e-9:
                    out.append(_ang(c, (o.p[0] + t * d[0], o.p[1] + t * d[1])))
        else:
            for pt in _circle_circle(c, r, o.c, o.r):
                if o.contains_angle(_ang(o.c, pt)):
                    out.append(_ang(c, pt))
    return out


def _boundaries(entities: list[dict], skip: str) -> list:
    out = []
    for e in entities:
        if e["id"] != skip and e["type"] != "point":
            out.extend(_pieces(e))
    return out


# ------------------------------------------------------------- id / cleanup
_KEEP_ON_CHANGE = {"horizontal", "vertical", "parallel", "perpendicular", "angle", "radius", "diameter", "concentric"}


def _refs(c: dict) -> list[str]:
    return [r for r in (c.get("a"), c.get("b"), c.get("c")) if isinstance(r, str)]


def prune_constraints(constraints: list[dict], changed: set[str], removed: set[str]) -> list[dict]:
    out = []
    for c in constraints:
        refs = _refs(c)
        ents = {r.split(".")[0] for r in refs}
        if ents & removed:
            continue
        if ents & changed:
            whole = all("." not in r for r in refs if r.split(".")[0] in changed)
            if not (whole and c["type"] in _KEEP_ON_CHANGE):
                continue
        out.append(c)
    return out


class _Ids:
    def __init__(self, entities):
        self.used = {e["id"] for e in entities}
        self.n = 1

    def __call__(self) -> str:
        while f"e{self.n}" in self.used:
            self.n += 1
        self.used.add(f"e{self.n}")
        return f"e{self.n}"


def _carry(src: dict, e: dict) -> dict:
    for k in ("construction", "layer"):
        if k in src:
            e[k] = src[k]
    return e


# ---------------------------------------------------------------- explode
def explode_entity(e: dict) -> list[dict]:
    """Rect / polyline / polygon / slot -> plain lines (+ arcs for slot ends). Ids are not assigned."""
    t = e["type"]
    if t == "rect":
        (x1, y1), (x2, y2) = e["p1"], e["p2"]
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        closed = True
    elif t == "polyline":
        pts = [tuple(p) for p in e["points"]]
        closed = bool(e.get("closed"))
    elif t == "polygon":
        pts = sketch2d._regular_polygon_pts(e["c"], float(e["r"]), int(e.get("sides", 6)), float(e.get("rotation", 0)))
        closed = True
    elif t == "slot":
        (x1, y1), (x2, y2) = e["p1"], e["p2"]
        w = float(e["width"]) / 2
        a = math.atan2(y2 - y1, x2 - x1)
        nx, ny = -math.sin(a) * w, math.cos(a) * w
        ad = math.degrees(a)
        return [_carry(e, x) for x in (
            {"type": "line", "p1": [x1 + nx, y1 + ny], "p2": [x2 + nx, y2 + ny]},
            {"type": "arc", "c": [x2, y2], "r": w, "start": ad - 90, "end": ad + 90},
            {"type": "line", "p1": [x2 - nx, y2 - ny], "p2": [x1 - nx, y1 - ny]},
            {"type": "arc", "c": [x1, y1], "r": w, "start": ad + 90, "end": ad + 270},
        )]
    else:
        return [dict(e)]
    if closed and math.dist(pts[0], pts[-1]) < 1e-12:
        pts = pts[:-1]
    n = len(pts)
    segs = [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]
    return [_carry(e, {"type": "line", "p1": list(a), "p2": list(b)}) for a, b in segs if math.dist(a, b) > 1e-12]


def explode(entities: list[dict], constraints: list[dict], ids: list[str]) -> tuple[list[dict], list[dict]]:
    nid = _Ids(entities)
    out, changed = [], set()
    for e in entities:
        if e["id"] in ids and e["type"] in ("rect", "polyline", "polygon", "slot"):
            changed.add(e["id"])
            for x in explode_entity(e):
                x["id"] = nid()
                out.append(x)
        else:
            out.append(e)
    return out, prune_constraints(constraints, set(), changed)


# ------------------------------------------------------------------- trim
def _dist_to_entity(e: dict, at) -> float:
    best = math.inf
    for pc in _pieces(e):
        if isinstance(pc, Seg):
            d = (pc.q[0] - pc.p[0], pc.q[1] - pc.p[1])
            L2 = d[0] ** 2 + d[1] ** 2
            t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((at[0] - pc.p[0]) * d[0] + (at[1] - pc.p[1]) * d[1]) / L2))
            best = min(best, math.dist(at, (pc.p[0] + t * d[0], pc.p[1] + t * d[1])))
        else:
            a = _ang(pc.c, at)
            if pc.contains_angle(a):
                best = min(best, abs(math.dist(at, pc.c) - pc.r))
            else:
                for b in (pc.a0, pc.a1):
                    best = min(best, math.dist(at, (pc.c[0] + pc.r * math.cos(b), pc.c[1] + pc.r * math.sin(b))))
    return best


def _explode_target(entities, constraints, eid, at):
    """If eid is a compound entity, explode it and return the id of the piece nearest `at`."""
    e = next(x for x in entities if x["id"] == eid)
    if e["type"] not in ("rect", "polyline", "polygon", "slot"):
        return entities, constraints, eid
    before = {x["id"] for x in entities}
    entities, constraints = explode(entities, constraints, [eid])
    new = [x for x in entities if x["id"] not in before]
    return entities, constraints, min(new, key=lambda x: _dist_to_entity(x, at))["id"]


def trim(entities: list[dict], constraints: list[dict], eid: str, at) -> tuple[list[dict], list[dict]]:
    """Remove the piece of entity `eid` between the intersections that bracket the point `at`."""
    entities = [dict(e) for e in entities]
    entities, constraints, eid = _explode_target(entities, constraints, eid, at)
    e = next(x for x in entities if x["id"] == eid)
    others = _boundaries(entities, eid)
    nid = _Ids(entities)
    t = e["type"]
    removed, changed, added = set(), set(), []

    if t == "line":
        p1, p2 = e["p1"], e["p2"]
        d = (p2[0] - p1[0], p2[1] - p1[1])
        L2 = d[0] ** 2 + d[1] ** 2 or 1.0
        tc = ((at[0] - p1[0]) * d[0] + (at[1] - p1[1]) * d[1]) / L2
        cuts = sorted(x for x in _hits_on_line(p1, p2, others) if EPS < x < 1 - EPS)
        lo = max((x for x in cuts if x < tc), default=None)
        hi = min((x for x in cuts if x > tc), default=None)
        P = lambda s: [p1[0] + s * d[0], p1[1] + s * d[1]]  # noqa: E731
        if lo is None and hi is None:
            removed.add(eid)
        elif lo is None:
            e["p1"] = P(hi)
            changed.add(eid)
        elif hi is None:
            e["p2"] = P(lo)
            changed.add(eid)
        else:
            added.append(_carry(e, {"id": nid(), "type": "line", "p1": P(hi), "p2": list(p2)}))
            e["p2"] = P(lo)
            changed.add(eid)
    elif t in ("arc", "circle"):
        c, r = e["c"], float(e["r"])
        a_click = _ang(c, at)
        hits = _hits_on_circle(c, r, others)
        if t == "arc":
            a0 = math.radians(e["start"])
            span = math.radians(_arc_span(e["start"], e["end"]))
            rel = lambda a: (a - a0) % (2 * math.pi)  # noqa: E731
            cuts = sorted(rel(a) for a in hits if EPS < rel(a) < span - EPS)
            sc = rel(a_click)
            lo = max((x for x in cuts if x < sc), default=None)
            hi = min((x for x in cuts if x > sc), default=None)
            deg = lambda s: math.degrees(a0 + s)  # noqa: E731
            if lo is None and hi is None:
                removed.add(eid)
            elif lo is None:
                e["start"] = deg(hi)
                changed.add(eid)
            elif hi is None:
                e["end"] = deg(lo)
                changed.add(eid)
            else:
                added.append(_carry(e, {"id": nid(), "type": "arc", "c": list(c), "r": r, "start": deg(hi), "end": e["end"]}))
                e["end"] = deg(lo)
                changed.add(eid)
        else:
            uniq = sorted({round(a % (2 * math.pi), 9) for a in hits})
            if len(uniq) < 2:
                if not uniq:
                    removed.add(eid)
                else:
                    raise ValueError("a circle needs two intersections to be trimmed")
            else:
                rel = [(a - a_click) % (2 * math.pi) for a in uniq]
                nxt = uniq[min(range(len(uniq)), key=lambda i: rel[i])]           # first cut CCW of the click
                prv = uniq[max(range(len(uniq)), key=lambda i: rel[i])]           # first cut CW of the click
                src = dict(e)
                e.clear()
                e.update(_carry(src, {"id": eid, "type": "arc", "c": list(c), "r": r,
                                      "start": math.degrees(nxt), "end": math.degrees(prv)}))
                changed.add(eid)
    else:
        raise ValueError(f"cannot trim a {t}")

    entities = [x for x in entities if x["id"] not in removed] + added
    return entities, prune_constraints(constraints, changed, removed)


# ----------------------------------------------------------------- extend
def extend(entities: list[dict], constraints: list[dict], eid: str, at) -> tuple[list[dict], list[dict]]:
    """Extend the end of line/arc `eid` nearest to `at` up to the next boundary."""
    entities = [dict(e) for e in entities]
    e = next(x for x in entities if x["id"] == eid)
    others = _boundaries(entities, eid)
    if e["type"] == "line":
        p1, p2 = e["p1"], e["p2"]
        end = "p2" if math.dist(at, p2) <= math.dist(at, p1) else "p1"
        a, b = (p1, p2) if end == "p2" else (p2, p1)  # extend from a through b
        hits = [t for t in _hits_on_line(a, b, others, ray=True) if t > 1 + 1e-7]
        if not hits:
            raise ValueError("nothing to extend to")
        t = min(hits)
        e[end] = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
    elif e["type"] == "arc":
        c, r = e["c"], float(e["r"])
        sp = (e["c"][0] + r * math.cos(math.radians(e["start"])), c[1] + r * math.sin(math.radians(e["start"])))
        ep = (c[0] + r * math.cos(math.radians(e["end"])), c[1] + r * math.sin(math.radians(e["end"])))
        hits = _hits_on_circle(c, r, others)
        if math.dist(at, ep) <= math.dist(at, sp):
            a1 = math.radians(e["end"])
            ds = [(h - a1) % (2 * math.pi) for h in hits]
            ds = [d for d in ds if 1e-7 < d < 2 * math.pi - math.radians(_arc_span(e["start"], e["end"]))]
            if not ds:
                raise ValueError("nothing to extend to")
            e["end"] = math.degrees(a1 + min(ds))
        else:
            a0 = math.radians(e["start"])
            ds = [(a0 - h) % (2 * math.pi) for h in hits]
            ds = [d for d in ds if 1e-7 < d < 2 * math.pi - math.radians(_arc_span(e["start"], e["end"]))]
            if not ds:
                raise ValueError("nothing to extend to")
            e["start"] = math.degrees(a0 - min(ds))
    else:
        raise ValueError(f"cannot extend a {e['type']}")
    return entities, prune_constraints(constraints, {eid}, set())


# ----------------------------------------------------------------- fillet
def fillet(entities: list[dict], constraints: list[dict], radius: float, at=None, a: str | None = None,
           b: str | None = None, tol: float = 0.5) -> tuple[list[dict], list[dict]]:
    """Round the corner between two lines with a tangent arc.

    Either name the lines (a, b) or give a corner point `at`; compound entities (rect,
    polyline, polygon) with a vertex at that corner are exploded first.
    """
    r = float(radius)
    if r <= 0:
        raise ValueError("fillet radius must be positive")
    entities = [dict(e) for e in entities]
    if a is None or b is None:
        if at is None:
            raise ValueError("give two lines or a corner point")
        compound = [e["id"] for e in entities if e["type"] in ("rect", "polyline", "polygon")
                    and any(math.dist(at, (x["p1"])) < tol or math.dist(at, x["p2"]) < tol for x in explode_entity(e))]
        if compound:
            entities, constraints = explode(entities, constraints, compound)
        near = [e for e in entities if e["type"] == "line" and min(math.dist(at, e["p1"]), math.dist(at, e["p2"])) < tol]
        if len(near) != 2:
            raise ValueError(f"found {len(near)} lines meeting at that corner (need exactly 2)")
        a, b = near[0]["id"], near[1]["id"]
    la = next(e for e in entities if e["id"] == a)
    lb = next(e for e in entities if e["id"] == b)
    if la["type"] != "line" or lb["type"] != "line":
        raise ValueError("fillet works on two lines")
    da = (la["p2"][0] - la["p1"][0], la["p2"][1] - la["p1"][1])
    db = (lb["p2"][0] - lb["p1"][0], lb["p2"][1] - lb["p1"][1])
    hit = _line_line(la["p1"], da, lb["p1"], db)
    if hit is None:
        raise ValueError("lines are parallel")
    X = (la["p1"][0] + hit[0] * da[0], la["p1"][1] + hit[0] * da[1])

    def far(line):  # the endpoint that is kept, and the key of the one that moves
        return ("p1", "p2") if math.dist(line["p1"], X) >= math.dist(line["p2"], X) else ("p2", "p1")

    ka, ma = far(la)
    kb, mb = far(lb)
    ua = _unit((la[ka][0] - X[0], la[ka][1] - X[1]))
    ub = _unit((lb[kb][0] - X[0], lb[kb][1] - X[1]))
    cos_t = max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1]))
    theta = math.acos(cos_t)
    if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
        raise ValueError("lines are collinear")
    t = r / math.tan(theta / 2)
    if t > math.dist(la[ka], X) + 1e-9 or t > math.dist(lb[kb], X) + 1e-9:
        raise ValueError("fillet radius is too large for these lines")
    Ta = [X[0] + ua[0] * t, X[1] + ua[1] * t]
    Tb = [X[0] + ub[0] * t, X[1] + ub[1] * t]
    bis = _unit((ua[0] + ub[0], ua[1] + ub[1]))
    dc = r / math.sin(theta / 2)
    C = [X[0] + bis[0] * dc, X[1] + bis[1] * dc]
    s, e_ = math.degrees(_ang(C, Ta)), math.degrees(_ang(C, Tb))
    swap = _arc_span(s, e_) > 180
    if swap:
        s, e_ = e_, s
    la[ma], lb[mb] = Ta, Tb
    arc_id = _Ids(entities)()
    arc = _carry(la, {"id": arc_id, "type": "arc", "c": C, "r": r, "start": s, "end": e_})
    constraints = prune_constraints(constraints, {a, b}, set())
    end_a, end_b = ("end", "start") if swap else ("start", "end")
    constraints = constraints + [
        {"type": "coincident", "a": f"{a}.{ma}", "b": f"{arc_id}.{end_a}"},
        {"type": "coincident", "a": f"{b}.{mb}", "b": f"{arc_id}.{end_b}"},
        {"type": "tangent", "a": a, "b": arc_id},
        {"type": "tangent", "a": b, "b": arc_id},
        {"type": "radius", "a": arc_id, "value": r},
    ]
    return entities + [arc], constraints


def _unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-15 else v


# ----------------------------------------------------- constraint inference
def _point_refs(e: dict) -> list[tuple[str, tuple[float, float]]]:
    t, i = e["type"], e["id"]
    out = []
    if t in ("line", "rect", "slot"):
        out += [(f"{i}.p1", tuple(e["p1"])), (f"{i}.p2", tuple(e["p2"]))]
    if t == "arc":
        c, r = e["c"], e["r"]
        for k in ("start", "end"):
            a = math.radians(e[k])
            out.append((f"{i}.{k}", (c[0] + r * math.cos(a), c[1] + r * math.sin(a))))
    if t in ("circle", "arc", "polygon", "ellipse"):
        out.append((f"{i}.c", tuple(e["c"])))
    if t == "point":
        out.append((f"{i}.p", tuple(e["p"])))
    if t == "polyline":
        out += [(f"{i}.p{k}", tuple(p)) for k, p in enumerate(e["points"])]
    return out


def _has(constraints, typ, refs: set[str]) -> bool:
    return any(c["type"] == typ and set(_refs(c)) == refs for c in constraints)


def infer_constraints(entities: list[dict], constraints: list[dict], ids: list[str] | None = None,
                      tol: float = TOL, ang_tol_deg: float = 0.5) -> list[dict]:
    """New constraints implied by the geometry of entities `ids` (default: all).

    Detects horizontal / vertical lines, coincident end points, points lying on lines or
    circles, and lines tangent to an arc they start from. Existing constraints are never
    duplicated.
    """
    by_id = {e["id"]: e for e in entities}
    target = [by_id[i] for i in (ids if ids is not None else list(by_id)) if i in by_id]
    tid = {e["id"] for e in target}
    have = list(constraints)
    new: list[dict] = []

    def add(c):
        if not _has(have, c["type"], set(_refs(c))):
            have.append(c)
            new.append(c)

    tan_tol = math.tan(math.radians(ang_tol_deg))
    for e in target:
        if e["type"] == "line":
            dx, dy = e["p2"][0] - e["p1"][0], e["p2"][1] - e["p1"][1]
            if math.hypot(dx, dy) < 1e-9:
                continue
            if not any(c["type"] in ("horizontal", "vertical") and c.get("a") == e["id"] and c.get("b") is None for c in have):
                if abs(dy) <= tan_tol * abs(dx):
                    add({"type": "horizontal", "a": e["id"]})
                elif abs(dx) <= tan_tol * abs(dy):
                    add({"type": "vertical", "a": e["id"]})

    # coincidence: each target point joins the first matching point of a different entity
    all_refs = [(ref, p, e["id"]) for e in entities for ref, p in _point_refs(e) if not ref.endswith(".c") or e["type"] == "point"]
    centres = [(ref, p, e["id"]) for e in entities for ref, p in _point_refs(e) if ref.endswith(".c")]
    linked: set[str] = set()
    for e in target:
        for ref, p in _point_refs(e):
            if ref in linked:
                continue
            pool = centres if ref.endswith(".c") else all_refs
            for oref, q, oid in pool:
                if oid == e["id"] or math.dist(p, q) > tol:
                    continue
                if oid in tid and oref in linked:
                    continue
                add({"type": "concentric" if ref.endswith(".c") else "coincident",
                     **({"a": e["id"], "b": oid} if ref.endswith(".c") else {"a": ref, "b": oref})})
                linked.update({ref, oref})
                break

    # point on line / circle, and tangency at shared end points
    for e in target:
        for ref, p in _point_refs(e):
            if ref.endswith(".c") or ref in linked:
                continue
            for o in entities:
                if o["id"] == e["id"]:
                    continue
                if o["type"] == "line":
                    a, b = o["p1"], o["p2"]
                    d = (b[0] - a[0], b[1] - a[1])
                    L = math.hypot(*d)
                    if L < 1e-9:
                        continue
                    s = ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / (L * L)
                    if 1e-6 < s < 1 - 1e-6 and abs(_cross(d[0], d[1], p[0] - a[0], p[1] - a[1])) / L < tol:
                        add({"type": "point_on_line", "a": ref, "b": o["id"]})
                        break
                elif o["type"] in ("circle", "arc"):
                    if abs(math.dist(p, o["c"]) - o["r"]) < tol and min(math.dist(p, q) for _, q in _point_refs(o)) > tol:
                        add({"type": "point_on_circle", "a": ref, "b": o["id"]})
                        break
    for e in target:
        if e["type"] != "line":
            continue
        d = _unit((e["p2"][0] - e["p1"][0], e["p2"][1] - e["p1"][1]))
        for o in entities:
            if o["type"] != "arc" or o["id"] == e["id"]:
                continue
            for _, q in _point_refs(o)[:2]:
                if min(math.dist(q, e["p1"]), math.dist(q, e["p2"])) < tol:
                    radial = _unit((q[0] - o["c"][0], q[1] - o["c"][1]))
                    if abs(d[0] * radial[0] + d[1] * radial[1]) < math.sin(math.radians(ang_tol_deg)):
                        add({"type": "tangent", "a": e["id"], "b": o["id"]})
    return new


# --------------------------------------------------------------- dispatcher
EDIT_OPS = ("trim", "extend", "fillet", "explode")


def edit(entities: list[dict], constraints: list[dict], op: str, params: dict, ids: list[str] | None = None):
    entities = sketch2d.ensure_ids([dict(e) for e in entities])
    constraints = [dict(c) for c in constraints]
    if op == "trim":
        return trim(entities, constraints, params["id"], params["at"])
    if op == "extend":
        return extend(entities, constraints, params["id"], params["at"])
    if op == "fillet":
        return fillet(entities, constraints, params.get("radius", 2.0), at=params.get("at"),
                      a=params.get("a"), b=params.get("b"), tol=float(params.get("tol", 0.5)))
    if op == "explode":
        return explode(entities, constraints, ids or params.get("ids") or [e["id"] for e in entities])
    raise ValueError(f"unknown edit op '{op}' ({', '.join(EDIT_OPS)})")
