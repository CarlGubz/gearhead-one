"""Verification of the N-D (3-D) native solver and of the Python fallbacks of FEniCSx / PyFR."""
from pathlib import Path

import numpy as np
import pytest

import app.cfd.solver.ns3d as ns3d
from app.cfd.schemas import (Boundaries, Boundary, CaseConfig, Domain, Initial, Material, MeshSettings, Models, Obstacle,
                         Probe, RunSettings)
from app.cfd.solver import post
from app.cfd.solver.backends.ac_fallback import AcFallbackBackend
from app.cfd.solver.backends.fem_fallback import FemFallbackBackend
from app.cfd.solver.grid3d import build_grid_nd, sample_nd
from app.cfd.solver.ns2d import FlowSolver
from app.cfd.solver.ns3d import FlowSolver3D
from app.cfd.solver.runner import Hooks, run_simulation

UNIT = dict(density=1, conductivity=1, specific_heat=1)


def cavity(dim="2d", n=16, nz=4, **bnd):
    return CaseConfig(dimension=dim, material=Material(viscosity=0.01, **UNIT), mesh=MeshSettings(nx=n, ny=n, nz=nz),
                      domain=Domain(depth=0.25), boundaries=Boundaries(top=Boundary(type="wall", wall_velocity=1.0), **bnd))


def test_nd_solver_in_2d_is_identical_to_the_2d_solver():
    case = cavity()
    case.obstacles = [Obstacle(id="o", shape="circle", x=0.4, y=0.4, radius=0.15)]
    a, b = FlowSolver3D(case), FlowSolver(case)
    for _ in range(20):
        ra, rb = a.step(), b.step()
    assert np.abs(a.U[0] - b.u).max() < 1e-13 and np.abs(a.U[1] - b.v).max() < 1e-13
    assert np.abs(a.p - b.p).max() < 1e-12
    assert ra.u == pytest.approx(rb.u, rel=1e-4)  # residual normalisation differs slightly


def test_3d_cavity_between_symmetry_planes_reproduces_the_2d_flow():
    c2, c3 = cavity(), cavity("3d", back=Boundary(type="symmetry"), front=Boundary(type="symmetry"))
    c2.run.fixed_dt = c3.run.fixed_dt = 0.01  # the explicit stability limit differs between 2-D and 3-D
    s2, s3 = FlowSolver(c2), FlowSolver3D(c3)
    for _ in range(150):
        s2.step()
        s3.step()
    u3 = s3.cell_fields()["u"]
    for k in range(4):
        assert np.abs(u3[:, :, k] - s2.cell_fields()["u"]).max() < 1e-10
    assert np.abs(s3.U[2]).max() < 1e-12


def test_square_duct_friction_factor():
    """Fully developed laminar flow in a square duct: Darcy f Re = 56.91 (Shah & London)."""
    case = CaseConfig(dimension="3d", material=Material(viscosity=0.02, **UNIT), domain=Domain(width=5, height=1, depth=1),
                      mesh=MeshSettings(nx=40, ny=10, nz=10),
                      boundaries=Boundaries(left=Boundary(type="inlet", velocity=1), right=Boundary(type="outlet")),
                      initial=Initial(velocity_x=1), run=RunSettings(tolerance=1e-6))
    r = run_simulation(case)
    assert r.converged
    p = r.solver.cell_fields()["p"]
    dx = 5 / 40
    i0, i1 = int(3 / dx), int(4.5 / dx)
    dpdx = (p[i0].mean() - p[i1].mean()) / ((i1 - i0) * dx)
    f_re = 2 * dpdx * (1 / 0.02)
    assert f_re == pytest.approx(56.91, rel=0.02)
    assert abs(r.solver.mass_imbalance()) < 1e-10


def test_spectral_pcg_pressure_solver_matches_sparse_lu(monkeypatch):
    case = CaseConfig(dimension="3d", material=Material(viscosity=0.05, **UNIT), domain=Domain(width=2, height=1, depth=1),
                      mesh=MeshSettings(nx=20, ny=10, nz=10),
                      obstacles=[Obstacle(id="s", shape="sphere", x=0.7, y=0.5, z=0.5, radius=0.2)],
                      boundaries=Boundaries(left=Boundary(type="inlet", velocity=1), right=Boundary(type="outlet")),
                      initial=Initial(velocity_x=1))
    direct = FlowSolver3D(case)
    monkeypatch.setattr(ns3d, "DIRECT_LIMIT", {2: 0, 3: 0})
    iterative = FlowSolver3D(case)
    assert direct.poisson_kind == "sparse LU" and "CG" in iterative.poisson_kind
    for _ in range(10):
        direct.step()
        iterative.step()
    assert np.abs(direct.U[0] - iterative.U[0]).max() < 1e-6
    assert np.abs(iterative.divergence(iterative.U)[iterative.F]).max() < 1e-6


def test_3d_sphere_loads_and_probes():
    case = CaseConfig(dimension="3d", material=Material(viscosity=0.02, **UNIT), domain=Domain(width=2, height=0.6, depth=0.6),
                      mesh=MeshSettings(nx=30, ny=9, nz=9),
                      obstacles=[Obstacle(id="sph", shape="sphere", x=0.6, y=0.3, z=0.3, radius=0.12)],
                      boundaries=Boundaries(left=Boundary(type="inlet", velocity=1), right=Boundary(type="outlet")),
                      initial=Initial(velocity_x=1), probes=[Probe(id="w", x=1.0, y=0.3)],
                      run=RunSettings(mode="transient", end_time=0.3, snapshot_count=3))
    seen = []
    r = run_simulation(case, Hooks(on_monitor=lambda it, t, res, v: seen.append((res, v))))
    assert r.status == "completed" and len(r.frames) >= 2
    res, vals = seen[-1]
    assert "w" in res and "w.w" in vals and "sph.Fz" in vals
    loads = r.summary["obstacles"]["sph"]
    assert loads["Fx"] > 0 and abs(loads["Fy"]) < 1e-6 and abs(loads["Fz"]) < 1e-6  # symmetric body
    assert r.summary["cells"] == 30 * 9 * 9


def test_3d_heated_block_energy_balance():
    case = CaseConfig(dimension="3d", material=Material(viscosity=0.02, density=1, conductivity=0.02, specific_heat=1),
                      domain=Domain(width=1.5, height=0.5, depth=0.5), mesh=MeshSettings(nx=24, ny=8, nz=8),
                      models=Models(energy=True),
                      obstacles=[Obstacle(id="b", shape="rectangle", x=0.4, y=0.25, z=0.25, width=0.2, height=0.2, depth=0.2,
                                          thermal="fixed", temperature=1.0)],
                      boundaries=Boundaries(left=Boundary(type="inlet", velocity=1, temperature=0.0), right=Boundary(type="outlet")),
                      initial=Initial(velocity_x=1, temperature=0.0), run=RunSettings(tolerance=1e-6))
    r = run_simulation(case)
    s = r.solver
    q = s.obstacle_loads()["b"]["Q"]
    area = s.g.dy * s.g.dz
    out = float((s.rho * s.cp * s.U[0][-1] * (s.T[-1] - 0.0)).sum() * area)
    assert r.converged and q > 0 and q == pytest.approx(out, rel=0.03)


def test_3d_slices_and_exports(tmp_path):
    case = CaseConfig(dimension="3d", material=Material(viscosity=0.05, **UNIT), mesh=MeshSettings(nx=8, ny=6, nz=4),
                      domain=Domain(width=2, height=1.5, depth=1),
                      boundaries=Boundaries(top=Boundary(type="wall", wall_velocity=1)), run=RunSettings(max_iterations=40))
    r = run_simulation(case)
    s = r.solver
    path = tmp_path / "r.npz"
    post.save_frames(path, s.result_meta("steady"), s.S, r.times, r.frames)
    res = post.load_result(path)
    for axis, shape in ((2, (8, 6)), (1, (8, 4)), (0, (6, 4))):
        pv = post.plane_view(res, -1, axis, 1)
        assert pv["fields"]["u"].shape == shape and pv["plane"]["axis"] == axis
    pay = post.fields_payload(res, -1, 1, 2)
    assert pay["axes"] == ["x", "z"] and pay["vectors"] == ["u", "w"] and pay["nx"] == 8 and pay["ny"] == 4
    line = post.probe_line(res, -1, (0, 0.5), (2, 0.5), 10, 2, 2)
    assert len(line["values"]["speed"]) == 10
    post.export_vtu(res, -1, tmp_path / "r.vtu")
    assert b"VTKFile" in (tmp_path / "r.vtu").read_bytes()
    import meshio

    m = meshio.read(tmp_path / "r.vtu")
    assert m.cells[0].type == "hexahedron" and len(m.cells[0].data) == 8 * 6 * 4
    assert post.export_csv(res, -1).startswith("x,y,z,solid")


# --------------------------------------------------------------------------- Python fallbacks


def test_fem_fallback_natural_convection_nusselt():
    alpha = (1e-4 / 0.71) ** 0.5
    case = CaseConfig(material=Material(density=1, viscosity=0.71 * alpha, conductivity=alpha, specific_heat=1, thermal_expansion=1),
                      mesh=MeshSettings(nx=24, ny=24), models=Models(energy=True, buoyancy=True, gravity_y=-1, reference_temperature=0.5),
                      boundaries=Boundaries(left=Boundary(type="wall", thermal="fixed", temperature=1),
                                            right=Boundary(type="wall", thermal="fixed", temperature=0)),
                      initial=Initial(temperature=0.5), run=RunSettings(tolerance=1e-7))
    r = FemFallbackBackend().run(case, Hooks(), Path("."))
    T = r.frames[-1]["T"][:, :, 0]
    nu_hot = np.mean((1 - T[0, :]) / (0.5 / 24))
    assert r.converged and nu_hot == pytest.approx(2.243, rel=0.05)  # de Vahl Davis (1983)


def test_fem_fallback_cavity_and_3d():
    r = FemFallbackBackend().run(cavity(n=32), Hooks(), Path("."))
    g = build_grid_nd(cavity(n=32))
    assert r.converged
    assert sample_nd(r.frames[-1]["u"][:, :, 0], g, (0.5, 0.9766)) == pytest.approx(0.84123, abs=0.03)
    assert sample_nd(r.frames[-1]["u"][:, :, 0], g, (0.5, 0.5)) == pytest.approx(-0.2058, abs=0.04)
    c3 = cavity("3d", n=8, nz=4)
    r3 = FemFallbackBackend().run(c3, Hooks(), Path("."))
    assert r3.converged and r3.frames[-1]["w"].shape == (8, 8, 4) and r3.summary["backend"] == "python-fem"


def test_ac_fallback_poiseuille_and_cavity():
    case = CaseConfig(material=Material(viscosity=0.1, **UNIT), domain=Domain(width=4, height=1), mesh=MeshSettings(nx=40, ny=12),
                      boundaries=Boundaries(left=Boundary(type="inlet", velocity=1, profile="parabolic"), right=Boundary(type="outlet")),
                      run=RunSettings(tolerance=1e-6, max_iterations=200_000))
    r = AcFallbackBackend().run(case, Hooks(), Path("."))
    p = r.frames[-1]["p"][:, :, 0]
    assert r.converged
    assert p[1].mean() - p[-2].mean() == pytest.approx(12 * 0.1 * 4 * (1 - 2 / 40), rel=0.05)
    assert abs(r.summary["mass_imbalance"]) < 1e-6
    rc = AcFallbackBackend().run(cavity(n=24), Hooks(), Path("."))
    g = build_grid_nd(cavity(n=24))
    assert sample_nd(rc.frames[-1]["u"][:, :, 0], g, (0.5, 0.5)) == pytest.approx(-0.2058, abs=0.02)
