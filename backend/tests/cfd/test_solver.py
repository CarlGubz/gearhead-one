"""Solver verification against published benchmarks / analytic solutions."""
import numpy as np
import pytest

from app.cfd.schemas import (Boundaries, Boundary, CaseConfig, Domain, Initial, Material, MeshSettings, Models,
                         Obstacle, Probe, RunSettings)
from app.cfd.solver.ns2d import FlowSolver
from app.cfd.solver.runner import run_simulation


def converge(s, tol=1e-7, maxit=60000, **kw):
    for it in range(1, maxit):
        r = s.step(**kw)
        if max(r.u, r.v, r.T) < tol and it > 50:
            return it
    return maxit


def test_lid_driven_cavity_re100_matches_ghia():
    case = CaseConfig(
        material=Material(density=1, viscosity=0.01, conductivity=1, specific_heat=1),
        mesh=MeshSettings(nx=48, ny=48),
        boundaries=Boundaries(top=Boundary(type="wall", wall_velocity=1.0)),
    )
    s = FlowSolver(case)
    converge(s, 1e-6)
    uc = s.cell_fields()["u"]
    # Ghia, Ghia & Shin (1982), Re=100, u along the vertical centreline
    ghia = {0.9766: 0.84123, 0.5: -0.20581, 0.2813: -0.15662, 0.0547: -0.03717}
    for y, ref in ghia.items():
        assert s.sample(uc, 0.5, y) == pytest.approx(ref, abs=0.02)
    assert s.mass_imbalance() == 0.0


def test_poiseuille_pressure_drop_and_profile():
    case = CaseConfig(
        material=Material(density=1, viscosity=0.1, conductivity=1, specific_heat=1),
        domain=Domain(width=4, height=1),
        mesh=MeshSettings(nx=60, ny=20),
        boundaries=Boundaries(left=Boundary(type="inlet", velocity=1, profile="parabolic"), right=Boundary(type="outlet")),
    )
    s = FlowSolver(case)
    converge(s, 1e-8)
    f = s.cell_fields()
    dp = f["p"][1, :].mean() - f["p"][-2, :].mean()
    L = 4 * (1 - 2 / 60)
    assert dp == pytest.approx(12 * 0.1 * 1 * L, rel=0.05)
    assert f["u"][-1, :].max() == pytest.approx(1.5, rel=0.03)
    assert abs(s.mass_imbalance()) < 1e-10


def test_natural_convection_ra1e4_nusselt():
    alpha = (1e-4 / 0.71) ** 0.5
    nu = 0.71 * alpha
    case = CaseConfig(
        material=Material(density=1, viscosity=nu, conductivity=alpha, specific_heat=1, thermal_expansion=1),
        mesh=MeshSettings(nx=24, ny=24),
        models=Models(energy=True, buoyancy=True, gravity_y=-1, reference_temperature=0.5),
        boundaries=Boundaries(left=Boundary(type="wall", thermal="fixed", temperature=1),
                              right=Boundary(type="wall", thermal="fixed", temperature=0)),
        initial=Initial(temperature=0.5),
    )
    s = FlowSolver(case)
    converge(s, 1e-7, 100000)
    Nu = np.mean((1 - s.T[0, :]) / (s.g.dx / 2))
    assert Nu == pytest.approx(2.243, rel=0.06)  # de Vahl Davis (1983)


def test_heated_obstacle_energy_balance():
    case = CaseConfig(
        domain=Domain(width=0.2, height=0.05), mesh=MeshSettings(nx=80, ny=20),
        models=Models(energy=True),
        obstacles=[Obstacle(id="b", shape="rectangle", x=0.06, y=0.0125, width=0.02, height=0.025, thermal="fixed", temperature=343.15)],
        boundaries=Boundaries(left=Boundary(type="inlet", velocity=0.2, profile="parabolic", temperature=293.15), right=Boundary(type="outlet")),
        initial=Initial(velocity_x=0.2, temperature=293.15), run=RunSettings(tolerance=1e-6),
    )
    s = FlowSolver(case)
    converge(s, 1e-6, 40000, update_energy=False)
    assert s.solve_energy_steady()
    q = s.monitors()["b.Q"]
    out = (s.rho * s.cp * s.u[-1, :] * (s.T[-1, :] - 293.15)).sum() * s.g.dy
    assert q > 0 and q == pytest.approx(out, rel=0.02)  # heat leaving the block == enthalpy flux at outlet
    assert s.monitors()["b.Fx"] > 0  # drag


def test_enclosed_pocket_and_closed_domain_are_pinned():
    # solid ring creates an isolated fluid pocket that must not make the Poisson system singular
    obs = [Obstacle(id="ring", shape="rectangle", x=0.5, y=0.5, width=0.4, height=0.4)]
    case = CaseConfig(mesh=MeshSettings(nx=20, ny=20), obstacles=obs,
                      boundaries=Boundaries(top=Boundary(type="wall", wall_velocity=1)))
    s = FlowSolver(case)
    for _ in range(20):
        s.step()
    assert np.isfinite(s.u).all()


def test_runner_transient_cylinder_produces_frames_and_loads():
    case = CaseConfig(
        material=Material(density=1, viscosity=1e-3, conductivity=1, specific_heat=1),
        domain=Domain(width=1.1, height=0.41), mesh=MeshSettings(nx=55, ny=20),
        obstacles=[Obstacle(id="cyl", shape="circle", x=0.2, y=0.2, radius=0.05)],
        boundaries=Boundaries(left=Boundary(type="inlet", velocity=1, profile="parabolic"), right=Boundary(type="outlet")),
        initial=Initial(velocity_x=1.0), probes=[Probe(id="w", x=0.6, y=0.2)],
        run=RunSettings(mode="transient", end_time=0.5, snapshot_count=5),
    )
    seen = []
    from app.cfd.solver.runner import Hooks
    res = run_simulation(case, Hooks(on_monitor=lambda it, t, r, v: seen.append(v)))
    assert res.status == "completed" and len(res.frames) >= 3
    assert res.times[-1] == pytest.approx(0.5, abs=0.05)
    assert "cyl.Fx" in seen[-1] and seen[-1]["cyl.Fx"] > 0
    assert "w.u" in seen[-1]
