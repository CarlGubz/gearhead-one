"""The ``SolverBackend`` seam: every solver (native, OpenFOAM, FEniCSx, PyFR, Python fallbacks)
implements this small interface and returns its result on the structured grid of the case."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..grid3d import build_grid_nd
from ..ns3d import as3d, boundary_pressures_cc, monitors_cc, obstacle_loads_cc, result_meta_cc, vorticity


class BackendUnavailable(RuntimeError):
    """The requested external solver cannot run here and no fallback is allowed."""


class BackendFailed(RuntimeError):
    """The external solver ran but failed (its log is in the run directory)."""


@dataclass
class Availability:
    ok: bool
    mode: str = ""  # native | docker | wsl | python
    detail: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "mode": self.mode, "detail": self.detail}


@dataclass
class BackendResult:
    status: str  # completed | cancelled
    converged: bool
    iterations: int
    sim_time: float
    meta: dict
    solid: np.ndarray
    times: list[float] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


@dataclass
class CheckMsg:
    level: str
    section: str
    message: str


class SolverBackend:
    id = "base"
    label = "Base"
    description = ""
    #: what the backend can solve; used by the case check and the UI
    capabilities: dict = {"dims": (2, 3), "steady": True, "transient": True, "energy": True, "buoyancy": True,
                          "turbulence": False}
    #: id of the backend used when this one is unavailable (None = no fallback)
    fallback_id: str | None = None
    #: True when this backend writes an input deck that can be downloaded / run elsewhere
    exportable = False

    def availability(self) -> Availability:
        return Availability(True, "python", "built in")

    def check(self, case) -> list[CheckMsg]:
        out = []
        caps = self.capabilities
        if case.ndim not in caps["dims"]:
            out.append(CheckMsg("error", "general", f"{self.label} does not support {case.ndim}-D cases."))
        if case.models.energy and not caps["energy"]:
            out.append(CheckMsg("error", "models", f"{self.label} solves isothermal flow only - switch the energy equation off or pick another solver."))
        if case.models.turbulence != "laminar" and not caps["turbulence"]:
            out.append(CheckMsg("error", "models", f"Turbulence models need the OpenFOAM backend ({self.label} is laminar)."))
        if case.run.mode == "transient" and not caps["transient"]:
            out.append(CheckMsg("error", "run", f"{self.label} does not support transient runs."))
        return out

    def run(self, case, hooks, run_dir: Path) -> BackendResult:  # pragma: no cover - interface
        raise NotImplementedError

    def export_case(self, case, dest: Path) -> list[Path]:  # pragma: no cover - interface
        raise NotImplementedError(f"{self.label} has no exportable input deck")

    def info(self) -> dict:
        av = self.availability()
        return {"id": self.id, "label": self.label, "description": self.description,
                "capabilities": {**self.capabilities, "dims": list(self.capabilities["dims"])},
                "available": av.as_dict(), "fallback": self.fallback_id, "exportable": self.exportable}


# --------------------------------------------------------------------------- shared helpers


def cell_centres(case) -> tuple:
    g = build_grid_nd(case)
    return g, np.stack([c.ravel() for c in g.mesh_coords()], axis=1)


def frame_from_cc(g, fields: dict) -> dict[str, np.ndarray]:
    """Arrays in the cell-centred result format (float32, shape (nx, ny, nz))."""
    zero = np.zeros(g.n)
    return {c: as3d(np.where(g.solid, 0.0, fields.get(c, zero)) if c != "T" else fields.get("T", zero)).astype(np.float32)
            for c in ("u", "v", "w", "p", "T")}


def summary_from_cc(case, g, fields: dict, *, iterations: int, sim_time: float, wall: float, converged: bool,
                    notes: list[str], backend: str, loads: dict | None = None) -> dict:
    """Run summary computed from cell-centred fields (same keys as the native runner)."""
    nd = g.ndim
    m = case.material
    F = g.fluid
    uc = [fields["u"], fields["v"]] + ([fields["w"]] if nd == 3 else [])
    speed = np.sqrt(sum(u**2 for u in uc))
    speeds = [b.velocity for b in (getattr(case.boundaries, s) for s in case.sides()) if b.type == "inlet"]
    speeds += [abs(b.wall_velocity) for b in (getattr(case.boundaries, s) for s in case.sides()) if b.type == "wall"]
    u_ref = max(speeds) if speeds else max(float(speed[F].max()), 1e-12)
    summ = {
        "iterations": int(iterations),
        "sim_time": float(sim_time),
        "wall_time_s": float(wall),
        "converged": bool(converged),
        "cells": int(g.n_cells),
        "fluid_cells": int(F.sum()),
        "max_speed": float(speed[F].max()),
        "p_min": float(fields["p"][F].min()),
        "p_max": float(fields["p"][F].max()),
        "mass_imbalance": float(fields.get("_mass_imbalance", 0.0)),
        "reynolds_ref": m.density * u_ref * min(g.L) / m.viscosity,
        "notes": notes,
        "backend": backend,
        "dimension": nd,
    }
    if case.models.energy:
        summ["T_min"] = float(fields["T"][F].min())
        summ["T_max"] = float(fields["T"][F].max())
    bcs = {s: getattr(case.boundaries, s) for s in case.sides()}
    inlet_p, outlet_p = boundary_pressures_cc(g, bcs, fields["p"])
    if inlet_p and outlet_p:
        summ["pressure_drop"] = float(np.mean(inlet_p) - np.mean(outlet_p))
    if loads is None and case.obstacles:
        loads = obstacle_loads_cc(g, case.obstacles, fields["p"], uc, fields["T"], m.viscosity, m.conductivity, case.models.energy)
    if loads:
        summ["obstacles"] = loads
    return summ


def monitors_from_cc(case, g, fields: dict, dt: float = 0.0) -> dict[str, float]:
    m = case.material
    uc = [fields["u"], fields["v"]] + ([fields["w"]] if g.ndim == 3 else [])
    f = dict(fields)
    f["speed"] = np.sqrt(sum(u**2 for u in uc))
    loads = obstacle_loads_cc(g, case.obstacles, fields["p"], uc, fields["T"], m.viscosity, m.conductivity, case.models.energy)
    return monitors_cc(case, g, f, 0.0, dt, loads, case.models.energy)


def meta_for(case, g, backend: str, iteration: int, **extra) -> dict:
    return result_meta_cc(g, case.models.energy, case.material.density, iteration, case.run.mode, backend=backend, **extra)


def full_fields(g, arrays: dict) -> dict:
    """Ensure u, v, w, p, T and vorticity exist with the grid shape (missing components = 0)."""
    zero = np.zeros(g.n)
    f = {c: np.asarray(arrays.get(c, zero), dtype=float).reshape(g.n) for c in ("u", "v", "w", "p", "T")}
    f["vorticity"] = vorticity([f["u"], f["v"]] + ([f["w"]] if g.ndim == 3 else []), g.h)
    return f


class ProgressTracker:
    """Turns residual / time information into a 0..0.99 progress fraction like the native runner."""

    def __init__(self, case):
        self.case = case
        self.r_first = None
        self.t0 = time.time()

    def fraction(self, iteration: int, sim_time: float, residual: float | None) -> float:
        rs = self.case.run
        if rs.mode == "transient":
            return min(max(sim_time / rs.end_time, 0.0), 0.99)
        frac = iteration / max(rs.max_iterations, 1)
        if residual and residual > 0:
            if self.r_first is None and iteration > 3:
                self.r_first = residual
            if self.r_first and self.r_first > rs.tolerance:
                fr = (math.log10(self.r_first) - math.log10(residual)) / (math.log10(self.r_first) - math.log10(rs.tolerance))
                frac = max(frac, fr)
        return min(max(frac, 0.0), 0.99)
