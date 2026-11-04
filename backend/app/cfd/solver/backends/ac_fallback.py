"""Artificial-compressibility solver - the pure-Python stand-in for PyFR.

PyFR solves incompressible flow with the *artificial compressibility* (AC) method of Chorin:

    dp/dtau + zeta * div(u) = 0
    du/dtau + du/dt + (u . grad) u = -grad p + nu lap u

marched explicitly in pseudo-time ``tau`` (Runge-Kutta), with *dual time stepping* for transient
flows (an inner pseudo-time loop per physical step, backward Euler in physical time). This module
solves exactly that system with the same time integration family, but discretised with the
2nd-order MAC finite-volume operators of :mod:`ns3d` instead of high-order flux reconstruction.

* Pseudo-time: 3-stage SSP Runge-Kutta, local pseudo-step from the acoustic speed
  ``|u| + sqrt(u^2 + zeta)`` and the viscous limit, ``zeta = (3 U_ref)^2`` (PyFR ``ac-zeta``).
* The physical-time source ``-(u - u_n)/dt`` is part of the pseudo-time residual; the pseudo
  step is capped at ``dt`` so the explicit Runge-Kutta stays stable.
* Outlets: zero-gradient velocity; the outlet pressure ``p = 0`` is imposed weakly by relaxing the
  boundary-cell pressure towards 0 at the acoustic rate (a simple non-reflecting condition).
* Steady cases: pure pseudo-time marching until velocity change *and* divergence are below the
  tolerance.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from ..ns3d import FlowSolver3D, Residuals3D, _s
from ..runner import run_simulation
from .base import BackendResult, SolverBackend


class ACResiduals(Residuals3D):
    def max(self) -> float:
        return max(self.u, self.v, self.w, self.p, self.T)


class ACSolver(FlowSolver3D):
    """Same grid, boundary conditions and outputs as :class:`FlowSolver3D`; AC time integration."""

    inner_max = 60  # pseudo iterations per physical step (transient)

    def __init__(self, case, **kw):
        super().__init__(case, **kw)
        self.zeta = (3.0 * self.u_ref) ** 2
        self.p = np.zeros(self.g.n)

    # p is advanced by AC, not obtained from a Poisson solve; the Poisson set-up is skipped
    def _setup_poisson(self):
        self.poisson = None
        self.poisson_kind = "artificial compressibility (no Poisson solve)"
        g = self.g
        self.idx = -np.ones(g.n, dtype=np.int64)
        self.idx[self.F] = np.arange(int(self.F.sum()))

    def _pseudo_dt(self) -> float:
        g = self.g
        umax = max(float(np.abs(u).max()) for u in self.U)
        c = umax + math.sqrt(umax**2 + self.zeta)
        dt = min(h / c for h in g.h)
        nu = max(self.nu, self.alpha if self.energy else 0.0)
        dt = min(dt, 1.0 / (2.0 * nu * sum(1 / h**2 for h in g.h)))
        return self.rs.cfl * dt

    def _rates(self, U, p, gam, U_n=None, dt_phys=None):
        """Pseudo-time derivatives (dU/dtau at interior faces, dp/dtau at fluid cells)."""
        nd, g = self.nd, self.g
        rhs = self.momentum_rhs(U, gam)
        I = slice(1, -1)
        dU = []
        for c in range(nd):
            grad = (p[_s(nd, c, slice(1, None))] - p[_s(nd, c, slice(None, -1))]) / g.h[c]
            r = rhs[c] - grad
            if U_n is not None:
                r = r - (U[c][_s(nd, c, I)] - U_n[c][_s(nd, c, I)]) / dt_phys
            dU.append(np.where(self.activeF[c][_s(nd, c, I)], r, 0.0))
        div = self.divergence(U)
        dp = np.where(self.F, -self.zeta * div, 0.0)
        return dU, dp, div

    def _outlets(self, U, p):
        """Outlet faces: zero-gradient tangential + pressure pulled to 0 at the face (half cell)."""
        nd, g = self.nd, self.g
        for c in range(nd):
            n, h = g.n[c], g.h[c]
            lo, hi = self.has_out[c]
            if lo:
                U[c][_s(nd, c, 0)] = np.where(self.outF[c][_s(nd, c, 0)], U[c][_s(nd, c, 1)], 0.0)
            if hi:
                U[c][_s(nd, c, n)] = np.where(self.outF[c][_s(nd, c, n)], U[c][_s(nd, c, n - 1)], 0.0)

    def _outlet_pressure_relax(self, p, dtau):
        """Dirichlet p = 0 at outlet faces enters through the boundary-cell pressure (AC wave exit)."""
        nd, g = self.nd, self.g
        for c in range(nd):
            n, h = g.n[c], g.h[c]
            lo, hi = self.has_out[c]
            for flag, cell, face in ((lo, 0, 0), (hi, n - 1, n)):
                if flag:
                    sl = _s(nd, c, cell)
                    m = self.outF[c][_s(nd, c, face)]
                    p[sl] = np.where(m, p[sl] * (1.0 - min(1.0, 2.0 * dtau * math.sqrt(self.zeta) / h)), p[sl])

    def _rk_stage_update(self, U0, p0, U, p, dU, dp, dtau, a, b):
        nd = self.nd
        I = slice(1, -1)
        out_U = []
        for c in range(nd):
            Uc = U[c].copy()
            new = a * U0[c][_s(nd, c, I)] + b * (U[c][_s(nd, c, I)] + dtau * dU[c])
            Uc[_s(nd, c, I)] = np.where(self.activeF[c][_s(nd, c, I)], new, U[c][_s(nd, c, I)])
            out_U.append(Uc)
        pn = a * p0 + b * (p + dtau * dp)
        self._outlets(out_U, pn)
        self._outlet_pressure_relax(pn, b * dtau)
        return out_U, pn

    def _pseudo_step(self, gam, U_n=None, dt_phys=None):
        dtau = self._pseudo_dt()
        if dt_phys:
            dtau = min(dtau, dt_phys)
        U0, p0 = [u.copy() for u in self.U], self.p.copy()
        U, p = U0, p0
        div = None
        for a, b in ((0.0, 1.0), (0.75, 0.25), (1.0 / 3.0, 2.0 / 3.0)):
            dU, dp, div = self._rates(U, p, gam, U_n, dt_phys)
            U, p = self._rk_stage_update(U0, p0, U, p, dU, dp, dtau, a, b)
        return U, p, dtau, div

    def step(self, update_energy: bool = True) -> ACResiduals:
        t0 = time.perf_counter()
        steady = self.rs.mode == "steady"
        res = ACResiduals(ndim=self.nd)
        U_prev = [u.copy() for u in self.U]
        if steady:
            dt_phys = None
            gam = self.blend(self._pseudo_dt())
            U, p, dtau, div = self._pseudo_step(gam)
            dt_report = dtau
        else:
            dt_phys = self.rs.fixed_dt or super().stable_dt()
            gam = self.blend(dt_phys)
            U_n = [u.copy() for u in self.U]
            for k in range(self.inner_max):
                U, p, dtau, div = self._pseudo_step(gam, U_n, dt_phys)
                change = max(float(np.linalg.norm(U[c] - self.U[c])) for c in range(self.nd))
                self.U, self.p = U, p
                scale = math.sqrt(sum(float(np.linalg.norm(u)) ** 2 for u in U)) + 1e-12
                div_n = float(np.sqrt(np.mean(div[self.F] ** 2))) * self.L_ref / self.u_ref
                if k >= 2 and change / scale < 1e-6 and div_n < 1e-4:
                    break
            dt_report = dt_phys
        sc = self.t_ref / dt_report
        total = math.sqrt(sum(float(np.linalg.norm(u)) ** 2 for u in U))
        comps = [float(np.linalg.norm(U[c] - U_prev[c]) / (np.linalg.norm(U[c]) + 1e-6 * total + 1e-12 * self.u_ref) * sc)
                 for c in range(self.nd)]
        res.u, res.v = comps[0], comps[1]
        res.w = comps[2] if self.nd == 3 else 0.0
        res.p = float(np.sqrt(np.mean(div[self.F] ** 2))) * self.L_ref / self.u_ref
        self.U, self.p = U, p
        if self.energy and update_energy:
            res.T = self._energy_step(dt_report, gam)
        self.time += dt_report
        self.iteration += 1
        self.last_dt = dt_report
        self._check_finite(res)
        self.wall_time += time.perf_counter() - t0
        return res

    def describe_mesh(self) -> str:
        return super().describe_mesh() + f"; AC zeta = {self.zeta:.4g}"


class AcFallbackBackend(SolverBackend):
    id = "python-ac"
    label = "Python artificial compressibility (PyFR fallback)"
    description = ("Pure NumPy artificial-compressibility solver (PyFR's ac-navier-stokes equations, SSP-RK3 pseudo-time, "
                   "dual time stepping) on 2nd-order MAC finite volumes. Used automatically when PyFR is not installed.")
    capabilities = {"dims": (2, 3), "steady": True, "transient": True, "energy": False, "buoyancy": False, "turbulence": False}

    def run(self, case, hooks, run_dir: Path) -> BackendResult:
        solver = ACSolver(case)
        result = run_simulation(case, hooks, solver=solver)
        s = result.solver
        meta = s.result_meta(case.run.mode)
        meta["backend"] = self.id
        result.summary["backend"] = self.id
        result.summary["dimension"] = case.ndim
        return BackendResult(result.status, result.converged, s.iteration, s.time, meta, s.S,
                             result.times or [s.time], result.frames or [s.state_arrays()], result.summary)
