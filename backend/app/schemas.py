"""Pydantic request/response schemas for projects, meshes, models and jobs."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default="", max_length=2000)


class PrimitiveMeshRequest(BaseModel):
    """
    Generate a mesh for a primitive (box, cylinder, sphere, tube, plate_hole, lbracket, cone, torus).

    mesher "builtin" uses the structured / Delaunay generators in meshgen.py and `element_type` (C3D4 | C3D10 | C3D8 | C3D20);
    mesher "gmsh" hands the shape to Gmsh and takes the family / order / algorithms / refinement from `gmsh` (see
    fea/gmsh_options.py). With Gmsh the element type follows `gmsh.family` and `gmsh.order`, and `divisions` only sets the
    automatic element size (smallest dimension / divisions) when `gmsh.size` is 0.
    """
    shape: str
    dims: Dict[str, float] = {}
    divisions: int = Field(default=4, ge=1, le=24)
    element_type: str = "C3D4"   # C3D4 | C3D10 | C3D8 | C3D20
    mesher: Literal["builtin", "gmsh"] = "builtin"
    gmsh: Optional[Dict[str, Any]] = None


class AppendPartRequest(PrimitiveMeshRequest):
    offset: List[float] = [0.0, 0.0, 0.0]
    name: Optional[str] = Field(default=None, max_length=60)
    material_id: Optional[str] = None


class MaterialUpdate(BaseModel):
    name: str = "Steel"
    E: float = Field(default=210e9, gt=0)               # Young's modulus, Pa
    nu: float = Field(default=0.3, gt=-1, lt=0.5)       # Poisson's ratio
    density: float = Field(default=7850.0, ge=0)        # kg/m^3
    conductivity: float = Field(default=50.0, ge=0)     # W/(m K)
    specific_heat: float = Field(default=490.0, ge=0)   # J/(kg K)
    alpha: float = 0.0                                  # thermal expansion, 1/K
    yield_stress: float = Field(default=0.0, ge=0)      # Pa, 0 = elastic
    hardening: float = Field(default=0.0, ge=0)         # Pa, linear isotropic hardening modulus


class MaterialItem(MaterialUpdate):
    """A material library entry (name + properties)."""
    name: str = Field(default="Material", min_length=1, max_length=60)


class PartUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    material_id: Optional[str] = None
    section: Optional[Dict[str, Any]] = None     # structural parts: beam/shell/truss/spring section properties


class StructuralMeshRequest(BaseModel):
    """Generate a structural model: kind = line | portal | plate | cylinder; element_type = B31 | T3D2 | SPRING2 | S4."""
    kind: str
    dims: Dict[str, float] = {}
    divisions: int = Field(default=4, ge=1, le=200)
    element_type: str = "B31"
    section: Optional[Dict[str, Any]] = None
    material_id: Optional[str] = None


class AppendStructuralRequest(StructuralMeshRequest):
    offset: List[float] = [0.0, 0.0, 0.0]
    name: Optional[str] = Field(default=None, max_length=60)
    merge: bool = True            # join nodes that coincide with existing nodes (needed to connect members)


class CustomStructuralMesh(BaseModel):
    """Arbitrary truss / frame / spring layout: node coordinates and 2-node connectivity (or 4-node shells)."""
    nodes: List[List[float]]
    elements: List[List[int]]
    element_type: str
    section: Optional[Dict[str, Any]] = None


class LayerCreate(BaseModel):
    """New model on top of the stack: empty, or a copy of `duplicate_of`."""
    name: str = Field("Model", max_length=80)
    process: Literal["cad", "cam", "cae"] = "cae"
    duplicate_of: Optional[str] = None


class LayerUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    process: Optional[Literal["cad", "cam", "cae"]] = None
    visible: Optional[bool] = None


class LayerMove(BaseModel):
    direction: Literal["up", "down"]


class TranslateRequest(BaseModel):
    offset: List[float] = Field(min_length=3, max_length=3)


class BoundaryCondition(BaseModel):
    """
    bc_type: "displacement" (fixed/pinned/symmetry/prescribed; `dofs` subset of 0..2, or 0..5 on a
    reference point) or "temperature" (fixed temperature in values[0]). If omitted, thermal analyses
    treat every BC as a temperature BC and mechanical analyses as a displacement BC.
    """
    id: Optional[str] = None
    name: str = "Fixed"
    bc_type: Optional[str] = None
    node_ids: List[int] = []
    ref_id: Optional[str] = None
    dofs: List[int] = []
    values: Optional[List[float]] = None


class Load(BaseModel):
    """
    load_type:
      force         total `vector` [N] split across node_ids
      pressure      `faces` + `magnitude` (Pa, positive = into the surface)
      traction      `faces` + `vector` (Pa)
      ref_force     `ref_id` + `vector` [N] + `moment` [N m] on a reference point
      heat_flux     concentrated nodal heat `magnitude` [W] split across node_ids
      surface_flux  `faces` + `magnitude` [W/m^2], positive = heat into the body
      body_heat     `magnitude` [W/m^3] in the whole volume
      convection    `faces` + `h` [W/m^2K] + `T_inf`
      radiation     `faces` + `emissivity` + `T_inf`
      line_load     structural: `vector` [N/m] on the beams of `part_id` (all beams if omitted)
      point_mass    structural: `magnitude` [kg] lumped at each of node_ids (modal / dynamic analyses and gravity)
    A `force` load on a structural model may also carry a `moment` [N m] (split over node_ids like the force).
    """
    id: Optional[str] = None
    name: str = "Force"
    load_type: str = "force"
    node_ids: List[int] = []
    vector: List[float] = [0.0, 0.0, 0.0]
    moment: List[float] = [0.0, 0.0, 0.0]
    faces: Optional[List[List[int]]] = None
    magnitude: Optional[float] = None
    h: Optional[float] = None
    T_inf: Optional[float] = None
    emissivity: Optional[float] = None
    ref_id: Optional[str] = None
    part_id: Optional[str] = None      # line_load: the part whose beams carry the load (all beams if omitted)


class GravityUpdate(BaseModel):
    enabled: bool = False
    x: float = 0.0
    y: float = 0.0
    z: float = -9.81


class AnalysisTypeUpdate(BaseModel):
    analysis_type: str
    n_modes: int = Field(default=6, ge=1, le=50)


class SettingsUpdate(BaseModel):
    """Merged into project.settings. Unknown keys are rejected so typos surface early."""
    model_config = ConfigDict(extra="forbid")
    n_modes: Optional[int] = Field(default=None, ge=1, le=50)
    increments: Optional[int] = Field(default=None, ge=1, le=200)
    t_end: Optional[float] = Field(default=None, gt=0)
    n_steps: Optional[int] = Field(default=None, ge=1, le=20000)
    theta: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    initial_temperature: Optional[float] = None
    reference_temperature: Optional[float] = None
    uniform_temperature: Optional[float] = None
    absolute_zero: Optional[float] = None
    thermal_mode: Optional[str] = None
    linear_solver: Optional[str] = None
    auto_threshold: Optional[int] = Field(default=None, ge=1000)
    solver_tolerance: Optional[float] = Field(default=None, gt=0, le=1e-2)
    nlgeom: Optional[bool] = None
    finite_strain_model: Optional[str] = None
    rayleigh_alpha: Optional[float] = Field(default=None, ge=0)
    rayleigh_beta: Optional[float] = Field(default=None, ge=0)
    history_nodes: Optional[List[int]] = None
    amplitude: Optional[Dict[str, Any]] = None
    centrifugal: Optional[Dict[str, Any]] = None
    clear: Optional[List[str]] = None  # keys to remove (e.g. uniform_temperature)

    @field_validator("linear_solver")
    @classmethod
    def _solver(cls, v):
        if v not in (None, "auto", "direct", "iterative"):
            raise ValueError("linear_solver must be 'auto', 'direct' or 'iterative'")
        return v

    @field_validator("finite_strain_model")
    @classmethod
    def _fsm(cls, v):
        if v not in (None, "svk", "neohookean"):
            raise ValueError("finite_strain_model must be 'svk' or 'neohookean'")
        return v

    @field_validator("thermal_mode")
    @classmethod
    def _mode(cls, v):
        if v not in (None, "steady", "transient"):
            raise ValueError("thermal_mode must be 'steady' or 'transient'")
        return v


class ReferencePoint(BaseModel):
    id: Optional[str] = None
    name: str = "RP"
    coords: List[float] = Field(min_length=3, max_length=3)


class Constraint(BaseModel):
    """rigid: ref_id + (faces | node_ids).  tie: slave_faces|slave_node_ids + master_faces (+ tolerance)."""
    id: Optional[str] = None
    name: str = "Constraint"
    type: str
    ref_id: Optional[str] = None
    faces: Optional[List[List[int]]] = None
    node_ids: List[int] = []
    slave_faces: Optional[List[List[int]]] = None
    slave_node_ids: List[int] = []
    master_faces: Optional[List[List[int]]] = None
    tolerance: Optional[float] = None
    master_part: Optional[str] = None   # tie/contact: the two parts involved
    slave_part: Optional[str] = None
    penalty_factor: Optional[float] = Field(default=None, gt=0, le=1e6)   # contact stiffness multiplier (default 10)


class NodeSet(BaseModel):
    id: Optional[str] = None
    name: str = Field(min_length=1, max_length=60)
    kind: str = "nodes"  # "nodes" | "faces"
    ids: List[Any] = []


_DEFAULTS = {
    "element_type": "C3D4", "settings": {}, "reference_points": [], "constraints": [], "node_sets": [],
    "materials": [], "parts": [], "surface_parts": [], "surface_mid": [], "history_pos": -1, "history_labels": [],
    "gravity": {"enabled": False, "x": 0.0, "y": 0.0, "z": -9.81},
}


def _legacy_surface_mid(data) -> List[List[int]]:
    try:
        from .fea.model import FEMesh
        mesh = FEMesh(data["nodes"], data["elements"], data["element_type"])
        return mesh.boundary().tri_mid.tolist()
    except Exception:  # noqa: BLE001 - picking falls back to corner nodes
        return []


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str
    nodes: List[List[float]]
    elements: List[List[int]]
    surface_faces: List[List[int]]
    element_type: str = "C3D4"
    material: Dict[str, Any]
    boundary_conditions: List[Dict[str, Any]]
    loads: List[Dict[str, Any]]
    analysis_type: str
    n_modes: int
    gravity: Dict[str, Any]
    settings: Dict[str, Any] = {}
    reference_points: List[Dict[str, Any]] = []
    constraints: List[Dict[str, Any]] = []
    node_sets: List[Dict[str, Any]] = []
    materials: List[Dict[str, Any]] = []
    parts: List[Dict[str, Any]] = []
    surface_parts: List[int] = []
    surface_mid: List[List[int]] = []     # quadratic solids: mid-side node of each render triangle's edges (the pick cursor snaps to them)
    history_pos: int = -1
    history_labels: List[str] = []
    active_layer_id: Optional[str] = None
    layer_stack: List[Dict[str, Any]] = []   # the project's model stack, top first (see layers.py)

    @model_validator(mode="wrap")
    @classmethod
    def _fill_nulls(cls, data, handler):
        # Rows created before v3 hold NULL in the new columns: serve defaults instead of failing validation.
        if not isinstance(data, dict):
            values = {k: getattr(data, k, None) for k in cls.model_fields}
            data = {k: (_DEFAULTS[k] if v is None and k in _DEFAULTS else v) for k, v in values.items()}
        if isinstance(data, dict) and data.get("elements"):
            # projects created before parts existed: serve one implicit part
            if not data.get("parts"):
                data["parts"] = [{"id": "p0", "name": "Body", "elem_start": 0, "elem_end": len(data["elements"]),
                                  "material_id": "default"}]
            if not data.get("surface_parts"):
                data["surface_parts"] = [0] * len(data.get("surface_faces") or [])
            # quadratic projects saved before mid-side picking existed: rebuild the table (it is stored again on the next edit)
            if (not data.get("surface_mid") and data.get("element_type") in ("C3D10", "C3D20")
                    and len(data.get("surface_faces") or [])):
                data["surface_mid"] = _legacy_surface_mid(data)
        return handler(data)


class ProjectSummary(BaseModel):
    """Light listing row (no mesh or result arrays)."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str = ""
    analysis_type: str = "static"


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    layer_id: Optional[str] = None
    status: str
    solver_used: str
    log: str
    progress: float
    results: Dict[str, Any]


class JobSummary(BaseModel):
    """A run without its result arrays (the Results panel lists these)."""
    id: str
    layer_id: Optional[str] = None
    status: str
    progress: float = 0.0
    analysis_type: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
