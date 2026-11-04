"""Pre-run validation ("case check"): errors block a run, warnings and info guide the user.

Dimension-aware (2-D / 3-D) and backend-aware: the selected solver backend adds its own checks
(capabilities, availability, fallback that will be used).
"""
from __future__ import annotations

import math

from ..config import get_settings
from ..schemas import CaseConfig, CheckItem, CheckResult
from ..solver.grid3d import build_grid_nd, obstacle_bounds, probe_point

# measured single-core cost of one native time step: (fixed, per cell) seconds
NATIVE_COST = {2: (1.5e-3, 4.5e-7), 3: (2.0e-3, 2.2e-6)}


def check_case(case: CaseConfig) -> CheckResult:
    s = get_settings()
    items: list[CheckItem] = []

    def add(level, section, msg):
        items.append(CheckItem(level=level, section=section, message=msg))

    nd = case.ndim
    m, d, mesh = case.material, case.domain, case.mesh
    b = case.boundaries
    sides = {name: getattr(b, name) for name in case.sides()}
    n = (mesh.nx, mesh.ny, mesh.nz)[:nd]
    L = (d.width, d.height, d.depth)[:nd]
    h = tuple(l / k for l, k in zip(L, n))
    n_cells = case.n_cells

    # ---- mesh / geometry
    if n_cells > s.max_cells:
        add("error", "mesh", f"{n_cells:,} cells exceeds the server limit of {s.max_cells:,}. Reduce "
                             + ("nx / ny / nz." if nd == 3 else "nx / ny."))
    if nd == 3 and mesh.nz < 4:
        add("error", "mesh", "A 3-D case needs at least 4 cells in z.")
    grid = build_grid_nd(case)
    fluid = grid.n_fluid
    solid_frac = 1 - fluid / n_cells
    if fluid == 0:
        add("error", "geometry", "Obstacles fill the entire domain - there are no fluid cells.")
    elif solid_frac > 0.6:
        add("warning", "geometry", f"{solid_frac:.0%} of the domain is solid.")
    aspect = max(h) / min(h)
    if aspect > 5:
        add("warning", "mesh", f"Cells are strongly stretched (aspect ratio {aspect:.1f}). "
                               "Use cell counts proportional to the domain size.")
    min_cells = 27 if nd == 3 else 9
    for k, ob in enumerate(case.obstacles):
        cells = int((grid.solid_id == k).sum())
        lo, hi = obstacle_bounds(ob, nd, d.depth)
        if any(hi[a] < 0 or lo[a] > L[a] for a in range(nd)):
            add("warning", "geometry", f"'{ob.name}' lies completely outside the domain and is ignored.")
        elif cells == 0:
            add("warning", "geometry", f"'{ob.name}' is smaller than a mesh cell and is not represented. Refine the mesh or enlarge it.")
        elif cells < min_cells:
            add("warning", "geometry", f"'{ob.name}' is covered by only {cells} cells - its shape and loads will be poorly resolved.")
        if ob.thermal == "fixed" and not case.models.energy:
            add("warning", "models", f"'{ob.name}' has a fixed temperature but the energy equation is off.")
        if nd == 2 and ob.shape == "sphere":
            add("info", "geometry", f"'{ob.name}' is a sphere; in 2-D it is treated as a circle.")
        if nd == 2 and ob.shape == "mesh":
            add("info", "geometry", f"'{ob.name}' is an imported solid; in 2-D its section through the centre (z = 0 of the "
                                    "source geometry) is used. Switch to 3-D to flow around the whole body.")
    for p in case.probes:
        pt = probe_point(p, nd, d.depth)
        if not all(0 <= pt[a] <= L[a] for a in range(nd)):
            add("warning", "monitors", f"Probe '{p.name}' is outside the domain.")

    # ---- boundaries
    types = [v.type for v in sides.values()]
    has_in, has_out = "inlet" in types, "outlet" in types
    if has_in and not has_out:
        add("error", "boundaries", "An incompressible domain with an inlet needs at least one outlet.")
    if has_out and not has_in:
        add("warning", "boundaries", "There is an outlet but no inlet; flow can only be driven by buoyancy or moving walls.")
    for name, v in sides.items():
        if v.type == "inlet" and v.velocity <= 0:
            add("error", "boundaries", f"Inlet '{name}' has zero velocity.")
        if v.type == "wall" and v.thermal == "fixed" and not case.models.energy:
            add("warning", "models", f"Wall '{name}' has a fixed temperature but the energy equation is off.")
    ini = case.initial
    driven = (
        has_in
        or any(v.type == "wall" and v.wall_velocity != 0 for v in sides.values())
        or ini.velocity_x != 0 or ini.velocity_y != 0 or (nd == 3 and ini.velocity_z != 0)
        or (case.models.energy and case.models.buoyancy)
    )
    if not driven:
        add("warning", "boundaries", "Nothing drives the flow (no inlet, moving wall or buoyancy) - the solution will stay at rest.")

    # ---- models
    mo = case.models
    g_vec = (mo.gravity_x, mo.gravity_y, mo.gravity_z)[:nd]
    if mo.buoyancy and not mo.energy:
        add("warning", "models", "Buoyancy requires the energy equation; it is ignored while energy is off.")
    if mo.buoyancy and mo.energy and not any(g_vec):
        add("warning", "models", "Buoyancy is on but gravity is zero.")
    if mo.energy and not (
        any(v.thermal == "fixed" and v.type == "wall" for v in sides.values())
        or has_in
        or any(o.thermal == "fixed" for o in case.obstacles)
        or case.heat_sources
    ):
        add("info", "models", "Energy is on but no thermal driver (fixed temperature, inlet or heat source) is defined.")

    # ---- physics estimates
    speeds = [v.velocity for v in sides.values() if v.type == "inlet"]
    speeds += [abs(v.wall_velocity) for v in sides.values() if v.type == "wall"]
    u_ref = max(speeds) if speeds else 0.0
    Lref = min(L)
    nu = m.viscosity / m.density
    alpha = m.conductivity / (m.density * m.specific_heat)
    Pr = nu / alpha
    stats: dict = {
        "dimension": nd,
        "cells": n_cells,
        "fluid_cells": fluid,
        "dx": h[0],
        "dy": h[1],
        "aspect_ratio": aspect,
        "solid_fraction": solid_frac,
        "prandtl": Pr,
        "kinematic_viscosity": nu,
        "thermal_diffusivity": alpha,
    }
    if nd == 3:
        stats["dz"] = h[2]
    turbulent_model = mo.turbulence != "laminar"
    if u_ref > 0:
        Re = u_ref * Lref / nu
        stats["reynolds"] = Re
        add("info", "physics", f"Reference Reynolds number ≈ {Re:,.0f} (U = {u_ref:g} m/s, L = {Lref:g} m).")
        if Re > 2300 and not turbulent_model:
            if case.run.mode != "transient":
                add("warning", "physics", "Re > 2300: real flow would be turbulent. This solver is laminar (no turbulence model) - "
                                          "results are only valid for laminar/unsteady laminar flow. Choose a k-ε / k-ω SST model with the OpenFOAM backend.")
            else:
                add("warning", "physics", "Re > 2300: no turbulence model is active; resolved unsteady results are not turbulence-accurate.")
        if turbulent_model and Re < 2000:
            add("info", "physics", "A RANS turbulence model is selected for a laminar Reynolds number.")
        re_cell = u_ref * max(h) / nu
        stats["cell_reynolds"] = re_cell
        if re_cell > 200 and not turbulent_model:
            add("warning", "mesh", f"Cell Reynolds number is {re_cell:.0f}; the mesh is coarse for this flow and results will be numerically diffusive.")
    if mo.energy and mo.buoyancy:
        temps = [v.temperature for v in sides.values() if v.thermal == "fixed" and v.type == "wall"]
        temps += [o.temperature for o in case.obstacles if o.thermal == "fixed"]
        if len(temps) > 1:
            dT = max(temps) - min(temps)
            gm = math.sqrt(sum(c * c for c in g_vec))
            Ra = gm * m.thermal_expansion * dT * Lref**3 / (nu * alpha)
            stats["rayleigh"] = Ra
            add("info", "physics", f"Rayleigh number ≈ {Ra:.3g} (Pr = {Pr:.3g}).")
            if Ra > 1e8 and not turbulent_model:
                add("warning", "physics", "Ra > 1e8: natural convection would be turbulent; not modelled.")
    c0, c1 = NATIVE_COST[nd]
    steps_per_s = 1.0 / (c0 + c1 * n_cells)
    stats["steps_per_second_estimate"] = steps_per_s
    if case.run.mode == "transient":
        dt_diff = 1.0 / (2 * max(nu, alpha if mo.energy else 0) * sum(1 / x**2 for x in h))
        dt = case.run.cfl * min(dt_diff, min(h) / max(u_ref, 1e-9))
        steps = case.run.end_time / dt
        stats["estimated_steps"] = steps
        stats["estimated_wall_seconds"] = steps / steps_per_s
        if case.solver.backend == "native":
            add("info", "run", f"Roughly {steps:,.0f} time steps (≈ {steps / steps_per_s:,.0f} s on a typical server core).")
            if steps > case.run.max_iterations:
                add("warning", "run", "Estimated steps exceed 'max iterations'; the run may stop before the end time.")
    if case.run.fixed_dt is not None:
        dt_diff = 1.0 / (2 * max(nu, alpha if mo.energy else 0) * sum(1 / x**2 for x in h))
        if case.run.fixed_dt > dt_diff or (u_ref > 0 and case.run.fixed_dt > min(h) / u_ref):
            add("warning", "run", "The fixed time step exceeds the explicit stability limit; the native solver will probably diverge.")

    # ---- solver backend
    _check_backend(case, add, stats)

    ok = not any(i.level == "error" for i in items)
    return CheckResult(ok=ok, items=items, stats=stats)


def _check_backend(case: CaseConfig, add, stats: dict) -> None:
    from ..solver.backends import BACKENDS

    b = BACKENDS[case.solver.backend]
    av = b.availability()
    target = b
    if not av.ok:
        if b.fallback_id and case.solver.allow_fallback:
            target = BACKENDS[b.fallback_id]
            add("info", "general", f"{b.label} is not available on this server ({av.detail}). "
                                   f"The run will use the built-in equivalent: {target.label}.")
        else:
            add("error", "general", f"{b.label} is not available on this server ({av.detail}). "
                                    "Install it, allow the Python fallback, or choose another solver.")
    elif b.id != "native":
        add("info", "general", f"Solver: {b.label} via {av.mode} ({av.detail}).")
    stats["backend"] = target.id
    stats["backend_requested"] = b.id
    stats["backend_available"] = av.ok
    msgs = target.check(case)
    if target is not b:  # capability errors of the requested backend matter too (e.g. turbulence)
        seen = {m.message for m in msgs}
        msgs += [m for m in b.check(case) if m.level == "error" and m.message not in seen]
    for msg in msgs:
        add(msg.level, msg.section, msg.message)
