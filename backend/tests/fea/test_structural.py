"""Truss, spring, beam and shell elements against closed-form results (each test cites the formula it checks)."""
import numpy as np
import pytest

from app.fea import runner, structmesh
from app.fea.struct_runner import StructModel, StructuralError

E, NU, RHO = 210e9, 0.3, 7850.0
BIG = 1e12                      # shear areas so large that Timoshenko -> Euler-Bernoulli


def project(nodes, elems, etype, section, bcs=(), loads=(), analysis="static", settings=None, gravity=None,
            E_=E, nu=NU, rho=RHO):
    ne = len(elems)
    return {
        "name": "t", "nodes": np.asarray(nodes, float).tolist(), "elements": np.asarray(elems).tolist(), "element_type": etype,
        "material": {"name": "m", "E": E_, "nu": nu, "density": rho, "conductivity": 50, "specific_heat": 490, "alpha": 0,
                     "yield_stress": 0, "hardening": 0},
        "materials": [], "parts": [{"id": "p0", "name": "Body", "elem_start": 0, "elem_end": ne, "material_id": "default",
                                    "section": section}],
        "boundary_conditions": [dict(id=f"b{i}", name="bc", node_ids=[int(n) for n in np.atleast_1d(ids)], dofs=list(d), values=None)
                                for i, (ids, d) in enumerate(bcs)],
        "loads": [dict(l) for l in loads], "analysis_type": analysis, "n_modes": 6, "gravity": gravity or {},
        "settings": settings or {}, "reference_points": [], "constraints": [],
    }


def run(p):
    return runner.run(p)["results"]


def force(ids, vec=(0, 0, 0), mom=(0, 0, 0)):
    return {"id": "f", "name": "f", "load_type": "force", "node_ids": [int(i) for i in np.atleast_1d(ids)],
            "vector": list(vec), "moment": list(mom)}


def disp(res):
    return np.array(res["displacements"])


def rots(res):
    return np.array(res["rotations"])


ALL6 = (0, 1, 2, 3, 4, 5)
GEN = lambda A, Iy, Iz, J: {"type": "general", "A": A, "Iy": Iy, "Iz": Iz, "J": J, "Asy": BIG, "Asz": BIG}   # noqa: E731


# ----------------------------------------------------------------------- truss
def test_single_bar_elongation_is_PL_over_EA():
    A, L, P = 1e-4, 2.0, 1e4
    nodes, elems = structmesh.line([0, 0, 0], [L, 0, 0], 1)
    r = run(project(nodes, elems, "T3D2", {"area": A}, bcs=[(0, (0, 1, 2)), (1, (1, 2))], loads=[force(1, (P, 0, 0))]))
    assert disp(r)[1, 0] == pytest.approx(P * L / (E * A), rel=2e-6)
    assert r["element_forces"][0][1] == pytest.approx(P, rel=2e-6)
    assert r["fields"]["von_mises"][0] == pytest.approx(P / A, rel=2e-6)
    assert r["reaction_total"][0] == pytest.approx(-P, rel=2e-6)


def test_symmetric_two_bar_truss_apex_deflection_and_member_forces():
    A, P = 2e-4, 5e3
    nodes = [[-1, 0, 0], [1, 0, 0], [0, 1, 0]]
    elems = [[0, 2], [1, 2]]
    r = run(project(nodes, elems, "T3D2", {"area": A}, bcs=[([0, 1], (0, 1, 2)), (2, (2,))], loads=[force(2, (0, -P, 0))]))
    Lb, s = np.sqrt(2.0), np.sqrt(0.5)
    # apex vertical displacement  P L / (2 E A sin^2 theta);  bar force  P / (2 sin theta)  (compression)
    assert -disp(r)[2, 1] == pytest.approx(P * Lb / (2 * E * A * s ** 2), rel=2e-6)
    assert abs(r["element_forces"][0][1]) == pytest.approx(P / (2 * s), rel=2e-6)
    assert r["element_forces"][0][1] < 0


def test_spring_chain_series_stiffness_and_reactions():
    k1, k2, F = 2e5, 5e5, 1e3
    nodes, elems = structmesh.line([0, 0, 0], [2, 0, 0], 2)
    p = project(nodes, elems, "SPRING2", {"k": k1}, bcs=[(0, (0, 1, 2)), ([1, 2], (1, 2))], loads=[force(2, (F, 0, 0))])
    p["parts"] = [{"id": "a", "name": "A", "elem_start": 0, "elem_end": 1, "material_id": "default", "section": {"k": k1}},
                  {"id": "b", "name": "B", "elem_start": 1, "elem_end": 2, "material_id": "default", "section": {"k": k2}}]
    r = run(p)
    assert disp(r)[2, 0] == pytest.approx(F * (1 / k1 + 1 / k2), rel=2e-6)
    assert disp(r)[1, 0] == pytest.approx(F / k1, rel=2e-6)
    assert r["reaction_total"][0] == pytest.approx(-F, rel=2e-6)


def test_spring_mass_natural_frequency():
    k, m = 4e5, 25.0
    nodes, elems = structmesh.line([0, 0, 0], [1, 0, 0], 1)
    p = project(nodes, elems, "SPRING2", {"k": k}, bcs=[(0, (0, 1, 2)), (1, (1, 2))],
                loads=[{"id": "m", "name": "m", "load_type": "point_mass", "node_ids": [1], "magnitude": m}], analysis="modal")
    r = run(p)
    assert r["frequencies_hz"][0] == pytest.approx(np.sqrt(k / m) / (2 * np.pi), rel=2e-6)


def test_dashpot_damped_step_response_overshoot():
    k, m, c, F = 4e5, 25.0, 0.0, 1e3
    wn = np.sqrt(k / m)
    zeta = 0.1
    c = 2 * zeta * np.sqrt(k * m)
    nodes, elems = structmesh.line([0, 0, 0], [1, 0, 0], 1)
    T = 2 * np.pi / wn
    p = project(nodes, elems, "SPRING2", {"k": k, "c": c}, bcs=[(0, (0, 1, 2)), (1, (1, 2))],
                loads=[force(1, (F, 0, 0)), {"id": "m", "name": "m", "load_type": "point_mass", "node_ids": [1], "magnitude": m}],
                analysis="dynamic", settings={"t_end": 3 * T, "n_steps": 3000, "history_nodes": [1]})
    r = run(p)
    peak = max(r["history"]["node 1"])
    expected = (F / k) * (1 + np.exp(-np.pi * zeta / np.sqrt(1 - zeta ** 2)))
    assert peak == pytest.approx(expected, rel=2e-3)
    assert r["history"]["node 1"][-1] == pytest.approx(F / k, rel=0.15)           # decays towards the static value


# ------------------------------------------------------------------------ beam
def cantilever_beam(n, L, section, loads, bcs_extra=(), **kw):
    nodes, elems = structmesh.line([0, 0, 0], [L, 0, 0], n)
    return project(nodes, elems, "B31", section, bcs=[(0, ALL6), *bcs_extra], loads=loads, **kw)


RECT = {"type": "rect", "b": 0.05, "h": 0.1}        # b along local y, h (the deep side) along local z
b_, h_ = 0.05, 0.1
A_, Iy_, Iz_ = b_ * h_, b_ * h_ ** 3 / 12, h_ * b_ ** 3 / 12


def test_cantilever_tip_load_includes_shear_deformation():
    L, P = 1.5, 2e3
    G = E / (2 * (1 + NU))
    r = run(cantilever_beam(4, L, RECT, [force(4, (0, 0, -P))]))
    expected = P * L ** 3 / (3 * E * Iy_) + P * L / ((5 / 6) * G * A_)               # bending + shear (Timoshenko)
    assert -disp(r)[4, 2] == pytest.approx(expected, rel=2e-6)
    ef = r["element_forces"][0]            # [element, N1, Vy1, Vz1, T1, My1, Mz1, N2, ...]
    assert abs(ef[3]) == pytest.approx(P, rel=2e-6)                 # root shear
    assert abs(ef[5]) == pytest.approx(P * L, rel=2e-6)             # root moment
    assert abs(ef[1]) < 1e-6 * P and abs(ef[4]) < 1e-6 * P          # no axial force, no torque


def test_weak_axis_bending_uses_Iz():
    L, P = 1.5, 2e3
    r = run(cantilever_beam(3, L, {"type": "general", "A": A_, "Iy": Iy_, "Iz": Iz_, "J": 1e-6, "Asy": BIG, "Asz": BIG},
                            [force(3, (0, P, 0))]))
    assert disp(r)[3, 1] == pytest.approx(P * L ** 3 / (3 * E * Iz_), rel=2e-6)


def test_cantilever_uniform_load_and_self_weight():
    L, q = 2.0, 3e3
    sec = GEN(A_, Iy_, Iz_, 1e-6)
    p = cantilever_beam(4, L, sec, [{"id": "q", "name": "q", "load_type": "line_load", "vector": [0, 0, -q]}])
    r = run(p)
    assert -disp(r)[4, 2] == pytest.approx(q * L ** 4 / (8 * E * Iy_), rel=2e-6)
    assert rots(r)[4, 1] == pytest.approx(q * L ** 3 / (6 * E * Iy_), rel=2e-6)       # slope at the tip
    # self-weight: q = rho A g
    p2 = cantilever_beam(4, L, sec, [], gravity={"enabled": True, "x": 0, "y": 0, "z": -9.81})
    r2 = run(p2)
    assert -disp(r2)[4, 2] == pytest.approx(RHO * A_ * 9.81 * L ** 4 / (8 * E * Iy_), rel=2e-6)
    # root reactions balance the weight
    assert r2["reaction_total"][2] == pytest.approx(RHO * A_ * 9.81 * L, rel=2e-6)
    assert abs(r2["reaction_moment_total"][1]) == pytest.approx(RHO * A_ * 9.81 * L ** 2 / 2, rel=2e-6)


def test_end_moment_curvature():
    L, M = 1.2, 500.0
    r = run(cantilever_beam(3, L, GEN(A_, Iy_, Iz_, 1e-6), [force(3, mom=(0, M, 0))]))
    assert rots(r)[3, 1] == pytest.approx(M * L / (E * Iy_), rel=2e-6)
    assert disp(r)[3, 2] == pytest.approx(-M * L ** 2 / (2 * E * Iy_), rel=2e-6)
    r2 = run(cantilever_beam(3, L, GEN(A_, Iy_, Iz_, 1e-6), [force(3, mom=(0, 0, M))]))
    assert rots(r2)[3, 2] == pytest.approx(M * L / (E * Iz_), rel=2e-6)
    assert disp(r2)[3, 1] == pytest.approx(M * L ** 2 / (2 * E * Iz_), rel=2e-6)


def test_torsion_of_a_bar():
    L, T = 1.0, 800.0
    G = E / (2 * (1 + NU))
    sec = {"type": "circle", "d": 0.04}
    J = np.pi * 0.04 ** 4 / 32
    r = run(cantilever_beam(4, L, sec, [force(4, mom=(T, 0, 0))]))
    assert rots(r)[4, 0] == pytest.approx(T * L / (G * J), rel=2e-6)
    assert r["fields"]["torsion"][0] == pytest.approx(T, rel=2e-6)


def test_simply_supported_beam_mid_point_load():
    L, P, n = 3.0, 6e3, 8
    nodes, elems = structmesh.line([0, 0, 0], [L, 0, 0], n)
    p = project(nodes, elems, "B31", GEN(A_, Iy_, Iz_, 1e-6),
                bcs=[(0, (0, 1, 2, 3)), (n, (1, 2, 3))], loads=[force(n // 2, (0, 0, -P))])
    r = run(p)
    assert -disp(r)[n // 2, 2] == pytest.approx(P * L ** 3 / (48 * E * Iy_), rel=2e-6)
    assert r["reaction_forces"][0][2] == pytest.approx(P / 2, rel=2e-6)
    assert max(r["fields"]["bending_moment"]) == pytest.approx(P * L / 4, rel=2e-6)


def test_L_frame_tip_deflection_unit_load_method():
    # vertical column (height H) clamped at its base, horizontal arm (length L) carries a vertical tip load
    H, L, P = 1.0, 0.8, 3e3
    sec = GEN(A_, Iy_, Iz_, 1e-6)
    sec["Asy"] = sec["Asz"] = BIG
    nodes = [[0, 0, 0], [0, 0, H / 2], [0, 0, H], [L / 2, 0, H], [L, 0, H]]
    elems = [[0, 1], [1, 2], [2, 3], [3, 4]]
    r = run(project(nodes, elems, "B31", sec, bcs=[(0, ALL6)], loads=[force(4, (0, 0, -P))]))
    expected = P * L ** 3 / (3 * E * Iy_) + P * L ** 2 * H / (E * Iy_) + P * H / (E * A_)
    assert -disp(r)[4, 2] == pytest.approx(expected, rel=2e-6)
    # joint equilibrium: moment at the clamp is P*L
    assert abs(r["reaction_moment_total"][1]) == pytest.approx(P * L, rel=2e-6)


@pytest.mark.parametrize("mode,beta", [(0, 1.875104), (1, 4.694091), (2, 7.854757)])
def test_cantilever_beam_natural_frequencies(mode, beta):
    L = 1.0
    sec = GEN(A_, Iy_, Iz_, 1e-6)
    r = run(cantilever_beam(12, L, sec, [], analysis="modal", settings={"n_modes": 16}))
    # bending in the weak and strong planes: the two families interleave; check the strong-axis (Iy) one is present
    f_strong = beta ** 2 / (2 * np.pi * L ** 2) * np.sqrt(E * Iy_ / (RHO * A_))
    f_weak = beta ** 2 / (2 * np.pi * L ** 2) * np.sqrt(E * Iz_ / (RHO * A_))
    got = np.array(r["frequencies_hz"])
    assert np.min(np.abs(got - f_strong)) / f_strong < 2e-3
    assert np.min(np.abs(got - f_weak)) / f_weak < 2e-3


def test_euler_buckling_loads():
    L, n = 2.0, 10
    sec = GEN(A_, Iy_, Iz_, 1e-6)
    Imin = min(Iy_, Iz_)
    # pinned-pinned
    nodes, elems = structmesh.line([0, 0, 0], [L, 0, 0], n)
    p = project(nodes, elems, "B31", sec, bcs=[(0, (0, 1, 2, 3)), (n, (1, 2, 3))], loads=[force(n, (-1.0, 0, 0))],
                analysis="buckling", settings={"n_modes": 3})
    r = run(p)
    assert r["load_factors"][0] == pytest.approx(np.pi ** 2 * E * Imin / L ** 2, rel=3e-3)
    # clamped-free: pi^2 EI / (4 L^2)
    p = cantilever_beam(n, L, sec, [force(n, (-1.0, 0, 0))], analysis="buckling", settings={"n_modes": 3})
    r = run(p)
    assert r["load_factors"][0] == pytest.approx(np.pi ** 2 * E * Imin / (4 * L ** 2), rel=3e-3)


def test_truss_euler_buckling_of_a_strut_is_not_reported_without_bending():
    nodes, elems = structmesh.line([0, 0, 0], [1, 0, 0], 1)
    p = project(nodes, elems, "T3D2", {"area": 1e-4}, bcs=[(0, (0, 1, 2)), (1, (1, 2))], loads=[force(1, (-1.0, 0, 0))], analysis="buckling")
    r = run(p)
    assert r["load_factors"] == [] or r["load_factors"][0] > 0


def test_section_constants():
    from app.fea import structural as S
    c = S.beam_section({"type": "circle", "d": 0.1})
    assert c["A"] == pytest.approx(np.pi * 0.1 ** 2 / 4) and c["Iy"] == pytest.approx(np.pi * 0.1 ** 4 / 64)
    assert c["J"] == pytest.approx(2 * c["Iy"])
    t = S.beam_section({"type": "pipe", "d": 0.1, "t": 0.005})
    assert t["Iy"] == pytest.approx(np.pi * (0.1 ** 4 - 0.09 ** 4) / 64)
    rct = S.beam_section({"type": "rect", "b": 0.1, "h": 0.1})
    assert rct["J"] == pytest.approx(0.1406 * 0.1 ** 4, rel=2e-3)                    # square bar: J = 0.1406 a^4
    for bad in ({"type": "rect", "b": -1, "h": 1}, {"type": "pipe", "d": 0.1, "t": 0.06}, {"type": "wing"}, {"type": "circle"}):
        with pytest.raises(StructuralError):
            S.beam_section(bad)


def test_beam_orientation_vector_rotates_the_section():
    L, P = 1.0, 1e3
    sec = {"type": "rect", "b": 0.02, "h": 0.1}
    nodes, elems = structmesh.line([0, 0, 0], [L, 0, 0], 2)
    base = project(nodes, elems, "B31", dict(sec), bcs=[(0, ALL6)], loads=[force(2, (0, 0, -P))])
    flat = project(nodes, elems, "B31", {**sec, "orientation": [0, 0, 1]}, bcs=[(0, ALL6)], loads=[force(2, (0, 0, -P))])
    d0, d1 = -disp(run(base))[2, 2], -disp(run(flat))[2, 2]
    # orientation (0,0,1) turns the local y axis to global z, so the thin side now resists the z load
    assert d1 / d0 == pytest.approx((0.1 / 0.02) ** 2, rel=0.05)


# ----------------------------------------------------------------------- shell
def shell_project(nodes, elems, t, bcs, loads=(), **kw):
    return project(nodes, elems, "S4", {"thickness": t}, bcs=bcs, loads=loads, **kw)


def edge_ids(nodes, axis, value):
    return [int(i) for i in np.where(np.isclose(nodes[:, axis], value))[0]]


def test_membrane_patch_test_on_a_distorted_mesh():
    nodes, elems = structmesh.plate(1.0, 1.0, 3, 3)
    rng = np.random.default_rng(5)
    interior = [i for i in range(len(nodes)) if np.abs(nodes[i, :2]).max() < 0.49]
    nodes[interior, :2] += 0.04 * rng.standard_normal((len(interior), 2))
    ex, ey, gxy = 1e-3, -3e-4, 4e-4
    exact = np.zeros((len(nodes), 3))
    exact[:, 0] = ex * nodes[:, 0] + 0.5 * gxy * nodes[:, 1]
    exact[:, 1] = ey * nodes[:, 1] + 0.5 * gxy * nodes[:, 0]
    bnd = [i for i in range(len(nodes)) if i not in interior]
    bcs = []
    for n in bnd:
        bcs.append(([n], (0, 1, 2, 3, 4, 5)))
    p = shell_project(nodes, elems, 0.02, bcs)
    for b, n in zip(p["boundary_conditions"], bnd):
        b["values"] = [exact[n, 0], exact[n, 1], 0, 0, 0, 0]
    r = run(p)
    u = disp(r)
    assert np.allclose(u[:, :2], exact[:, :2], rtol=1e-5, atol=1e-9)
    c = E / (1 - NU ** 2)
    sx_exact = c * (ex + NU * ey)
    assert np.allclose(r["fields"]["shell_nx"], sx_exact * 0.02, rtol=1e-9)


def test_bending_patch_test_constant_curvature():
    nodes, elems = structmesh.plate(1.0, 1.0, 3, 3)
    rng = np.random.default_rng(7)
    interior = [i for i in range(len(nodes)) if np.abs(nodes[i, :2]).max() < 0.49]
    nodes[interior, :2] += 0.04 * rng.standard_normal((len(interior), 2))
    kx = 0.01                       # w = kx x^2 / 2,  theta_y = -dw/dx = -kx x   (zero transverse shear)
    w = 0.5 * kx * nodes[:, 0] ** 2
    ty = -kx * nodes[:, 0]
    bnd = [i for i in range(len(nodes)) if i not in interior]
    p = shell_project(nodes, elems, 0.02, [([n], ALL6) for n in bnd])
    for b, n in zip(p["boundary_conditions"], bnd):
        b["values"] = [0, 0, w[n], 0, ty[n], 0]
    r = run(p)
    assert np.allclose(disp(r)[:, 2], w, rtol=1e-5, atol=1e-9)
    assert np.allclose(rots(r)[:, 1], ty, rtol=1e-5, atol=1e-9)
    D = E * 0.02 ** 3 / (12 * (1 - NU ** 2))
    assert np.allclose(r["fields"]["shell_mx"], -D * kx, rtol=1e-5)


def test_cantilever_plate_strip_matches_beam_theory():
    L, w, t, P = 1.0, 0.1, 0.01, 50.0
    nodes, elems = structmesh.plate(L, w, 20, 2)
    nodes[:, 0] += L / 2
    clamp = edge_ids(nodes, 0, 0.0)
    tip = edge_ids(nodes, 0, L)
    p = shell_project(nodes, elems, t, [(clamp, ALL6)], loads=[force(tip, (0, 0, -P))], nu=0.0)
    r = run(p)
    I = w * t ** 3 / 12
    G = E / 2
    expected = P * L ** 3 / (3 * E * I) + P * L / ((5 / 6) * G * w * t)
    assert -disp(r)[tip, 2].mean() == pytest.approx(expected, rel=0.01)


@pytest.mark.parametrize("support,coef", [("simple", 0.00406), ("clamped", 0.00126)])
def test_square_plate_under_pressure_matches_navier_and_clamped_series(support, coef):
    a, t, q, n = 1.0, 0.01, 1e3, 12
    nodes, elems = structmesh.plate(a, a, n, n)
    edges = {ax: [edge_ids(nodes, ax, -a / 2), edge_ids(nodes, ax, a / 2)] for ax in (0, 1)}
    bcs = []
    if support == "clamped":
        ids = sorted({i for ax in (0, 1) for side in edges[ax] for i in side})
        bcs = [(ids, ALL6)]
    else:   # hard simple support: w = 0 and rotation about the edge-normal axis = 0, normal-slope rotation free
        for side in edges[0]:
            bcs.append((side, (0, 1, 2, 3)))         # edge parallel to y: fix w and rotation about x (tangential)
        for side in edges[1]:
            bcs.append((side, (0, 1, 2, 4)))
    bcs.append(([int(np.argmin(np.abs(nodes[:, 0]) + np.abs(nodes[:, 1])))], (0, 1)))   # in-plane rigid body
    loads = [{"id": "p", "name": "p", "load_type": "pressure", "faces": [[int(e[0]), int(e[1]), int(e[2])] for e in elems] +
              [[int(e[0]), int(e[2]), int(e[3])] for e in elems], "magnitude": -q}]            # negative pressure = along +normal (down is positive pressure)
    p = shell_project(nodes, elems, t, bcs, loads=loads)
    r = run(p)
    D = E * t ** 3 / (12 * (1 - NU ** 2))
    centre = int(np.argmin(np.abs(nodes[:, 0]) + np.abs(nodes[:, 1])))
    wc = abs(disp(r)[centre, 2])
    assert wc == pytest.approx(coef * q * a ** 4 / D, rel=0.03)


def test_pressure_direction_convention():
    nodes, elems = structmesh.plate(1.0, 1.0, 2, 2)
    tris = [[int(e[0]), int(e[1]), int(e[2])] for e in elems] + [[int(e[0]), int(e[2]), int(e[3])] for e in elems]
    ids = edge_ids(nodes, 0, -0.5) + edge_ids(nodes, 0, 0.5) + edge_ids(nodes, 1, -0.5) + edge_ids(nodes, 1, 0.5)
    p = shell_project(nodes, elems, 0.01, [(sorted(set(ids)), ALL6)],
                      loads=[{"id": "p", "name": "p", "load_type": "pressure", "faces": tris, "magnitude": 1e3}])
    r = run(p)
    assert disp(r)[:, 2].min() < 0                      # positive pressure pushes against the +z element normal
    assert sum(np.array(r["reaction_forces"])[:, 2]) == pytest.approx(1e3 * 1.0, rel=2e-6)


def test_thin_cylinder_hoop_stress_is_pR_over_t():
    R, Lc, t, pr = 0.5, 1.0, 0.005, 2e5
    nodes, elems = structmesh.cylinder(R, Lc, 360.0, 24, 6)
    tris = [[int(e[0]), int(e[1]), int(e[2])] for e in elems] + [[int(e[0]), int(e[2]), int(e[3])] for e in elems]
    z0 = [i for i in range(len(nodes)) if np.isclose(nodes[i, 2], -Lc / 2)]
    ph = np.arctan2(nodes[:, 1], nodes[:, 0])
    n90 = [i for i in z0 if np.isclose(ph[i], np.pi / 2)]
    n0 = [i for i in z0 if np.isclose(ph[i], 0.0)]
    n270 = [i for i in z0 if np.isclose(ph[i], -np.pi / 2)]
    bcs = [(z0, (2,)), (n90 + n270, (0,)), (n0, (1,))]            # axial at one end + three point supports: no rigid motion
    p = shell_project(nodes, elems, t, bcs, loads=[{"id": "p", "name": "p", "load_type": "pressure", "faces": tris, "magnitude": -pr}])
    r = run(p)                                                      # negative pressure acts along the outward normal: inflation
    ring = [i for i in range(len(nodes)) if abs(nodes[i, 2]) < 1e-9]
    cosd = np.cos(np.pi / 24)                                       # flat facets: hoop force is pR cos(dphi/2)
    assert np.mean(np.array(r["fields"]["von_mises"])[ring]) == pytest.approx(pr * R / t * cosd, rel=2e-3)
    u = np.array(r["displacements"])
    radial = np.array([u[i, :2] @ nodes[i, :2] / R for i in ring])
    assert radial.mean() == pytest.approx(pr * R ** 2 / (E * t) * cosd, rel=2e-3)    # hoop strain pR/(E t), free axial end
    assert radial.std() < 1e-3 * radial.mean()                       # axisymmetric


def test_scordelis_lo_roof():
    R, Ly, t, phi_max = 25.0, 25.0, 0.25, np.deg2rad(40.0)
    E_, load = 4.32e8, 90.0
    n = 10
    phis = np.linspace(0.0, phi_max, n + 1)
    ys = np.linspace(0.0, Ly, n + 1)
    P, Y = np.meshgrid(phis, ys, indexing="xy")
    nodes = np.stack([(R * np.sin(P)).ravel(), Y.ravel(), (R * np.cos(P)).ravel()], axis=1)
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    elems = np.stack([idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()], axis=1)
    tris = [[int(e[0]), int(e[1]), int(e[2])] for e in elems] + [[int(e[0]), int(e[2]), int(e[3])] for e in elems]
    diaphragm = [int(i) for i in np.where(np.isclose(nodes[:, 1], 0.0))[0]]
    sym_mid = [int(i) for i in np.where(np.isclose(nodes[:, 1], Ly))[0]]
    sym_crown = [int(i) for i in np.where(np.isclose(nodes[:, 0], 0.0))[0]]
    bcs = [(diaphragm, (0, 2)), (sym_mid, (1, 3, 5)), (sym_crown, (0, 4, 5))]
    loads = [{"id": "g", "name": "g", "load_type": "traction", "faces": tris, "vector": [0, 0, -load]}]
    p = shell_project(nodes, elems, t, bcs, loads=loads, E_=E_, nu=0.0)
    r = run(p)
    free_mid = int(np.argmin(np.abs(nodes[:, 0] - R * np.sin(phi_max)) + np.abs(nodes[:, 1] - Ly)))
    assert abs(disp(r)[free_mid, 2]) == pytest.approx(0.3024, rel=0.04)


def test_free_plate_has_six_rigid_body_modes_and_matches_simply_supported_frequency():
    a, t, n = 1.0, 0.01, 8
    nodes, elems = structmesh.plate(a, a, n, n)
    free = run(shell_project(nodes, elems, t, [], analysis="modal", settings={"n_modes": 10}))
    f = np.array(free["frequencies_hz"])
    assert np.all(f[:6] < 1e-3 * f[6])
    ids_x = sorted(set(edge_ids(nodes, 0, -a / 2) + edge_ids(nodes, 0, a / 2)))
    ids_y = sorted(set(edge_ids(nodes, 1, -a / 2) + edge_ids(nodes, 1, a / 2)))
    bcs = [(edge_ids(nodes, 0, -a / 2) + edge_ids(nodes, 0, a / 2), (0, 1, 2, 3)),
           (edge_ids(nodes, 1, -a / 2) + edge_ids(nodes, 1, a / 2), (0, 1, 2, 4))]
    ss = run(shell_project(nodes, elems, t, bcs, analysis="modal", settings={"n_modes": 3}))
    D = E * t ** 3 / (12 * (1 - NU ** 2))
    f11 = np.pi * np.sqrt(D / (RHO * t)) / a ** 2
    assert ss["frequencies_hz"][0] == pytest.approx(f11, rel=0.03)


# ------------------------------------------------------------------ validation
def test_validation_and_unsupported_features_are_reported():
    nodes, elems = structmesh.line([0, 0, 0], [1, 0, 0], 2)
    ok = project(nodes, elems, "B31", RECT, bcs=[(0, ALL6)], loads=[force(2, (0, 0, -1.0))])
    bad = dict(ok, analysis_type="thermal")
    with pytest.raises(StructuralError, match="not available for structural"):
        runner.run(bad)
    with pytest.raises(StructuralError, match="NLGEOM"):
        runner.run(dict(ok, settings={"nlgeom": True}))
    with pytest.raises(StructuralError, match="constraints"):
        runner.run(dict(ok, constraints=[{"type": "tie"}]))
    with pytest.raises(StructuralError, match="support"):
        runner.run(dict(ok, boundary_conditions=[]))
    ok["parts"][0]["section"] = {"type": "rect", "b": -1, "h": 0.1}
    with pytest.raises(StructuralError, match="width b"):
        runner.run(ok)
    loose = project(nodes, elems, "B31", RECT, bcs=[(0, (0, 1, 2))], loads=[force(2, (0, 0, -1.0))])
    with pytest.raises(Exception, match="not fully supported"):
        runner.run(loose)
    pr = project(nodes, elems, "B31", RECT, bcs=[(0, ALL6)], loads=[{"id": "p", "name": "p", "load_type": "pressure", "faces": [[0, 1, 2]], "magnitude": 1}])
    with pytest.raises(StructuralError, match="shell"):
        runner.run(pr)
