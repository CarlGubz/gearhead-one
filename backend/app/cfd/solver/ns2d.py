"""2-D incompressible Navier-Stokes + energy solver.

Numerical method (see README, "Solver"):

* Staggered (MAC) grid, finite volume, uniform Cartesian cells with immersed solid cells.
* Chorin fractional-step projection: explicit momentum predictor (Hirt/Griebel blended
  central/donor-cell convection, central diffusion), pressure Poisson equation solved with a
  pre-factorised sparse LU (SciPy ``splu``), then velocity correction.
* Optional energy equation (cell-centred, same blended convection) with Boussinesq buoyancy,
  volumetric heat sources and fixed-temperature / adiabatic walls and obstacles.
* Boundary conditions per side: wall (optionally moving), inlet (uniform / parabolic),
  outlet (p = 0 gauge, zero-gradient velocity) and symmetry.

All quantities are SI. Pressure inside the solver is kinematic (p/rho); it is multiplied by
rho when exported.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.ndimage import label
from scipy.sparse.linalg import splu

from .grid import Grid, build_grid

SIDES = ("left", "right", "bottom", "top")


class SolverDiverged(RuntimeError):
    pass


@dataclass
class Residuals:
    u: float = 0.0
    v: float = 0.0
    p: float = 0.0
    T: float = 0.0

    def as_dict(self, energy: bool) -> dict:
        d = {"u": self.u, "v": self.v, "p": self.p}
        if energy:
            d["T"] = self.T
        return d

    def max(self) -> float:
        return max(self.u, self.v, self.T)


class FlowSolver:
    supports_direct_energy = True

    def __init__(self, case, grid: Grid | None = None):
        self.case = case
        g = self.g = grid or build_grid(case)
        nx, ny, dx, dy = g.nx, g.ny, g.dx, g.dy
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
        self.gx, self.gy = case.models.gravity_x, case.models.gravity_y
        self.T_ref = case.models.reference_temperature
        self.bc = {s: getattr(case.boundaries, s) for s in SIDES}
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
    def _profile(self, side: str, fluid_flags: np.ndarray, coords: np.ndarray, h: float) -> np.ndarray:
        """Normal-velocity magnitude per boundary face, mean over open faces = bc.velocity."""
        bc = self.bc[side]
        out = np.where(fluid_flags, bc.velocity, 0.0).astype(float)
        if bc.profile == "parabolic" and fluid_flags.any():
            lo = coords[fluid_flags].min() - h / 2
            hi = coords[fluid_flags].max() + h / 2
            s = (coords - lo) / (hi - lo)
            shape = np.where(fluid_flags, 6.0 * s * (1 - s), 0.0)  # mean of 6s(1-s) over [0,1] is 1
            shape = np.clip(shape, 0, None)
            mean = shape[fluid_flags].mean()
            out = bc.velocity * shape / (mean if mean > 0 else 1.0)
        return out

    def _setup_faces(self):
        g, S = self.g, self.S
        nx, ny = g.nx, g.ny
        # u faces (nx+1, ny): i = face index (x = i*dx)
        solidU = np.zeros((nx + 1, ny), bool)
        solidU[1:nx] = S[:-1] | S[1:]
        solidU[0] = S[0]
        solidU[nx] = S[nx - 1]
        solidV = np.zeros((nx, ny + 1), bool)
        solidV[:, 1:ny] = S[:, :-1] | S[:, 1:]
        solidV[:, 0] = S[:, 0]
        solidV[:, ny] = S[:, ny - 1]
        self.solidU, self.solidV = solidU, solidV

        activeU = np.zeros((nx + 1, ny), bool)
        activeU[1:nx] = ~solidU[1:nx]
        activeV = np.zeros((nx, ny + 1), bool)
        activeV[:, 1:ny] = ~solidV[:, 1:ny]
        self.activeU, self.activeV = activeU, activeV

        # prescribed (non-solved) normal velocities at domain boundary faces
        self.u_fix = np.zeros((nx + 1, ny))  # values of prescribed faces (0 elsewhere)
        self.v_fix = np.zeros((nx, ny + 1))
        self.outU = np.zeros((nx + 1, ny), bool)  # outlet faces
        self.outV = np.zeros((nx, ny + 1), bool)
        yc, xc, dx, dy = g.yc, g.xc, g.dx, g.dy
        # left / right
        fl = ~S[0]
        fr = ~S[nx - 1]
        if self.bc["left"].type == "inlet":
            self.u_fix[0] = self._profile("left", fl, yc, dy)
        elif self.bc["left"].type == "outlet":
            self.outU[0] = fl
        if self.bc["right"].type == "inlet":
            self.u_fix[nx] = -self._profile("right", fr, yc, dy)
        elif self.bc["right"].type == "outlet":
            self.outU[nx] = fr
        fb = ~S[:, 0]
        ft = ~S[:, ny - 1]
        if self.bc["bottom"].type == "inlet":
            self.v_fix[:, 0] = self._profile("bottom", fb, xc, dx)
        elif self.bc["bottom"].type == "outlet":
            self.outV[:, 0] = fb
        if self.bc["top"].type == "inlet":
            self.v_fix[:, ny] = -self._profile("top", ft, xc, dx)
        elif self.bc["top"].type == "outlet":
            self.outV[:, ny] = ft

        # tangential ghost-value rules: ghost = a*value + b
        def ghost(side: str, tangential_wall_speed_ok=True):
            bc = self.bc[side]
            if bc.type == "wall":
                return -1.0, 2.0 * bc.wall_velocity
            if bc.type == "inlet":
                return -1.0, 0.0
            return 1.0, 0.0  # outlet / symmetry (zero-gradient)

        self.gh = {s: ghost(s) for s in SIDES}

    def _setup_poisson(self):
        g, S, F = self.g, self.S, self.F
        nx, ny, dx, dy = g.nx, g.ny, g.dx, g.dy
        idx = -np.ones((nx, ny), dtype=np.int64)
        idx[F] = np.arange(F.sum())
        n = int(F.sum())
        rows, cols, vals = [], [], []
        diag = np.zeros(n)

        # interior x faces
        ff = F[:-1] & F[1:]
        a = idx[:-1][ff]
        b = idx[1:][ff]
        w = 1.0 / dx**2
        rows += [a, b]
        cols += [b, a]
        vals += [np.full(a.shape, w)] * 2
        np.add.at(diag, a, -w)
        np.add.at(diag, b, -w)
        ff = F[:, :-1] & F[:, 1:]
        a = idx[:, :-1][ff]
        b = idx[:, 1:][ff]
        w = 1.0 / dy**2
        rows += [a, b]
        cols += [b, a]
        vals += [np.full(a.shape, w)] * 2
        np.add.at(diag, a, -w)
        np.add.at(diag, b, -w)
        # outlet faces: p = 0 at distance h/2
        dir_cell = np.zeros((nx, ny), bool)
        for arr, sl, h in (
            (self.outU[0], (0, slice(None)), dx),
            (self.outU[nx], (nx - 1, slice(None)), dx),
            (self.outV[:, 0], (slice(None), 0), dy),
            (self.outV[:, ny], (slice(None), ny - 1), dy),
        ):
            sel = idx[sl][arr]
            np.add.at(diag, sel, -2.0 / h**2)
            dir_cell[sl] |= arr

        # pin one cell in every connected fluid component that has no Dirichlet cell
        lab, ncomp = label(F)  # 4-connectivity
        pinned = []
        for c in range(1, ncomp + 1):
            comp = lab == c
            if not (dir_cell & comp).any():
                first = np.argwhere(comp)[0]
                pinned.append(idx[first[0], first[1]])
        self.n_pinned = len(pinned)

        A = sp.coo_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)
        ).tocsr() + sp.diags(diag)
        if pinned:
            A = A.tolil()
            for pc in pinned:
                A.rows[pc] = [pc]
                A.data[pc] = [1.0]
            A = A.tocsr()
        self.pin_idx = np.array(pinned, dtype=np.int64)
        self.idx = idx
        self.lu = splu(A.tocsc())

    def _setup_energy(self):
        g, S, F = self.g, self.S, self.F
        nx, ny = g.nx, g.ny
        # solid-fluid interface Dirichlet data for interior faces
        Ts = np.where(np.isnan(g.solid_T), 0.0, g.solid_T)
        dirS = ~np.isnan(g.solid_T)
        self.fs_x = F[:-1] & S[1:] & dirS[1:]  # fluid left, solid right (Dirichlet)
        self.fs_x_T = Ts[1:]
        self.sf_x = S[:-1] & F[1:] & dirS[:-1]
        self.sf_x_T = Ts[:-1]
        self.fs_y = F[:, :-1] & S[:, 1:] & dirS[:, 1:]
        self.fs_y_T = Ts[:, 1:]
        self.sf_y = S[:, :-1] & F[:, 1:] & dirS[:, :-1]
        self.sf_y_T = Ts[:, :-1]
        self.ff_x = F[:-1] & F[1:]
        self.ff_y = F[:, :-1] & F[:, 1:]
        # heat sources (K/s)
        q = np.zeros((nx, ny))
        X, Y = np.meshgrid(g.xc, g.yc, indexing="ij")
        for hs in self.case.heat_sources:
            m = (np.abs(X - hs.x) <= hs.width / 2) & (np.abs(Y - hs.y) <= hs.height / 2) & F
            q[m] += hs.power_density / (self.rho * self.cp)
        self.q_src = q

    def _reference_scales(self):
        speeds = [abs(b.velocity) for b in self.bc.values() if b.type == "inlet"]
        speeds += [abs(b.wall_velocity) for b in self.bc.values() if b.type == "wall"]
        temps = [b.temperature for b in self.bc.values() if b.thermal == "fixed" and b.type == "wall"]
        temps += [b.temperature for b in self.bc.values() if b.type == "inlet"]
        temps += [o.temperature for o in self.case.obstacles if o.thermal == "fixed"]
        dT = (max(temps) - min(temps)) if len(temps) > 1 else 0.0
        self.dT_ref = dT if dT > 0 else 1.0
        L = min(self.g.lx, self.g.ly)
        u_ref = max(speeds) if speeds else 0.0
        if u_ref == 0.0 and self.buoyancy and dT > 0:
            gm = math.hypot(self.gx, self.gy)
            u_ref = math.sqrt(gm * self.beta * dT * L)
        if u_ref == 0.0:
            u_ref = max(abs(self.case.initial.velocity_x), abs(self.case.initial.velocity_y), 1e-3)
        self.u_ref = u_ref
        self.L_ref = L
        self.t_ref = L / u_ref

    # ------------------------------------------------------------------ state
    def init_fields(self):
        g = self.g
        nx, ny = g.nx, g.ny
        ini = self.case.initial
        self.u = np.where(self.activeU, ini.velocity_x, 0.0)
        self.v = np.where(self.activeV, ini.velocity_y, 0.0)
        self._apply_boundary_velocity(self.u, self.v)
        self.p = np.zeros((nx, ny))
        self.T = np.where(self.S, np.where(np.isnan(g.solid_T), ini.temperature, g.solid_T), ini.temperature)
        self.time = 0.0
        self.iteration = 0
        self.last_dt = 0.0
        self.wall_time = 0.0

    def _apply_boundary_velocity(self, u, v):
        u[~self.activeU & ~self.outU] = self.u_fix[~self.activeU & ~self.outU]
        v[~self.activeV & ~self.outV] = self.v_fix[~self.activeV & ~self.outV]
        # solid-adjacent faces are always zero
        u[self.solidU] = 0.0
        v[self.solidV] = 0.0
        # outlets: zero gradient copy
        nx, ny = self.g.nx, self.g.ny
        if self.outU[0].any():
            u[0] = np.where(self.outU[0], u[1], 0.0)
        if self.outU[nx].any():
            u[nx] = np.where(self.outU[nx], u[nx - 1], 0.0)
        if self.outV[:, 0].any():
            v[:, 0] = np.where(self.outV[:, 0], v[:, 1], 0.0)
        if self.outV[:, ny].any():
            v[:, ny] = np.where(self.outV[:, ny], v[:, ny - 1], 0.0)

    # ------------------------------------------------------------------ helpers
    def _u_neighbours(self, u):
        """Tangential (y) neighbours of u with wall/obstacle mirror ghosts."""
        up = np.empty_like(u)
        dn = np.empty_like(u)
        up[:, :-1] = u[:, 1:]
        dn[:, 1:] = u[:, :-1]
        a, b = self.gh["top"]
        up[:, -1] = a * u[:, -1] + b
        a, b = self.gh["bottom"]
        dn[:, 0] = a * u[:, 0] + b
        sU = self.solidU
        up[:, :-1] = np.where(sU[:, 1:], -u[:, :-1], up[:, :-1])
        dn[:, 1:] = np.where(sU[:, :-1], -u[:, 1:], dn[:, 1:])
        return up, dn

    def _v_neighbours(self, v):
        rt = np.empty_like(v)
        lf = np.empty_like(v)
        rt[:-1] = v[1:]
        lf[1:] = v[:-1]
        a, b = self.gh["right"]
        rt[-1] = a * v[-1] + b
        a, b = self.gh["left"]
        lf[0] = a * v[0] + b
        sV = self.solidV
        rt[:-1] = np.where(sV[1:], -v[:-1], rt[:-1])
        lf[1:] = np.where(sV[:-1], -v[1:], lf[1:])
        return rt, lf

    # ------------------------------------------------------------------ time step
    def stable_dt(self) -> float:
        g = self.g
        umax = max(float(np.abs(self.u).max()), 1e-12)
        vmax = max(float(np.abs(self.v).max()), 1e-12)
        nu = max(self.nu, self.alpha if self.energy else 0.0)
        dt_diff = 1.0 / (2.0 * nu * (1 / g.dx**2 + 1 / g.dy**2))
        dt = min(dt_diff, g.dx / umax, g.dy / vmax)
        if self.buoyancy:
            dT = float(np.abs(self.T[self.F] - self.T_ref).max())
            gm = math.hypot(self.gx, self.gy)
            a = gm * self.beta * dT
            if a > 0:
                dt = min(dt, math.sqrt(min(g.dx, g.dy) / a))
        return self.rs.cfl * dt

    def step(self, update_energy: bool = True) -> Residuals:
        t0 = time.perf_counter()
        g = self.g
        nx, ny, dx, dy = g.nx, g.ny, g.dx, g.dy
        dt = self.rs.fixed_dt or self.stable_dt()
        u, v, nu = self.u, self.v, self.nu
        umax = max(float(np.abs(u).max()), 1e-12)
        vmax = max(float(np.abs(v).max()), 1e-12)
        if self.rs.convection_blend is None:
            gam = min(1.2 * max(umax * dt / dx, vmax * dt / dy), 1.0)
        else:
            gam = self.rs.convection_blend

        u_up, u_dn = self._u_neighbours(u)
        v_rt, v_lf = self._v_neighbours(v)

        # ------------------------------ u momentum at faces i = 1..nx-1
        uc = 0.5 * (u[:-1] + u[1:])  # (nx, ny) cell centre
        ui = u[1:nx]
        du2dx = (uc[1:] ** 2 - uc[:-1] ** 2) / dx + gam / dx * (
            np.abs(uc[1:]) * (ui - u[2 : nx + 1]) / 2 - np.abs(uc[:-1]) * (u[0 : nx - 1] - ui) / 2
        )
        vt = 0.5 * (v[0 : nx - 1, 1 : ny + 1] + v[1:nx, 1 : ny + 1])
        vb = 0.5 * (v[0 : nx - 1, 0:ny] + v[1:nx, 0:ny])
        uu, ud = u_up[1:nx], u_dn[1:nx]
        duvdy = (vt * 0.5 * (ui + uu) - vb * 0.5 * (ud + ui)) / dy + gam / dy * (
            np.abs(vt) * (ui - uu) / 2 - np.abs(vb) * (ud - ui) / 2
        )
        lapu = (u[2 : nx + 1] - 2 * ui + u[0 : nx - 1]) / dx**2 + (uu - 2 * ui + ud) / dy**2
        rhs_u = nu * lapu - du2dx - duvdy
        if self.buoyancy and self.gx != 0.0:
            Tf = 0.5 * (self.T[0 : nx - 1] + self.T[1:nx])
            rhs_u = rhs_u - self.gx * self.beta * (Tf - self.T_ref)
        us = u.copy()
        us[1:nx] = np.where(self.activeU[1:nx], ui + dt * rhs_u, u[1:nx])

        # ------------------------------ v momentum at faces j = 1..ny-1
        vc = 0.5 * (v[:, :-1] + v[:, 1:])
        vi = v[:, 1:ny]
        dv2dy = (vc[:, 1:] ** 2 - vc[:, :-1] ** 2) / dy + gam / dy * (
            np.abs(vc[:, 1:]) * (vi - v[:, 2 : ny + 1]) / 2 - np.abs(vc[:, :-1]) * (v[:, 0 : ny - 1] - vi) / 2
        )
        ur = 0.5 * (u[1 : nx + 1, 0 : ny - 1] + u[1 : nx + 1, 1:ny])
        ul = 0.5 * (u[0:nx, 0 : ny - 1] + u[0:nx, 1:ny])
        vr, vl = v_rt[:, 1:ny], v_lf[:, 1:ny]
        duvdx = (ur * 0.5 * (vi + vr) - ul * 0.5 * (vl + vi)) / dx + gam / dx * (
            np.abs(ur) * (vi - vr) / 2 - np.abs(ul) * (vl - vi) / 2
        )
        lapv = (v[:, 2 : ny + 1] - 2 * vi + v[:, 0 : ny - 1]) / dy**2 + (vr - 2 * vi + vl) / dx**2
        rhs_v = nu * lapv - dv2dy - duvdx
        if self.buoyancy and self.gy != 0.0:
            Tf = 0.5 * (self.T[:, 0 : ny - 1] + self.T[:, 1:ny])
            rhs_v = rhs_v - self.gy * self.beta * (Tf - self.T_ref)
        vs = v.copy()
        vs[:, 1:ny] = np.where(self.activeV[:, 1:ny], vi + dt * rhs_v, v[:, 1:ny])

        # outlet faces: zero-gradient predictor
        if self.outU[0].any():
            us[0] = np.where(self.outU[0], us[1], 0.0)
        if self.outU[nx].any():
            us[nx] = np.where(self.outU[nx], us[nx - 1], 0.0)
        if self.outV[:, 0].any():
            vs[:, 0] = np.where(self.outV[:, 0], vs[:, 1], 0.0)
        if self.outV[:, ny].any():
            vs[:, ny] = np.where(self.outV[:, ny], vs[:, ny - 1], 0.0)

        # ------------------------------ pressure projection
        div = (us[1:] - us[:-1]) / dx + (vs[:, 1:] - vs[:, :-1]) / dy
        rhs = div[self.F] / dt
        if self.n_pinned:
            rhs[self.pin_idx] = 0.0
        pv = self.lu.solve(rhs)
        p = np.zeros((nx, ny))
        p[self.F] = pv
        un = us.copy()
        vn = vs.copy()
        un[1:nx] = np.where(self.activeU[1:nx], us[1:nx] - dt * (p[1:] - p[:-1]) / dx, us[1:nx])
        vn[:, 1:ny] = np.where(self.activeV[:, 1:ny], vs[:, 1:ny] - dt * (p[:, 1:] - p[:, :-1]) / dy, vs[:, 1:ny])
        # Dirichlet (p=0) outlet faces
        if self.outU[0].any():
            un[0] = np.where(self.outU[0], us[0] - dt * (p[0] - 0.0) / (dx / 2), 0.0)
        if self.outU[nx].any():
            un[nx] = np.where(self.outU[nx], us[nx] - dt * (0.0 - p[nx - 1]) / (dx / 2), 0.0)
        if self.outV[:, 0].any():
            vn[:, 0] = np.where(self.outV[:, 0], vs[:, 0] - dt * (p[:, 0] - 0.0) / (dy / 2), 0.0)
        if self.outV[:, ny].any():
            vn[:, ny] = np.where(self.outV[:, ny], vs[:, ny] - dt * (0.0 - p[:, ny - 1]) / (dy / 2), 0.0)

        res = Residuals()
        sc = self.t_ref / dt
        du = un - u
        dv = vn - v
        res.u = float(np.linalg.norm(du) / (np.linalg.norm(un) + 1e-30 * 0 + 1e-12 * self.u_ref) * sc)
        res.v = float(np.linalg.norm(dv) / (np.linalg.norm(vn) + 1e-12 * self.u_ref) * sc)
        res.p = float(np.linalg.norm(p - self.p) / (np.linalg.norm(p) + 1e-12 * self.u_ref**2) * sc)
        self.u, self.v, self.p = un, vn, p

        if self.energy and update_energy:
            res.T = self._energy_step(dt, gam)

        self.time += dt
        self.iteration += 1
        self.last_dt = dt
        if not (np.isfinite(res.u) and np.isfinite(res.v) and np.isfinite(self.u).all()):
            raise SolverDiverged(
                f"Solution diverged at iteration {self.iteration} (t={self.time:.4g}). "
                "Try a finer mesh, a smaller CFL number, or lower velocities."
            )
        self.wall_time += time.perf_counter() - t0
        return res

    # ------------------------------------------------------------------ energy
    def _energy_step(self, dt: float, gam: float) -> float:
        g = self.g
        nx, ny, dx, dy = g.nx, g.ny, g.dx, g.dy
        T, u, v, al = self.T, self.u, self.v, self.alpha
        Fx = np.zeros((nx + 1, ny))
        Fy = np.zeros((nx, ny + 1))
        TL, TR = T[:-1], T[1:]
        uf = u[1:nx]
        Tf = 0.5 * (TL + TR) - 0.5 * gam * np.sign(uf) * (TR - TL)
        Fx[1:nx] = np.where(self.ff_x, uf * Tf - al * (TR - TL) / dx, 0.0)
        Fx[1:nx] += np.where(self.fs_x, -al * (self.fs_x_T - TL) / (dx / 2), 0.0)
        Fx[1:nx] += np.where(self.sf_x, -al * (TR - self.sf_x_T) / (dx / 2), 0.0)
        TB, TT = T[:, :-1], T[:, 1:]
        vf = v[:, 1:ny]
        Tf = 0.5 * (TB + TT) - 0.5 * gam * np.sign(vf) * (TT - TB)
        Fy[:, 1:ny] = np.where(self.ff_y, vf * Tf - al * (TT - TB) / dy, 0.0)
        Fy[:, 1:ny] += np.where(self.fs_y, -al * (self.fs_y_T - TB) / (dy / 2), 0.0)
        Fy[:, 1:ny] += np.where(self.sf_y, -al * (TT - self.sf_y_T) / (dy / 2), 0.0)

        F = self.F
        # domain boundaries -------------------------------------------------
        def side_flux(side, Tc, un, h, sign):
            """Flux in the +axis direction through a boundary face. sign=-1 left/bottom, +1 right/top."""
            bc = self.bc[side]
            if bc.type == "inlet":
                Tb = bc.temperature
                return un * np.where(un * (-sign) >= 0, Tb, Tc) - al * sign * (Tb - Tc) / (h / 2)
            if bc.type == "outlet":
                return un * Tc
            if bc.type == "wall" and bc.thermal == "fixed":
                return -al * sign * (bc.temperature - Tc) / (h / 2)
            return np.zeros_like(Tc)

        Fx[0] = np.where(F[0], side_flux("left", T[0], u[0], dx, -1), 0.0)
        Fx[nx] = np.where(F[nx - 1], side_flux("right", T[nx - 1], u[nx], dx, +1), 0.0)
        Fy[:, 0] = np.where(F[:, 0], side_flux("bottom", T[:, 0], v[:, 0], dy, -1), 0.0)
        Fy[:, ny] = np.where(F[:, ny - 1], side_flux("top", T[:, ny - 1], v[:, ny], dy, +1), 0.0)

        dTdt = -(Fx[1:] - Fx[:-1]) / dx - (Fy[:, 1:] - Fy[:, :-1]) / dy + self.q_src
        Tn = np.where(F, T + dt * dTdt, T)
        r = float(
            np.linalg.norm((Tn - T)[F])
            / (np.linalg.norm((Tn - self.T_ref)[F]) + 1e-3 * self.dT_ref * math.sqrt(F.sum()))
            * self.t_ref
            / dt
        )
        self.T = Tn
        return r


    # ------------------------------------------------------------------ steady energy (direct)
    def solve_energy_steady(self) -> bool:
        """Direct steady solve of the energy equation on the (frozen) velocity field.

        Hybrid differencing (central when the face Peclet number < 2, blending to upwind above).
        Returns False if the system has no Dirichlet data / is singular (caller falls back to
        pseudo-time stepping).
        """
        g, F, al = self.g, self.F, self.alpha
        nx, ny, dx, dy = g.nx, g.ny, g.dx, g.dy
        idx = self.idx
        n = int(F.sum())
        rows, cols, vals = [], [], []
        rhs = self.q_src[F].astype(float).copy()
        has_dir = False

        def add(Li, Ri, aL, aR, c, h):
            """Flux F = aL*T_L + aR*T_R + c through a face; Li/Ri are cell index arrays (or None)."""
            nonlocal rhs
            if Li is not None:
                rows.extend([Li, Li]); cols.extend([Li, Ri if Ri is not None else Li])
                vals.extend([aL / h, (aR / h) if Ri is not None else 0 * aL])
                np.add.at(rhs, Li, -c / h)
            if Ri is not None:
                rows.extend([Ri, Ri]); cols.extend([Ri, Li if Li is not None else Ri])
                vals.extend([-aR / h, (-aL / h) if Li is not None else 0 * aR])
                np.add.at(rhs, Ri, c / h)

        # interior fluid-fluid faces ------------------------------------------------
        for axis in (0, 1):
            if axis == 0:
                ff, vel, h = self.ff_x, self.u[1:nx], dx
                Li, Ri = idx[:-1][ff], idx[1:][ff]
            else:
                ff, vel, h = self.ff_y, self.v[:, 1:ny], dy
                Li, Ri = idx[:, :-1][ff], idx[:, 1:][ff]
            u = vel[ff]
            pe = np.abs(u) * h / al
            gam = np.clip(1.0 - 2.0 / np.maximum(pe, 1e-30), 0.0, 1.0)
            s = np.sign(u)
            aL = u * (0.5 + 0.5 * gam * s) + al / h
            aR = u * (0.5 - 0.5 * gam * s) - al / h
            add(Li, Ri, aL, aR, np.zeros_like(aL), h)
        # fluid/solid interfaces ----------------------------------------------------
        for axis in (0, 1):
            if axis == 0:
                fs, fsT, sf, sfT, h = self.fs_x, self.fs_x_T, self.sf_x, self.sf_x_T, dx
                fs_cell, sf_cell = idx[:-1][fs], idx[1:][sf]
            else:
                fs, fsT, sf, sfT, h = self.fs_y, self.fs_y_T, self.sf_y, self.sf_y_T, dy
                fs_cell, sf_cell = idx[:, :-1][fs], idx[:, 1:][sf]
            if fs_cell.size:
                has_dir = True
                k = 2 * al / h
                add(fs_cell, None, np.full(fs_cell.shape, k), 0 * fs_cell, -k * fsT[fs], h)
            if sf_cell.size:
                has_dir = True
                k = 2 * al / h
                add(None, sf_cell, 0 * sf_cell, np.full(sf_cell.shape, -k), k * sfT[sf], h)
        # domain boundaries ---------------------------------------------------------
        def boundary(side, cells_idx, un, h, sign):
            nonlocal has_dir
            bc = self.bc[side]
            ok = cells_idx >= 0
            ci = cells_idx[ok]
            un = un[ok]
            if ci.size == 0:
                return
            k = 2 * al * sign / h
            if bc.type == "inlet":
                has_dir = True
                Tb = bc.temperature
                inflow = un * (-sign) >= 0
                c0 = np.where(inflow, un * Tb - k * Tb, -k * Tb)
                c1 = np.where(inflow, k, un + k)
            elif bc.type == "outlet":
                c0 = np.zeros_like(un)
                c1 = un
            elif bc.type == "wall" and bc.thermal == "fixed":
                has_dir = True
                c0 = np.full(un.shape, -k * bc.temperature)
                c1 = np.full(un.shape, k)
            else:
                return
            if sign < 0:  # cell is on the + side of the face (R)
                add(None, ci, 0 * c1, c1, c0, h)
            else:
                add(ci, None, c1, 0 * c1, c0, h)

        boundary("left", idx[0], self.u[0], dx, -1)
        boundary("right", idx[nx - 1], self.u[nx], dx, +1)
        boundary("bottom", idx[:, 0], self.v[:, 0], dy, -1)
        boundary("top", idx[:, ny - 1], self.v[:, ny], dy, +1)
        if not has_dir:
            return False
        A = sp.coo_matrix(
            (np.concatenate([np.asarray(v, float).ravel() for v in vals]),
             (np.concatenate([np.asarray(r).ravel() for r in rows]), np.concatenate([np.asarray(c).ravel() for c in cols]))),
            shape=(n, n),
        ).tocsc()
        try:
            sol = splu(A).solve(rhs)
        except RuntimeError:
            return False
        if not np.all(np.isfinite(sol)):
            return False
        self.T = np.where(F, 0.0, self.T)
        self.T[F] = sol
        return True

    # ------------------------------------------------------------------ outputs
    def cell_fields(self, u=None, v=None, p=None, T=None) -> dict[str, np.ndarray]:
        """Cell-centred fields, shape (nx, ny); solid cells are 0 (p, velocity) / NaN-free."""
        u = self.u if u is None else u
        v = self.v if v is None else v
        p = self.p if p is None else p
        T = self.T if T is None else T
        S = self.S
        uc = 0.5 * (u[:-1] + u[1:])
        vc = 0.5 * (v[:, :-1] + v[:, 1:])
        uc = np.where(S, 0.0, uc)
        vc = np.where(S, 0.0, vc)
        speed = np.hypot(uc, vc)
        vort = np.gradient(vc, self.g.dx, axis=0) - np.gradient(uc, self.g.dy, axis=1)
        vort = np.where(S, 0.0, vort)
        return {
            "u": uc,
            "v": vc,
            "speed": speed,
            "p": np.where(S, 0.0, p * self.rho),
            "T": T.copy(),
            "vorticity": vort,
        }

    def mass_imbalance(self) -> float:
        g = self.g
        inflow = (
            np.clip(self.u[0], 0, None).sum() * g.dy
            + np.clip(-self.u[g.nx], 0, None).sum() * g.dy
            + np.clip(self.v[:, 0], 0, None).sum() * g.dx
            + np.clip(-self.v[:, g.ny], 0, None).sum() * g.dx
        )
        outflow = (
            np.clip(-self.u[0], 0, None).sum() * g.dy
            + np.clip(self.u[g.nx], 0, None).sum() * g.dy
            + np.clip(-self.v[:, 0], 0, None).sum() * g.dx
            + np.clip(self.v[:, g.ny], 0, None).sum() * g.dx
        )
        if inflow < 1e-14:
            return 0.0
        return float((inflow - outflow) / inflow)

    def obstacle_loads(self) -> dict[str, dict]:
        """Force (pressure + viscous, N per metre depth) and heat transfer (W per metre) per obstacle."""
        g, S, F = self.g, self.S, self.F
        nx, ny, dx, dy = g.nx, g.ny, g.dx, g.dy
        f = self.cell_fields()
        pk = self.p * self.rho
        uc, vc = f["u"], f["v"]
        out = {}
        for k, ob in enumerate(self.case.obstacles):
            mine = g.solid_id == k
            fx = fy = q = 0.0
            Tfix = g.solid_T
            # east neighbour solid (fluid cell i, solid i+1): pressure pushes solid in +x
            for sl_f, sl_s, axis, sgn in (
                ((slice(0, nx - 1), slice(None)), (slice(1, nx), slice(None)), 0, +1),
                ((slice(1, nx), slice(None)), (slice(0, nx - 1), slice(None)), 0, -1),
                ((slice(None), slice(0, ny - 1)), (slice(None), slice(1, ny)), 1, +1),
                ((slice(None), slice(1, ny)), (slice(None), slice(0, ny - 1)), 1, -1),
            ):
                sel = F[sl_f] & mine[sl_s]
                if not sel.any():
                    continue
                pf = pk[sl_f][sel]
                if axis == 0:
                    fx += sgn * float(pf.sum()) * dy
                    fy += float(self.mu * vc[sl_f][sel].sum() / (dx / 2) * dy)
                    h, area = dx, dy
                else:
                    fy += sgn * float(pf.sum()) * dx
                    fx += float(self.mu * uc[sl_f][sel].sum() / (dy / 2) * dx)
                    h, area = dy, dx
                if self.energy:
                    Ts = Tfix[sl_s][sel]
                    Tc = self.T[sl_f][sel]
                    ok = ~np.isnan(Ts)
                    q += float(self.rho * self.cp * self.alpha * ((Ts - Tc)[ok]).sum() / (h / 2) * area)
            out[ob.id] = {"Fx": fx, "Fy": fy, "Q": q}
        return out

    def sample(self, field: np.ndarray, x: float, y: float) -> float:
        g = self.g
        fx = np.clip((x - g.xc[0]) / g.dx, 0, g.nx - 1)
        fy = np.clip((y - g.yc[0]) / g.dy, 0, g.ny - 1)
        i0, j0 = int(math.floor(fx)), int(math.floor(fy))
        i1, j1 = min(i0 + 1, g.nx - 1), min(j0 + 1, g.ny - 1)
        a, b = fx - i0, fy - j0
        return float(
            field[i0, j0] * (1 - a) * (1 - b)
            + field[i1, j0] * a * (1 - b)
            + field[i0, j1] * (1 - a) * b
            + field[i1, j1] * a * b
        )

    def monitors(self) -> dict[str, float]:
        f = self.cell_fields()
        vals: dict[str, float] = {
            "dt": self.last_dt,
            "max_speed": float(f["speed"].max()),
            "mass_imbalance": self.mass_imbalance(),
        }
        for pr in self.case.probes:
            vals[f"{pr.id}.u"] = self.sample(f["u"], pr.x, pr.y)
            vals[f"{pr.id}.v"] = self.sample(f["v"], pr.x, pr.y)
            vals[f"{pr.id}.p"] = self.sample(f["p"], pr.x, pr.y)
            if self.energy:
                vals[f"{pr.id}.T"] = self.sample(f["T"], pr.x, pr.y)
        for oid, d in self.obstacle_loads().items():
            vals[f"{oid}.Fx"] = d["Fx"]
            vals[f"{oid}.Fy"] = d["Fy"]
            if self.energy:
                vals[f"{oid}.Q"] = d["Q"]
        return vals

    def boundary_pressures(self) -> tuple[list[float], list[float]]:
        g, f, fluid = self.g, self.cell_fields(), self.F
        inlet_p, outlet_p = [], []
        for side, sl in (("left", (0, slice(None))), ("right", (g.nx - 1, slice(None))),
                         ("bottom", (slice(None), 0)), ("top", (slice(None), g.ny - 1))):
            cells = fluid[sl]
            if not cells.any():
                continue
            vals = f["p"][sl][cells]
            if self.bc[side].type == "inlet":
                inlet_p.append(float(vals.mean()))
            elif self.bc[side].type == "outlet":
                outlet_p.append(float(vals.mean()))
        return inlet_p, outlet_p

    def result_meta(self, mode: str) -> dict:
        g = self.g
        return {"nx": g.nx, "ny": g.ny, "lx": g.lx, "ly": g.ly, "energy": self.energy, "rho": self.rho,
                "iteration": self.iteration, "mode": mode}

    def describe_mesh(self) -> str:
        g = self.g
        return f"Mesh: {g.nx} x {g.ny} cells ({self.F.sum()} fluid cells), dx={g.dx:.4g} m, dy={g.dy:.4g} m"

    def state_arrays(self) -> dict[str, np.ndarray]:
        return {
            "u": self.u.astype(np.float32),
            "v": self.v.astype(np.float32),
            "p": (self.p * self.rho).astype(np.float32),
            "T": self.T.astype(np.float32),
        }
