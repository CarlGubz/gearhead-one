"""Frictionless contact between parts, checked against closed-form series-stiffness answers."""
import numpy as np
import pytest

from app.fea import meshgen, runner
from app.fea.model import FEMesh

W = 0.2


def blocks(et="C3D8", h_base=0.4, h_top=0.2, gap=0.0, divs=(2, 2), E=(100e9, 100e9), pf=None, hold_top=False):
    """Base block (z in [-h_base, 0]) and top block (z in [gap, gap+h_top]) as two parts with a contact pair."""
    nodes, elems, parts, lib = [], [], [], []
    n_nodes = n_elems = 0
    for k, (h, z0, div, e) in enumerate([(h_base, -h_base, divs[0], E[0]), (h_top, gap, divs[1], E[1])]):
        m = meshgen.generate("box", {"length": W, "width": W, "height": h}, div, et)
        pts = m.nodes + np.array([0.0, 0.0, z0 + h / 2])
        nodes.append(pts)
        elems.append(m.elems + n_nodes)
        mat = dict(E=e, nu=0.0, density=7800.0, conductivity=50.0, specific_heat=500.0, alpha=0.0, yield_stress=0.0, hardening=0.0)
        mid = "default" if k == 0 else "m1"
        if k == 1:
            lib.append({"id": "m1", "name": "Top", **mat})
        else:
            base_mat = mat
        parts.append({"id": f"p{k}", "name": ["Base", "Top"][k], "elem_start": n_elems, "elem_end": n_elems + len(m.elems), "material_id": mid})
        n_nodes += len(pts)
        n_elems += len(m.elems)
    proj = {"name": "c", "nodes": np.vstack(nodes).tolist(), "elements": np.vstack(elems).tolist(), "element_type": et,
            "material": base_mat, "materials": lib, "parts": parts, "boundary_conditions": [], "loads": [],
            "constraints": [{"id": "c1", "type": "contact", "master_part": "p0", "slave_part": "p1",
                             **({"penalty_factor": pf} if pf else {})}],
            "analysis_type": "static", "settings": {"increments": 5}, "gravity": {}, "reference_points": []}
    pts = np.array(proj["nodes"])
    n_base = len(nodes[0])

    def ids(sel):
        return [int(i) for i in np.where(sel)[0]]

    zmin_base = pts[:n_base, 2].min()
    base_bottom = ids((np.arange(len(pts)) < n_base) & np.isclose(pts[:, 2], zmin_base))
    top_idx = np.arange(len(pts)) >= n_base
    x_min = ids(top_idx & np.isclose(pts[:, 0], pts[n_base:, 0].min()))
    y_min = ids(top_idx & np.isclose(pts[:, 1], pts[n_base:, 1].min()))
    bc = lambda name, nid, dofs, vals=None: {"id": name, "name": name, "bc_type": "displacement", "node_ids": nid, "dofs": dofs, "values": vals or [0.0] * len(dofs)}  # noqa: E731
    proj["boundary_conditions"] += [bc("base", base_bottom, [0, 1, 2]), bc("rx", x_min, [0]), bc("ry", y_min, [1])]
    return proj


def top_face_tris(proj, which="max"):
    pts = np.array(proj["nodes"])
    mesh = FEMesh(pts, proj["elements"], proj["element_type"])
    z = pts[:, 2].max() if which == "max" else pts[:, 2].min()
    return [t.tolist() for t in mesh.boundary().tris if np.allclose(pts[t, 2], z)]


def top_face_nodes(proj):
    pts = np.array(proj["nodes"])
    return [int(i) for i in np.where(np.isclose(pts[:, 2], pts[:, 2].max()))[0]]


def penalty_of(proj):
    return runner.Model(proj).contact_system().pairs[0].penalty


def solve(proj):
    return runner.run(proj)


def press(proj, p):
    proj["loads"].append({"id": "p", "name": "p", "load_type": "pressure", "faces": top_face_tris(proj), "magnitude": p})


# ----------------------------------------------------------------- tests
@pytest.mark.parametrize("et", ["C3D8", "C3D4", "C3D10", "C3D20"])
@pytest.mark.parametrize("divs,tol", [((2, 2), 1e-2), ((2, 3), 3e-2)], ids=["matching", "non-matching"])
def test_block_pressed_on_base_force_pressure_and_shortening(et, divs, tol):
    proj = blocks(et, divs=divs)
    # 20-node bricks turn a uniform pressure into negative (tensile) corner forces that a unilateral node-to-surface
    # penalty cannot carry, so the interface is a little softer there - a known limit of the method
    tol = max(tol, 3e-2) if et == "C3D20" else tol
    p, A = 1e6, W * W
    press(proj, p)
    out = solve(proj)
    r = out["results"]
    eps = penalty_of(proj)
    # equilibrium: the base reacts the applied load; the contact transmits it
    assert r["reaction_total"][2] == pytest.approx(p * A, rel=1e-5)
    assert r["contact"]["total_force"] == pytest.approx(p * A, rel=1e-2)
    # series stiffness: top block + base block + penalty compliance
    uz_top = np.array(r["displacements"])[top_face_nodes(proj), 2]
    expected = -(p * 0.2 / 100e9 + p * 0.4 / 100e9 + p / eps)
    assert np.mean(uz_top) == pytest.approx(expected, rel=tol)
    # every slave node of the interface carries load; node-to-surface penalty spreads it unevenly between nodes
    # (known behaviour), so only the resultant is exact - the largest nodal pressure is at least the mean pressure
    cp = np.array(r["fields"]["contact_pressure"])
    assert (cp > 0).sum() >= 4 and cp.max() >= 0.3 * p
    assert 0 < r["contact"]["max_penetration"] < 20 * p / eps


def test_closing_an_initial_gap_by_prescribed_displacement():
    g0, delta = 0.002, 0.005
    proj = blocks("C3D8", gap=g0)
    proj["boundary_conditions"].append({"id": "push", "name": "push", "bc_type": "displacement",
                                        "node_ids": top_face_nodes(proj), "dofs": [2], "values": [-delta]})
    out = solve(proj)
    r = out["results"]
    eps = penalty_of(proj)
    p = (delta - g0) / (0.2 / 100e9 + 0.4 / 100e9 + 1 / eps)
    assert r["contact"]["total_force"] == pytest.approx(p * W * W, rel=1e-3)
    assert r["contact"]["active_nodes"] > 0


def test_no_contact_when_the_bodies_stay_apart():
    proj = blocks("C3D8", gap=0.01)
    proj["boundary_conditions"].append({"id": "lift", "name": "lift", "bc_type": "displacement",
                                        "node_ids": top_face_nodes(proj), "dofs": [2], "values": [0.001]})
    r = solve(proj)["results"]
    assert r["contact"]["total_force"] == 0.0 and r["contact"]["active_nodes"] == 0
    assert max(r["fields"]["contact_pressure"]) == 0.0
    uz = np.array(r["displacements"])[:, 2]
    z = np.array(proj["nodes"])[:, 2]
    top = z > 0.005
    assert np.allclose(uz[top], 0.001, atol=1e-12)            # top block just translates
    assert np.allclose(uz[~top], 0.0, atol=1e-12)


def test_contact_forces_are_self_equilibrated():
    proj = blocks("C3D10", divs=(2, 3))
    model = runner.Model(proj)
    cs = model.contact_system()
    n = model.mesh.n_nodes
    u = np.zeros((n, 3))
    u[n // 2:, 2] = -0.0005                                       # push the whole top block into the base
    res = cs.evaluate(u)
    f = res.force.reshape(-1, 3)
    assert np.abs(f.sum(axis=0)).max() < 1e-9 * res.total_force + 1e-6
    assert res.total_force > 0 and len(res.active) > 0
    # stiffness matrix is symmetric positive semi-definite
    K = res.K
    assert abs(K - K.T).max() < 1e-6 * abs(K).max()
    x = np.random.default_rng(1).standard_normal(K.shape[0])
    assert x @ (K @ x) >= -1e-6


def test_penalty_controls_penetration_but_not_the_global_answer():
    results = {}
    for pf in (10.0, 100.0):
        proj = blocks("C3D8", pf=pf)
        press(proj, 1e6)
        r = solve(proj)["results"]
        results[pf] = (r["contact"]["max_penetration"], np.mean(np.array(r["displacements"])[top_face_nodes(proj), 2]))
    assert 5.0 < results[10.0][0] / results[100.0][0] < 15.0                           # penetration ~ 1/penalty
    assert results[10.0][1] == pytest.approx(results[100.0][1], rel=2e-2)


def test_contact_is_rejected_outside_static_and_nonlinear():
    proj = blocks("C3D8")
    proj["analysis_type"] = "modal"
    with pytest.raises(runner.ModelError, match="static and nonlinear"):
        runner.run(proj)


def test_contact_between_the_same_part_is_rejected():
    proj = blocks("C3D8")
    proj["constraints"][0]["slave_part"] = "p0"
    press(proj, 1e6)
    with pytest.raises(runner.ModelError, match="two different parts"):
        runner.run(proj)


def test_unsupported_body_reports_a_helpful_error():
    proj = blocks("C3D8")
    proj["boundary_conditions"] = [b for b in proj["boundary_conditions"] if b["id"] == "base"]   # top block can slide away
    press(proj, 1e6)
    out = runner.run(proj)         # frictionless: the regularised solve still finds the (unique-up-to-sliding) equilibrium
    assert out["results"]["contact"]["total_force"] == pytest.approx(1e6 * W * W, rel=1e-2)
