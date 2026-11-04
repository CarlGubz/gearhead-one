"""Multiple parts and per-part materials: closed-form checks through the project runner and the engine."""
import numpy as np
import pytest

from app.fea import analyses as A
from app.fea import assembly as asm
from app.fea import meshgen, runner
from app.fea.constraints import ConstraintSystem
from app.fea.materials import Material, MaterialMap
from app.fea.model import FEMesh

W = 0.2  # cross-section edge


def mat_dict(E=100e9, nu=0.0, rho=7800.0, k=50.0, c=500.0, alpha=0.0, sy=0.0, H=0.0, **kw):
    return dict(E=E, nu=nu, density=rho, conductivity=k, specific_heat=c, alpha=alpha, yield_stress=sy, hardening=H, **kw)


def stack(specs, et="C3D8"):
    """
    Bars along x, each (length, material dict, divisions), placed end to end starting at x = 0.
    Returns a project dict (separate, disconnected meshes = separate parts) ready for runner.run().
    """
    nodes, elems, parts, lib = [], [], [], []
    x0, n_nodes, n_elems = 0.0, 0, 0
    for k, (L, md, div) in enumerate(specs):
        m = meshgen.generate("box", {"length": L, "width": W, "height": W}, div, et)
        pts = m.nodes + np.array([x0 + L / 2, 0, 0])
        nodes.append(pts)
        elems.append(m.elems + n_nodes)
        mid = "default" if k == 0 else f"m{k}"
        if k > 0:
            lib.append({"id": mid, "name": f"Mat{k}", **md})
        parts.append({"id": f"p{k}", "name": f"Part{k}", "elem_start": n_elems, "elem_end": n_elems + len(m.elems), "material_id": mid})
        n_nodes += len(pts)
        n_elems += len(m.elems)
        x0 += L
    proj = {
        "name": "t", "nodes": np.vstack(nodes).tolist(), "elements": np.vstack(elems).tolist(), "element_type": et,
        "material": specs[0][1], "materials": lib, "parts": parts, "boundary_conditions": [], "loads": [],
        "constraints": [], "analysis_type": "static", "settings": {}, "gravity": {}, "reference_points": [],
    }
    return proj


def ids_at(proj, axis, which):
    pts = np.array(proj["nodes"])
    v = pts[:, axis].min() if which == "min" else pts[:, axis].max()
    return [int(i) for i in np.where(np.isclose(pts[:, axis], v))[0]]


def tris_at(proj, axis, which):
    pts = np.array(proj["nodes"])
    v = pts[:, axis].min() if which == "min" else pts[:, axis].max()
    mesh = FEMesh(pts, proj["elements"], proj["element_type"])
    return [t.tolist() for t in mesh.boundary().tris if np.allclose(pts[t, axis], v)]


def rollers(proj):
    for ax in range(3):
        proj["boundary_conditions"].append(
            {"id": f"r{ax}", "name": f"roll{ax}", "bc_type": "displacement", "node_ids": ids_at(proj, ax, "min"), "dofs": [ax], "values": [0.0]})


def tie_parts(proj, master="p0", slave="p1", tol=None):
    c = {"id": "tie", "type": "tie", "master_part": master, "slave_part": slave}
    if tol:
        c["tolerance"] = tol
    proj["constraints"].append(c)


def solve(proj):
    return runner.run(proj)["results"]


# ------------------------------------------------------------ mechanics
@pytest.mark.parametrize("et", ["C3D4", "C3D10", "C3D8", "C3D20"])
@pytest.mark.parametrize("divs", [(2, 2), (2, 3)], ids=["matching", "non-matching"])
def test_series_bars_with_different_modulus_are_exact(et, divs):
    L1, L2, E1, E2, sigma = 1.0, 0.5, 100e9, 40e9, 1e6
    proj = stack([(L1, mat_dict(E=E1), divs[0]), (L2, mat_dict(E=E2), divs[1])], et)
    rollers(proj)
    tie_parts(proj)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -sigma})
    r = solve(proj)
    # Matching meshes: the patch test passes exactly. Non-matching: a one-way node-to-surface tie is only
    # first-order accurate (the master shape function is not reproduced by the slave mesh), so allow 1%.
    tol = 1e-7 if divs[0] == divs[1] else 1e-2
    assert max(r["fields"]["ux"]) == pytest.approx(sigma * (L1 / E1 + L2 / E2), rel=tol)
    vm = np.array(r["fields"]["von_mises"])
    assert np.median(np.abs(vm / sigma - 1)) < max(1e-6, 20 * tol)
    assert r["reaction_total"][0] == pytest.approx(-sigma * W * W, rel=1e-7)


def test_assigning_a_different_material_changes_the_answer():
    proj = stack([(1.0, mat_dict(E=100e9), 2), (0.5, mat_dict(E=100e9), 2)])
    rollers(proj)
    tie_parts(proj)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -1e6})
    a = max(solve(proj)["fields"]["ux"])
    proj["materials"][0]["E"] = 10e9  # soften only the second part
    b = max(solve(proj)["fields"]["ux"])
    assert a == pytest.approx(1e6 * 1.5 / 100e9, rel=1e-7)
    assert b == pytest.approx(1e6 * (1.0 / 100e9 + 0.5 / 10e9), rel=1e-7)


def test_tie_between_parts_with_a_gap_is_rejected_with_advice():
    proj = stack([(1.0, mat_dict(), 2), (0.5, mat_dict(), 2)])
    pts = np.array(proj["nodes"])
    p1 = proj["parts"][1]
    nodes_p1 = np.unique(np.array(proj["elements"])[p1["elem_start"]:p1["elem_end"]])
    pts[nodes_p1, 0] += 0.05  # open a 5 cm gap
    proj["nodes"] = pts.tolist()
    rollers(proj)
    tie_parts(proj)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -1e6})
    with pytest.raises(runner.ModelError, match="No slave node"):
        runner.run(proj)


def test_part_tie_tolerance_closes_small_gaps():
    proj = stack([(1.0, mat_dict(), 2), (0.5, mat_dict(), 2)])
    pts = np.array(proj["nodes"])
    p1 = proj["parts"][1]
    nodes_p1 = np.unique(np.array(proj["elements"])[p1["elem_start"]:p1["elem_end"]])
    pts[nodes_p1, 0] += 0.001
    proj["nodes"] = pts.tolist()
    rollers(proj)
    tie_parts(proj, tol=0.01)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -1e6})
    r = solve(proj)
    assert r["max_displacement"] > 0


def test_untied_parts_are_a_rigid_body_error_not_a_crash():
    proj = stack([(1.0, mat_dict(), 2), (0.5, mat_dict(), 2)])
    rollers(proj)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -1e6})
    with pytest.raises(A.SolverError):
        runner.run(proj)  # second part floats free: singular


# ----------------------------------------------------------------- thermal
@pytest.mark.parametrize("divs,tol", [((3, 3), 1e-6), ((2, 3), 0.02)], ids=["matching", "non-matching"])
def test_series_conduction_across_tied_parts(divs, tol):
    L1, L2, k1, k2 = 1.0, 0.5, 100.0, 20.0
    proj = stack([(L1, mat_dict(k=k1), divs[0]), (L2, mat_dict(k=k2), divs[1])], "C3D10")
    proj["analysis_type"] = "thermal"
    proj["boundary_conditions"] = [
        {"id": "a", "name": "hot", "bc_type": "temperature", "node_ids": ids_at(proj, 0, "min"), "dofs": [0], "values": [100.0]},
        {"id": "b", "name": "cold", "bc_type": "temperature", "node_ids": ids_at(proj, 0, "max"), "dofs": [0], "values": [0.0]},
    ]
    tie_parts(proj)
    r = solve(proj)
    q = 100.0 / (L1 / k1 + L2 / k2)
    T = np.array(r["temperatures"])
    x = np.array(proj["nodes"])[:, 0]
    iface = np.isclose(x, L1)
    assert np.allclose(T[iface], 100.0 - q * L1 / k1, atol=100 * tol)
    assert np.median(r["fields"]["flux_magnitude"]) == pytest.approx(q, rel=max(1e-3, 3 * tol))  # median: ties spike locally when non-matching


def test_transient_conduction_with_tied_parts_matches_steady_limit():
    proj = stack([(0.1, mat_dict(k=50.0), 2), (0.1, mat_dict(k=10.0), 2)], "C3D8")
    proj["analysis_type"] = "thermal_transient"
    proj["settings"] = {"t_end": 5e5, "n_steps": 50, "initial_temperature": 0.0, "theta": 1.0}
    proj["boundary_conditions"] = [
        {"id": "a", "name": "hot", "bc_type": "temperature", "node_ids": ids_at(proj, 0, "min"), "dofs": [0], "values": [100.0]},
        {"id": "b", "name": "cold", "bc_type": "temperature", "node_ids": ids_at(proj, 0, "max"), "dofs": [0], "values": [0.0]},
    ]
    tie_parts(proj)
    r = solve(proj)
    x = np.array(proj["nodes"])[:, 0]
    q = 100.0 / (0.1 / 50 + 0.1 / 10)
    assert np.array(r["temperatures"])[np.isclose(x, 0.1)].mean() == pytest.approx(100 - q * 0.1 / 50, rel=2e-3)


# -------------------------------------------------- engine-level property tests
def merged(specs, et="C3D8"):
    proj = stack(specs, et)
    mesh = FEMesh(proj["nodes"], proj["elements"], et)
    lib = {"default": proj["material"], **{m["id"]: m for m in proj["materials"]}}
    groups = [(Material.from_dict(lib[p["material_id"]]), np.arange(p["elem_start"], p["elem_end"])) for p in proj["parts"]]
    return proj, mesh, MaterialMap(mesh.n_elems, groups)


def test_mass_and_gravity_use_each_parts_density():
    proj, mesh, mats = merged([(1.0, mat_dict(rho=7800.0), 2), (0.5, mat_dict(rho=2700.0), 2)], "C3D10")
    expected = (7800.0 * 1.0 + 2700.0 * 0.5) * W * W
    M = asm.assemble_mass(mesh, mats.prop("density"))
    ones_x = np.zeros(3 * mesh.n_nodes)
    ones_x[0::3] = 1.0
    assert ones_x @ (M @ ones_x) == pytest.approx(expected, rel=1e-9)
    f = asm.body_force_vector(mesh, lambda xg: np.broadcast_to([0.0, 0.0, -9.81], xg.shape).copy(), scale=mats.prop("density"))
    assert f[2::3].sum() == pytest.approx(-9.81 * expected, rel=1e-9)


def test_one_part_plastic_other_elastic_in_the_same_model():
    """
    Only the part with a yield stress yields. (J2 flow is volume preserving, so the yielding part wants to
    contract laterally while the elastic part does not: the real field is 3-D and not uniform, so we check
    bounds, localisation and equilibrium instead of a 1-D closed form.)
    """
    E, sy, H, sigma = 200e9, 250e6, 2e9, 300e6
    proj, mesh, mats = merged([(0.5, mat_dict(E=E, nu=0.0, sy=sy, H=H), 1), (0.5, mat_dict(E=E, nu=0.0), 1)], "C3D20")
    proj["constraints"] = [{"type": "tie", "master_part": "p0", "slave_part": "p1"}]
    rollers(proj)
    proj["analysis_type"] = "nonlinear"
    proj["settings"] = {"increments": 6}
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -sigma})
    r = solve(proj)
    eps_p = (sigma - sy) / H
    free_plastic_tip = 0.5 * (sigma / E + eps_p) + 0.5 * (sigma / E)   # upper bound (no lateral restraint)
    elastic_tip = sigma / E * 1.0
    tip = max(r["fields"]["ux"])
    assert elastic_tip < tip < free_plastic_tip
    pe = np.array(r["fields"]["plastic_strain"])
    x = np.array(proj["nodes"])[:, 0]
    assert pe[x < 0.49].max() > 0.5 * eps_p            # the plastic part yields
    assert abs(pe[x > 0.51]).max() == 0.0              # the elastic part never does
    assert r["reaction_total"][0] == pytest.approx(-sigma * W * W, rel=1e-6)  # global equilibrium


def test_nonlinear_analysis_with_only_elastic_materials_is_linear():
    el = mat_dict(E=200e9, nu=0.3)
    proj, mesh, mats = merged([(0.5, el, 1), (0.5, el, 1)], "C3D8")
    proj["constraints"] = [{"type": "tie", "master_part": "p0", "slave_part": "p1"}]
    rollers(proj)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -1e6})
    lin = solve(proj)
    proj["analysis_type"] = "nonlinear"
    nl = solve(proj)
    assert nl["max_displacement"] == pytest.approx(lin["max_displacement"], rel=1e-8)


def test_thermal_strain_uses_each_parts_expansion_coefficient():
    a1, a2, dT = 10e-6, 25e-6, 100.0
    proj, mesh, mats = merged([(1.0, mat_dict(alpha=a1), 2), (0.5, mat_dict(alpha=a2), 2)], "C3D8")
    cs = ConstraintSystem(mesh)
    fixed = {}
    for k, sl in enumerate([slice(0, mesh.n_nodes // 2 + 0), slice(mesh.n_nodes // 2, None)]):
        pass
    # each body is held by symmetry planes at its own minimum faces (they are not connected)
    n1 = len(np.unique(np.array(proj["elements"])[proj["parts"][0]["elem_start"]:proj["parts"][0]["elem_end"]]))
    for body, (lo_i, hi_i) in enumerate([(0, n1), (n1, mesh.n_nodes)]):
        pts = mesh.nodes[lo_i:hi_i]
        for ax in range(3):
            for i in np.where(np.isclose(pts[:, ax], pts[:, ax].min()))[0]:
                fixed[3 * (lo_i + int(i)) + ax] = 0.0
    prob = A.MechProblem(mesh, mats, cs, fixed, np.zeros(cs.n_full), np.full(mesh.n_nodes, dT), 0.0)
    out = A.static(prob)
    u = out.u.reshape(-1, 3)
    assert u[:n1, 0].max() - u[:n1, 0].min() == pytest.approx(a1 * dT * 1.0, rel=1e-8)
    assert u[n1:, 0].max() - u[n1:, 0].min() == pytest.approx(a2 * dT * 0.5, rel=1e-8)
    assert np.abs(out.stress).max() < 1e-3 * 100e9 * a2 * dT  # free expansion -> no stress


def test_equal_materials_split_into_groups_equal_the_uniform_model():
    mesh = meshgen.generate("box", {"length": 1.0, "width": W, "height": W}, 2, "C3D10")
    mat = Material.from_dict(mat_dict(E=210e9, nu=0.3))
    half = mesh.elems.shape[0] // 2
    split = MaterialMap(mesh.n_elems, [(mat, np.arange(half)), (Material.from_dict(mat_dict(E=210e9, nu=0.3)), np.arange(half, mesh.n_elems))])
    cs = ConstraintSystem(mesh)
    lo = mesh.nodes[:, 0].min()
    fixed = {3 * int(n) + k: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 0], lo)) for k in range(3)}
    f = asm.pressure_vector(mesh, mesh.faces_where(lambda p: np.isclose(p[:, 0], mesh.nodes[:, 0].max())), -1e6)
    one = A.static(A.MechProblem(mesh, mat, cs, fixed, f))
    two = A.static(A.MechProblem(mesh, split, cs, fixed, f))
    assert np.allclose(one.u, two.u, rtol=1e-10, atol=1e-18)
    assert np.allclose(one.stress, two.stress, rtol=1e-9, atol=1e-3)
    assert A.frequency(A.MechProblem(mesh, mat, cs, fixed, f), 3).values == pytest.approx(
        A.frequency(A.MechProblem(mesh, split, cs, fixed, f), 3).values, rel=1e-10)


def test_missing_or_bad_part_definitions_are_reported():
    proj = stack([(1.0, mat_dict(), 2)])
    proj["parts"][0]["elem_end"] -= 1
    with pytest.raises(runner.ModelError, match="do not cover"):
        runner.Model(proj)
    proj = stack([(1.0, mat_dict(), 2)])
    proj["parts"][0]["material_id"] = "ghost"
    with pytest.raises(runner.ModelError, match="no longer exists"):
        runner.Model(proj)


def test_legacy_project_without_parts_still_runs():
    proj = stack([(1.0, mat_dict(E=210e9, nu=0.3), 2)])
    proj.pop("parts")
    proj.pop("materials")
    rollers(proj)
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": tris_at(proj, 0, "max"), "magnitude": -1e6})
    r = solve(proj)
    assert max(r["fields"]["ux"]) == pytest.approx(1e6 * 1.0 / 210e9, rel=1e-7)
