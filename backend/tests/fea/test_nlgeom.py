"""Large-deformation (NLGEOM) checks: invariance, consistent tangent, closed-form stretch and the elastica."""
import numpy as np
import pytest

from app.fea import analyses as A
from app.fea import assembly as asm
from app.fea import meshgen, runner
from app.fea.constraints import ConstraintSystem
from app.fea.finite import finite_assemble
from app.fea.materials import Material
from tests.fea.test_contact import blocks, press

STEEL = Material(E=210e9, nu=0.3)


def rotation(axis, angle):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * K @ K


@pytest.mark.parametrize("model", ["svk", "neohookean"])
@pytest.mark.parametrize("et", ["C3D4", "C3D8", "C3D10", "C3D20"])
def test_rigid_rotation_costs_nothing(model, et):
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.3, "height": 0.2}, 2, et)
    R = rotation([1.0, 2.0, 0.5], np.deg2rad(110))
    u = (mesh.nodes @ R.T - mesh.nodes) + np.array([0.3, -2.0, 0.7])
    out = finite_assemble(mesh, STEEL, u.ravel(), model)
    assert np.abs(out.fint).max() < 1e-6 * STEEL.E * 0.2 ** 2
    assert np.abs(out.stress).max() < 1e-6 * STEEL.E
    # the small-strain assembly is NOT invariant - that is the reason this analysis exists
    lin = asm.mech_assemble(mesh, STEEL, u.ravel(), None, None, 0.0, want_K=False)
    assert np.abs(lin.fint).max() > 1e3 * np.abs(out.fint).max()


@pytest.mark.parametrize("model", ["svk", "neohookean"])
def test_tangent_matches_finite_differences(model):
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.4, "height": 0.3}, 2, "C3D8")
    rng = np.random.default_rng(3)
    u = 0.008 * rng.standard_normal(3 * mesh.n_nodes)
    d = rng.standard_normal(3 * mesh.n_nodes)
    mat = Material(E=1e6, nu=0.3)
    K = finite_assemble(mesh, mat, u, model).K
    h = 1e-6
    fd = (finite_assemble(mesh, mat, u + h * d, model, False).fint - finite_assemble(mesh, mat, u - h * d, model, False).fint) / (2 * h)
    assert np.linalg.norm(K @ d - fd) < 1e-6 * np.linalg.norm(fd)
    assert abs(K - K.T).max() < 1e-9 * abs(K).max()


@pytest.mark.parametrize("model", ["svk", "neohookean"])
def test_cauchy_stress_of_a_homogeneous_stretch(model):
    mat = Material(E=2e6, nu=0.3)
    mesh = meshgen.generate("box", {"length": 1.0, "width": 1.0, "height": 1.0}, 1, "C3D8")
    lx, ly = 1.6, 0.85
    F = np.diag([lx, ly, ly])
    u = (mesh.nodes @ F.T - mesh.nodes)
    out = finite_assemble(mesh, mat, u.ravel(), model, False)
    lam = mat.E * mat.nu / ((1 + mat.nu) * (1 - 2 * mat.nu))
    mu = mat.G
    J = np.linalg.det(F)
    if model == "svk":
        E = 0.5 * (F.T @ F - np.eye(3))
        S = lam * np.trace(E) * np.eye(3) + 2 * mu * E
        sig = F @ S @ F.T / J
    else:
        b = F @ F.T
        sig = mu / J * (b - np.eye(3)) + lam * np.log(J) / J * np.eye(3)
    got = out.stress[0, 0]
    assert got[0] == pytest.approx(sig[0, 0], rel=1e-10)
    assert got[1] == pytest.approx(sig[1, 1], rel=1e-10)
    assert abs(got[3]) < 1e-9 * abs(sig[0, 0])


def solve_nl(mesh, mat, fixed, f, model="svk", inc=10):
    cs = ConstraintSystem(mesh, [], [])
    prob = A.MechProblem(mesh, mat, cs, fixed, f)
    prob.nlgeom, prob.finite_model = True, model
    return A.nonlinear_static(prob, inc)


@pytest.mark.parametrize("model,stretch", [("svk", 1.5), ("svk", 2.0), ("neohookean", 1.5), ("neohookean", 2.5)])
def test_uniaxial_stretch_solved_by_newton_matches_closed_form(model, stretch):
    # nu = 0 -> the lateral stretch stays exactly 1 and the nominal (first Piola-Kirchhoff) stress is closed form
    mat = Material(E=1e6, nu=0.0)
    mu = mat.G
    mesh = meshgen.generate("box", {"length": 1.0, "width": 1.0, "height": 1.0}, 2, "C3D8")
    lo, hi = mesh.nodes.min(axis=0), mesh.nodes.max(axis=0)
    fixed = {}
    for ax in (1, 2):
        for n in mesh.nodes_where(lambda p, ax=ax: np.isclose(p[:, ax], lo[ax])):
            fixed[3 * int(n) + ax] = 0.0
    for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo[0])):
        fixed[3 * int(n)] = 0.0
    for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi[0])):
        fixed[3 * int(n)] = (stretch - 1.0) * 1.0
    out = solve_nl(mesh, mat, fixed, np.zeros(3 * mesh.n_nodes), model, inc=8)
    P = mu * (stretch - 1.0 / stretch) if model == "neohookean" else mu * stretch * (stretch ** 2 - 1.0)
    R = out.reactions.reshape(-1, 3)
    end = mesh.nodes_where(lambda p: np.isclose(p[:, 0], hi[0]))
    assert R[end, 0].sum() == pytest.approx(P * 1.0, rel=2e-4)
    assert -R[mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo[0])), 0].sum() == pytest.approx(P, rel=2e-4)
    sig_xx = out.stress[..., 0].mean()
    assert sig_xx == pytest.approx(P, rel=2e-4)              # Cauchy = P F^T / J, and J = F_xx here


# --------------------------------------------------------------- the elastica
ELASTICA = {1.0: (0.3017, 0.0562), 2.0: (0.4939, 0.1606), 3.0: (0.6033, 0.2554)}   # PL^2/EI -> (v/L, shortening/L), Mattiasson


@pytest.mark.parametrize("alpha", sorted(ELASTICA))
def test_cantilever_large_tip_deflection_vs_elastica(alpha):
    L, w = 1.0, 0.05
    mat = Material(E=1e9, nu=0.0)
    EI = mat.E * w ** 4 / 12.0
    P = alpha * EI / L ** 2
    mesh = meshgen.generate("box", {"length": L, "width": w, "height": w}, 1, "C3D20")
    mesh.nodes[:, 0] += L / 2                                      # clamp plane at x = 0
    fixed = {3 * int(n) + d: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], 0.0)) for d in range(3)}
    tip = mesh.faces_where(lambda p: np.isclose(p[:, 0], L))
    f = asm.face_vector(mesh, tip, lambda xg, n: np.broadcast_to([0.0, 0.0, -P / w ** 2], xg.shape).copy())
    out = solve_nl(mesh, mat, fixed, f, "svk", inc=10)
    u = out.u.reshape(-1, 3)
    tip_nodes = mesh.nodes_where(lambda p: np.isclose(p[:, 0], L))
    v = -u[tip_nodes, 2].mean() / L
    shorten = -u[tip_nodes, 0].mean() / L
    ev, es = ELASTICA[alpha]
    assert v == pytest.approx(ev, rel=0.02)
    assert shorten == pytest.approx(es, rel=0.04)
    # the linear (small-strain) answer for the same load is far off: this is what NLGEOM fixes
    lin = alpha / 3.0
    assert abs(lin - ev) / ev > 0.1 * alpha


def test_small_loads_reproduce_the_linear_solution():
    L, w = 1.0, 0.1
    mat = Material(E=70e9, nu=0.3)
    mesh = meshgen.generate("box", {"length": L, "width": w, "height": w}, 2, "C3D20")
    mesh.nodes[:, 0] += L / 2
    fixed = {3 * int(n) + d: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], 0.0)) for d in range(3)}
    tip = mesh.faces_where(lambda p: np.isclose(p[:, 0], L))
    f = asm.face_vector(mesh, tip, lambda xg, n: np.broadcast_to([0.0, 0.0, -1e3], xg.shape).copy())
    nl = solve_nl(mesh, mat, fixed, f, "svk", inc=2)
    cs = ConstraintSystem(mesh, [], [])
    lin = A.static(A.MechProblem(mesh, mat, cs, fixed, f))
    assert np.abs(nl.u - lin.u).max() < 1e-4 * np.abs(lin.u).max()
    nh = solve_nl(mesh, mat, fixed, f, "neohookean", inc=2)
    assert np.abs(nh.u - lin.u).max() < 1e-3 * np.abs(lin.u).max()


# ------------------------------------------------------------------ runner level
def _bar_project(et="C3D8", **settings):
    proj = blocks(et)
    press(proj, 1e6)
    proj["settings"].update(settings)
    return proj


def test_nlgeom_with_contact_conserves_force():
    proj = _bar_project(nlgeom=True, increments=3)
    out = runner.run(proj)
    r = out["results"]
    assert "large deformation" in out["log"]
    assert r["contact"]["total_force"] == pytest.approx(1e6 * 0.04, rel=1e-2)
    assert r["reaction_total"][2] == pytest.approx(1e6 * 0.04, rel=1e-5)


def test_nlgeom_setting_is_validated_and_limited():
    proj = _bar_project()
    proj["constraints"] = []                                  # no contact: plain NLGEOM static of two loose blocks is unsupported
    proj["settings"].update(nlgeom=True, finite_strain_model="bogus")
    with pytest.raises(runner.ModelError, match="svk or neohookean"):
        runner.run(proj)
    proj["settings"].update(finite_strain_model="svk")
    proj["analysis_type"] = "modal"
    with pytest.raises(runner.ModelError, match="static and nonlinear"):
        runner.run(proj)
    proj["analysis_type"] = "static"
    proj["material"] = {**proj["material"], "yield_stress": 1e6}
    proj["materials"] = []
    proj["parts"] = []
    with pytest.raises((runner.ModelError, A.SolverError, ValueError), match="plasticity"):
        runner.run(proj)
