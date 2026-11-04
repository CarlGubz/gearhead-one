"""
Analysis drivers: linear static, nonlinear (J2 plastic) static, frequency, linear buckling,
steady and transient heat transfer, and implicit transient dynamics (Newmark-beta).

Everything works in the *reduced* DOF space produced by ConstraintSystem; boundary conditions
are eliminated by partitioning, so prescribed values are satisfied exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
from scipy import linalg as dense_linalg
from scipy import sparse
from scipy.sparse import linalg as spla

from . import assembly as asm
from .constraints import ConstraintSystem
from .finite import FiniteStrainError, finite_assemble
from .materials import Material, MaterialMap, as_map, principal
from .linsolve import Geometry, LinearSolveError, SolverOptions, make_solver
from .model import FEMesh

Progress = Optional[Callable[[float, str], None]]
SIGMA_SB = 5.670374419e-8  # Stefan-Boltzmann, W/(m^2 K^4)


class SolverError(ValueError):
    """User-facing solver failure (bad model, singular system, no convergence)."""


# ------------------------------------------------------------- linear algebra
def _factor(A, opts: Optional[SolverOptions], geo: Optional[Geometry]):
    """Prepare a solver for the SPD matrix A (direct LU, or two-level Schwarz PCG for big systems)."""
    try:
        return make_solver(A, opts, geo)
    except LinearSolveError as exc:
        raise SolverError(
            "The stiffness matrix is singular: the model is not fully constrained (rigid-body motion is "
            "possible). Add supports that stop all translations and rotations.") from exc


def _solve_with(solver, b: np.ndarray) -> np.ndarray:
    try:
        return solver.solve(b)
    except LinearSolveError as exc:
        raise SolverError(f"The linear solver failed: {exc}. Try a coarser mesh, quadratic elements or the direct solver.") from exc


def mech_geometry(cs: ConstraintSystem, mesh: FEMesh) -> Geometry:
    """Position / component of every REDUCED mechanical unknown (for the iterative preconditioner)."""
    kept = np.where(~cs.slave)[0]
    n3 = 3 * mesh.n_nodes
    xyz = np.zeros((len(kept), 3))
    comp = np.zeros(len(kept), dtype=int)
    node = np.zeros(len(kept), dtype=np.int64)
    is_node = kept < n3
    xyz[is_node] = mesh.nodes[kept[is_node] // 3]
    comp[is_node] = kept[is_node] % 3
    node[is_node] = kept[is_node] // 3
    for k in np.where(~is_node)[0]:  # reference-point DOFs
        slot = (kept[k] - n3) // 6
        xyz[k] = cs.refs[cs.ref_order[slot]].coords
        comp[k] = (kept[k] - n3) % 3
        node[k] = mesh.n_nodes + slot
    return Geometry(xyz, comp, node)


def _partition(n: int, fixed: Dict[int, float]):
    fixed_idx = np.array(sorted(fixed), dtype=np.int64)
    up = np.array([fixed[i] for i in fixed_idx], dtype=float)
    free = np.setdiff1d(np.arange(n), fixed_idx)
    return free, fixed_idx, up


def solve_partitioned(K: sparse.spmatrix, f: np.ndarray, fixed: Dict[int, float],
                      opts: Optional[SolverOptions] = None, geo: Optional[Geometry] = None) -> np.ndarray:
    """Solve K u = f with u[fixed] prescribed. Detects (near-)singular systems."""
    n = K.shape[0]
    free, fidx, up = _partition(n, fixed)
    u = np.zeros(n)
    u[fidx] = up
    if len(free) == 0:
        return u
    K = K.tocsr()
    Kff = K[free][:, free].tocsc()
    rhs = f[free] - (K[free][:, fidx] @ up if len(fidx) else 0.0)
    uf = _solve_with(_factor(Kff, opts, None if geo is None else geo.subset(free)), rhs)
    scale = max(np.linalg.norm(rhs), 1e-300)
    resid = np.linalg.norm(Kff @ uf - rhs) / scale
    if not np.all(np.isfinite(uf)) or resid > 1e-6:
        raise SolverError(
            "The system could not be solved accurately - the model is probably under-constrained "
            "(rigid-body motion). Check the supports.")
    u[free] = uf
    return u


def _report(progress: Progress, frac: float, msg: str) -> None:
    if progress:
        progress(frac, msg)


# ------------------------------------------------------------------- amplitudes
def amplitude(spec: Optional[dict]) -> Callable[[float], float]:
    spec = spec or {}
    kind = spec.get("type", "step")
    if kind == "ramp":
        tr = max(float(spec.get("duration", 1.0)), 1e-12)
        return lambda t: min(max(t / tr, 0.0), 1.0)
    if kind == "sine":
        f = float(spec.get("frequency", 1.0))
        return lambda t: float(np.sin(2 * np.pi * f * t))
    if kind == "pulse":
        tp = float(spec.get("duration", 1.0))
        return lambda t: 1.0 if t < tp else 0.0
    return lambda t: 1.0


# ---------------------------------------------------------------- mech problem
@dataclass
class MechProblem:
    mesh: FEMesh
    mat: object  # Material or MaterialMap
    cs: ConstraintSystem
    fixed: Dict[int, float]          # full DOF -> prescribed value
    f_ext: np.ndarray                # full DOF vector
    T: Optional[np.ndarray] = None   # nodal temperature field (thermal strain), or None
    T_ref: float = 0.0
    solver: SolverOptions = field(default_factory=SolverOptions)
    contact: Optional[object] = None  # ContactSystem (frictionless contact between parts)
    nlgeom: bool = False              # large-deformation (total Lagrangian) kinematics
    finite_model: str = "svk"          # 'svk' | 'neohookean' (only used with nlgeom)


@dataclass
class StaticOut:
    u: np.ndarray                    # (3N,)
    reactions: np.ndarray            # (3N,) at fixed DOFs, else 0
    stress: np.ndarray               # (ne,ng,6)
    strain: np.ndarray               # (ne,ng,6)
    energy: float
    alpha_p: Optional[np.ndarray] = None
    ref_motion: Dict[str, np.ndarray] = field(default_factory=dict)  # ref id -> [ux,uy,uz,rx,ry,rz]
    log: str = ""
    contact: Optional[dict] = None   # pressure, gap, forces and counts of the final contact state


def _ref_motion(cs: ConstraintSystem, u_full: np.ndarray) -> Dict[str, np.ndarray]:
    return {rid: u_full[cs.ref_dof(rid, 0): cs.ref_dof(rid, 0) + 6].copy() for rid in cs.ref_order}


def _reactions(prob: MechProblem, fint: np.ndarray, fc: Optional[np.ndarray] = None) -> np.ndarray:
    R = np.zeros(3 * prob.mesh.n_nodes)
    for d in prob.fixed:
        if d < len(R):
            R[d] = fint[d] - prob.f_ext[d] - (0.0 if fc is None else fc[d])
    return R


def static(prob: MechProblem, progress: Progress = None) -> StaticOut:
    mesh, mat, cs = prob.mesh, prob.mat, prob.cs
    n3 = 3 * mesh.n_nodes
    zero = np.zeros(n3)
    _report(progress, 0.2, "Assembling stiffness matrix")
    out0 = asm.mech_assemble(mesh, mat, zero, None, prob.T, prob.T_ref, want_K=True)
    f = prob.f_ext.copy()
    f[:n3] -= out0.fint  # thermal pre-strain load (zero if no temperature field)
    K_red = cs.reduce_matrix(out0.K)
    f_red = cs.reduce_vector(f)
    _report(progress, 0.5, f"Solving {K_red.shape[0]} equations")
    u_red = solve_partitioned(K_red, f_red, cs.reduced_fixed(prob.fixed), prob.solver, mech_geometry(cs, mesh))
    u_full = cs.expand(u_red)
    u = u_full[:n3]
    out = asm.mech_assemble(mesh, mat, u, None, prob.T, prob.T_ref, want_K=False)
    return StaticOut(u, _reactions(prob, out.fint), out.stress, out.strain, out.energy,
                     ref_motion=_ref_motion(cs, u_full), log="Linear static solution\n" + prob.solver.summary())


def nonlinear_static(prob: MechProblem, increments: int = 10, max_iter: int = 30, tol: float = 1e-6,
                     progress: Progress = None) -> StaticOut:
    """
    Incremental-iterative Newton-Raphson: J2 plasticity (small strain), large deformation (total Lagrangian,
    `prob.nlgeom`) and/or frictionless contact between parts.
    With contact the iteration only counts as converged when the set of nodes in contact stops changing.
    """
    mesh, mat, cs = prob.mesh, prob.mat, prob.cs
    n3 = 3 * mesh.n_nodes
    f_red_full = cs.reduce_vector(prob.f_ext)
    fixed_red = cs.reduced_fixed(prob.fixed)
    free, fidx, up = _partition(cs.n_red, fixed_red)
    geo_free = mech_geometry(cs, mesh).subset(free)
    state = asm.PlasticState.zeros(mesh)
    u_red = np.zeros(cs.n_red)
    lam, dl, total_iters = 0.0, 1.0 / max(1, increments), 0
    log: List[str] = []
    out = None
    contact = prob.contact
    size = float(np.ptp(mesh.nodes, axis=0).max()) or 1.0
    prev_active = np.array([], dtype=np.int64)
    cres = None
    nlgeom = prob.nlgeom
    if nlgeom and as_map(mat, mesh).plastic:
        raise SolverError("Large-deformation analysis does not support plasticity yet. Remove the yield stress or switch NLGEOM off.")
    if nlgeom and prob.T is not None:
        raise SolverError("Large-deformation analysis does not support thermal strain yet. Remove the temperature change or switch NLGEOM off.")

    def assemble(u_nodes, st, T_eff, want_K):
        if nlgeom:
            o = finite_assemble(mesh, mat, u_nodes, prob.finite_model, want_K)
            o.state = st
            return o
        return asm.mech_assemble(mesh, mat, u_nodes, st, T_eff, prob.T_ref, want_K=want_K)

    while lam < 1.0 - 1e-12:
        step = min(dl, 1.0 - lam)
        target = lam + step
        trial = u_red.copy()
        trial[fidx] = up * target
        T_eff = None if prob.T is None else prob.T_ref + target * (prob.T - prob.T_ref)
        converged = False
        active_before = prev_active
        for it in range(max_iter):
            u_nodes = cs.expand(trial)[:n3]
            try:
                out = assemble(u_nodes, state, T_eff, True)
            except FiniteStrainError as exc:
                if step <= 1e-4 * 2:
                    raise SolverError(str(exc)) from exc
                break                                  # trial iterate inverted an element: cut the increment back
            fint_red = cs.reduce_vector(out.fint)
            r = target * f_red_full - fint_red
            norms = [np.linalg.norm(target * f_red_full), np.linalg.norm(fint_red), 1e-30]
            K_total = out.K
            if contact is not None:
                cres = contact.evaluate(u_nodes)
                fc_red = cs.reduce_vector(cres.force)
                r = r + fc_red
                norms.append(np.linalg.norm(fc_red))
                K_total = out.K + cres.K
            ref = max(norms)
            if contact is not None:   # absolute floor so a force-free rigid motion is not held to round-off accuracy
                ref = max(ref, 1e-10 * float(abs(out.K.diagonal()).max()) * size)
            settled = contact is None or np.array_equal(cres.active, active_before)
            if np.linalg.norm(r[free]) <= tol * ref and settled:
                converged = True
                break
            if contact is not None:
                active_before = cres.active
            K_red = cs.reduce_matrix(K_total)
            Kff = K_red[free][:, free].tocsc()
            if contact is not None:  # frictionless bodies have tangential rigid-body freedom: keep the solve regular
                Kff = Kff + sparse.diags(np.full(Kff.shape[0], 1e-9 * float(Kff.diagonal().mean())))
            try:
                du = _solve_with(_factor(Kff, prob.solver, geo_free), r[free])
            except SolverError as exc:
                raise SolverError("Tangent stiffness became singular or the solve failed (model unstable, fully plastic "
                                  "or a body is not held in place): " + str(exc)) from exc
            trial[free] += du
        total_iters += it + 1
        if not converged and out is None:
            raise SolverError("The first iteration of the analysis failed (inverted element).")
        if converged:
            state = out.state
            u_red = trial
            lam = target
            prev_active = cres.active if contact is not None else prev_active
            log.append(f"increment to {lam:.3f}: {it} iterations"
                       + (f", {len(cres.active)} contact nodes" if contact is not None else ""))
            _report(progress, 0.15 + 0.8 * lam, f"Load factor {lam:.2f}")
            dl = min(dl * 1.5, 1.0)
        else:
            dl = step / 2.0
            if dl < 1e-4:
                raise SolverError(
                    f"The nonlinear solution did not converge near load factor {lam:.3f}"
                    + (" (contact chattering or an unsupported body?). Add supports that hold every body, "
                       "lower the penalty factor, or use more increments." if contact is not None
                       else " (buckling or an element inverted?). Reduce the load or use more increments." if nlgeom
                       else " (plastic collapse?). Reduce the load or increase the hardening modulus."))
    u_full = cs.expand(u_red)
    u = u_full[:n3]
    final = assemble(u, state, prob.T, False)
    fc_final = None
    contact_out = None
    if contact is not None:
        cres = contact.evaluate(u)
        fc_final = cres.force
        contact_out = {
            "pressure": cres.pressure, "gap": cres.gap, "active": int(len(cres.active)),
            "max_penetration": cres.max_penetration, "total_force": cres.total_force, "force": cres.force,
        }
    plastic = as_map(mat, mesh).plastic
    head = "Nonlinear static" + (f" (large deformation, {prob.finite_model})" if nlgeom else "")
    text = f"{head}: {total_iters} Newton iterations\n" + "\n".join(log[-12:])
    if contact_out is not None:
        text += (f"\nContact: {contact_out['active']} node(s) in contact, max penetration {contact_out['max_penetration']:.3g} m, "
                 f"total normal force {contact_out['total_force']:.6g} N")
    return StaticOut(u, _reactions(prob, final.fint, fc_final), final.stress, final.strain, final.energy,
                     alpha_p=state.alpha if plastic else None, ref_motion=_ref_motion(cs, u_full),
                     log=text + "\n" + prob.solver.summary(), contact=contact_out)


# ------------------------------------------------------------------ eigen solves
@dataclass
class ModalOut:
    values: np.ndarray               # frequencies (Hz) or buckling load factors
    shapes: List[np.ndarray]         # (N,3) each, normalised to unit max component
    log: str = ""


def _normalised_shapes(cs: ConstraintSystem, free: np.ndarray, vecs: np.ndarray, n_red: int, n_nodes: int):
    shapes = []
    for m in range(vecs.shape[1]):
        u_red = np.zeros(n_red)
        u_red[free] = vecs[:, m]
        u = cs.expand(u_red)[: 3 * n_nodes].reshape(n_nodes, 3)
        mx = np.max(np.abs(u))
        shapes.append(u / mx if mx > 1e-300 else u)
    return shapes


def frequency(prob: MechProblem, n_modes: int = 6, progress: Progress = None) -> ModalOut:
    mesh, mat, cs = prob.mesh, prob.mat, prob.cs
    if as_map(mat, mesh).prop("density").min() <= 0:
        raise SolverError("A frequency analysis needs a density greater than zero in every material.")
    _report(progress, 0.2, "Assembling K and M")
    out0 = asm.mech_assemble(mesh, mat, np.zeros(3 * mesh.n_nodes), None, None, 0.0, want_K=True)
    K = cs.reduce_matrix(out0.K)
    M = cs.reduce_matrix(asm.assemble_mass(mesh, as_map(mat, mesh).prop("density")))
    fixed_red = cs.reduced_fixed({d: 0.0 for d in prob.fixed})
    free, _, _ = _partition(cs.n_red, fixed_red)
    if len(free) < 2:
        raise SolverError("There are no free degrees of freedom left to vibrate.")
    Kff = K[free][:, free].tocsc()
    Mff = M[free][:, free].tocsc()
    n_modes = max(1, min(int(n_modes), len(free) - 2))
    free_free = len(fixed_red) == 0
    sigma = -1e-3 * float(Kff.diagonal().mean() / max(Mff.diagonal().mean(), 1e-300)) if free_free else 0.0
    _report(progress, 0.5, "Solving eigenproblem")
    try:
        vals, vecs = spla.eigsh(Kff, k=n_modes, M=Mff, sigma=sigma, which="LM")
    except (RuntimeError, spla.ArpackError, ValueError) as exc:
        raise SolverError(
            "Could not solve the eigenproblem - the supports probably do not remove all rigid-body motion. "
            "Fix a full face (several non-collinear nodes), not a point.") from exc
    order = np.argsort(vals)
    vals = np.clip(vals[order], 0.0, None)
    shapes = _normalised_shapes(cs, free, vecs[:, order], cs.n_red, mesh.n_nodes)
    return ModalOut(np.sqrt(vals) / (2 * np.pi), shapes, "Free-free modal analysis (6 rigid-body modes at ~0 Hz)" if free_free else "Modal analysis")


def buckling(prob: MechProblem, n_modes: int = 4, progress: Progress = None) -> ModalOut:
    """Linear (eigenvalue) buckling: (K + lambda K_sigma) phi = 0 for the reference load in prob.f_ext."""
    mesh, mat, cs = prob.mesh, prob.mat, prob.cs
    st = static(MechProblem(mesh, mat, cs, prob.fixed, prob.f_ext), progress=None)
    _report(progress, 0.5, "Assembling geometric stiffness")
    out0 = asm.mech_assemble(mesh, mat, np.zeros(3 * mesh.n_nodes), None, None, 0.0, want_K=True)
    K = cs.reduce_matrix(out0.K)
    Ks_full = asm.assemble_geometric(mesh, st.stress)
    # Rayleigh quotient of the static response: >= 0 means the load stiffens the structure (tension),
    # so there is no positive buckling factor. Checked up front because ARPACK would otherwise stall.
    if float(st.u @ (Ks_full @ st.u)) >= 0.0:
        raise SolverError("No buckling mode exists: the reference load stiffens the model (tension). "
                          "Reverse the load so it compresses the part, e.g. a pressure pushing INTO the end face.")
    Ks = cs.reduce_matrix(Ks_full)
    fixed_red = cs.reduced_fixed({d: 0.0 for d in prob.fixed})
    free, _, _ = _partition(cs.n_red, fixed_red)
    Kff, Ksf = K[free][:, free].tocsc(), Ks[free][:, free].tocsc()
    n_modes = max(1, min(int(n_modes), len(free) - 2))
    _report(progress, 0.7, "Solving buckling eigenproblem")
    # K phi = lambda (-Ks) phi  <=>  (-Ks) phi = mu K phi with mu = 1/lambda: the LARGEST mu are the lowest loads.
    if len(free) <= 1500:
        w, v = dense_linalg.eigh((-Ksf).toarray(), Kff.toarray())
    else:
        try:
            w, v = spla.eigsh(-Ksf, k=n_modes, M=Kff, which="LA", tol=1e-6, maxiter=60, ncv=min(len(free) - 1, max(2 * n_modes + 1, 24)))
        except spla.ArpackNoConvergence as exc:
            if len(exc.eigenvalues) == 0:
                raise SolverError("The buckling eigensolver did not converge. Try a coarser mesh or fewer modes.") from exc
            w, v = exc.eigenvalues, exc.eigenvectors
    pos = np.where(w > 1e-14)[0]
    if len(pos) == 0:
        raise SolverError("No positive buckling load factor was found for this load direction.")
    lam = 1.0 / w[pos]
    order = np.argsort(lam)[:n_modes]
    shapes = _normalised_shapes(cs, free, v[:, pos][:, order], cs.n_red, mesh.n_nodes)
    return ModalOut(lam[order], shapes, "Linear buckling: eigenvalues are load factors applied to the reference load")


# ----------------------------------------------------------------------- dynamics
@dataclass
class TransientOut:
    times: np.ndarray
    frames: List[np.ndarray]          # displacement (N,3) or temperature (N,) per stored frame
    history: Dict[str, np.ndarray]    # name -> array over `times` (all steps, not just frames)
    final: np.ndarray
    log: str = ""


def dynamic(prob: MechProblem, t_end: float, n_steps: int, amp: Callable[[float], float],
            rayleigh_alpha: float = 0.0, rayleigh_beta: float = 0.0, history_nodes: List[int] = (),
            max_frames: int = 50, progress: Progress = None) -> TransientOut:
    """Implicit Newmark (average acceleration, unconditionally stable) with Rayleigh damping."""
    mesh, mat, cs = prob.mesh, prob.mat, prob.cs
    if as_map(mat, mesh).prop("density").min() <= 0:
        raise SolverError("A dynamic analysis needs a density greater than zero in every material.")
    n3 = 3 * mesh.n_nodes
    out0 = asm.mech_assemble(mesh, mat, np.zeros(n3), None, None, 0.0, want_K=True)
    K = cs.reduce_matrix(out0.K)
    M = cs.reduce_matrix(asm.assemble_mass(mesh, as_map(mat, mesh).prop("density")))
    C = rayleigh_alpha * M + rayleigh_beta * K
    f_red = cs.reduce_vector(prob.f_ext)
    fixed_red = cs.reduced_fixed({d: 0.0 for d in prob.fixed})
    free, fidx, _ = _partition(cs.n_red, fixed_red)
    if len(fidx) == 0:
        raise SolverError("Add at least one support before a dynamic analysis (otherwise the model just flies away).")
    dt = t_end / n_steps
    beta, gamma = 0.25, 0.5
    a0, a1 = 1.0 / (beta * dt ** 2), gamma / (beta * dt)
    a2, a3 = 1.0 / (beta * dt), 1.0 / (2 * beta) - 1.0
    a4, a5 = gamma / beta - 1.0, dt / 2.0 * (gamma / beta - 2.0)
    Kf, Mf, Cf = K[free][:, free].tocsc(), M[free][:, free].tocsc(), C[free][:, free].tocsc()
    geo_free = mech_geometry(cs, mesh).subset(free)
    lu = _factor((Kf + a0 * Mf + a1 * Cf).tocsc(), prob.solver, geo_free)
    mlu = _factor(Mf, prob.solver, geo_free)
    ff = f_red[free]
    u = np.zeros(len(free))
    v = np.zeros(len(free))
    a = _solve_with(mlu, amp(0.0) * ff)
    hist_t, hist_max = [0.0], [0.0]
    hist_nodes = {int(n): [np.zeros(3)] for n in history_nodes}
    stride = max(1, n_steps // max_frames)
    frames, times = [np.zeros((mesh.n_nodes, 3))], [0.0]

    def to_nodes(uf: np.ndarray) -> np.ndarray:
        ur = np.zeros(cs.n_red)
        ur[free] = uf
        return cs.expand(ur)[:n3].reshape(-1, 3)

    for n in range(1, n_steps + 1):
        t = n * dt
        rhs = amp(t) * ff + Mf @ (a0 * u + a2 * v + a3 * a) + Cf @ (a1 * u + a4 * v + a5 * a)
        u_new = _solve_with(lu, rhs)
        a_new = a0 * (u_new - u) - a2 * v - a3 * a
        v = v + dt * ((1 - gamma) * a + gamma * a_new)
        u, a = u_new, a_new
        un = to_nodes(u)
        hist_t.append(t)
        hist_max.append(float(np.linalg.norm(un, axis=1).max()))
        for nid in hist_nodes:
            hist_nodes[nid].append(un[nid].copy())
        if n % stride == 0 or n == n_steps:
            frames.append(un)
            times.append(t)
        if n % max(1, n_steps // 10) == 0:
            _report(progress, 0.2 + 0.75 * n / n_steps, f"t = {t:.4g} s")
    history = {"max_displacement": np.array(hist_max)}
    for nid, series in hist_nodes.items():
        arr = np.array(series)
        for k, ax in enumerate("xyz"):
            history[f"node{nid}_u{ax}"] = arr[:, k]
    return TransientOut(np.array(hist_t), frames, history, frames[-1], "Implicit Newmark-beta dynamics\n" + prob.solver.summary())


# ---------------------------------------------------------------------- thermal
@dataclass
class ThermalProblem:
    mesh: FEMesh
    mat: object  # Material or MaterialMap
    T_fixed: Dict[int, float]
    Q: np.ndarray                           # nodal heat inflow (W), len N
    h_matrix: Optional[sparse.csr_matrix] = None   # film convection surface matrix
    h_vector: Optional[np.ndarray] = None   # film convection h*T_inf load
    radiation: List[dict] = field(default_factory=list)  # {faces, emissivity, T_inf}
    T_init: float = 0.0
    abs_zero: float = -273.15
    cs: Optional[ConstraintSystem] = None   # ties make the temperature continuous between separately meshed parts
    solver: SolverOptions = field(default_factory=SolverOptions)


class _ScalarReduction:
    """Maps the full nodal temperature vector to the reduced one when tie constraints are present."""

    def __init__(self, tp: ThermalProblem):
        pair = tp.cs.scalar_T() if tp.cs is not None else None
        self.n = tp.mesh.n_nodes
        self.Ts = self.kept = self.red_index = None
        if pair is not None:
            self.Ts, self.red_index, self.kept = pair
        self.n_red = self.n if pair is None else len(self.kept)

    def mat(self, K):
        return K.tocsr() if self.Ts is None else (self.Ts.T @ K @ self.Ts).tocsr()

    def vec(self, f):
        return f if self.Ts is None else self.Ts.T @ f

    def full(self, Tr):
        return Tr if self.Ts is None else self.Ts @ Tr

    def reduce_state(self, T):
        return T if self.Ts is None else T[self.kept]

    def geometry(self, mesh: FEMesh) -> Geometry:
        idx = np.arange(self.n) if self.kept is None else self.kept
        return Geometry(mesh.nodes[idx], None, idx)

    def fixed(self, T_fixed: Dict[int, float]) -> Dict[int, float]:
        if self.Ts is None:
            return dict(T_fixed)
        out = {}
        for k, v in T_fixed.items():
            if self.red_index[k] < 0:
                raise SolverError("A fixed temperature is applied to a node that is tied to another part. "
                                  "Apply it to the master surface instead.")
            out[int(self.red_index[k])] = v
        return out


def _thermal_K(tp: ThermalProblem) -> sparse.csr_matrix:
    K = asm.assemble_conductivity(tp.mesh, as_map(tp.mat, tp.mesh).prop("conductivity"))
    if tp.h_matrix is not None:
        K = K + tp.h_matrix
    return K.tocsr()


def thermal_steady(tp: ThermalProblem, progress: Progress = None) -> np.ndarray:
    mesh = tp.mesh
    if as_map(tp.mat, tp.mesh).prop("conductivity").min() <= 0:
        raise SolverError("Thermal analysis needs a conductivity greater than zero in every material.")
    red = _ScalarReduction(tp)
    K_full = _thermal_K(tp)
    rhs_full = tp.Q + (tp.h_vector if tp.h_vector is not None else 0.0)
    has_sink = bool(tp.T_fixed) or tp.h_matrix is not None or bool(tp.radiation)
    if not has_sink:
        raise SolverError(
            "No fixed temperature, convection or radiation is defined - a steady-state heat problem "
            "with only heat inputs has no solution. Fix a temperature or add convection/radiation.")
    _report(progress, 0.3, "Solving conduction")
    fixed_r = red.fixed(tp.T_fixed)
    Kr, rr = red.mat(K_full), red.vec(rhs_full)
    Tr = solve_partitioned(Kr, rr, fixed_r, tp.solver, red.geometry(mesh)) if (fixed_r or tp.h_matrix is not None) else None
    if not tp.radiation:
        return red.full(Tr)
    # Newton iteration for the T^4 radiation boundary condition (in the reduced variables)
    n = mesh.n_nodes
    if Tr is None:
        T0 = np.full(n, tp.T_init)
        for k, v in tp.T_fixed.items():
            T0[k] = v
        Tr = red.reduce_state(T0)
    free, _, _ = _partition(red.n_red, fixed_r)
    for it in range(60):
        T = red.full(Tr)
        f_rad = np.zeros(n)
        K_rad = sparse.csr_matrix((n, n))
        for rad in tp.radiation:
            eps, Tinf = float(rad["emissivity"]), float(rad["T_inf"])

            def law(Tg, eps=eps, Tinf=Tinf):
                tk, tinf = Tg - tp.abs_zero, Tinf - tp.abs_zero
                return SIGMA_SB * eps * (tk ** 4 - tinf ** 4), 4 * SIGMA_SB * eps * tk ** 3

            f, Kr_face = asm.face_scalar_nonlinear(mesh, rad["faces"], T, law)
            f_rad += f
            K_rad = K_rad + Kr_face
        R = red.vec(K_full @ T + f_rad - rhs_full)
        J = red.mat(K_full + K_rad)
        dT = np.zeros(red.n_red)
        try:
            dT[free] = _solve_with(_factor(J[free][:, free].tocsc(), tp.solver, red.geometry(mesh).subset(free)), -R[free])
        except SolverError as exc:
            raise SolverError("Radiation problem could not be solved: " + str(exc)) from exc
        Tr = Tr + dT
        _report(progress, 0.3 + 0.6 * min(1.0, it / 15), f"Radiation iteration {it + 1}")
        if np.max(np.abs(dT)) < 1e-9 * (1.0 + np.max(np.abs(Tr))):
            return red.full(Tr)
    raise SolverError("The radiation problem did not converge within 60 iterations.")


def thermal_transient(tp: ThermalProblem, t_end: float, n_steps: int, theta: float = 1.0,
                      amp: Callable[[float], float] = lambda t: 1.0, history_nodes: List[int] = (),
                      max_frames: int = 50, progress: Progress = None) -> TransientOut:
    mesh = tp.mesh
    if tp.radiation:
        raise SolverError("Radiation is only supported in steady-state heat transfer.")
    tm = as_map(tp.mat, mesh)
    if tm.prop("density").min() <= 0 or tm.prop("specific_heat").min() <= 0:
        raise SolverError("Transient heat transfer needs density and specific heat greater than zero in every material.")
    red = _ScalarReduction(tp)
    K_full = _thermal_K(tp)
    C_full = asm.assemble_capacity(mesh, tm.prop("density") * tm.prop("specific_heat"))
    base_full = tp.Q + (tp.h_vector if tp.h_vector is not None else 0.0)
    dt = t_end / n_steps
    fixed_r = red.fixed(tp.T_fixed)
    free, fidx, up = _partition(red.n_red, fixed_r)
    A = red.mat(C_full / dt + theta * K_full)
    lu = _factor(A[free][:, free].tocsc(), tp.solver, red.geometry(mesh).subset(free))
    B_full = (C_full / dt - (1 - theta) * K_full).tocsr()
    T0 = np.full(mesh.n_nodes, float(tp.T_init))
    for k, v in tp.T_fixed.items():
        T0[k] = v
    Tr = red.reduce_state(T0)
    Tr[fidx] = up
    T = red.full(Tr)
    stride = max(1, n_steps // max_frames)
    times, frames = [0.0], [T.copy()]
    hist_t, hist_max, hist_min = [0.0], [T.max()], [T.min()]
    hist_nodes = {int(k): [T[int(k)]] for k in history_nodes}
    for step in range(1, n_steps + 1):
        t = step * dt
        rhs = red.vec(B_full @ T + (theta * amp(t) + (1 - theta) * amp(t - dt)) * base_full)
        rhs_f = rhs[free] - A[free][:, fidx] @ up if len(fidx) else rhs[free]
        Tr = Tr.copy()
        Tr[free] = _solve_with(lu, rhs_f)
        T = red.full(Tr)
        hist_t.append(t)
        hist_max.append(T.max())
        hist_min.append(T.min())
        for k in hist_nodes:
            hist_nodes[k].append(T[k])
        if step % stride == 0 or step == n_steps:
            frames.append(T.copy())
            times.append(t)
        if step % max(1, n_steps // 10) == 0:
            _report(progress, 0.2 + 0.75 * step / n_steps, f"t = {t:.4g} s")
    history = {"max_temperature": np.array(hist_max), "min_temperature": np.array(hist_min)}
    for k, series in hist_nodes.items():
        history[f"node{k}_T"] = np.array(series)
    return TransientOut(np.array(hist_t), frames, history, T, "Transient heat transfer (theta-method)\n" + tp.solver.summary())
