"""Uniform Cartesian grid with immersed solid obstacles (staircase / cell-mask approach)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Grid:
    nx: int
    ny: int
    lx: float
    ly: float
    dx: float
    dy: float
    xc: np.ndarray  # (nx,) cell-centre x
    yc: np.ndarray  # (ny,) cell-centre y
    solid: np.ndarray  # (nx, ny) bool
    solid_id: np.ndarray  # (nx, ny) int, -1 = fluid else obstacle index
    solid_T: np.ndarray  # (nx, ny) float, NaN = adiabatic

    @property
    def fluid(self) -> np.ndarray:
        return ~self.solid

    @property
    def n_fluid(self) -> int:
        return int(self.fluid.sum())


def build_grid(case) -> Grid:
    nx, ny = case.mesh.nx, case.mesh.ny
    lx, ly = case.domain.width, case.domain.height
    dx, dy = lx / nx, ly / ny
    xc = (np.arange(nx) + 0.5) * dx
    yc = (np.arange(ny) + 0.5) * dy
    X, Y = np.meshgrid(xc, yc, indexing="ij")
    solid = np.zeros((nx, ny), dtype=bool)
    solid_id = np.full((nx, ny), -1, dtype=int)
    solid_T = np.full((nx, ny), np.nan)
    for k, ob in enumerate(case.obstacles):
        if ob.shape == "rectangle":
            m = (np.abs(X - ob.x) <= ob.width / 2) & (np.abs(Y - ob.y) <= ob.height / 2)
        elif ob.shape == "mesh":
            from .meshsolid import mask_on_grid  # imported solid (FEA model / CAD part): its section at z = centre
            m = mask_on_grid(ob, [X, Y], case.domain.depth)
        else:
            m = (X - ob.x) ** 2 + (Y - ob.y) ** 2 <= ob.radius**2
        solid |= m
        solid_id[m] = k
        solid_T[m] = ob.temperature if ob.thermal == "fixed" else np.nan
    return Grid(nx, ny, lx, ly, dx, dy, xc, yc, solid, solid_id, solid_T)
