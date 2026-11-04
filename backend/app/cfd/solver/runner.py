"""Drives a :class:`FlowSolver` through a complete run and reports through callbacks."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .ns2d import FlowSolver, SolverDiverged
from .ns3d import FlowSolver3D

MAX_FRAME_BYTES = 96 * 1024 * 1024


@dataclass
class Hooks:
    log: Callable[[str, str], None] = lambda level, msg: None
    on_monitor: Callable[[int, float, dict, dict], None] = lambda it, t, res, vals: None
    on_progress: Callable[[float, int, float], None] = lambda frac, it, t: None
    on_snapshot: Callable[[FlowSolver], None] = lambda s: None
    should_stop: Callable[[], bool] = lambda: False
    # used by external backends that do not expose a FlowSolver: (meta, solid, time, frame arrays)
    save_latest: Callable[[dict, np.ndarray, float, dict], None] = lambda meta, solid, t, frame: None


@dataclass
class RunResult:
    status: str  # completed | cancelled
    converged: bool
    solver: FlowSolver
    times: list[float] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _summary(s: FlowSolver, converged: bool, wall: float, notes: list[str]) -> dict:
    f = s.cell_fields()
    fluid = s.F
    summ = {
        "iterations": s.iteration,
        "sim_time": s.time,
        "wall_time_s": wall,
        "converged": converged,
        "cells": int(s.S.size),
        "fluid_cells": int(fluid.sum()),
        "max_speed": float(f["speed"][fluid].max()),
        "p_min": float(f["p"][fluid].min()),
        "p_max": float(f["p"][fluid].max()),
        "mass_imbalance": s.mass_imbalance(),
        "reynolds_ref": s.rho * s.u_ref * s.L_ref / s.mu,
        "notes": notes,
    }
    if s.energy:
        summ["T_min"] = float(f["T"][fluid].min())
        summ["T_max"] = float(f["T"][fluid].max())
    # pressure drop between inlet-adjacent and outlet-adjacent cells (gauge)
    inlet_p, outlet_p = s.boundary_pressures()
    if inlet_p and outlet_p:
        summ["pressure_drop"] = float(np.mean(inlet_p) - np.mean(outlet_p))
    loads = s.obstacle_loads()
    if loads:
        summ["obstacles"] = loads
    return summ


def make_solver(case):
    """The native solver for a case: the validated 2-D solver, or the N-D solver for 3-D cases."""
    return FlowSolver3D(case) if case.ndim == 3 else FlowSolver(case)


def run_simulation(case, hooks: Hooks | None = None, solver=None) -> RunResult:
    hooks = hooks or Hooks()
    s = solver if solver is not None else make_solver(case)
    rs = case.run
    notes: list[str] = []
    hooks.log("info", s.describe_mesh())
    hooks.log(
        "info",
        f"nu={s.nu:.4g} m²/s, Re_ref≈{s.rho * s.u_ref * s.L_ref / s.mu:.4g}"
        + (f", alpha={s.alpha:.4g} m²/s" if s.energy else ""),
    )
    steady = rs.mode == "steady"
    decoupled_energy = steady and s.energy and not s.buoyancy and s.supports_direct_energy
    if decoupled_energy:
        hooks.log("info", "Steady, non-buoyant energy: flow is converged first, then temperature is solved directly.")
    t_wall0 = time.time()
    last_prog = last_snap = 0.0
    frames: list[dict] = []
    times: list[float] = []
    frame_bytes = sum(a.nbytes for a in s.state_arrays().values())
    max_frames = max(2, min(rs.snapshot_count, MAX_FRAME_BYTES // max(frame_bytes, 1)))
    frame_dt = rs.end_time / max(max_frames - 1, 1)
    next_frame = 0.0
    if not steady:
        frames.append(s.state_arrays())
        times.append(0.0)
        next_frame = frame_dt
    converged = False
    status = "completed"
    res = None
    r_first = None
    try:
        while True:
            res = s.step(update_energy=not decoupled_energy)
            it = s.iteration
            if it % rs.monitor_interval == 0 or it == 1:
                hooks.on_monitor(it, s.time, res.as_dict(s.energy and not decoupled_energy), s.monitors())
            now = time.time()
            if now - last_snap > 1.5:
                hooks.on_snapshot(s)
                last_snap = now
            if now - last_prog > 0.4:
                if steady:
                    r = res.max()
                    if r_first is None and it > 20 and r > 0:
                        r_first = r
                    frac_res = 0.0
                    if r_first and r > 0 and r_first > rs.tolerance:
                        frac_res = (math.log10(r_first) - math.log10(r)) / (math.log10(r_first) - math.log10(rs.tolerance))
                    frac = max(it / rs.max_iterations, frac_res)
                else:
                    frac = s.time / rs.end_time
                hooks.on_progress(min(max(frac, 0.0), 0.99), it, s.time)
                last_prog = now
                if hooks.should_stop():
                    status = "cancelled"
                    break
            if not steady and s.time >= next_frame and len(frames) < max_frames:
                frames.append(s.state_arrays())
                times.append(s.time)
                next_frame += frame_dt
            if steady:
                r = res.max()
                if it > 30 and r < rs.tolerance:
                    converged = True
                    break
                if it >= rs.max_iterations:
                    notes.append(f"Iteration limit ({rs.max_iterations}) reached before convergence (residual {r:.2e}).")
                    hooks.log("warning", notes[-1])
                    break
            else:
                if s.time >= rs.end_time:
                    converged = True
                    break
                if it >= rs.max_iterations:
                    notes.append(f"Iteration limit reached at t={s.time:.4g} s (end time {rs.end_time:g} s).")
                    hooks.log("warning", notes[-1])
                    break
    except SolverDiverged as exc:
        hooks.log("error", str(exc))
        raise

    if status == "completed" and decoupled_energy:
        hooks.log("info", f"Flow converged in {s.iteration} iterations; solving temperature field …")
        if not s.solve_energy_steady():
            notes.append("Direct energy solve unavailable; fell back to pseudo-time stepping.")
            hooks.log("warning", notes[-1])
            for _ in range(rs.max_iterations):
                res = s.step(update_energy=True)
                if res.T < rs.tolerance:
                    break
    if status == "completed":
        if steady or not frames or times[-1] < s.time - 1e-12:
            frames.append(s.state_arrays())
            times.append(s.time)
        else:
            frames[-1] = s.state_arrays()
            times[-1] = s.time
        hooks.on_monitor(s.iteration, s.time, (res.as_dict(s.energy and not decoupled_energy) if res else {}), s.monitors())
    wall = time.time() - t_wall0
    summary = _summary(s, converged, wall, notes)
    hooks.log(
        "info" if status == "completed" else "warning",
        f"{'Finished' if status == 'completed' else 'Cancelled'} after {s.iteration} iterations, "
        f"t={s.time:.4g} s, wall {wall:.1f} s" + (" (converged)" if converged else ""),
    )
    return RunResult(status, converged, s, times, frames, summary)
