"""Pure NumPy/SciPy finite-element Navier-Stokes solver - the stand-in for FEniCSx.

Used when DOLFINx is not installed. It solves the same equations with the same kind of method
(continuous Galerkin finite elements, monolithic velocity-pressure solve) on the structured
CFDFlow grid:

* Q1 (bi-/tri-linear) elements for velocity, pressure and temperature on the fluid cells;
  obstacle cells are simply not part of the mesh.
* Equal-order interpolation is stabilised with PSPG (pressure) and SUPG (convection), Tezduyar
  stabilisation parameter ``tau = ((2/dt)^2 + (2|a|/h)^2 + 9 (4 nu / h^2)^2)^(-1/2)``.
* Steady: Picard (Oseen) iterations on the monolithic system until the relative velocity change
  is below the tolerance. Transient: backward Euler with a semi-implicit (linearised) convection.
* Temperature: SUPG-stabilised convection-diffusion solved after each flow solve; Boussinesq
  coupling through the body force.
* Every structured element has the same geometry, so the reference element matrices are computed
  once and the velocity-dependent parts are assembled with vectorised ``einsum`` calls.

The output is the cell average of the nodal values, i.e. the CFDFlow cell-centred format.
"""
from __future__ import annotations

import itertools
import math
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import splu

from ...config import get_settings
from ..grid3d import AXIS_SIDES, build_grid_nd
from .base import (BackendResult, CheckMsg, ProgressTracker, SolverBackend, frame_from_cc, full_fields, meta_for,
                   monitors_from_cc, summary_from_cc)

INWARD = {"left": 1.0, "right": -1.0, "bottom": 1.0, "top": -1.0, "back": 1.0, "front": -1.0}
TANGENT = {"left": 1, "right": 1, "bottom": 0, "top": 0, "back": 0, "front": 0}


class FemSolver:
    def __init__(self, case):
        self.case = case
        g = self.g = build_grid_nd(case)
        nd = self.nd = g.ndim
        m = case.material
        self.rho, self.nu = m.density, m.viscosity / m.density
        self.alpha = m.conductivity / (m.density * m.specific_heat)
        self.energy = case.models.energy
        self.buoy = self.energy and case.models.buoyancy
        self.gvec = np.array((case.models.gravity_x, case.models.gravity_y, case.models.gravity_z)[:nd])
        self.beta, self.Tref = m.thermal_expansion, case.models.reference_temperature
        self.bc = {s: getattr(case.boundaries, s) for pair in AXIS_SIDES[:nd] for s in pair}
        self.nn = tuple(k + 1 for k in g.n)  # nodes per axis
        self.n_nodes = int(np.prod(self.nn))
        self._reference_element()
        self._connectivity()
        self._boundary_data()
        speeds = [b.velocity for b in self.bc.values() if b.type == "inlet"] + [abs(b.wall_velocity) for b in self.bc.values() if b.type == "wall"]
        self.u_ref = max(speeds + [abs(case.initial.velocity_x), abs(case.initial.velocity_y), 1e-3])
        if self.buoy and not speeds:
            temps = [b.temperature for b in self.bc.values() if b.type == "wall" and b.thermal == "fixed"]
            temps += [o.temperature for o in case.obstacles if o.thermal == "fixed"]
            dT = (max(temps) - min(temps)) if len(temps) > 1 else 1.0
            self.u_ref = max(self.u_ref, math.sqrt(np.linalg.norm(self.gvec) * self.beta * dT * min(g.L)))

    # ------------------------------------------------------------------ setup
    def _reference_element(self):
        nd, h = self.nd, self.g.h
        gp = np.array([-1.0, 1.0]) / math.sqrt(3.0)
        self.corners = list(itertools.product((0, 1), repeat=nd))  # node offsets, axis order
        quad = list(itertools.product(gp, repeat=nd))
        nq, nc = len(quad), len(self.corners)
        N = np.zeros((nq, nc))
        dN = np.zeros((nq, nc, nd))
        for q, xi in enumerate(quad):
            for c, off in enumerate(self.corners):
                s = [2 * o - 1 for o in off]
                f = [(1 + s[a] * xi[a]) / 2 for a in range(nd)]
                N[q, c] = np.prod(f)
                for a in range(nd):
                    d = s[a] / 2 * 2 / h[a]
                    dN[q, c, a] = d * np.prod([f[b] for b in range(nd) if b != a])
        self.N, self.dN = N, dN
        self.detJ = float(np.prod(h))  # (h/2)^nd * 2^nd weights
        self.wq = self.detJ / nq
        self.M = self.wq * N.T @ N
        self.K = self.wq * np.einsum("qca,qda->cd", dN, dN)
        self.G = [self.wq * np.einsum("qc,qd->cd", dN[:, :, a], N) for a in range(nd)]  # G[a][c,d] = int dN_c/dx_a N_d

    def _connectivity(self):
        g, nd = self.g, self.nd
        cells = np.argwhere(g.fluid)  # (E, nd)
        self.cells = cells
        conn = np.zeros((cells.shape[0], len(self.corners)), dtype=np.int64)
        for c, off in enumerate(self.corners):
            conn[:, c] = np.ravel_multi_index(tuple(cells[:, a] + off[a] for a in range(nd)), self.nn)
        self.conn = conn
        self.active = np.zeros(self.n_nodes, bool)
        self.active[conn.ravel()] = True
        # nodes touching a solid cell (obstacle surface), and their fixed temperature if any
        self.solid_node = np.zeros(self.n_nodes, bool)
        self.solid_node_T = np.full(self.n_nodes, np.nan)
        scells = np.argwhere(g.solid)
        for off in self.corners:
            ids = np.ravel_multi_index(tuple(scells[:, a] + off[a] for a in range(nd)), self.nn)
            self.solid_node[ids] = True
            Ts = g.solid_T[tuple(scells.T)]
            ok = ~np.isnan(Ts)
            self.solid_node_T[ids[ok]] = Ts[ok]
        self.solid_node &= self.active
        coords = np.meshgrid(*[np.arange(k) * h for k, h in zip(self.nn, g.h)], indexing="ij")
        self.X = np.stack([c.ravel() for c in coords], axis=1)

    def _boundary_data(self):
        nd, g = self.nd, self.g
        nv = self.n_nodes
        self.vel_fixed = np.zeros((nv, nd), bool)
        self.vel_value = np.zeros((nv, nd))
        self.T_fixed = np.zeros(nv, bool)
        self.T_value = np.zeros(nv)
        idx = np.indices(self.nn).reshape(nd, -1).T
        self.has_outlet = False
        # priority: symmetry < inlet < wall < obstacle
        order = {"symmetry": 0, "outlet": 0, "inlet": 1, "wall": 2}
        sides = sorted(self.bc, key=lambda s: order[self.bc[s].type])
        for side in sides:
            b = self.bc[side]
            a = next(i for i, pair in enumerate(AXIS_SIDES) if side in pair)
            on = idx[:, a] == (0 if side == AXIS_SIDES[a][0] else self.nn[a] - 1)
            if b.type == "outlet":
                self.has_outlet = True
            elif b.type == "symmetry":
                self.vel_fixed[on, a] = True
                self.vel_value[on, a] = 0.0
            elif b.type == "wall":
                self.vel_fixed[on] = True
                self.vel_value[on] = 0.0
                self.vel_value[on, TANGENT[side]] = b.wall_velocity
            else:
                prof = np.full(on.sum(), b.velocity)
                if b.profile == "parabolic":
                    for t in range(nd):
                        if t != a:
                            s = self.X[on, t] / g.L[t]
                            prof = prof * 6 * s * (1 - s)
                self.vel_fixed[on] = True
                self.vel_value[on] = 0.0
                self.vel_value[on, a] = INWARD[side] * prof
            if self.energy and (b.type == "inlet" or (b.type == "wall" and b.thermal == "fixed")):
                self.T_fixed[on] = True
                self.T_value[on] = b.temperature
        self.vel_fixed[self.solid_node] = True
        self.vel_value[self.solid_node] = 0.0
        fixedT = self.solid_node & ~np.isnan(self.solid_node_T)
        self.T_fixed[fixedT] = True
        self.T_value[fixedT] = self.solid_node_T[fixedT]

    # ------------------------------------------------------------------ assembly
    def _element_quantities(self, U, dt):
        """Convective velocity at quadrature points, a.grad(N), and tau per element."""
        Ue = U[self.conn]  # (E, nc, nd)
        aq = np.einsum("qc,ecd->eqd", self.N, Ue)
        adv = np.einsum("eqa,qca->eqc", aq, self.dN)
        amag = np.linalg.norm(Ue.mean(axis=1), axis=1)
        hmin = min(self.g.h)

        def tau(diff):
            inv = (2 * amag / hmin) ** 2 + 9 * (4 * diff / hmin**2) ** 2
            if dt:
                inv = inv + (2 / dt) ** 2
            return 1.0 / np.sqrt(np.maximum(inv, 1e-300))

        return aq, adv, tau

    def _coo(self, blocks):
        rows, cols, vals = [], [], []
        E, nc = self.conn.shape
        for (rc, cc), B in blocks:
            r = self.conn[:, :, None] * self.ndof_node + rc
            c = self.conn[:, None, :] * self.ndof_node + cc
            Bb = np.broadcast_to(B, (E, nc, nc))
            rows.append(np.broadcast_to(r, (E, nc, nc)).ravel())
            cols.append(np.broadcast_to(c, (E, nc, nc)).ravel())
            vals.append(Bb.ravel())
        return np.concatenate(rows), np.concatenate(cols), np.concatenate(vals)

    def assemble_flow(self, A_vel, U_old, T, dt):
        """Linear system for (u, p) with convective velocity ``A_vel`` (Picard / semi-implicit)."""
        nd, nu = self.nd, self.nu
        self.ndof_node = nd + 1
        aq, adv, tau_f = self._element_quantities(A_vel, dt)
        tau = tau_f(nu)[:, None, None]
        w = self.wq
        C = w * np.einsum("qc,eqd->ecd", self.N, adv)
        Sa = tau * w * np.einsum("eqc,eqd->ecd", adv, adv)
        diag = C + nu * self.K + Sa
        if dt:
            diag = diag + self.M / dt + tau * w * np.einsum("eqc,qd->ecd", adv, self.N) / dt
        blocks = []
        for a in range(nd):
            blocks.append(((a, a), diag))
            supg_p = tau * w * np.einsum("eqc,qd->ecd", adv, self.dN[:, :, a])
            blocks.append(((a, nd), -self.G[a][None] + supg_p))
            pspg_u = tau * w * np.einsum("qc,eqd->ecd", self.dN[:, :, a], adv)
            if dt:
                pspg_u = pspg_u + tau * w * np.einsum("qc,qd->cd", self.dN[:, :, a], self.N)[None] / dt
            blocks.append(((nd, a), self.G[a].T[None] + pspg_u))
        blocks.append(((nd, nd), tau * self.K[None]))
        rows, cols, vals = self._coo(blocks)
        n = self.n_nodes * self.ndof_node
        A = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
        b = np.zeros(n)
        # body force (Boussinesq) and time terms on the right-hand side
        E, nc = self.conn.shape
        force_q = np.zeros((E, self.N.shape[0], nd))
        if self.buoy:
            Tq = np.einsum("qc,ec->eq", self.N, T[self.conn])
            force_q = -(Tq - self.Tref)[:, :, None] * self.beta * self.gvec[None, None, :]
        if dt:
            force_q = force_q + np.einsum("qc,ecd->eqd", self.N, U_old[self.conn]) / dt
        if self.buoy or dt:
            for a in range(nd):
                Fa = force_q[:, :, a]
                rb = w * (np.einsum("qc,eq->ec", self.N, Fa) + tau[:, :, 0] * np.einsum("eqc,eq->ec", adv, Fa))
                np.add.at(b, self.conn * self.ndof_node + a, rb)
                rp = w * tau[:, :, 0] * np.einsum("qc,eq->ec", self.dN[:, :, a], Fa)
                np.add.at(b, self.conn * self.ndof_node + nd, rp)
        return A, b

    def _lu_solve(self, A, b, ndn: int) -> np.ndarray:
        """Sparse LU with a banded ordering: nodes numbered with the longest axis varying slowest
        (much less fill-in than COLAMD for these structured 3-D systems)."""
        key = (ndn, A.shape[0])
        if getattr(self, "_perm_key", None) != key:
            idx = np.indices(self.nn).reshape(self.nd, -1)
            axes = sorted(range(self.nd), key=lambda a: -self.nn[a])
            order = np.argsort(np.ravel_multi_index(tuple(idx[a] for a in axes), [self.nn[a] for a in axes]), kind="stable")
            self._perm = (order[:, None] * ndn + np.arange(ndn)).ravel()
            self._perm_key = key
        perm = self._perm
        lu = splu(A.tocsr()[perm][:, perm].tocsc(), permc_spec="NATURAL")
        x = np.empty_like(b)
        x[perm] = lu.solve(b[perm])
        return x

    def _apply_dirichlet(self, A, b, fixed: np.ndarray, values: np.ndarray):
        keep = (~fixed).astype(float)
        A = sp.diags(keep) @ A + sp.diags(fixed.astype(float))
        b = np.where(fixed, values, b)
        return A.tocsc(), b

    def solve_flow(self, A_vel, U_old, T, dt):
        nd = self.nd
        A, b = self.assemble_flow(A_vel, U_old, T, dt)
        ndn = nd + 1
        fixed = np.zeros(self.n_nodes * ndn, bool)
        values = np.zeros_like(b)
        for a in range(nd):
            fixed[a::ndn] = self.vel_fixed[:, a] | ~self.active
            values[a::ndn] = np.where(self.active, self.vel_value[:, a], 0.0)
        fixed[nd::ndn] = ~self.active
        if not self.has_outlet:
            pin = int(np.flatnonzero(self.active & ~self.solid_node)[0]) if (self.active & ~self.solid_node).any() \
                else int(np.flatnonzero(self.active)[0])
            fixed[pin * ndn + nd] = True
        A, b = self._apply_dirichlet(A, b, fixed, values)
        x = self._lu_solve(A, b, ndn)
        return x.reshape(self.n_nodes, ndn)[:, :nd], x.reshape(self.n_nodes, ndn)[:, nd]

    def solve_energy(self, U, T_old, dt):
        al = self.alpha
        self.ndof_node = 1
        aq, adv, tau_f = self._element_quantities(U, dt)
        tau = tau_f(al)[:, None, None]
        w = self.wq
        Ae = w * np.einsum("qc,eqd->ecd", self.N, adv) + al * self.K[None] + tau * w * np.einsum("eqc,eqd->ecd", adv, adv)
        if dt:
            Ae = Ae + self.M[None] / dt + tau * w * np.einsum("eqc,qd->ecd", adv, self.N) / dt
        rows, cols, vals = self._coo([((0, 0), Ae)])
        A = sp.csr_matrix((vals, (rows, cols)), shape=(self.n_nodes, self.n_nodes))
        b = np.zeros(self.n_nodes)
        src_q = np.zeros((self.conn.shape[0], self.N.shape[0]))
        q_cell = self._q_cell()
        if q_cell is not None:
            src_q += q_cell[:, None]
        if dt:
            src_q += np.einsum("qc,ec->eq", self.N, T_old[self.conn]) / dt
        rb = w * (np.einsum("qc,eq->ec", self.N, src_q) + tau[:, :, 0] * np.einsum("eqc,eq->ec", adv, src_q))
        np.add.at(b, self.conn, rb)
        fixed = self.T_fixed | ~self.active
        values = np.where(self.T_fixed, self.T_value, self.case.initial.temperature)
        A, b = self._apply_dirichlet(A, b, fixed, values)
        return self._lu_solve(A, b, 1)

    def _q_cell(self):
        if not self.case.heat_sources:
            return None
        from ..grid3d import source_mask

        coords = [c[tuple(self.cells.T)] for c in self.g.mesh_coords()]
        q = np.zeros(self.cells.shape[0])
        m = self.case.material
        for hs in self.case.heat_sources:
            q += source_mask(hs, coords, self.case.domain.depth) * hs.power_density / (m.density * m.specific_heat)
        return q

    # ------------------------------------------------------------------ output
    def cell_fields(self, U, p, T) -> dict[str, np.ndarray]:
        g, nd = self.g, self.nd
        out = {}
        comps = ["u", "v", "w"][:nd]
        for name, vals in [(c, U[:, a]) for a, c in enumerate(comps)] + [("p", p * self.rho), ("T", T)]:
            arr = np.zeros(g.n) if name != "T" else np.full(g.n, self.case.initial.temperature)
            arr[tuple(self.cells.T)] = vals[self.conn].mean(axis=1)
            out[name] = arr
        if self.energy:
            fixed = ~np.isnan(g.solid_T)
            out["T"][fixed] = g.solid_T[fixed]
        if nd == 2:
            out["w"] = np.zeros(g.n)
        return out


class FemFallbackBackend(SolverBackend):
    id = "python-fem"
    label = "Python finite elements (FEniCSx fallback)"
    description = ("Pure NumPy/SciPy stabilised finite-element solver (Q1/Q1 PSPG/SUPG, monolithic, Picard / backward Euler). "
                   "Used automatically when FEniCSx is not installed.")
    capabilities = {"dims": (2, 3), "steady": True, "transient": True, "energy": True, "buoyancy": True, "turbulence": False}

    def check(self, case) -> list[CheckMsg]:
        out = super().check(case)
        lim = get_settings().max_fem_cells
        if case.n_cells > lim:
            out.append(CheckMsg("error", "mesh", f"The Python finite-element solver is limited to {lim:,} cells "
                                                 f"({case.n_cells:,} requested). Coarsen the mesh or install FEniCSx."))
        return out

    def run(self, case, hooks, run_dir: Path) -> BackendResult:
        t0 = time.time()
        s = FemSolver(case)
        g, nd, rs = s.g, s.nd, case.run
        hooks.log("info", f"Python FEM: {' x '.join(map(str, g.n))} cells ({g.n_fluid} fluid), {s.active.sum()} nodes, "
                          f"Q1/Q1 PSPG/SUPG{', energy' if s.energy else ''}{', Boussinesq' if s.buoy else ''}")
        ini = case.initial
        U = np.zeros((s.n_nodes, nd))
        U[:] = np.array((ini.velocity_x, ini.velocity_y, ini.velocity_z)[:nd])
        U[s.vel_fixed] = s.vel_value[s.vel_fixed]
        U[~s.active] = 0.0
        T = np.where(s.T_fixed, s.T_value, ini.temperature).astype(float)
        p = np.zeros(s.n_nodes)
        tracker = ProgressTracker(case)
        steady = rs.mode == "steady"
        times, frames = [], []
        status, converged, it, t = "completed", False, 0, 0.0
        tol = max(rs.tolerance, 1e-10)
        dt = 0.0 if steady else (rs.fixed_dt or rs.cfl * min(g.h) / s.u_ref)
        frame_dt = rs.end_time / max(rs.snapshot_count - 1, 1)
        next_frame = frame_dt
        last_snap = time.time()

        def snapshot(force=False):
            nonlocal last_snap
            if force or time.time() - last_snap > 2.0:
                last_snap = time.time()
                f = s.cell_fields(U, p, T)
                hooks.save_latest(meta_for(case, g, self.id, it), g.solid, t, frame_from_cc(g, f))

        if not steady:
            times.append(0.0)
            frames.append(frame_from_cc(g, s.cell_fields(U, p, T)))
        max_it = min(rs.max_iterations, 300) if steady else rs.max_iterations
        while True:
            U_old = U.copy()
            T_old = T.copy()
            step = 0.0 if steady else min(dt, rs.end_time - t)
            if steady:
                Un, p = s.solve_flow(U, U_old, T, 0.0)
                relax = 1.0 if it < 2 else 0.8
                U = relax * Un + (1 - relax) * U_old
            else:
                U, p = s.solve_flow(U_old, U_old, T, step)
            if s.energy:
                T = s.solve_energy(U, T_old, step)
            it += 1
            t += step
            norm = np.linalg.norm(U) + 1e-12 * s.u_ref
            res = {"u": float(np.linalg.norm(U[:, 0] - U_old[:, 0]) / norm), "v": float(np.linalg.norm(U[:, 1] - U_old[:, 1]) / norm)}
            if nd == 3:
                res["w"] = float(np.linalg.norm(U[:, 2] - U_old[:, 2]) / norm)
            if s.energy:
                res["T"] = float(np.linalg.norm(T - T_old) / (np.linalg.norm(T - s.Tref) + 1e-12))
            if not np.all(np.isfinite(U)):
                raise RuntimeError(f"Python FEM diverged at iteration {it}; refine the mesh or reduce the time step.")
            f = s.cell_fields(U, p, T)
            if it % max(1, rs.monitor_interval if not steady else 1) == 0 or it == 1:
                hooks.on_monitor(it, t, res, monitors_from_cc(case, g, full_fields(g, f), dt))
            r = max(res.values())
            hooks.on_progress(tracker.fraction(it, t, r), it, t)
            snapshot()
            if not steady and t >= next_frame - 1e-12 and t < rs.end_time - 1e-12:
                times.append(t)
                frames.append(frame_from_cc(g, f))
                next_frame += frame_dt
            if hooks.should_stop():
                status = "cancelled"
                break
            if steady and r < tol and it > 2:
                converged = True
                break
            if not steady and t >= rs.end_time - 1e-12:
                converged = True
                break
            if it >= max_it:
                break
        f = s.cell_fields(U, p, T)
        times.append(t)
        frames.append(frame_from_cc(g, f))
        notes = [] if converged or status == "cancelled" else [f"Iteration limit ({max_it}) reached before convergence."]
        summary = summary_from_cc(case, g, full_fields(g, f), iterations=it, sim_time=t, wall=time.time() - t0,
                                  converged=converged, notes=notes, backend=self.id)
        hooks.log("info", f"Python FEM {'finished' if status == 'completed' else 'stopped'} after {it} iterations"
                          + (" (converged)" if converged else "") + f", wall {time.time() - t0:.1f} s")
        return BackendResult(status, converged, it, t, meta_for(case, g, self.id, it), g.solid, times, frames, summary)
