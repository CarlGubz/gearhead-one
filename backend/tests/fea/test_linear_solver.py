"""The iterative (two-level Schwarz PCG) solver must agree with the direct solver everywhere it is used."""
import numpy as np
import pytest

from app.fea import analyses as A
from app.fea import assembly as asm
from app.fea import meshgen
from app.fea.constraints import ConstraintSystem, RefPoint
from app.fea.linsolve import Geometry, SolverOptions, make_solver
from app.fea.materials import Material
from tests.fea.test_benchmarks import STEEL, _clamp, bar, problem, roller_bcs, xmax_faces

ITER = dict(mode="iterative", block_dofs=500)


def opts(**kw):
    return SolverOptions(**{**ITER, **kw})


def rel(a, b):
    return np.linalg.norm(a - b) / np.linalg.norm(b)


@pytest.mark.parametrize("et", ["C3D4", "C3D10", "C3D8", "C3D20"])
def test_static_matches_direct_for_every_element_type(et):
    mesh = bar(et, div=3)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -1e6)
    p = problem(mesh, fixed=roller_bcs(mesh), f=f)
    direct = A.static(p)
    p.solver = opts()
    it = A.static(p)
    assert "Schwarz" in it.log and "sparse LU" in direct.log
    assert rel(it.u, direct.u) < 1e-6
    assert np.allclose(it.stress, direct.stress, rtol=1e-4, atol=1e-2)
    assert it.u.reshape(-1, 3)[:, 0].max() == pytest.approx(1e6 * 2.0 / STEEL.E, rel=1e-6)


def test_cantilever_bending_iterative_equals_direct():
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.1, "height": 0.1}, 3, "C3D10")
    f = asm.face_vector(mesh, xmax_faces(mesh), lambda xg, n: np.broadcast_to([0.0, 0.0, -1e5], xg.shape).copy())
    p = problem(mesh, fixed=_clamp(mesh), f=f)
    d = A.static(p)
    p.solver = opts()
    i = A.static(p)
    assert rel(i.u, d.u) < 1e-6


def test_constrained_models_work_iteratively_ties_and_rigid_bodies():
    # rigid body with a reference point (6 extra DOFs)
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.1, "height": 0.1}, 3, "C3D20")
    lo, hi = mesh.nodes[:, 0].min(), mesh.nodes[:, 0].max()
    slaves = [int(n) for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi))]
    ref = RefPoint("r", np.array([hi + 0.1, 0.0, 0.0]))
    cons = [{"type": "rigid", "ref_id": "r", "node_ids": slaves}]
    cs = ConstraintSystem(mesh, [ref], cons)
    fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}
    f = np.zeros(cs.n_full)
    f[cs.ref_dof("r", 2)] = -1000.0
    prob = A.MechProblem(mesh, STEEL, cs, fixed, f)
    d = A.static(prob)
    prob.solver = opts()
    i = A.static(prob)
    assert rel(i.u, d.u) < 1e-6
    assert i.ref_motion["r"] == pytest.approx(d.ref_motion["r"], rel=1e-5)


def test_nonlinear_plasticity_iterative_matches_direct():
    mat = Material(E=200e9, nu=0.3, yield_stress=250e6, hardening=2e9)
    mesh = bar("C3D10", L=1.0, w=0.1, div=2)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -300e6)
    p = problem(mesh, mat=mat, fixed=roller_bcs(mesh), f=f)
    d = A.nonlinear_static(p, increments=4)
    p.solver = opts()
    i = A.nonlinear_static(p, increments=4)
    assert rel(i.u, d.u) < 1e-5
    assert i.alpha_p.max() == pytest.approx(d.alpha_p.max(), rel=1e-4)


def test_dynamic_and_transient_heat_iterative_matches_direct():
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.05, "height": 0.05}, 2, "C3D20")
    f = asm.face_vector(mesh, xmax_faces(mesh), lambda xg, n: np.broadcast_to([0.0, 0.0, -4e4], xg.shape).copy())
    p = problem(mesh, fixed=_clamp(mesh), f=f)
    kw = dict(t_end=0.02, n_steps=40, amp=lambda t: 1.0)
    d = A.dynamic(p, **kw)
    p.solver = opts()
    i = A.dynamic(p, **kw)
    assert rel(i.final, d.final) < 1e-5

    tmesh = bar("C3D10", L=0.5, w=0.1, div=2)
    lo, hi = tmesh.nodes[:, 0].min(), tmesh.nodes[:, 0].max()
    fixed = {int(n): 100.0 for n in tmesh.nodes_where(lambda q: np.isclose(q[:, 0], lo))}
    fixed.update({int(n): 0.0 for n in tmesh.nodes_where(lambda q: np.isclose(q[:, 0], hi))})
    tp = A.ThermalProblem(tmesh, Material(), fixed, np.zeros(tmesh.n_nodes), T_init=0.0)
    steady_d = A.thermal_steady(tp)
    tr_d = A.thermal_transient(tp, t_end=500.0, n_steps=10)
    tp.solver = opts()
    assert np.allclose(A.thermal_steady(tp), steady_d, atol=1e-5)
    assert np.allclose(A.thermal_transient(tp, t_end=500.0, n_steps=10).final, tr_d.final, atol=1e-5)


def test_auto_mode_switches_on_size_and_reports_the_solver_used():
    mesh = bar("C3D8", div=3)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -1e6)
    p = problem(mesh, fixed=roller_bcs(mesh), f=f)
    assert "sparse LU" in A.static(p).log                      # small model -> direct
    p.solver = SolverOptions(mode="auto", auto_threshold=1000, block_dofs=500)
    assert "Schwarz" in A.static(p).log                         # same model, lower threshold -> iterative
    p.solver = SolverOptions(mode="direct")
    assert "sparse LU" in A.static(p).log


def test_singular_systems_are_still_reported_iteratively():
    mesh = bar("C3D8", div=3)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), 1e5)
    p = problem(mesh, f=f)
    p.solver = opts()
    with pytest.raises(A.SolverError):
        A.static(p)


def test_failed_iteration_falls_back_to_direct_or_errors_when_too_big():
    mesh = bar("C3D8", div=3)
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -1e6)
    p = problem(mesh, fixed=roller_bcs(mesh), f=f)
    reference = A.static(p)
    p.solver = opts(max_iter=1)                                  # CG cannot converge in one iteration
    fallback = A.static(p)
    assert rel(fallback.u, reference.u) < 1e-9 and "sparse LU" in fallback.log.splitlines()[-1] + fallback.log
    p.solver = opts(max_iter=1, direct_fallback_limit=10)        # too big to fall back
    with pytest.raises(A.SolverError, match="did not converge|linear solver failed"):
        A.static(p)


def test_preconditioner_is_symmetric_positive_definite():
    mesh = bar("C3D10", div=2)
    K = asm.mech_assemble(mesh, STEEL, np.zeros(3 * mesh.n_nodes)).K.tocsr()
    fixed = sorted(3 * int(n) + k for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], mesh.nodes[:, 0].min())) for k in range(3))
    free = np.setdiff1d(np.arange(K.shape[0]), fixed)
    Kf = K[free][:, free].tocsr()
    node = free // 3
    s = make_solver(Kf, opts(), Geometry(mesh.nodes[node], free % 3, node))
    rng = np.random.default_rng(0)
    x, y = rng.standard_normal(len(free)), rng.standard_normal(len(free))
    Mx, My = s.iter._apply(x), s.iter._apply(y)
    assert abs(y @ Mx - x @ My) < 1e-8 * abs(y @ Mx)            # symmetric
    assert all(z @ s.iter._apply(z) > 0 for z in (x, y, x + y))  # positive


def test_moderately_large_model_is_solved_exactly_without_a_global_factorisation():
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.5, "height": 0.5}, 7, "C3D10")   # ~18k DOFs
    sigma = 2e6
    f = asm.pressure_vector(mesh, xmax_faces(mesh), -sigma)
    p = problem(mesh, mat=Material(E=210e9, nu=0.0), fixed=roller_bcs(mesh), f=f)
    p.solver = SolverOptions(mode="auto", auto_threshold=10_000)
    out = A.static(p)
    assert "Schwarz" in out.log and 3 * mesh.n_nodes > 10_000
    assert out.u.reshape(-1, 3)[:, 0].max() == pytest.approx(sigma * 1.0 / 210e9, rel=1e-6)
