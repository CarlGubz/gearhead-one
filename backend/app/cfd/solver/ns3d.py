"""Dimension-generic (2-D / 3-D) incompressible Navier-Stokes + energy solver.

This is the native 3-D solver of CFDFlow and the Python stand-in for OpenFOAM's laminar
``icoFoam`` / ``pimpleFoam`` / ``buoyantPimpleFoam`` when OpenFOAM is not installed. It uses the
same numerical method as the validated 2-D solver (:mod:`ns2d`), written once for any number of
axes:

* Staggered (MAC) grid, finite volume, uniform Cartesian cells with immersed solid cells
  (cell-mask, staircase surfaces). Velocity component ``c`` lives on the faces normal to axis
  ``c``; pressure and temperature at cell centres.
* Chorin fractional-step projection. Explicit momentum predictor with Hirt/Griebel blended
  central / donor-cell convection and central diffusion.
* Pressure Poisson equation: pre-factorised sparse LU for small systems; for larger 3-D meshes
  a preconditioned conjugate-gradient solve whose preconditioner is the *exact* fast-Fourier
  (DCT/DST) inverse of the obstacle-free Laplacian with the same wall / outlet conditions.
* Optional energy equation with Boussinesq buoyancy, heat sources and fixed-temperature or
  adiabatic walls and obstacles.

Boundary sides: ``left/right`` (x), ``bottom/top`` (y), ``back/front`` (z). Moving walls slide
along +x (bottom, top, back, front) or +y (left, right).

Pressure inside the solver is kinematic (p / rho) and multiplied by rho for output.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import scipy.fft as sfft
import scipy.sparse as sp
from scipy.ndimage import label
from scipy.sparse.linalg import LinearOperator, cg, splu

from .grid3d import AXIS_SIDES, GridND, build_grid_nd, probe_point, sample_nd, source_mask
from .ns2d import SolverDiverged

TANGENT = {"left": 1, "right": 1, "bottom": 0, "top": 0, "back": 0, "front": 0}
DIRECT_LIMIT = {2: 40_000, 3: 15_000}  # fluid cells up to which the pressure system is LU-factorised


def _s(nd: int, axis: int, s) -> tuple:
    t = [slice(None)] * nd
    t[axis] = s
    return tuple(t)


@dataclass
class Residuals3D:
    u: float = 0.0
    v: float = 0.0
    w: float = 0.0
    p: float = 0.0
    T: float = 0.0
    ndim: int = 3

    def as_dict(self, energy: bool) -> dict:
        d = {"u": self.u, "v": self.v, "p": self.p}
        if self.ndim == 3:
            d["w"] = self.w
        if energy:
            d["T"] = self.T
        return d

    def max(self) -> float:
        return max(self.u, self.v, self.w, self.T)


# --------------------------------------------------------------------------- pressure solvers


class _DirectPoisson:
    def __init__(self, A: sp.csr_matrix):
        self.lu = splu(A.tocsc())

    def solve(self, rhs: np.ndarray, x0: np.ndarray | None = None) -> np.ndarray:
        return self.lu.solve(rhs)


class _SpectralPCG:
    """CG on -A (SPD) preconditioned by the DCT/DST inverse of the obstacle-free Laplacian."""

    def __init__(self, A: sp.csr_matrix, fluid: np.ndarray, h, axis_bc: list[tuple[bool, bool]], tol: float = 1e-9):
        self.A = (-A).tocsr()
        self.F = fluid
        self.shape = fluid.shape
        self.tol = tol
        self.last_iters = 0
        lam = np.zeros(self.shape)
        self.transforms = []
        for a, (dir_lo, dir_hi) in enumerate(axis_bc):
            n = self.shape[a]
            k = np.arange(n)
            if not dir_lo and not dir_hi:
                theta, kind = np.pi * k / n, ("dct", 2)
            elif dir_lo and dir_hi:
                theta, kind = np.pi * (k + 1) / n, ("dst", 2)
            elif dir_hi:  # Neumann low, Dirichlet high
                theta, kind = np.pi * (k + 0.5) / n, ("dct", 4)
            else:
                theta, kind = np.pi * (k + 0.5) / n, ("dst", 4)
            ev = (2.0 - 2.0 * np.cos(theta)) / h[a] ** 2
            shp = [1] * len(self.shape)
            shp[a] = n
            lam = lam + ev.reshape(shp)
            self.transforms.append(kind)
        zero = lam <= 0
        lam[zero] = 1.0 / (min(h) ** 2 * fluid.size)
        self.inv_lam = 1.0 / lam
        nf = int(fluid.sum())
        self.M = LinearOperator((nf, nf), matvec=self._precondition, dtype=float)

    def _precondition(self, r: np.ndarray) -> np.ndarray:
        x = np.zeros(self.shape)
        x[self.F] = r
        for a, (fam, typ) in enumerate(self.transforms):
            x = (sfft.dct if fam == "dct" else sfft.dst)(x, type=typ, axis=a, norm="ortho")
        x *= self.inv_lam
        for a, (fam, typ) in enumerate(self.transforms):
            x = (sfft.idct if fam == "dct" else sfft.idst)(x, type=typ, axis=a, norm="ortho")
        return x[self.F]

    def solve(self, rhs: np.ndarray, x0: np.ndarray | None = None) -> np.ndarray:
        it = [0]

        def cb(_):
            it[0] += 1

        sol, info = cg(self.A, -rhs, x0=None if x0 is None else x0, rtol=self.tol, atol=0.0, maxiter=2000, M=self.M, callback=cb)
        self.last_iters = it[0]
        return sol


# --------------------------------------------------------------------------- solver


class FlowSolver3D:
    """Projection solver on an N-D MAC grid. ``ndim`` defaults to the case dimension."""

    supports_direct_energy = False

    def __init__(self, case, grid: GridND | None = None, ndim: int | None = None):
        self.case = case
        g = self.g = grid or build_grid_nd(case, ndim)
        self.nd = g.ndim
        m = case.material
        self.rho = m.density
        self.mu = m.viscosity
        self.nu = m.viscosity / m.density
        self.alpha = m.conductivity / (m.density * m.specific_heat)
        self.cp = m.specific_heat
        self.k = m.conductivity
        self.energy = case.models.energy
        self.buoyancy = bool(case.models.buoyancy and case.models.energy)
        self.beta = m.thermal_expansion
        self.gvec = (case.models.gravity_x, case.models.gravity_y, case.models.gravity_z)[: self.nd]
        self.T_ref = case.models.reference_temperature
        self.sides = [s for pair in AXIS_SIDES[: self.nd] for s in pair]
        self.bc = {s: getattr(case.boundaries, s) for s in self.sides}
        self.rs = case.run
        self.S = g.solid
        self.F = ~g.solid
        if not self.F.any():
            raise ValueError("The domain contains no fluid cells (obstacles fill the whole domain).")
        self._setup_faces()
        self._setup_poisson()
        self._setup_energy()
        self._reference_scales()
        self.init_fields()

    # ------------------------------------------------------------------ setup
    def _profile(self, side: str, c: int, open_faces: np.ndarray) -> np.ndarray:
        """Normal inlet speed per boundary face; the mean over the open faces equals bc.velocity."""
        bc = self.bc[side]
        out = np.where(open_faces, bc.velocity, 0.0).astype(float)
        if bc.profile != "parabolic" or not open_faces.any():
            return out
        g = self.g
        t_axes = [a for a in range(self.nd) if a != c]
        coords = np.meshgrid(*[g.centres[a] for a in t_axes], indexing="ij")
        shape = np.ones(open_faces.shape)
        for C, a in zip(coords, t_axes):
            lo = C[open_faces].min() - g.h[a] / 2
            hi = C[open_faces].max() + g.h[a] / 2
            s = (C - lo) / (hi - lo)
            shape = shape * np.clip(6.0 * s * (1 - s), 0, None)
        shape = np.where(open_faces, shape, 0.0)
        mean = shape[open_faces].mean()
        return bc.velocity * shape / (mean if mean > 0 else 1.0)

    def _setup_faces(self):
        g, S, nd = self.g, self.S, self.nd
        n = g.n
        self.solidF, self.activeF, self.fixF, self.outF = [], [], [], []
        for c in range(nd):
            shp = list(n)
            shp[c] += 1
            solid = np.zeros(shp, bool)
            solid[_s(nd, c, slice(1, n[c]))] = S[_s(nd, c, slice(None, -1))] | S[_s(nd, c, slice(1, None))]
            solid[_s(nd, c, 0)] = S[_s(nd, c, 0)]
            solid[_s(nd, c, n[c])] = S[_s(nd, c, n[c] - 1)]
            active = np.zeros(shp, bool)
            active[_s(nd, c, slice(1, n[c]))] = ~solid[_s(nd, c, slice(1, n[c]))]
            fix = np.zeros(shp)
            out = np.zeros(shp, bool)
            lo, hi = AXIS_SIDES[c]
            open_lo, open_hi = ~S[_s(nd, c, 0)], ~S[_s(nd, c, n[c] - 1)]
            if self.bc[lo].type == "inlet":
                fix[_s(nd, c, 0)] = self._profile(lo, c, open_lo)
            elif self.bc[lo].type == "outlet":
                out[_s(nd, c, 0)] = open_lo
            if self.bc[hi].type == "inlet":
                fix[_s(nd, c, n[c])] = -self._profile(hi, c, open_hi)
            elif self.bc[hi].type == "outlet":
                out[_s(nd, c, n[c])] = open_hi
            self.solidF.append(solid)
            self.activeF.append(active)
            self.fixF.append(fix)
            self.outF.append(out)
        self.has_out = [(self.outF[c][_s(nd, c, 0)].any(), self.outF[c][_s(nd, c, n[c])].any()) for c in range(nd)]

        # tangential ghost rule ghost = a * value + b, per (component, side)
        self.gh = {}
        for c in range(nd):
            for side in self.sides:
                bc = self.bc[side]
                if bc.type == "wall":
                    self.gh[(c, side)] = (-1.0, 2.0 * bc.wall_velocity if TANGENT[side] == c else 0.0)
                elif bc.type == "inlet":
                    self.gh[(c, side)] = (-1.0, 0.0)
                else:  # outlet / symmetry: zero gradient
                    self.gh[(c, side)] = (1.0, 0.0)

    def _setup_poisson(self):
        g, F, nd = self.g, self.F, self.nd
        n = g.n
        idx = -np.ones(n, dtype=np.int64)
        nf = int(F.sum())
        idx[F] = np.arange(nf)
        rows, cols, vals = [], [], []
        diag = np.zeros(nf)
        dir_cell = np.zeros(n, bool)
        for a in range(nd):
            lo, hi = _s(nd, a, slice(None, -1)), _s(nd, a, slice(1, None))
            ff = F[lo] & F[hi]
            ia, ib = idx[lo][ff], idx[hi][ff]
            w = 1.0 / g.h[a] ** 2
            rows += [ia, ib]
            cols += [ib, ia]
            vals += [np.full(ia.shape, w)] * 2
            np.add.at(diag, ia, -w)
            np.add.at(diag, ib, -w)
            for end, cell in ((0, 0), (n[a], n[a] - 1)):
                face_out = self.outF[a][_s(nd, a, end)]
                sl = _s(nd, a, cell)
                np.add.at(diag, idx[sl][face_out], -2.0 / g.h[a] ** 2)
                dir_cell[sl] |= face_out
        # components without a Dirichlet cell: pin p = 0 at one phantom face (keeps A symmetric
        # and is exact for a compatible right-hand side)
        lab, ncomp = label(F)
        pinned = []
        for comp in range(1, ncomp + 1):
            cm = lab == comp
            if not (dir_cell & cm).any():
                first = tuple(np.argwhere(cm)[0])
                pinned.append(idx[first])
                diag[idx[first]] -= 1.0 / min(g.h) ** 2
        self.n_pinned = len(pinned)
        A = sp.coo_matrix((np.concatenate(vals) if vals else np.zeros(0),
                           (np.concatenate(rows) if rows else np.zeros(0, int), np.concatenate(cols) if cols else np.zeros(0, int))),
                          shape=(nf, nf)).tocsr() + sp.diags(diag)
        self.idx = idx
        if nf <= DIRECT_LIMIT[nd]:
            self.poisson = _DirectPoisson(A)
            self.poisson_kind = "sparse LU"
        else:
            axis_bc = []
            for a in range(nd):
                lo, hi = AXIS_SIDES[a]
                axis_bc.append((self.bc[lo].type == "outlet", self.bc[hi].type == "outlet"))
            self.poisson = _SpectralPCG(A, F, g.h, axis_bc)
            self.poisson_kind = "spectral-preconditioned CG"

    def _setup_energy(self):
        g, S, F, nd = self.g, self.S, self.F, self.nd
        self.Ts = np.where(np.isnan(g.solid_T), 0.0, g.solid_T)
        dirS = ~np.isnan(g.solid_T)
        self.e_masks = []
        for a in range(nd):
            lo, hi = _s(nd, a, slice(None, -1)), _s(nd, a, slice(1, None))
            self.e_masks.append({
                "ff": F[lo] & F[hi],
                "fs": F[lo] & S[hi] & dirS[hi],
                "sf": S[lo] & F[hi] & dirS[lo],
            })
        q = np.zeros(g.n)
        coords = g.mesh_coords()
        for hs in self.case.heat_sources:
            q[source_mask(hs, coords, self.case.domain.depth) & F] += hs.power_density / (self.rho * self.cp)
        self.q_src = q

    def _reference_scales(self):
        bcs = self.bc.values()
        speeds = [abs(b.velocity) for b in bcs if b.type == "inlet"] + [abs(b.wall_velocity) for b in bcs if b.type == "wall"]
        temps = [b.temperature for b in bcs if b.thermal == "fixed" and b.type == "wall"]
        temps += [b.temperature for b in bcs if b.type == "inlet"]
        temps += [o.temperature for o in self.case.obstacles if o.thermal == "fixed"]
        dT = (max(temps) - min(temps)) if len(temps) > 1 else 0.0
        self.dT_ref = dT if dT > 0 else 1.0
        L = min(self.g.L)
        u_ref = max(speeds) if speeds else 0.0
        if u_ref == 0.0 and self.buoyancy and dT > 0:
            u_ref = math.sqrt(math.hypot(*self.gvec) * self.beta * dT * L)
        if u_ref == 0.0:
            ini = self.case.initial
            u_ref = max(abs(ini.velocity_x), abs(ini.velocity_y), abs(ini.velocity_z), 1e-3)
        self.u_ref, self.L_ref, self.t_ref = u_ref, L, L / u_ref

    # ------------------------------------------------------------------ state
    def init_fields(self):
        ini = self.case.initial
        v0 = (ini.velocity_x, ini.velocity_y, ini.velocity_z)
        self.U = [np.where(self.activeF[c], v0[c], 0.0) for c in range(self.nd)]
        self._apply_bc(self.U)
        self.p = np.zeros(self.g.n)
        g = self.g
        self.T = np.where(self.S, np.where(np.isnan(g.solid_T), ini.temperature, g.solid_T), ini.temperature)
        self.time = 0.0
        self.iteration = 0
        self.last_dt = 0.0
        self.wall_time = 0.0

    def _outlet_copy(self, U, c):
        nd, n = self.nd, self.g.n[c]
        lo_out, hi_out = self.has_out[c]
        if lo_out:
            U[c][_s(nd, c, 0)] = np.where(self.outF[c][_s(nd, c, 0)], U[c][_s(nd, c, 1)], 0.0)
        if hi_out:
            U[c][_s(nd, c, n)] = np.where(self.outF[c][_s(nd, c, n)], U[c][_s(nd, c, n - 1)], 0.0)

    def _apply_bc(self, U):
        for c in range(self.nd):
            fixed = ~self.activeF[c] & ~self.outF[c]
            U[c][fixed] = self.fixF[c][fixed]
            U[c][self.solidF[c]] = 0.0
            self._outlet_copy(U, c)

    # ------------------------------------------------------------------ operators
    def _neighbours(self, Uc, c, d):
        """Neighbours of component c along axis d (d != c) with wall / obstacle mirror ghosts."""
        nd = self.nd
        plus = np.empty_like(Uc)
        minus = np.empty_like(Uc)
        head, tail = _s(nd, d, slice(None, -1)), _s(nd, d, slice(1, None))
        plus[head] = Uc[tail]
        minus[tail] = Uc[head]
        lo_side, hi_side = AXIS_SIDES[d]
        a, b = self.gh[(c, hi_side)]
        plus[_s(nd, d, -1)] = a * Uc[_s(nd, d, -1)] + b
        a, b = self.gh[(c, lo_side)]
        minus[_s(nd, d, 0)] = a * Uc[_s(nd, d, 0)] + b
        sF = self.solidF[c]
        plus[head] = np.where(sF[tail], -Uc[head], plus[head])
        minus[tail] = np.where(sF[head], -Uc[tail], minus[tail])
        return plus, minus

    def momentum_rhs(self, U, gam: float) -> list[np.ndarray]:
        """Explicit momentum right-hand side (per unit mass) at the interior faces of every component."""
        nd, h, nu = self.nd, self.g.h, self.nu
        I = slice(1, -1)
        out = []
        for c in range(nd):
            Uc = U[c]
            ui = Uc[_s(nd, c, I)]
            ucc = 0.5 * (Uc[_s(nd, c, slice(None, -1))] + Uc[_s(nd, c, slice(1, None))])
            A, B = ucc[_s(nd, c, slice(1, None))], ucc[_s(nd, c, slice(None, -1))]
            Up, Um = Uc[_s(nd, c, slice(2, None))], Uc[_s(nd, c, slice(None, -2))]
            conv = (A**2 - B**2) / h[c] + gam / h[c] * (np.abs(A) * (ui - Up) / 2 - np.abs(B) * (Um - ui) / 2)
            lap = (Up - 2 * ui + Um) / h[c] ** 2
            for d in range(nd):
                if d == c:
                    continue
                plus, minus = self._neighbours(Uc, c, d)
                up, dn = plus[_s(nd, c, I)], minus[_s(nd, c, I)]
                Ud = U[d]
                Ue = 0.5 * (Ud[_s(nd, c, slice(None, -1))] + Ud[_s(nd, c, slice(1, None))])
                top, bot = Ue[_s(nd, d, slice(1, None))], Ue[_s(nd, d, slice(None, -1))]
                conv = conv + (top * 0.5 * (ui + up) - bot * 0.5 * (dn + ui)) / h[d] + gam / h[d] * (
                    np.abs(top) * (ui - up) / 2 - np.abs(bot) * (dn - ui) / 2
                )
                lap = lap + (up - 2 * ui + dn) / h[d] ** 2
            rhs = nu * lap - conv
            if self.buoyancy and self.gvec[c] != 0.0:
                Tf = 0.5 * (self.T[_s(nd, c, slice(None, -1))] + self.T[_s(nd, c, slice(1, None))])
                rhs = rhs - self.gvec[c] * self.beta * (Tf - self.T_ref)
            out.append(rhs)
        return out

    def divergence(self, U) -> np.ndarray:
        nd, h = self.nd, self.g.h
        div = np.zeros(self.g.n)
        for c in range(nd):
            div += (U[c][_s(nd, c, slice(1, None))] - U[c][_s(nd, c, slice(None, -1))]) / h[c]
        return div

    def blend(self, dt: float) -> float:
        if self.rs.convection_blend is not None:
            return self.rs.convection_blend
        cour = max(float(np.abs(self.U[c]).max()) * dt / self.g.h[c] for c in range(self.nd))
        return min(1.2 * cour, 1.0)

    # ------------------------------------------------------------------ time step
    def stable_dt(self) -> float:
        g = self.g
        nu = max(self.nu, self.alpha if self.energy else 0.0)
        dt = 1.0 / (2.0 * nu * sum(1 / h**2 for h in g.h))
        for c in range(self.nd):
            dt = min(dt, g.h[c] / max(float(np.abs(self.U[c]).max()), 1e-12))
        if self.buoyancy:
            dT = float(np.abs(self.T[self.F] - self.T_ref).max())
            a = math.hypot(*self.gvec) * self.beta * dT
            if a > 0:
                dt = min(dt, math.sqrt(min(g.h) / a))
        return self.rs.cfl * dt

    def project(self, Us, dt: float):
        """Pressure projection of the predicted velocity; returns (U_new, p)."""
        nd, g = self.nd, self.g
        rhs = self.divergence(Us)[self.F] / dt
        pv = self.poisson.solve(rhs, self.p[self.F] if isinstance(self.poisson, _SpectralPCG) else None)
        p = np.zeros(g.n)
        p[self.F] = pv
        Un = [u.copy() for u in Us]
        I = slice(1, -1)
        for c in range(nd):
            h = g.h[c]
            grad = (p[_s(nd, c, slice(1, None))] - p[_s(nd, c, slice(None, -1))]) / h
            act = self.activeF[c][_s(nd, c, I)]
            Un[c][_s(nd, c, I)] = np.where(act, Us[c][_s(nd, c, I)] - dt * grad, Us[c][_s(nd, c, I)])
            lo_out, hi_out = self.has_out[c]
            n = g.n[c]
            if lo_out:
                Un[c][_s(nd, c, 0)] = np.where(self.outF[c][_s(nd, c, 0)],
                                               Us[c][_s(nd, c, 0)] - dt * p[_s(nd, c, 0)] / (h / 2), 0.0)
            if hi_out:
                Un[c][_s(nd, c, n)] = np.where(self.outF[c][_s(nd, c, n)],
                                               Us[c][_s(nd, c, n)] + dt * p[_s(nd, c, n - 1)] / (h / 2), 0.0)
        return Un, p

    def _residuals(self, Un, p, dt) -> Residuals3D:
        sc = self.t_ref / dt
        # per-component relative change; the total velocity norm in the denominator keeps a component
        # that is identically zero (e.g. w with symmetry planes) from producing 0/0 residuals
        total = math.sqrt(sum(float(np.linalg.norm(u)) ** 2 for u in Un))
        r = [float(np.linalg.norm(Un[c] - self.U[c]) / (np.linalg.norm(Un[c]) + 1e-6 * total + 1e-12 * self.u_ref) * sc)
             for c in range(self.nd)]
        res = Residuals3D(ndim=self.nd)
        res.u, res.v = r[0], r[1]
        res.w = r[2] if self.nd == 3 else 0.0
        res.p = float(np.linalg.norm(p - self.p) / (np.linalg.norm(p) + 1e-12 * self.u_ref**2) * sc)
        return res

    def step(self, update_energy: bool = True) -> Residuals3D:
        t0 = time.perf_counter()
        nd = self.nd
        dt = self.rs.fixed_dt or self.stable_dt()
        gam = self.blend(dt)
        rhs = self.momentum_rhs(self.U, gam)
        Us = [u.copy() for u in self.U]
        I = slice(1, -1)
        for c in range(nd):
            ui = self.U[c][_s(nd, c, I)]
            Us[c][_s(nd, c, I)] = np.where(self.activeF[c][_s(nd, c, I)], ui + dt * rhs[c], ui)
            self._outlet_copy(Us, c)
        Un, p = self.project(Us, dt)
        res = self._residuals(Un, p, dt)
        self.U, self.p = Un, p
        if self.energy and update_energy:
            res.T = self._energy_step(dt, gam)
        self.time += dt
        self.iteration += 1
        self.last_dt = dt
        self._check_finite(res)
        self.wall_time += time.perf_counter() - t0
        return res

    def _check_finite(self, res):
        if not (np.isfinite(res.u) and np.isfinite(res.v) and all(np.isfinite(u).all() for u in self.U)):
            raise SolverDiverged(
                f"Solution diverged at iteration {self.iteration} (t={self.time:.4g}). "
                "Try a finer mesh, a smaller CFL number, or lower velocities."
            )

    # ------------------------------------------------------------------ energy
    def energy_rhs(self, gam: float) -> np.ndarray:
        g, nd, T, al, F = self.g, self.nd, self.T, self.alpha, self.F
        I = slice(1, -1)
        dTdt = self.q_src.copy()
        for a in range(nd):
            h = g.h[a]
            m = self.e_masks[a]
            lo, hi = _s(nd, a, slice(None, -1)), _s(nd, a, slice(1, None))
            shp = list(g.n)
            shp[a] += 1
            Fl = np.zeros(shp)
            TL, TR = T[lo], T[hi]
            uf = self.U[a][_s(nd, a, I)]
            Tf = 0.5 * (TL + TR) - 0.5 * gam * np.sign(uf) * (TR - TL)
            flux = np.where(m["ff"], uf * Tf - al * (TR - TL) / h, 0.0)
            flux = flux + np.where(m["fs"], -al * (self.Ts[hi] - TL) / (h / 2), 0.0)
            flux = flux + np.where(m["sf"], -al * (TR - self.Ts[lo]) / (h / 2), 0.0)
            Fl[_s(nd, a, I)] = flux
            lo_side, hi_side = AXIS_SIDES[a]
            n = g.n[a]
            c0, c1 = _s(nd, a, 0), _s(nd, a, n - 1)
            Fl[_s(nd, a, 0)] = np.where(F[c0], self._side_flux(lo_side, T[c0], self.U[a][_s(nd, a, 0)], h, -1), 0.0)
            Fl[_s(nd, a, n)] = np.where(F[c1], self._side_flux(hi_side, T[c1], self.U[a][_s(nd, a, n)], h, +1), 0.0)
            dTdt -= (Fl[hi] - Fl[lo]) / h
        return dTdt

    def _side_flux(self, side, Tc, un, h, sign):
        """Energy flux in the +axis direction through a boundary face (sign -1 low side, +1 high side)."""
        bc, al = self.bc[side], self.alpha
        if bc.type == "inlet":
            Tb = bc.temperature
            return un * np.where(un * (-sign) >= 0, Tb, Tc) - al * sign * (Tb - Tc) / (h / 2)
        if bc.type == "outlet":
            return un * Tc
        if bc.type == "wall" and bc.thermal == "fixed":
            return -al * sign * (bc.temperature - Tc) / (h / 2)
        return np.zeros_like(Tc)

    def _energy_step(self, dt: float, gam: float) -> float:
        F = self.F
        Tn = np.where(F, self.T + dt * self.energy_rhs(gam), self.T)
        r = float(
            np.linalg.norm((Tn - self.T)[F])
            / (np.linalg.norm((Tn - self.T_ref)[F]) + 1e-3 * self.dT_ref * math.sqrt(F.sum()))
            * self.t_ref / dt
        )
        self.T = Tn
        return r

    def solve_energy_steady(self) -> bool:
        return False

    # ------------------------------------------------------------------ outputs
    def cell_velocity(self) -> list[np.ndarray]:
        nd = self.nd
        return [np.where(self.S, 0.0, 0.5 * (self.U[c][_s(nd, c, slice(None, -1))] + self.U[c][_s(nd, c, slice(1, None))]))
                for c in range(nd)]

    def cell_fields(self) -> dict[str, np.ndarray]:
        uc = self.cell_velocity()
        f = {"u": uc[0], "v": uc[1], "w": uc[2] if self.nd == 3 else np.zeros(self.g.n)}
        f["speed"] = np.sqrt(sum(u**2 for u in uc))
        f["p"] = np.where(self.S, 0.0, self.p * self.rho)
        f["T"] = self.T.copy()
        f["vorticity"] = np.where(self.S, 0.0, vorticity(uc, self.g.h))
        return f

    def mass_imbalance(self) -> float:
        g, nd = self.g, self.nd
        inflow = outflow = 0.0
        for c in range(nd):
            area = g.cell_volume / g.h[c]
            lo, hi = self.U[c][_s(nd, c, 0)], self.U[c][_s(nd, c, g.n[c])]
            inflow += (np.clip(lo, 0, None).sum() + np.clip(-hi, 0, None).sum()) * area
            outflow += (np.clip(-lo, 0, None).sum() + np.clip(hi, 0, None).sum()) * area
        if inflow < 1e-14:
            return 0.0
        return float((inflow - outflow) / inflow)

    def obstacle_loads(self) -> dict[str, dict]:
        f = self.cell_fields()
        uc = [f["u"], f["v"], f["w"]][: self.nd]
        return obstacle_loads_cc(self.g, self.case.obstacles, f["p"], uc, self.T, self.mu, self.k, self.energy)

    def boundary_pressures(self) -> tuple[list[float], list[float]]:
        return boundary_pressures_cc(self.g, self.bc, self.cell_fields()["p"])

    def sample(self, field: np.ndarray, *point) -> float:
        return sample_nd(field, self.g, point)

    def monitors(self) -> dict[str, float]:
        return monitors_cc(self.case, self.g, self.cell_fields(), self.mass_imbalance(), self.last_dt,
                           self.obstacle_loads(), self.energy)

    def state_arrays(self) -> dict[str, np.ndarray]:
        f = self.cell_fields()
        return {k: as3d(f[k]).astype(np.float32) for k in ("u", "v", "w", "p", "T")}

    def result_meta(self, mode: str) -> dict:
        return result_meta_cc(self.g, self.energy, self.rho, self.iteration, mode)

    def describe_mesh(self) -> str:
        g = self.g
        cells = " x ".join(str(k) for k in g.n)
        sizes = ", ".join(f"d{ax}={h:.4g} m" for ax, h in zip("xyz", g.h))
        return f"Mesh: {cells} cells ({self.F.sum()} fluid cells), {sizes}; pressure solver: {self.poisson_kind}"


# --------------------------------------------------------------------------- shared cell-centred helpers
# (also used to post-process OpenFOAM / FEniCSx / PyFR results sampled onto the structured grid)


def as3d(a: np.ndarray) -> np.ndarray:
    return a if a.ndim == 3 else a[..., None]


def vorticity(uc: list[np.ndarray], h) -> np.ndarray:
    """2-D: scalar w_z. 3-D: magnitude of the vorticity vector."""
    nd = len(uc)

    def d(f, a):
        return np.gradient(f, h[a], axis=a) if f.shape[a] > 1 else np.zeros_like(f)

    if nd == 2:
        return d(uc[1], 0) - d(uc[0], 1)
    wx = d(uc[2], 1) - d(uc[1], 2)
    wy = d(uc[0], 2) - d(uc[2], 0)
    wz = d(uc[1], 0) - d(uc[0], 1)
    return np.sqrt(wx**2 + wy**2 + wz**2)


def obstacle_loads_cc(g: GridND, obstacles, p_pa, uc, T, mu, k_cond, energy) -> dict[str, dict]:
    """Pressure + viscous force and heat-transfer rate on each staircase obstacle.

    3-D: N and W. 2-D: per metre of depth (N/m, W/m), like the 2-D solver.
    """
    nd, F = g.ndim, g.fluid
    out = {}
    for k, ob in enumerate(obstacles):
        mine = g.solid_id == k
        force = [0.0] * nd
        q = 0.0
        for a in range(nd):
            area = g.cell_volume / g.h[a]
            lo, hi = _s(nd, a, slice(None, -1)), _s(nd, a, slice(1, None))
            for sl_f, sl_s, sgn in ((lo, hi, +1), (hi, lo, -1)):
                sel = F[sl_f] & mine[sl_s]
                if not sel.any():
                    continue
                force[a] += sgn * float(p_pa[sl_f][sel].sum()) * area
                for t in range(nd):
                    if t != a:
                        force[t] += float(mu * uc[t][sl_f][sel].sum() / (g.h[a] / 2) * area)
                if energy:
                    Ts, Tc = g.solid_T[sl_s][sel], T[sl_f][sel]
                    ok = ~np.isnan(Ts)
                    q += float(k_cond * (Ts - Tc)[ok].sum() / (g.h[a] / 2) * area)
        d = {"Fx": force[0], "Fy": force[1], "Q": q}
        if nd == 3:
            d["Fz"] = force[2]
        out[ob.id] = d
    return out


def boundary_pressures_cc(g: GridND, bcs: dict, p_pa) -> tuple[list[float], list[float]]:
    nd, F = g.ndim, g.fluid
    inlet, outlet = [], []
    for a in range(nd):
        for side, cell in zip(AXIS_SIDES[a], (0, g.n[a] - 1)):
            sl = _s(nd, a, cell)
            cells = F[sl]
            if not cells.any():
                continue
            t = bcs[side].type
            if t == "inlet":
                inlet.append(float(p_pa[sl][cells].mean()))
            elif t == "outlet":
                outlet.append(float(p_pa[sl][cells].mean()))
    return inlet, outlet


def monitors_cc(case, g: GridND, f: dict, mass_imbalance: float, dt: float, loads: dict, energy: bool) -> dict[str, float]:
    vals: dict[str, float] = {"dt": dt, "max_speed": float(f["speed"].max()), "mass_imbalance": mass_imbalance}
    comps = ("u", "v", "w")[: g.ndim]
    for pr in case.probes:
        pt = probe_point(pr, g.ndim, case.domain.depth)
        for c in comps + ("p",) + (("T",) if energy else ()):
            vals[f"{pr.id}.{c}"] = sample_nd(f[c], g, pt)
    for oid, d in loads.items():
        for key in ("Fx", "Fy", "Fz"):
            if key in d:
                vals[f"{oid}.{key}"] = d[key]
        if energy:
            vals[f"{oid}.Q"] = d["Q"]
    return vals


def result_meta_cc(g: GridND, energy: bool, rho: float, iteration: int, mode: str, **extra) -> dict:
    return {
        "format": "cc",
        "dim": g.ndim,
        "nx": g.nx, "ny": g.ny, "nz": g.nz,
        "lx": g.lx, "ly": g.ly, "lz": g.lz if g.ndim == 3 else min(g.h),
        "energy": energy,
        "rho": rho,
        "iteration": iteration,
        "mode": mode,
        **extra,
    }
