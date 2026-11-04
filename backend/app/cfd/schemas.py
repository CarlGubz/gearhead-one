"""Pydantic schemas: the CFD case definition and API payloads."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# SQLite returns timezone-aware columns as naive datetimes (in UTC). Mark them as UTC so the browser does not read them
# as local time ("8 h ago" for a run that just finished).
UtcDatetime = Annotated[datetime, AfterValidator(lambda d: d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d)]

# --------------------------------------------------------------------------- case


class Material(BaseModel):
    name: str = "Air (20 °C)"
    density: float = Field(1.204, gt=0, description="kg/m³")
    viscosity: float = Field(1.825e-5, gt=0, description="dynamic viscosity, Pa·s")
    conductivity: float = Field(0.0257, gt=0, description="thermal conductivity, W/(m·K)")
    specific_heat: float = Field(1005.0, gt=0, description="J/(kg·K)")
    thermal_expansion: float = Field(3.43e-3, ge=0, description="β, 1/K (Boussinesq)")


class Domain(BaseModel):
    width: float = Field(1.0, gt=0, le=1e4, description="x extent, m")
    height: float = Field(1.0, gt=0, le=1e4, description="y extent, m")
    depth: float = Field(1.0, gt=0, le=1e4, description="z extent, m (3-D cases only)")


class MeshSettings(BaseModel):
    nx: int = Field(64, ge=4, le=1000)
    ny: int = Field(64, ge=4, le=1000)
    nz: int = Field(16, ge=1, le=1000, description="cells in z (3-D cases only)")


class Models(BaseModel):
    energy: bool = False
    buoyancy: bool = False
    gravity_x: float = 0.0
    gravity_y: float = -9.81
    gravity_z: float = 0.0
    reference_temperature: float = 293.15
    turbulence: Literal["laminar", "k_epsilon", "k_omega_sst"] = Field(
        "laminar", description="RANS model; anything but laminar requires the OpenFOAM backend"
    )


class Obstacle(BaseModel):
    id: str
    name: str = "Obstacle"
    shape: Literal["rectangle", "circle", "sphere", "mesh"] = Field(
        "rectangle",
        description="3-D: rectangle = box, circle = cylinder along z, sphere, mesh = closed triangulated solid "
                    "(an FEA model or CAD part sent to CFD); 2-D: sphere = circle, mesh = its section at z = centre",
    )
    x: float = 0.0
    y: float = 0.0
    z: float | None = Field(None, description="3-D centre z; None = mid-depth")
    width: float = Field(0.1, gt=0)
    height: float = Field(0.1, gt=0)
    depth: float | None = Field(None, gt=0, description="3-D z extent of boxes/cylinders; None = full depth")
    radius: float = Field(0.05, gt=0)
    thermal: Literal["adiabatic", "fixed"] = "adiabatic"
    temperature: float = 293.15
    # ---- shape == "mesh": closed triangle surface, vertices in metres *relative to the centre* (x, y, z) ----
    vertices: list[list[float]] | None = Field(None, description="mesh: [[x, y, z], ...] relative to the centre, m")
    triangles: list[list[int]] | None = Field(None, description="mesh: [[i, j, k], ...] indices into vertices")
    source: dict | None = Field(None, description="where an imported solid came from (FEA model / CAD part), for mapping results back")

    @model_validator(mode="after")
    def _mesh_fields(self):
        if self.shape != "mesh":
            return self
        v, t = self.vertices or [], self.triangles or []
        if len(v) < 4 or len(t) < 4:
            raise ValueError(f"obstacle '{self.name}': a mesh obstacle needs at least 4 vertices and 4 triangles")
        if len(t) > 50_000:
            raise ValueError(f"obstacle '{self.name}': at most 50 000 triangles")
        if any(len(p) != 3 for p in v) or any(len(f) != 3 for f in t):
            raise ValueError(f"obstacle '{self.name}': vertices are [x, y, z] and triangles [i, j, k]")
        n = len(v)
        if any(not (0 <= i < n) for f in t for i in f):
            raise ValueError(f"obstacle '{self.name}': a triangle refers to a missing vertex")
        return self

    def mesh_extent(self) -> tuple[list[float], list[float]]:
        """(lo, hi) of the mesh vertices relative to the centre."""
        v = self.vertices or [[0.0, 0.0, 0.0]]
        return [min(p[a] for p in v) for a in range(3)], [max(p[a] for p in v) for a in range(3)]


class HeatSource(BaseModel):
    id: str
    name: str = "Heat source"
    x: float = 0.0
    y: float = 0.0
    z: float | None = None
    width: float = Field(0.1, gt=0)
    height: float = Field(0.1, gt=0)
    depth: float | None = Field(None, gt=0)
    power_density: float = Field(1000.0, description="W/m³ (per metre depth)")


class Boundary(BaseModel):
    type: Literal["wall", "inlet", "outlet", "symmetry"] = "wall"
    velocity: float = Field(0.0, ge=0, description="inlet normal speed (into the domain), m/s")
    profile: Literal["uniform", "parabolic"] = "uniform"
    wall_velocity: float = Field(0.0, description="tangential wall speed (+x for top/bottom/front/back, +y for left/right)")
    thermal: Literal["adiabatic", "fixed"] = "adiabatic"
    temperature: float = 293.15


class Boundaries(BaseModel):
    left: Boundary = Boundary()
    right: Boundary = Boundary()
    top: Boundary = Boundary()
    bottom: Boundary = Boundary()
    back: Boundary = Boundary()  # z = 0 (3-D only)
    front: Boundary = Boundary()  # z = depth (3-D only)


class Initial(BaseModel):
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    velocity_z: float = 0.0
    pressure: float = 0.0
    temperature: float = 293.15


class RunSettings(BaseModel):
    mode: Literal["steady", "transient"] = "steady"
    end_time: float = Field(1.0, gt=0, description="transient: physical end time, s")
    max_iterations: int = Field(50_000, ge=1, le=5_000_000)
    cfl: float = Field(0.5, gt=0, le=0.9)
    tolerance: float = Field(1e-5, gt=0, lt=1, description="steady convergence tolerance")
    fixed_dt: float | None = Field(None, gt=0)
    convection_blend: float | None = Field(
        None, ge=0, le=1, description="0=central, 1=upwind; None = adaptive (Hirt/Griebel)"
    )
    monitor_interval: int = Field(5, ge=1, le=10_000)
    snapshot_count: int = Field(40, ge=1, le=200, description="transient: number of stored frames")


class Probe(BaseModel):
    id: str
    name: str = "Probe"
    x: float = 0.0
    y: float = 0.0
    z: float | None = Field(None, description="3-D only; None = mid-depth")


SolverBackendName = Literal["native", "openfoam", "fenicsx", "pyfr"]


class SolverSettings(BaseModel):
    backend: SolverBackendName = Field(
        "native",
        description="native = built-in finite-volume solver; openfoam / fenicsx / pyfr = external solver "
        "(falls back to the equivalent built-in Python implementation when the tool is not installed)",
    )
    allow_fallback: bool = Field(True, description="use the Python fallback when the external tool is unavailable")
    order: int = Field(2, ge=1, le=6, description="polynomial order (PyFR solution points, FEniCSx velocity degree)")
    processes: int = Field(1, ge=1, le=256, description="MPI ranks for OpenFOAM (decomposePar + mpirun)")


class CaseConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimension: Literal["2d", "3d"] = "2d"
    solver: SolverSettings = SolverSettings()
    material: Material = Material()
    domain: Domain = Domain()
    mesh: MeshSettings = MeshSettings()
    models: Models = Models()
    obstacles: list[Obstacle] = []
    heat_sources: list[HeatSource] = []
    boundaries: Boundaries = Boundaries()
    initial: Initial = Initial()
    run: RunSettings = RunSettings()
    probes: list[Probe] = []

    @model_validator(mode="after")
    def _unique_ids(self):
        for label, items in (("obstacle", self.obstacles), ("heat source", self.heat_sources), ("probe", self.probes)):
            ids = [i.id for i in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids")
        return self

    @property
    def ndim(self) -> int:
        return 3 if self.dimension == "3d" else 2

    @property
    def n_cells(self) -> int:
        return self.mesh.nx * self.mesh.ny * (self.mesh.nz if self.ndim == 3 else 1)

    def sides(self) -> tuple[str, ...]:
        return SIDES_3D if self.ndim == 3 else SIDES_3D[:4]


SIDES_3D = ("left", "right", "bottom", "top", "back", "front")


# --------------------------------------------------------------------------- API


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    template: str | None = None
    config: CaseConfig | None = None
    # Gearhead project the case belongs to; omitted = a new Gearhead project of the same name is created for it
    project_id: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    config: CaseConfig | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    name: str
    status: str
    progress: float
    iteration: int
    sim_time: float
    converged: bool
    error: str | None
    summary: dict | None
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str | None = None
    name: str
    description: str
    template: str | None
    config: CaseConfig
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ProjectListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str | None = None
    name: str
    description: str
    template: str | None
    updated_at: UtcDatetime
    created_at: UtcDatetime
    nx: int
    ny: int
    nz: int = 1
    dimension: str = "2d"
    backend: str = "native"
    mode: str
    energy: bool
    run_count: int
    last_run_status: str | None


class RunCreate(BaseModel):
    name: str = ""


class CheckItem(BaseModel):
    level: Literal["error", "warning", "info"]
    section: str
    message: str


class CheckResult(BaseModel):
    ok: bool
    items: list[CheckItem]
    stats: dict
