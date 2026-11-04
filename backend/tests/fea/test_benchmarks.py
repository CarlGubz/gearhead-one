"""
Analytic benchmarks for the standalone FE engine. Each test compares against a closed-form
solution, so a passing suite is evidence the solver is correct rather than merely running.
"""
import numpy as np
import pytest

from app.fea import analyses as A
from app.fea import assembly as asm
from app.fea import meshgen
from app.fea.constraints import ConstraintSystem, RefPoint
from app.fea.materials import Material
from app.fea.model import FEMesh
from app.fea.recovery import stress_fields

ALL = ["C3D4", "C3D10", "C3D8", "C3D20"]
STEEL = Material(E=210e9, nu=0.3, density=7850.0, conductivity=50.0, specific_heat=490.0, alpha=12e-6)


def bar(et, L=2.0, w=0.2, div=2):
    return meshgen.generate("box", {"length": L, "width": w, "height": w}, div, et)


def roller_bcs(mesh, fix_x_min=True):
    """Symmetry-plane supports: uniaxial states stay exactly uniform. Returns {full dof: 0}."""
    lo = mesh.nodes.min(axis=0)
    fixed = {}
    for ax, flag in enumerate([fix_x_min, True, True]):
        if flag:
            for n in mesh.nodes_where(lambda p, ax=ax: np.isclose(p[:, ax], lo[ax])):
                fixed[3 * int(n) + ax] = 0.0
    return fixed


def problem(mesh, mat=STEEL, fixed=None, f=None, T=None, Tref=0.0, refs=(), cons=()):
    cs = ConstraintSystem(mesh, refs, cons)
    f_ext = np.zeros(cs.n_full)
    if f is not None:
        f_ext[: len(f)] = f
    return A.MechProblem(mesh, mat, cs, fixed or {}, f_ext, T, Tref)


def xmax_faces(mesh):
    hi = mesh.nodes[:, 0].max()
    return mesh.faces_where(lambda p: np.isclose(p[:, 0], hi))


# ------------------------------------------------------------------ statics
@pytest.mark.parametrize("et", ALL)
def test_uniaxial_bar_is_exact(et):
    mesh = bar(et)
    sigma = 1e6
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -sigma)  # negative pressure = tension
    out = A.static(problem(mesh, fixed=roller_bcs(mesh), f=f))
    expected = sigma * 2.0 / STEEL.E
    ux = out.u.reshape(-1, 3)[:, 0]
    assert ux.max() == pytest.approx(expected, rel=1e-8)
    assert np.allclose(out.stress[..., 0], sigma, rtol=1e-8)
    assert np.abs(out.stress[..., 1:]).max() < 1e-3 * sigma
    # equilibrium: reactions balance the applied load
    Rx = out.reactions.reshape(-1, 3)[:, 0].sum()
    assert Rx == pytest.approx(-sigma * 0.04, rel=1e-8)


@pytest.mark.parametrize("et", ["C3D10", "C3D20"])
def test_cantilever_tip_deflection(et):
    L, b, P = 1.0, 0.1, 1000.0
    mesh = meshgen.generate("box", {"length": L, "width": b, "height": b}, 2, et)
    fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], mesh.nodes[:, 0].min()))
             for k in range(3)}
    faces = xmax_faces(mesh)
    area = b * b
    f = asm.face_vector(mesh, faces, lambda xg, n: np.broadcast_to([0.0, 0.0, -P / area], xg.shape).copy())
    out = A.static(problem(mesh, fixed=fixed, f=f))
    tip = out.u.reshape(-1, 3)[mesh.nodes_where(lambda p: np.isclose(p[:, 0], mesh.nodes[:, 0].max())), 2].mean()
    I = b ** 4 / 12
    euler = P * L ** 3 / (3 * STEEL.E * I)
    assert abs(tip) == pytest.approx(euler, rel=0.01)  # observed: within 0.3%


def test_gravity_self_weight_matches_beam_theory():
    L, b = 1.0, 0.1
    mesh = meshgen.generate("box", {"length": L, "width": b, "height": b}, 2, "C3D20")
    lo = mesh.nodes[:, 0].min()
    fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}
    rho_g = STEEL.density * 9.81
    f = asm.body_force_vector(mesh, lambda xg: np.broadcast_to([0.0, 0.0, -rho_g], xg.shape).copy())
    out = A.static(problem(mesh, fixed=fixed, f=f))
    tip = out.u.reshape(-1, 3)[:, 2].min()
    w = rho_g * b * b
    expected = w * L ** 4 / (8 * STEEL.E * b ** 4 / 12)
    assert abs(tip) == pytest.approx(expected, rel=0.06)
    assert f[2::3].sum() == pytest.approx(-w * L, rel=1e-9)  # total weight conserved


@pytest.mark.parametrize("et", ["C3D4", "C3D10", "C3D20"])
def test_thermal_expansion_free_and_restrained(et):
    mesh = bar(et)
    dT = 100.0
    T = np.full(mesh.n_nodes, dT)
    free = A.static(problem(mesh, fixed=roller_bcs(mesh), T=T, Tref=0.0))
    assert free.u.reshape(-1, 3)[:, 0].max() == pytest.approx(STEEL.alpha * dT * 2.0, rel=1e-8)
    assert np.abs(free.stress).max() < 1e-4 * STEEL.E * STEEL.alpha * dT
    fixed = roller_bcs(mesh)
    hi = mesh.nodes[:, 0].max()
    for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi)):
        fixed[3 * int(n)] = 0.0
    held = A.static(problem(mesh, fixed=fixed, T=T, Tref=0.0))
    assert np.allclose(held.stress[..., 0], -STEEL.E * STEEL.alpha * dT, rtol=1e-6)


# ---------------------------------------------------------- eigenproblems
def _beam_mesh(et, L=1.0, b=0.05):
    return meshgen.generate("box", {"length": L, "width": b, "height": b}, 2, et)


def _clamp(mesh):
    lo = mesh.nodes[:, 0].min()
    return {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}


@pytest.mark.parametrize("et,tol", [("C3D20", 0.01), ("C3D10", 0.01)])  # observed: within 0.15%
def test_cantilever_first_frequency(et, tol):
    L, b = 1.0, 0.05
    mesh = _beam_mesh(et, L, b)
    out = A.frequency(problem(mesh, fixed=_clamp(mesh)), n_modes=4)
    I, area = b ** 4 / 12, b * b
    f1 = (1.875104 ** 2 / (2 * np.pi * L ** 2)) * np.sqrt(STEEL.E * I / (STEEL.density * area))
    assert out.values[0] == pytest.approx(f1, rel=tol)
    assert out.values[1] == pytest.approx(f1, rel=tol)  # square section: two degenerate bending modes
    assert out.values[2] == pytest.approx(f1 * 6.2669, rel=0.06)


def test_free_free_has_rigid_body_modes():
    mesh = _beam_mesh("C3D10")
    out = A.frequency(problem(mesh), n_modes=8)
    assert np.all(out.values[:6] < 1.0)
    assert out.values[6] > 100.0


@pytest.mark.parametrize("et", ["C3D20", "C3D10"])
def test_euler_buckling_load(et):
    L, b, Pref = 1.0, 0.05, 1000.0
    mesh = _beam_mesh(et, L, b)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), Pref / (b * b))  # compression
    out = A.buckling(problem(mesh, fixed=_clamp(mesh), f=f), n_modes=3)
    I = b ** 4 / 12
    pcr = np.pi ** 2 * STEEL.E * I / (4 * L ** 2)
    assert out.values[0] * Pref == pytest.approx(pcr, rel=0.01)  # observed: within 0.16%


def test_buckling_sparse_path_on_larger_model_and_tension_is_rejected_fast():
    import time
    L, b = 1.0, 0.05
    mesh = meshgen.generate("box", {"length": L, "width": b, "height": b}, 3, "C3D20")  # ~10k DOFs -> ARPACK path
    comp = asm.pressure_vector(mesh, xmax_faces(mesh), 1000.0 / (b * b))
    out = A.buckling(problem(mesh, fixed=_clamp(mesh), f=comp), n_modes=3)
    pcr = np.pi ** 2 * STEEL.E * (b ** 4 / 12) / (4 * L ** 2)
    assert out.values[0] * 1000.0 == pytest.approx(pcr, rel=0.01)
    t0 = time.time()
    with pytest.raises(A.SolverError, match="tension|compress"):
        A.buckling(problem(mesh, fixed=_clamp(mesh), f=-comp), n_modes=3)
    assert time.time() - t0 < 20  # used to stall in ARPACK


# ---------------------------------------------------------------- plasticity
def test_bilinear_plasticity_uniaxial():
    mat = Material(E=200e9, nu=0.3, density=7800.0, yield_stress=250e6, hardening=2e9)
    mesh = bar("C3D20", L=1.0, w=0.1, div=1)
    sigma = 300e6
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -sigma)
    out = A.nonlinear_static(problem(mesh, mat=mat, fixed=roller_bcs(mesh), f=f), increments=6)
    eps_p = (sigma - mat.yield_stress) / mat.hardening
    eps = sigma / mat.E + eps_p
    assert out.u.reshape(-1, 3)[:, 0].max() == pytest.approx(eps * 1.0, rel=1e-4)
    assert out.alpha_p.max() == pytest.approx(eps_p, rel=1e-4)
    assert np.allclose(out.stress[..., 0], sigma, rtol=1e-4)


def test_plasticity_matches_linear_below_yield():
    mat = Material(E=200e9, nu=0.3, yield_stress=250e6, hardening=2e9)
    mesh = bar("C3D10", L=1.0, w=0.1, div=1)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -100e6)
    p = problem(mesh, mat=mat, fixed=roller_bcs(mesh), f=f)
    nl = A.nonlinear_static(p, increments=2)
    lin = A.static(p)
    assert np.allclose(nl.u, lin.u, rtol=1e-6, atol=1e-12)
    assert nl.alpha_p.max() == 0.0


def test_plastic_cantilever_converges_and_exceeds_elastic_deflection():
    mat = Material(E=200e9, nu=0.3, yield_stress=250e6, hardening=1e9)
    L, b = 0.5, 0.05
    mesh = meshgen.generate("box", {"length": L, "width": b, "height": b}, 2, "C3D10")
    fixed = _clamp(mesh)
    # tip load giving ~1.4x the first-yield moment
    Mp = 1.4 * mat.yield_stress * b ** 3 / 6
    P = Mp / L
    f = asm.face_vector(mesh, xmax_faces(mesh), lambda xg, n: np.broadcast_to([0.0, 0.0, -P / b ** 2], xg.shape).copy())
    p = problem(mesh, mat=mat, fixed=fixed, f=f)
    nl = A.nonlinear_static(p, increments=8)
    lin = A.static(p)
    assert nl.alpha_p.max() > 0
    assert np.abs(nl.u).max() > 1.05 * np.abs(lin.u).max()


# ------------------------------------------------------------- constraints
def test_rigid_body_constraint_moves_slaves_exactly():
    mesh = bar("C3D10", L=1.0, w=0.1, div=1)
    lo, hi = mesh.nodes[:, 0].min(), mesh.nodes[:, 0].max()
    slaves = [int(n) for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi))]
    ref = RefPoint("r1", np.array([hi + 0.2, 0.0, 0.0]))
    cons = [{"type": "rigid", "ref_id": "r1", "node_ids": slaves}]
    cs = ConstraintSystem(mesh, [ref], cons)
    fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}
    fixed[cs.ref_dof("r1", 0)] = 1e-3
    for k in (1, 2, 3, 4, 5):
        fixed[cs.ref_dof("r1", k)] = 0.0
    out = A.static(problem(mesh, fixed=fixed, refs=[ref], cons=cons))
    u = out.u.reshape(-1, 3)
    assert np.allclose(u[slaves], [1e-3, 0, 0], atol=1e-14)
    assert u[:, 0].max() == pytest.approx(1e-3, rel=1e-6)


def test_remote_force_on_reference_point_with_lever_arm():
    """Force at an offset reference point = force + moment on the rigid face (cantilever + torque)."""
    L, b = 1.0, 0.1
    mesh = meshgen.generate("box", {"length": L, "width": b, "height": b}, 2, "C3D20")
    lo, hi = mesh.nodes[:, 0].min(), mesh.nodes[:, 0].max()
    slaves = [int(n) for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi))]
    ref = RefPoint("r1", np.array([hi, 0.0, 0.0]))
    cons = [{"type": "rigid", "ref_id": "r1", "node_ids": slaves}]
    cs = ConstraintSystem(mesh, [ref], cons)
    fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}
    P = 1000.0
    f = np.zeros(cs.n_full)
    f[cs.ref_dof("r1", 2)] = -P
    p = A.MechProblem(mesh, STEEL, cs, fixed, f)
    out = A.static(p)
    tip = out.ref_motion["r1"][2]
    assert abs(tip) == pytest.approx(P * L ** 3 / (3 * STEEL.E * b ** 4 / 12), rel=0.06)
    # rotation of the rigid end = P L^2 / (2 E I) about y
    assert abs(out.ref_motion["r1"][4]) == pytest.approx(P * L ** 2 / (2 * STEEL.E * b ** 4 / 12), rel=0.06)


def test_tie_between_separately_meshed_bodies_matches_monolithic():
    L, b = 1.0, 0.1
    whole = meshgen.generate("box", {"length": L, "width": b, "height": b}, 2, "C3D8")
    left = meshgen.generate("box", {"length": L / 2, "width": b, "height": b}, 2, "C3D8")
    right = meshgen.generate("box", {"length": L / 2, "width": b, "height": b}, 2, "C3D8")
    left.nodes[:, 0] -= L / 4
    right.nodes[:, 0] += L / 4
    merged = FEMesh(np.vstack([left.nodes, right.nodes]), np.vstack([left.elems, right.elems + left.n_nodes]), "C3D8")
    tip_dir = lambda mesh: asm.face_vector(  # noqa: E731
        mesh, xmax_faces(mesh), lambda xg, n: np.broadcast_to([0.0, 0.0, -1e6], xg.shape).copy())

    def solve(mesh, cons=()):
        lo = mesh.nodes[:, 0].min()
        fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}
        out = A.static(problem(mesh, fixed=fixed, f=tip_dir(mesh), cons=cons))
        return out.u.reshape(-1, 3)[:, 2].min()

    ref = solve(whole)
    bnd = merged.boundary()
    mid = 0.0
    master = bnd.tris[[i for i, t in enumerate(bnd.tris) if np.allclose(merged.nodes[t][:, 0], mid)
                       and set(t) <= set(range(left.n_nodes))]]
    slave_nodes = [int(n) for n in merged.nodes_where(lambda p: np.isclose(p[:, 0], mid))
                   if n >= left.n_nodes]
    tie = [{"type": "tie", "slave_node_ids": slave_nodes, "master_faces": master.tolist()}]
    assert solve(merged, tie) == pytest.approx(ref, rel=1e-9)


def test_unconstrained_static_is_rejected():
    mesh = bar("C3D4")
    f = asm.pressure_vector(mesh, xmax_faces(mesh), 1e5)
    with pytest.raises(A.SolverError, match="constrained|singular"):
        A.static(problem(mesh, f=f))


# ------------------------------------------------------------------ thermal
def _thermal(mesh, fixed=None, Q=None, h=None, rad=(), T0=0.0, k=50.0, rho=7850.0, c=490.0):
    return A.ThermalProblem(mesh, Material(conductivity=k, density=rho, specific_heat=c), fixed or {},
                            np.zeros(mesh.n_nodes) if Q is None else Q, *(h or (None, None)), list(rad), T0)


@pytest.mark.parametrize("et", ALL)
def test_steady_conduction_linear_profile(et):
    mesh = bar(et, L=2.0, w=0.2)
    lo, hi = mesh.nodes[:, 0].min(), mesh.nodes[:, 0].max()
    fixed = {int(n): 100.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo))}
    fixed.update({int(n): 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi))})
    T = A.thermal_steady(_thermal(mesh, fixed))
    assert np.allclose(T, 100.0 * (1 - (mesh.nodes[:, 0] - lo) / (hi - lo)), atol=1e-8)


def test_convection_boundary_balance():
    mesh = bar("C3D20", L=1.0, w=0.1)
    lo, hi = mesh.nodes[:, 0].min(), mesh.nodes[:, 0].max()
    fixed = {int(n): 100.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo))}
    faces = xmax_faces(mesh)
    h, Tinf, k = 25.0, 20.0, 50.0
    Kh = asm.face_scalar_matrix(mesh, faces, h)
    qh = asm.face_scalar_vector(mesh, faces, lambda xg: np.full(xg.shape[:2], h * Tinf))
    T = A.thermal_steady(_thermal(mesh, fixed, h=(Kh, qh), k=k))
    q = (100 - Tinf) / (1.0 / k + 1.0 / h)
    assert T[mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi))].mean() == pytest.approx(100 - q * 1.0 / k, rel=1e-8)


def test_radiation_equilibrium_of_isothermal_body():
    mesh = bar("C3D10", L=0.1, w=0.1, div=1)
    area = 6 * 0.1 * 0.1
    Q = np.zeros(mesh.n_nodes)
    Qtot = 50.0
    Q[:] = Qtot / mesh.n_nodes  # uniform spreading is enough with a very conductive body
    eps, Tinf = 0.8, 20.0
    allf = np.arange(len(mesh.boundary().face_nodes))
    rad = [{"faces": allf, "emissivity": eps, "T_inf": Tinf}]
    T = A.thermal_steady(_thermal(mesh, Q=Q, rad=rad, k=1e6, T0=100.0))
    Tk = lambda x: x + 273.15  # noqa: E731
    emitted = A.SIGMA_SB * eps * area * (Tk(T.mean()) ** 4 - Tk(Tinf) ** 4)
    assert emitted == pytest.approx(Qtot, rel=1e-3)


def test_transient_lumped_cooling_matches_exponential():
    mesh = bar("C3D8", L=0.1, w=0.1, div=1)
    rho, c, h, Tinf, T0 = 7850.0, 490.0, 10.0, 20.0, 100.0
    allf = np.arange(len(mesh.boundary().face_nodes))
    Kh = asm.face_scalar_matrix(mesh, allf, h)
    qh = asm.face_scalar_vector(mesh, allf, lambda xg: np.full(xg.shape[:2], h * Tinf))
    tau = rho * c * 0.001 / (h * 0.06)
    out = A.thermal_transient(_thermal(mesh, h=(Kh, qh), T0=T0, k=1e5, rho=rho, c=c), t_end=tau, n_steps=100, theta=0.5)
    assert out.final.mean() == pytest.approx(Tinf + (T0 - Tinf) * np.exp(-1.0), rel=2e-3)
    assert np.all(np.diff(out.history["max_temperature"]) <= 1e-9)  # monotone cooling


def test_steady_heat_without_sink_is_rejected():
    mesh = bar("C3D4")
    with pytest.raises(A.SolverError):
        A.thermal_steady(_thermal(mesh, Q=np.ones(mesh.n_nodes)))


# ------------------------------------------------------------------ dynamics
def test_dynamic_step_response_overshoot_is_two():
    L, b, P = 1.0, 0.05, 100.0
    mesh = _beam_mesh("C3D20", L, b)
    f = asm.face_vector(mesh, xmax_faces(mesh), lambda xg, n: np.broadcast_to([0.0, 0.0, -P / b ** 2], xg.shape).copy())
    p = problem(mesh, fixed=_clamp(mesh), f=f)
    static_tip = np.abs(A.static(p).u.reshape(-1, 3)[:, 2]).max()
    f1 = A.frequency(p, n_modes=1).values[0]
    T1 = 1.0 / f1
    out = A.dynamic(p, t_end=1.5 * T1, n_steps=300, amp=lambda t: 1.0)
    peak = max(np.abs(fr[:, 2]).max() for fr in out.frames)
    assert peak / static_tip == pytest.approx(2.0, rel=0.08)


def test_dynamic_rayleigh_damping_reduces_amplitude():
    L, b, P = 1.0, 0.05, 100.0
    mesh = _beam_mesh("C3D20", L, b)
    f = asm.face_vector(mesh, xmax_faces(mesh), lambda xg, n: np.broadcast_to([0.0, 0.0, -P / b ** 2], xg.shape).copy())
    p = problem(mesh, fixed=_clamp(mesh), f=f)
    T1 = 1.0 / A.frequency(p, n_modes=1).values[0]
    kw = dict(t_end=4 * T1, n_steps=400, amp=lambda t: 1.0)
    undamped = A.dynamic(p, **kw)
    damped = A.dynamic(p, rayleigh_alpha=40.0, **kw)
    tail = lambda o: np.abs(o.history["max_displacement"][-100:] - o.history["max_displacement"][-100:].mean()).max()  # noqa: E731
    assert tail(damped) < 0.5 * tail(undamped)


# ------------------------------------------------------------------ recovery
def test_nodal_recovery_of_uniform_stress_is_uniform():
    mesh = bar("C3D10")
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -2e6)
    out = A.static(problem(mesh, fixed=roller_bcs(mesh), f=f))
    fields = stress_fields(mesh, out.stress, out.strain, STEEL)
    assert np.allclose(fields["von_mises"], 2e6, rtol=1e-6)
    assert np.allclose(fields["principal_max"], 2e6, rtol=1e-6)
    assert np.allclose(fields["tresca"], 2e6, rtol=1e-6)
