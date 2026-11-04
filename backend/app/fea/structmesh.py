"""Mesh generators for structural models: beam lines, frames, plates and cylindrical shells."""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from .structural import STRUCT_TYPES, StructuralError

KINDS = {
    "line": "Straight line of elements between two points (beam, truss bar or spring)",
    "portal": "Portal frame: two columns and a beam (beams or truss bars)",
    "plate": "Rectangular flat plate / shell",
    "cylinder": "Cylindrical shell (full tube or an arc)",
}


NAMES = {"line": "Line", "portal": "Portal", "plate": "Plate", "cylinder": "Cylinder"}


def _check(dims: Dict[str, float], *names: str) -> None:
    for k in names:
        if k in dims and float(dims[k]) <= 0:
            raise StructuralError(f"Dimension '{k}' must be positive")


def line(p1, p2, n: int) -> Tuple[np.ndarray, np.ndarray]:
    p1, p2 = np.asarray(p1, dtype=float), np.asarray(p2, dtype=float)
    if np.linalg.norm(p2 - p1) <= 0:
        raise StructuralError("The two end points of a line must differ.")
    t = np.linspace(0.0, 1.0, n + 1)
    nodes = p1[None] + t[:, None] * (p2 - p1)[None]
    elems = np.stack([np.arange(n), np.arange(1, n + 1)], axis=1)
    return nodes, elems


def portal(width: float, height: float, n_col: int, n_beam: int) -> Tuple[np.ndarray, np.ndarray]:
    """Fixed-base portal in the x-z plane: columns at x = 0 and x = width, beam at z = height."""
    nodes: List[np.ndarray] = []
    elems: List[List[int]] = []

    def add(points):
        base = len(nodes)
        nodes.extend(points)
        return base

    left = [np.array([0.0, 0.0, height * k / n_col]) for k in range(n_col + 1)]
    right = [np.array([width, 0.0, height * k / n_col]) for k in range(n_col + 1)]
    bl = add(left)
    br = add(right)
    elems += [[bl + k, bl + k + 1] for k in range(n_col)]
    elems += [[br + k, br + k + 1] for k in range(n_col)]
    top_left, top_right = bl + n_col, br + n_col
    prev = top_left
    for k in range(1, n_beam):
        idx = add([np.array([width * k / n_beam, 0.0, height])])
        elems.append([prev, idx])
        prev = idx
    elems.append([prev, top_right])
    return np.array(nodes), np.array(elems, dtype=np.int64)


def plate(lx: float, ly: float, nx: int, ny: int) -> Tuple[np.ndarray, np.ndarray]:
    """Plate in the x-y plane centred on the origin; element normals point +z."""
    xs = np.linspace(-lx / 2, lx / 2, nx + 1)
    ys = np.linspace(-ly / 2, ly / 2, ny + 1)
    X, Y = np.meshgrid(xs, ys, indexing="xy")
    nodes = np.stack([X.ravel(), Y.ravel(), np.zeros(X.size)], axis=1)
    idx = np.arange((nx + 1) * (ny + 1)).reshape(ny + 1, nx + 1)
    elems = np.stack([idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()], axis=1)
    return nodes, elems.astype(np.int64)


def cylinder(radius: float, length: float, angle_deg: float, n_around: int, n_axial: int) -> Tuple[np.ndarray, np.ndarray]:
    """Cylindrical shell with its axis along z, centred on z = 0. 360 degrees closes into a tube. Normals point outward."""
    full = angle_deg >= 359.999
    nphi = n_around if full else n_around + 1
    phis = np.linspace(0.0, np.deg2rad(min(angle_deg, 360.0)), n_around + 1)
    if full:
        phis = phis[:-1]
    zs = np.linspace(-length / 2, length / 2, n_axial + 1)
    PH, Z = np.meshgrid(phis, zs, indexing="xy")
    nodes = np.stack([(radius * np.cos(PH)).ravel(), (radius * np.sin(PH)).ravel(), Z.ravel()], axis=1)
    idx = np.arange(nphi * (n_axial + 1)).reshape(n_axial + 1, nphi)
    i0 = idx[:-1, :]
    i1 = np.roll(idx[:-1, :], -1, axis=1) if full else idx[:-1, :]
    if full:
        a, b = idx[:-1, :].ravel(), np.roll(idx[:-1, :], -1, axis=1).ravel()
        c, d = np.roll(idx[1:, :], -1, axis=1).ravel(), idx[1:, :].ravel()
        elems = np.stack([a, b, c, d], axis=1)
    else:
        a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
        c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
        elems = np.stack([a, b, c, d], axis=1)
    return nodes, elems.astype(np.int64)


def generate(kind: str, dims: Dict[str, float], element_type: str, divisions: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """Dispatch used by the API. `dims` keys depend on the kind (see KINDS)."""
    if element_type not in STRUCT_TYPES:
        raise StructuralError(f"'{element_type}' is not a structural element type")
    g = lambda k, d: float(dims.get(k, d))  # noqa: E731
    div = max(1, min(int(divisions), 200))
    _check(dims, "width", "height", "length", "radius", "span", "depth")
    line_type = element_type in ("T3D2", "B31", "SPRING2")
    if kind == "line":
        if not line_type:
            raise StructuralError("A line needs beam, truss or spring elements.")
        p1 = [g("x1", 0.0), g("y1", 0.0), g("z1", 0.0)]
        p2 = [g("x2", 1.0), g("y2", 0.0), g("z2", 0.0)]
        return line(p1, p2, div)
    if kind == "portal":
        if element_type not in ("T3D2", "B31"):
            raise StructuralError("A portal frame needs beam or truss elements.")
        return portal(g("width", 2.0), g("height", 1.5), max(1, div // 2), max(1, div))
    if kind == "plate":
        if element_type != "S4":
            raise StructuralError("A plate needs shell (S4) elements.")
        lx, ly = g("length", 1.0), g("width", 1.0)
        ref = min(lx, ly)
        return plate(lx, ly, max(1, int(round(div * lx / ref))), max(1, int(round(div * ly / ref))))
    if kind == "cylinder":
        if element_type != "S4":
            raise StructuralError("A cylindrical shell needs shell (S4) elements.")
        r, L, ang = g("radius", 0.5), g("length", 2.0), g("angle", 360.0)
        if not 1.0 <= ang <= 360.0:
            raise StructuralError("The arc angle must be between 1 and 360 degrees")
        n_around = max(3, div * 2 if ang >= 359.999 else max(2, int(round(div * ang / 90.0))))
        n_axial = max(1, int(round(n_around * L / (r * np.deg2rad(ang)) / (1 if ang < 359.999 else 1))))
        n_axial = max(1, min(n_axial, 200))
        return cylinder(r, L, ang, n_around, n_axial)
    raise StructuralError(f"Unknown structural shape '{kind}'. Use one of: {', '.join(KINDS)}")
