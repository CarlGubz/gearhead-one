"""Built-in example cases (the "Examples" gallery)."""
from __future__ import annotations

from .schemas import (
    Boundaries,
    Boundary,
    CaseConfig,
    Domain,
    HeatSource,
    Initial,
    Material,
    MeshSettings,
    Models,
    Obstacle,
    Probe,
    RunSettings,
    SolverSettings,
)


def _unit_fluid(mu: float) -> Material:
    return Material(name="Non-dimensional fluid", density=1.0, viscosity=mu, conductivity=1.0, specific_heat=1.0, thermal_expansion=0.0)


def lid_driven_cavity() -> CaseConfig:
    return CaseConfig(
        material=_unit_fluid(0.01),
        domain=Domain(width=1, height=1),
        mesh=MeshSettings(nx=64, ny=64),
        boundaries=Boundaries(top=Boundary(type="wall", wall_velocity=1.0)),
        run=RunSettings(mode="steady", tolerance=1e-6),
        probes=[Probe(id="center", name="Cavity centre", x=0.5, y=0.5)],
    )


def channel_flow() -> CaseConfig:
    return CaseConfig(
        material=_unit_fluid(0.1),
        domain=Domain(width=4, height=1),
        mesh=MeshSettings(nx=80, ny=20),
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=1.0, profile="parabolic"),
            right=Boundary(type="outlet"),
        ),
        run=RunSettings(mode="steady", tolerance=1e-7),
        probes=[Probe(id="mid", name="Mid-channel", x=2.0, y=0.5)],
    )


def cylinder_in_channel() -> CaseConfig:
    """Schäfer & Turek 2D-2 benchmark geometry, Re = 100 (U_mean D / nu)."""
    return CaseConfig(
        material=Material(name="Benchmark fluid", density=1.0, viscosity=1e-3, conductivity=1.0, specific_heat=1.0, thermal_expansion=0.0),
        domain=Domain(width=2.2, height=0.41),
        mesh=MeshSettings(nx=220, ny=41),
        obstacles=[Obstacle(id="cyl", name="Cylinder", shape="circle", x=0.2, y=0.2, radius=0.05)],
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=1.0, profile="parabolic"),
            right=Boundary(type="outlet"),
        ),
        initial=Initial(velocity_x=1.0),
        run=RunSettings(mode="transient", end_time=15.0, snapshot_count=75, monitor_interval=5),
        probes=[Probe(id="wake", name="Wake probe", x=0.6, y=0.2)],
    )


def backward_facing_step() -> CaseConfig:
    return CaseConfig(
        material=_unit_fluid(0.005),
        domain=Domain(width=10, height=1),
        mesh=MeshSettings(nx=200, ny=40),
        obstacles=[Obstacle(id="step", name="Step", shape="rectangle", x=1.0, y=0.25, width=2.0, height=0.5)],
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=1.0, profile="parabolic"),
            right=Boundary(type="outlet"),
        ),
        initial=Initial(velocity_x=0.5),
        run=RunSettings(mode="steady", tolerance=1e-6),
        probes=[Probe(id="after", name="After step", x=4.0, y=0.25)],
    )


def natural_convection() -> CaseConfig:
    """Differentially heated square cavity, Ra = 1e5, Pr = 0.71 (de Vahl Davis benchmark)."""
    alpha = (1e-5 / 0.71) ** 0.5
    nu = 0.71 * alpha
    return CaseConfig(
        material=Material(name="Ra = 1e5, Pr = 0.71", density=1.0, viscosity=nu, conductivity=alpha, specific_heat=1.0, thermal_expansion=1.0),
        domain=Domain(width=1, height=1),
        mesh=MeshSettings(nx=48, ny=48),
        models=Models(energy=True, buoyancy=True, gravity_x=0, gravity_y=-1, reference_temperature=0.5),
        boundaries=Boundaries(
            left=Boundary(type="wall", thermal="fixed", temperature=1.0),
            right=Boundary(type="wall", thermal="fixed", temperature=0.0),
        ),
        initial=Initial(temperature=0.5),
        run=RunSettings(mode="steady", tolerance=1e-6),
        probes=[Probe(id="center", name="Cavity centre", x=0.5, y=0.5)],
    )


def electronics_cooling() -> CaseConfig:
    return CaseConfig(
        material=Material(),
        domain=Domain(width=0.2, height=0.05),
        mesh=MeshSettings(nx=120, ny=30),
        models=Models(energy=True, reference_temperature=293.15),
        obstacles=[
            Obstacle(id="chip", name="Heated chip", shape="rectangle", x=0.06, y=0.0125, width=0.02, height=0.025,
                     thermal="fixed", temperature=343.15),
            Obstacle(id="chip2", name="Second chip", shape="rectangle", x=0.11, y=0.0075, width=0.015, height=0.015,
                     thermal="fixed", temperature=333.15),
        ],
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=0.2, profile="parabolic", temperature=293.15),
            right=Boundary(type="outlet"),
        ),
        initial=Initial(velocity_x=0.2, temperature=293.15),
        run=RunSettings(mode="steady", tolerance=1e-6),
        probes=[Probe(id="exit", name="Near outlet", x=0.18, y=0.025)],
    )


# --------------------------------------------------------------------------- 3-D examples


def lid_driven_cube() -> CaseConfig:
    """3-D lid-driven cubic cavity, Re = 100 (lid moves in +x)."""
    return CaseConfig(
        dimension="3d",
        material=_unit_fluid(0.01),
        domain=Domain(width=1, height=1, depth=1),
        mesh=MeshSettings(nx=24, ny=24, nz=24),
        boundaries=Boundaries(top=Boundary(type="wall", wall_velocity=1.0)),
        run=RunSettings(mode="steady", tolerance=1e-5),
        probes=[Probe(id="center", name="Cube centre", x=0.5, y=0.5, z=0.5)],
    )


def square_duct() -> CaseConfig:
    """Developing laminar flow in a square duct (Re_Dh = 50); fully developed f Re = 56.91."""
    return CaseConfig(
        dimension="3d",
        material=_unit_fluid(0.02),
        domain=Domain(width=5, height=1, depth=1),
        mesh=MeshSettings(nx=50, ny=12, nz=12),
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=1.0),
            right=Boundary(type="outlet"),
        ),
        initial=Initial(velocity_x=1.0),
        run=RunSettings(mode="steady", tolerance=1e-6),
        probes=[Probe(id="axis", name="Duct axis (x = 4)", x=4.0, y=0.5, z=0.5)],
    )


def sphere_in_duct() -> CaseConfig:
    """Steady laminar flow past a sphere on the axis of a square duct (Re_D = 40)."""
    return CaseConfig(
        dimension="3d",
        material=_unit_fluid(0.005),
        domain=Domain(width=2.0, height=0.6, depth=0.6),
        mesh=MeshSettings(nx=60, ny=18, nz=18),
        obstacles=[Obstacle(id="sphere", name="Sphere", shape="sphere", x=0.5, y=0.3, z=0.3, radius=0.1)],
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=1.0),
            right=Boundary(type="outlet"),
            bottom=Boundary(type="symmetry"), top=Boundary(type="symmetry"),
            back=Boundary(type="symmetry"), front=Boundary(type="symmetry"),
        ),
        initial=Initial(velocity_x=1.0),
        run=RunSettings(mode="steady", tolerance=1e-5),
        probes=[Probe(id="wake", name="Wake (1 D behind)", x=0.8, y=0.3, z=0.3)],
    )


def heated_chip_3d() -> CaseConfig:
    """Air flowing over a heated chip in a 3-D channel - forced convection with a 3-D wake."""
    return CaseConfig(
        dimension="3d",
        material=Material(),
        domain=Domain(width=0.2, height=0.04, depth=0.08),
        mesh=MeshSettings(nx=50, ny=10, nz=20),
        models=Models(energy=True, reference_temperature=293.15),
        obstacles=[Obstacle(id="chip", name="Heated chip", shape="rectangle", x=0.06, y=0.006, z=0.04, width=0.02,
                            height=0.012, depth=0.02, thermal="fixed", temperature=343.15)],
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=0.3, profile="parabolic", temperature=293.15),
            right=Boundary(type="outlet"),
            back=Boundary(type="symmetry"), front=Boundary(type="symmetry"),
        ),
        initial=Initial(velocity_x=0.3, temperature=293.15),
        run=RunSettings(mode="steady", tolerance=1e-5),
        probes=[Probe(id="behind", name="Behind the chip", x=0.1, y=0.01, z=0.04)],
    )


def turbulent_step_openfoam() -> CaseConfig:
    """Backward-facing step at Re_h = 25 000 with OpenFOAM and the k-omega SST model."""
    return CaseConfig(
        material=Material(name="Water (20 °C)", density=998.2, viscosity=1.002e-3, conductivity=0.598, specific_heat=4182,
                          thermal_expansion=2.07e-4),
        domain=Domain(width=1.0, height=0.1),
        mesh=MeshSettings(nx=200, ny=40),
        models=Models(turbulence="k_omega_sst"),
        obstacles=[Obstacle(id="step", name="Step", shape="rectangle", x=0.1, y=0.025, width=0.2, height=0.05)],
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=0.5),
            right=Boundary(type="outlet"),
        ),
        initial=Initial(velocity_x=0.5),
        run=RunSettings(mode="steady", tolerance=1e-5, max_iterations=3000),
        solver=SolverSettings(backend="openfoam"),
        probes=[Probe(id="reattach", name="Recirculation zone", x=0.35, y=0.02)],
    )


def blank() -> CaseConfig:
    return CaseConfig(
        material=_unit_fluid(0.01),
        boundaries=Boundaries(
            left=Boundary(type="inlet", velocity=1.0),
            right=Boundary(type="outlet"),
        ),
    )


TEMPLATES = {
    "blank": ("Blank case", "Empty channel with a uniform inlet and an outlet - start from scratch.", blank),
    "lid_driven_cavity": (
        "Lid-driven cavity",
        "Classic Re = 100 benchmark. Steady laminar recirculation driven by a moving lid (compare with Ghia et al., 1982).",
        lid_driven_cavity,
    ),
    "channel_flow": ("Plane Poiseuille channel", "Developing laminar channel flow with a parabolic inlet; pressure drop matches 12 μ U L / H².", channel_flow),
    "cylinder_in_channel": (
        "Cylinder in channel (vortex shedding)",
        "Schäfer–Turek 2D-2 geometry at Re = 100. Transient run with a Kármán vortex street and frame animation.",
        cylinder_in_channel,
    ),
    "backward_facing_step": ("Backward-facing step", "Laminar separation and reattachment behind a step (Re_h = 100).", backward_facing_step),
    "natural_convection": (
        "Natural convection cavity",
        "Differentially heated square cavity, Ra = 1e5 - Boussinesq buoyancy-driven flow (de Vahl Davis benchmark).",
        natural_convection,
    ),
    "electronics_cooling": (
        "Electronics cooling (heated chips)",
        "Air flowing past two heated chips in a duct - conjugate-style forced convection with heat-transfer rates.",
        electronics_cooling,
    ),
    "lid_driven_cube": (
        "Lid-driven cube (3-D)",
        "3-D cubic cavity at Re = 100 - the 3-D counterpart of the Ghia benchmark; view it as slices.",
        lid_driven_cube,
    ),
    "square_duct": (
        "Square-duct flow (3-D)",
        "Developing laminar flow in a square duct; the fully developed friction factor is f·Re = 56.91.",
        square_duct,
    ),
    "sphere_in_duct": (
        "Flow past a sphere (3-D)",
        "Steady laminar flow past a sphere at Re = 40 with symmetry walls - drag force in N.",
        sphere_in_duct,
    ),
    "heated_chip_3d": (
        "Heated chip in a channel (3-D)",
        "Forced air cooling of a hot chip in a 3-D channel: temperature wake and heat-transfer rate in W.",
        heated_chip_3d,
    ),
    "turbulent_step_openfoam": (
        "Turbulent backward-facing step (OpenFOAM k-ω SST)",
        "Re_h = 25 000 water flow over a step solved by OpenFOAM's simpleFoam with the k-ω SST model.",
        turbulent_step_openfoam,
    ),
}


def list_templates() -> list[dict]:
    return [{"id": k, "name": v[0], "description": v[1]} for k, v in TEMPLATES.items()]


def build_template(tid: str) -> tuple[str, str, CaseConfig]:
    name, desc, fn = TEMPLATES[tid]
    return name, desc, fn()
