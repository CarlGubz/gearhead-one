#!/usr/bin/env python3
"""CFDFlow -> FEniCSx (DOLFINx) incompressible Navier-Stokes (+ energy) solver.

Standalone script: needs only DOLFINx (v0.9), NumPy and ``case.json`` (a CFDFlow case) in the
working directory. CFDFlow copies it into the run directory and runs it inside the DOLFINx
environment (native or the ``dolfinx/dolfinx`` Docker image); it can also be run by hand::

    python3 run_fenicsx.py            # reads case.json, writes result_fenicsx.npz

Method
------
* Structured hexahedral (3-D) / quadrilateral (2-D) mesh of the box; obstacle cells (cell centre
  inside the shape - the CFDFlow rule) are removed with ``create_submesh``; exterior facets are
  tagged by side or by obstacle.
* Taylor-Hood Q2/Q1 mixed element for (u, p), plus Q1 temperature when energy is on - one
  monolithic Newton solve (PETSc SNES-style ``NewtonSolver``, MUMPS LU) for the coupled
  Navier-Stokes + Boussinesq + energy system.
* Steady: direct Newton solve, with viscosity continuation (8, 4, 2, 1 x nu) if Newton fails.
  Transient: backward Euler in time, one Newton solve per step.
* Boundary conditions: walls (no-slip / moving), velocity inlets (uniform / parabolic), outlets
  (natural "do-nothing" condition, p ~ 0), symmetry (normal velocity = 0), fixed-temperature or
  adiabatic walls / obstacles. Closed domains pin the pressure at one vertex.
* Output: u, v, w, p, T evaluated at the centres of the CFDFlow grid cells (``result_fenicsx.npz``).
  Progress lines on stdout start with ``CFDFLOW `` followed by JSON.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

STOP_FILE = "STOP"


def emit(kind: str, **kw) -> None:
    print("CFDFLOW " + json.dumps({"type": kind, **kw}), flush=True)


# --------------------------------------------------------------------------- geometry (same rules as CFDFlow grid3d.py)


def z_span(item, depth):
    zc = item.get("z") if item.get("z") is not None else depth / 2
    d = item.get("depth") if item.get("depth") is not None else 2.0 * depth + 2.0 * abs(zc)
    return zc, d / 2


_GRID: dict = {}  # n, h, L of the structured grid (set in main); used by "mesh" obstacles


def _cells_mask(ob, X, inflate=0.0):
    """Imported solid ("mesh" obstacle): CFDFlow wrote its solid cells (flat C-order indices) into case.json."""
    n, h, L = _GRID["n"], _GRID["h"], _GRID["L"]
    nd = X.shape[0]
    solid = np.zeros(int(np.prod(n)), bool)
    solid[np.asarray(ob.get("cells") or [], dtype=np.int64)] = True
    solid = solid.reshape(n)

    def look(P):
        idx = tuple(np.clip(np.floor(P[a] / h[a]).astype(np.int64), 0, n[a] - 1) for a in range(nd))
        return solid[idx]

    m = look(X)
    if inflate > 0:
        for a in range(nd):
            for sgn in (-1.0, 1.0):
                Q = X.copy()
                Q[a] = Q[a] + sgn * inflate
                m |= look(Q) & (Q[a] >= 0) & (Q[a] <= L[a])
    return m


def obstacle_mask(ob, X, depth, inflate=0.0):
    """X: (nd, N) points. True where inside obstacle ``ob`` (optionally inflated by ``inflate``)."""
    nd = X.shape[0]
    if ob["shape"] == "mesh":
        return _cells_mask(ob, X, inflate)
    shape = ob["shape"] if not (nd == 2 and ob["shape"] == "sphere") else "circle"
    x, y = X[0], X[1]
    if shape == "sphere":
        zc = ob.get("z") if ob.get("z") is not None else depth / 2
        return (x - ob["x"]) ** 2 + (y - ob["y"]) ** 2 + (X[2] - zc) ** 2 <= (ob["radius"] + inflate) ** 2
    if shape == "rectangle":
        m = (np.abs(x - ob["x"]) <= ob["width"] / 2 + inflate) & (np.abs(y - ob["y"]) <= ob["height"] / 2 + inflate)
    else:
        m = (x - ob["x"]) ** 2 + (y - ob["y"]) ** 2 <= (ob["radius"] + inflate) ** 2
    if nd == 3:
        zc, hz = z_span(ob, depth)
        m &= np.abs(X[2] - zc) <= hz + inflate
    return m


def source_mask(hs, X, depth):
    m = (np.abs(X[0] - hs["x"]) <= hs["width"] / 2) & (np.abs(X[1] - hs["y"]) <= hs["height"] / 2)
    if X.shape[0] == 3:
        zc, hz = z_span(hs, depth)
        m &= np.abs(X[2] - zc) <= hz
    return m


SIDES = (("left", "right"), ("bottom", "top"), ("back", "front"))
INWARD = {"left": (1, 0, 0), "right": (-1, 0, 0), "bottom": (0, 1, 0), "top": (0, -1, 0), "back": (0, 0, 1), "front": (0, 0, -1)}
TANGENT = {"left": 1, "right": 1, "bottom": 0, "top": 0, "back": 0, "front": 0}


def main() -> int:
    from mpi4py import MPI
    from petsc4py import PETSc

    import basix.ufl
    import ufl
    from dolfinx import default_scalar_type, fem, geometry, mesh
    from dolfinx.fem.petsc import NonlinearProblem
    from dolfinx.nls.petsc import NewtonSolver

    with open("case.json", encoding="utf-8") as fh:
        case = json.load(fh)
    nd = 3 if case.get("dimension") == "3d" else 2
    dom, msh_s, mat, mod, rs = case["domain"], case["mesh"], case["material"], case["models"], case["run"]
    n = [msh_s["nx"], msh_s["ny"], msh_s["nz"]][:nd]
    L = [dom["width"], dom["height"], dom["depth"]][:nd]
    h = [l / k for l, k in zip(L, n)]
    _GRID.update(n=n, h=h, L=L)
    depth = dom["depth"]
    rho = mat["density"]
    nu = mat["viscosity"] / rho
    alpha = mat["conductivity"] / (rho * mat["specific_heat"])
    energy = bool(mod["energy"])
    buoy = energy and bool(mod["buoyancy"])
    gvec = [mod["gravity_x"], mod["gravity_y"], mod.get("gravity_z", 0.0)][:nd]
    beta, Tref = mat["thermal_expansion"], mod["reference_temperature"]
    bcs_cfg = case["boundaries"]
    sides = [s for pair in SIDES[:nd] for s in pair]
    order = max(2, int(case.get("solver", {}).get("order", 2)))
    comm = MPI.COMM_WORLD
    t_start = time.time()

    # ---------------------------------------------------------------- mesh + obstacles
    if nd == 3:
        msh = mesh.create_box(comm, [np.zeros(3), np.array(L)], n, cell_type=mesh.CellType.hexahedron)
    else:
        msh = mesh.create_rectangle(comm, [np.zeros(2), np.array(L)], n, cell_type=mesh.CellType.quadrilateral)
    tdim = msh.topology.dim
    fdim = tdim - 1
    ncell = msh.topology.index_map(tdim).size_local
    mids = mesh.compute_midpoints(msh, tdim, np.arange(ncell, dtype=np.int32))[:, :nd].T
    solid_cells = np.zeros(ncell, bool)
    for ob in case["obstacles"]:
        solid_cells |= obstacle_mask(ob, mids, depth)
    fluid_cells = np.flatnonzero(~solid_cells).astype(np.int32)
    sub = mesh.create_submesh(msh, tdim, fluid_cells)[0]
    sub.topology.create_connectivity(fdim, tdim)
    bfacets = mesh.exterior_facet_indices(sub.topology)
    fmid = mesh.compute_midpoints(sub, fdim, bfacets)[:, :nd].T
    tol = 1e-8 * max(L)
    facet_sets: dict[str, np.ndarray] = {}
    assigned = np.zeros(bfacets.size, bool)
    for a in range(nd):
        for side, val in zip(SIDES[a], (0.0, L[a])):
            m = np.abs(fmid[a] - val) < tol
            facet_sets[side] = bfacets[m & ~assigned]
            assigned |= m
    for k, ob in enumerate(case["obstacles"]):
        m = obstacle_mask(ob, fmid, depth, inflate=0.51 * max(h)) & ~assigned
        facet_sets[f"obs{k}"] = bfacets[m]
        assigned |= m
    emit("log", level="info", msg=f"DOLFINx mesh: {' x '.join(map(str, n))} cells, {fluid_cells.size} fluid, "
                                  f"{bfacets.size} boundary facets; Taylor-Hood Q{order}/Q{order - 1}"
                                  + (" + Q1 temperature" if energy else ""))

    # ---------------------------------------------------------------- spaces
    cell = sub.basix_cell()
    Pv = basix.ufl.element("Lagrange", cell, order, shape=(nd,))
    Pp = basix.ufl.element("Lagrange", cell, order - 1)
    els = [Pv, Pp] + ([basix.ufl.element("Lagrange", cell, 1)] if energy else [])
    W = fem.functionspace(sub, basix.ufl.mixed_element(els))
    V, _ = W.sub(0).collapse()
    w = fem.Function(W)
    w_n = fem.Function(W)
    tests = ufl.TestFunctions(W)
    parts = ufl.split(w)
    parts_n = ufl.split(w_n)
    u, p = parts[0], parts[1]
    v, q = tests[0], tests[1]
    u_n = parts_n[0]

    nu_c = fem.Constant(sub, default_scalar_type(nu))
    steady = rs["mode"] == "steady"
    u_ref = max([bcs_cfg[s]["velocity"] for s in sides if bcs_cfg[s]["type"] == "inlet"]
                + [abs(bcs_cfg[s]["wall_velocity"]) for s in sides if bcs_cfg[s]["type"] == "wall"] + [0.0])
    if u_ref == 0 and buoy:
        temps = [bcs_cfg[s]["temperature"] for s in sides if bcs_cfg[s]["thermal"] == "fixed" and bcs_cfg[s]["type"] == "wall"]
        temps += [o["temperature"] for o in case["obstacles"] if o["thermal"] == "fixed"]
        dT = (max(temps) - min(temps)) if len(temps) > 1 else 1.0
        u_ref = (np.linalg.norm(gvec) * beta * dT * min(L)) ** 0.5
    u_ref = max(u_ref, 1e-3)
    dt_val = rs.get("fixed_dt") or rs["cfl"] * min(h) / u_ref
    dt = fem.Constant(sub, default_scalar_type(dt_val))

    F = (nu_c * ufl.inner(ufl.grad(u), ufl.grad(v)) + ufl.inner(ufl.dot(ufl.grad(u), u), v)
         - p * ufl.div(v) + q * ufl.div(u)) * ufl.dx
    if not steady:
        F += ufl.inner((u - u_n) / dt, v) * ufl.dx
    if energy:
        T, s, T_n = parts[2], tests[2], parts_n[2]
        Q0 = fem.functionspace(sub, ("DG", 0))
        qsrc = fem.Function(Q0)
        qsrc.interpolate(lambda x: sum(source_mask(hs, x[:nd], depth) * hs["power_density"] / (rho * mat["specific_heat"])
                                       for hs in case["heat_sources"]) + 0.0 * x[0])
        F += (alpha * ufl.inner(ufl.grad(T), ufl.grad(s)) + ufl.dot(u, ufl.grad(T)) * s - qsrc * s) * ufl.dx
        if not steady:
            F += (T - T_n) / dt * s * ufl.dx
        if buoy:
            g = ufl.as_vector([default_scalar_type(c) for c in gvec])
            F += beta * (T - Tref) * ufl.inner(g, v) * ufl.dx

    # ---------------------------------------------------------------- boundary conditions
    bcs = []

    def velocity_bc(facets, func):
        f = fem.Function(V)
        f.interpolate(func)
        dofs = fem.locate_dofs_topological((W.sub(0), V), fdim, facets)
        bcs.append(fem.dirichletbc(f, dofs, W.sub(0)))

    def zero_vec(x):
        return np.zeros((nd, x.shape[1]))

    has_outlet = False
    for side in sides:
        b, facets = bcs_cfg[side], facet_sets[side]
        a = next(i for i, pair in enumerate(SIDES) if side in pair)
        if b["type"] == "wall":
            def wall(x, side=side, b=b):
                out = np.zeros((nd, x.shape[1]))
                out[TANGENT[side]] = b["wall_velocity"]
                return out
            velocity_bc(facets, wall)
        elif b["type"] == "inlet":
            def inlet(x, side=side, b=b, a=a):
                prof = np.full(x.shape[1], b["velocity"])
                if b["profile"] == "parabolic":
                    for t in range(nd):
                        if t != a:
                            st = x[t] / L[t]
                            prof = prof * 6 * st * (1 - st)
                out = np.zeros((nd, x.shape[1]))
                out[a] = INWARD[side][a] * prof
                return out
            velocity_bc(facets, inlet)
        elif b["type"] == "symmetry":
            Vc, _ = W.sub(0).sub(a).collapse()
            dofs = fem.locate_dofs_topological((W.sub(0).sub(a), Vc), fdim, facets)
            bcs.append(fem.dirichletbc(fem.Function(Vc), dofs, W.sub(0).sub(a)))
        else:
            has_outlet = True
    for k in range(len(case["obstacles"])):
        velocity_bc(facet_sets[f"obs{k}"], zero_vec)
    if not has_outlet:
        Qs, _ = W.sub(1).collapse()
        x0 = sub.geometry.x[0]
        dofs = fem.locate_dofs_geometrical((W.sub(1), Qs), lambda x: np.all(np.isclose(x.T, x0, atol=1e-10 * max(L)), axis=1))
        bcs.append(fem.dirichletbc(fem.Function(Qs), dofs, W.sub(1)))
    if energy:
        Ts_space, _ = W.sub(2).collapse()

        def temp_bc(facets, value):
            f = fem.Function(Ts_space)
            f.x.array[:] = value
            dofs = fem.locate_dofs_topological((W.sub(2), Ts_space), fdim, facets)
            bcs.append(fem.dirichletbc(f, dofs, W.sub(2)))

        for side in sides:
            b = bcs_cfg[side]
            if b["type"] == "inlet" or (b["type"] == "wall" and b["thermal"] == "fixed"):
                temp_bc(facet_sets[side], b["temperature"])
        for k, ob in enumerate(case["obstacles"]):
            if ob["thermal"] == "fixed":
                temp_bc(facet_sets[f"obs{k}"], ob["temperature"])

    # ---------------------------------------------------------------- initial state
    ini = case["initial"]
    v0 = [ini["velocity_x"], ini["velocity_y"], ini.get("velocity_z", 0.0)][:nd]
    w.sub(0).interpolate(lambda x: np.array([np.full(x.shape[1], c) for c in v0]))
    if energy:
        w.sub(2).interpolate(lambda x: np.full(x.shape[1], ini["temperature"]))
    w.x.scatter_forward()
    w_n.x.array[:] = w.x.array

    problem = NonlinearProblem(F, w, bcs=bcs)
    solver = NewtonSolver(comm, problem)
    solver.convergence_criterion = "incremental"
    solver.rtol = 1e-9
    solver.atol = 1e-12
    solver.max_it = 30
    solver.report = False
    ksp = solver.krylov_solver
    opts = PETSc.Options()
    pre = ksp.getOptionsPrefix()
    opts[f"{pre}ksp_type"] = "preonly"
    opts[f"{pre}pc_type"] = "lu"
    opts[f"{pre}pc_factor_mat_solver_type"] = "mumps"
    ksp.setFromOptions()

    # ---------------------------------------------------------------- sampling onto the CFDFlow grid
    centres = [(np.arange(k) + 0.5) * hh for k, hh in zip(n, h)]
    grid = np.meshgrid(*centres, indexing="ij")
    solid = np.zeros(n, bool)
    for ob in case["obstacles"]:
        solid |= obstacle_mask(ob, np.stack([c.ravel() for c in grid]), depth).reshape(n)
    flat = np.zeros((solid.size, 3))
    for a in range(nd):
        flat[:, a] = grid[a].ravel()
    fluid_idx = np.flatnonzero(~solid.ravel())
    pts = flat[fluid_idx]
    tree = geometry.bb_tree(sub, tdim)
    coll = geometry.compute_colliding_cells(sub, geometry.compute_collisions_points(tree, pts), pts)
    keep, cells = [], []
    for i in range(pts.shape[0]):
        links = coll.links(i)
        if len(links):
            keep.append(i)
            cells.append(links[0])
    keep = np.array(keep, dtype=int)
    cells = np.array(cells, dtype=np.int32)
    probe_pts = []
    for pr in case["probes"]:
        pp = [pr["x"], pr["y"], (pr["z"] if pr.get("z") is not None else depth / 2) if nd == 3 else 0.0]
        probe_pts.append(pp)
    probe_pts = np.array(probe_pts, dtype=float).reshape(-1, 3)
    probe_cells = []
    if len(probe_pts):
        pc = geometry.compute_colliding_cells(sub, geometry.compute_collisions_points(tree, probe_pts), probe_pts)
        probe_cells = [pc.links(i)[0] if len(pc.links(i)) else -1 for i in range(len(probe_pts))]

    def collapse():
        out = [w.sub(0).collapse(), w.sub(1).collapse()]
        if energy:
            out.append(w.sub(2).collapse())
        return out

    def sample():
        fs = collapse()
        nk = keep.size
        uv = np.asarray(fs[0].eval(pts[keep], cells[keep])).reshape(nk, nd) if nk else np.zeros((0, nd))
        pv = np.asarray(fs[1].eval(pts[keep], cells[keep])).reshape(nk) if nk else np.zeros(0)
        arrays = {}
        for name, vals in (("u", uv[:, 0]), ("v", uv[:, 1]), ("w", uv[:, 2] if nd == 3 else None), ("p", pv * rho)):
            a = np.zeros(solid.size)
            if vals is not None:
                a[fluid_idx[keep]] = vals
            arrays[name] = a.reshape(n + ([1] if nd == 2 else []))
        Tarr = np.full(solid.size, ini["temperature"])
        if energy:
            Tarr[fluid_idx[keep]] = np.asarray(fs[2].eval(pts[keep], cells[keep])).reshape(keep.size)
            for ob in case["obstacles"]:
                if ob["thermal"] == "fixed":
                    m = obstacle_mask(ob, np.stack([c.ravel() for c in grid]), depth)
                    Tarr[m] = ob["temperature"]
        arrays["T"] = Tarr.reshape(n + ([1] if nd == 2 else []))
        return arrays

    def probe_values():
        vals = {}
        if not len(probe_pts):
            return vals
        fs = collapse()
        for i, pr in enumerate(case["probes"]):
            c = probe_cells[i]
            if c < 0:
                continue
            one = np.array([c], dtype=np.int32)
            uv = np.asarray(fs[0].eval(probe_pts[i:i + 1], one)).reshape(-1)
            for k, comp in enumerate("uvw"[:nd]):
                vals[f"{pr['id']}.{comp}"] = float(uv[k])
            vals[f"{pr['id']}.p"] = float(np.asarray(fs[1].eval(probe_pts[i:i + 1], one)).reshape(-1)[0] * rho)
            if energy:
                vals[f"{pr['id']}.T"] = float(np.asarray(fs[2].eval(probe_pts[i:i + 1], one)).reshape(-1)[0])
        return vals

    def norm(f):
        return float(np.sqrt(comm.allreduce(np.dot(f, f), op=MPI.SUM)))

    def save(times, frames, iterations, converged, status):
        stacked = {k: np.stack([fr[k] for fr in frames]).astype(np.float32) for k in frames[0]}
        np.savez_compressed("result_fenicsx.tmp.npz", times=np.array(times), solid=solid.reshape(n + ([1] if nd == 2 else [])),
                            iterations=iterations, converged=converged, status=status,
                            wall=time.time() - t_start, **stacked)
        os.replace("result_fenicsx.tmp.npz", "result_fenicsx.npz")

    # ---------------------------------------------------------------- solve
    V_dofs = W.sub(0).collapse()[1]
    if steady:
        schedule, k = [1.0], 0  # direct Newton first; viscosity continuation 8-4-2-1 if it fails
        it_total, ok, continued = 0, False, False
        while k < len(schedule) and not os.path.exists(STOP_FILE):
            nu_c.value = nu * schedule[k]
            emit("log", level="info", msg=f"Newton solve with nu = {schedule[k]:g} x nu")
            try:
                n_it, conv = solver.solve(w)
            except RuntimeError as exc:
                n_it, conv = 0, False
                emit("log", level="warning", msg=f"Newton did not converge: {exc}")
            it_total += int(n_it)
            delta = w.x.array[V_dofs] - w_n.x.array[V_dofs]
            res = norm(delta) / (norm(w.x.array[V_dofs]) + 1e-12) if conv else 1.0
            emit("monitor", it=it_total, t=0.0, res={"u": max(res, 1e-16)}, values=probe_values())
            emit("progress", frac=min(0.95, (k + 1) / len(schedule)))
            if not conv:
                if continued:
                    break
                continued = True
                w.x.array[:] = w_n.x.array
                schedule, k = [8.0, 4.0, 2.0, 1.0], 0
                continue
            if schedule[k] == 1.0:
                ok = True
                break
            w_n.x.array[:] = w.x.array
            k += 1
        save([0.0], [sample()], it_total, ok, "completed" if ok else "not-converged")
        emit("log", level="info" if ok else "warning", msg=f"Steady Newton {'converged' if ok else 'did not converge'} "
                                                         f"({it_total} Newton iterations, {time.time() - t_start:.1f} s)")
        return 0 if ok else 3

    end_time = rs["end_time"]
    nframes = max(2, int(rs.get("snapshot_count", 20)))
    frame_dt = end_time / (nframes - 1)
    times, frames = [0.0], [sample()]
    next_frame = frame_dt
    t, step = 0.0, 0
    status = "completed"
    while t < end_time - 1e-12 and step < rs["max_iterations"]:
        dt.value = min(dt_val, end_time - t)
        w_n.x.array[:] = w.x.array
        n_it, conv = solver.solve(w)
        t += float(dt.value)
        step += 1
        du = w.x.array[V_dofs] - w_n.x.array[V_dofs]
        res = norm(du) / (norm(w.x.array[V_dofs]) + 1e-12) * (min(L) / u_ref) / float(dt.value)
        if step % max(1, rs.get("monitor_interval", 5)) == 0 or step == 1:
            emit("monitor", it=step, t=t, res={"u": res}, values={**probe_values(), "dt": float(dt.value)})
            emit("progress", frac=min(0.99, t / end_time))
        if t >= next_frame - 1e-12 or t >= end_time - 1e-12:
            times.append(t)
            frames.append(sample())
            next_frame += frame_dt
            save(times, frames, step, False, "running")
        if os.path.exists(STOP_FILE):
            status = "cancelled"
            break
    if times[-1] < t:
        times.append(t)
        frames.append(sample())
    save(times, frames, step, status == "completed", status)
    emit("log", level="info", msg=f"Transient DOLFINx run {status}: {step} steps to t = {t:.4g} s ({time.time() - t_start:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
