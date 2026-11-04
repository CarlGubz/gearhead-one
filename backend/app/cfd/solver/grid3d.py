"""Uniform Cartesian grid in 2-D or 3-D with immersed solid obstacles (cell-mask / staircase).

This is the dimension-generic counterpart of :mod:`grid`. Every solver backend (native 3-D,
OpenFOAM, FEniCSx, PyFR and their Python fallbacks) uses :func:`obstacle_mask` so that "which
cell is solid" means exactly the same thing everywhere - the rule is *cell centre inside the
shape*, which is also what OpenFOAM's ``topoSet`` uses.

3-D shape conventions
---------------------
* ``rectangle`` -> box ``width x height x depth`` centred at ``(x, y, z)``
* ``circle``    -> cylinder of ``radius`` whose axis is parallel to z, spanning ``depth``
* ``sphere``    -> sphere of ``radius``
* ``mesh``      -> closed triangle surface (an FEA model / CAD part sent to CFD), vertices relative to ``(x, y, z)``;
                   see :mod:`meshsolid`
* ``z = None`` means mid-depth, ``depth = None`` means the full domain depth.

In 2-D ``sphere`` is treated as ``circle`` and ``mesh`` is cut by the plane through its centre.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import meshsolid

AXIS_SIDES = (("left", "right"), ("bottom", "top"), ("back", "front"))
AXIS_NAMES = ("x", "y", "z")


@dataclass
class GridND:
    n: tuple[int, ...]
    L: tuple[float, ...]
    h: tuple[float, ...]
    centres: list[np.ndarray]
    solid: np.ndarray  # shape n, bool
    solid_id: np.ndarray  # shape n, -1 = fluid else obstacle index
    solid_T: np.ndarray  # shape n, NaN = adiabatic

    @property
    def ndim(self) -> int:
        return len(self.n)

    @property
    def fluid(self) -> np.ndarray:
        return ~self.solid

    @property
    def n_fluid(self) -> int:
        return int(self.fluid.sum())

    @property
    def n_cells(self) -> int:
        return int(np.prod(self.n))

    @property
    def cell_volume(self) -> float:
        return float(np.prod(self.h))

    # 2-D style accessors so code written for :class:`grid.Grid` keeps working
    nx = property(lambda s: s.n[0])
    ny = property(lambda s: s.n[1])
    nz = property(lambda s: s.n[2] if len(s.n) > 2 else 1)
    lx = property(lambda s: s.L[0])
    ly = property(lambda s: s.L[1])
    lz = property(lambda s: s.L[2] if len(s.L) > 2 else 0.0)
    dx = property(lambda s: s.h[0])
    dy = property(lambda s: s.h[1])
    dz = property(lambda s: s.h[2] if len(s.h) > 2 else 0.0)

    def mesh_coords(self) -> list[np.ndarray]:
        return np.meshgrid(*self.centres, indexing="ij")


def z_span(item, depth: float) -> tuple[float, float]:
    """Centre and half-extent in z of an obstacle / heat source (full depth when unset)."""
    zc = item.z if item.z is not None else depth / 2
    d = item.depth if getattr(item, "depth", None) is not None else 2.0 * depth + 2.0 * abs(zc)
    return zc, d / 2


def obstacle_mask(ob, coords: list[np.ndarray], depth: float = 1.0) -> np.ndarray:
    """Boolean mask of points (cell centres) inside obstacle ``ob``; ``coords`` = [X, Y(, Z)]."""
    X, Y = coords[0], coords[1]
    nd = len(coords)
    if ob.shape == "mesh":
        return meshsolid.mask_on_grid(ob, coords, depth)
    shape = ob.shape if not (nd == 2 and ob.shape == "sphere") else "circle"
    if shape == "sphere":
        zc = ob.z if ob.z is not None else depth / 2
        return (X - ob.x) ** 2 + (Y - ob.y) ** 2 + (coords[2] - zc) ** 2 <= ob.radius**2
    if shape == "rectangle":
        m = (np.abs(X - ob.x) <= ob.width / 2) & (np.abs(Y - ob.y) <= ob.height / 2)
    else:
        m = (X - ob.x) ** 2 + (Y - ob.y) ** 2 <= ob.radius**2
    if nd == 3:
        zc, hz = z_span(ob, depth)
        m &= np.abs(coords[2] - zc) <= hz
    return m


def source_mask(hs, coords: list[np.ndarray], depth: float = 1.0) -> np.ndarray:
    m = (np.abs(coords[0] - hs.x) <= hs.width / 2) & (np.abs(coords[1] - hs.y) <= hs.height / 2)
    if len(coords) == 3:
        zc, hz = z_span(hs, depth)
        m &= np.abs(coords[2] - zc) <= hz
    return m


def obstacle_bounds(ob, ndim: int, depth: float) -> tuple[list[float], list[float]]:
    """Axis-aligned bounding box (lo, hi) of an obstacle."""
    if ob.shape == "mesh":
        lo, hi = meshsolid.bounds(ob, ndim, depth)
        if ndim == 3:
            lo[2], hi[2] = max(lo[2], 0.0), min(hi[2], depth)
        return lo, hi
    if ob.shape == "rectangle":
        lo = [ob.x - ob.width / 2, ob.y - ob.height / 2]
        hi = [ob.x + ob.width / 2, ob.y + ob.height / 2]
    else:
        lo = [ob.x - ob.radius, ob.y - ob.radius]
        hi = [ob.x + ob.radius, ob.y + ob.radius]
    if ndim == 3:
        if ob.shape == "sphere":
            zc = ob.z if ob.z is not None else depth / 2
            lo.append(zc - ob.radius)
            hi.append(zc + ob.radius)
        else:
            zc, hz = z_span(ob, depth)
            lo.append(max(zc - hz, 0.0))
            hi.append(min(zc + hz, depth))
    return lo, hi


def probe_point(pr, ndim: int, depth: float) -> tuple[float, ...]:
    if ndim == 3:
        return (pr.x, pr.y, pr.z if pr.z is not None else depth / 2)
    return (pr.x, pr.y)


def build_grid_nd(case, ndim: int | None = None) -> GridND:
    nd = ndim or case.ndim
    n = (case.mesh.nx, case.mesh.ny, case.mesh.nz)[:nd]
    L = (case.domain.width, case.domain.height, case.domain.depth)[:nd]
    h = tuple(l / k for l, k in zip(L, n))
    centres = [(np.arange(k) + 0.5) * d for k, d in zip(n, h)]
    coords = np.meshgrid(*centres, indexing="ij")
    solid = np.zeros(n, dtype=bool)
    solid_id = np.full(n, -1, dtype=int)
    solid_T = np.full(n, np.nan)
    for k, ob in enumerate(case.obstacles):
        m = obstacle_mask(ob, coords, case.domain.depth)
        solid |= m
        solid_id[m] = k
        solid_T[m] = ob.temperature if ob.thermal == "fixed" else np.nan
    return GridND(tuple(n), tuple(L), h, centres, solid, solid_id, solid_T)


def sample_nd(field: np.ndarray, grid: GridND, point) -> float:
    """Multilinear interpolation of a cell-centred field at a physical point (clamped)."""
    idx0, w = [], []
    for a in range(grid.ndim):
        f = float(np.clip((point[a] - grid.h[a] / 2) / grid.h[a], 0, grid.n[a] - 1))
        i0 = int(np.floor(f))
        idx0.append(i0)
        w.append(f - i0)
    total = 0.0
    for corner in range(2**grid.ndim):
        weight = 1.0
        idx = []
        for a in range(grid.ndim):
            bit = (corner >> a) & 1
            idx.append(min(idx0[a] + bit, grid.n[a] - 1))
            weight *= w[a] if bit else 1 - w[a]
        if weight:
            total += weight * float(field[tuple(idx)])
    return total
