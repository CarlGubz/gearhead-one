"""Pydantic request bodies (responses are plain JSON dicts built by the routers)."""
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    # Gearhead project to attach the CAD project to; omitted = a new Gearhead project of the same name
    project_id: Optional[str] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class SketchIn(BaseModel):
    name: str = "Sketch"
    plane: str = "XY"
    offset: float = 0.0
    entities: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    layers: list[dict[str, Any]] = []


class SketchPatch(BaseModel):
    name: Optional[str] = None
    plane: Optional[str] = None
    offset: Optional[float] = None
    entities: Optional[list[dict[str, Any]]] = None
    constraints: Optional[list[dict[str, Any]]] = None
    layers: Optional[list[dict[str, Any]]] = None


class SketchEdit(BaseModel):
    op: str  # trim | extend | fillet | explode
    ids: Optional[list[str]] = None
    params: dict[str, Any] = {}


class InferIn(BaseModel):
    entities: list[dict[str, Any]]
    constraints: list[dict[str, Any]] = []
    ids: Optional[list[str]] = None


class SketchOp(BaseModel):
    op: str
    ids: Optional[list[str]] = None
    params: dict[str, Any] = {}
    replace: bool = False  # replace the source entities instead of appending (transform ops only)


class PartIn(BaseModel):
    name: str = "Part"
    material: str = "steel"
    color: Optional[str] = None
    parameters: dict[str, Any] = {}


class PartPatch(BaseModel):
    name: Optional[str] = None
    material: Optional[str] = None
    color: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None


class FeatureIn(BaseModel):
    type: str
    params: dict[str, Any] = {}
    name: Optional[str] = None
    index: Optional[int] = None  # insert position (default: append)


class FeaturePatch(BaseModel):
    params: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    suppressed: Optional[bool] = None


class FeatureMove(BaseModel):
    index: int


class AssemblyIn(BaseModel):
    name: str = "Assembly"


class ComponentIn(BaseModel):
    part_id: int
    name: Optional[str] = None
    position: list[float] = [0.0, 0.0, 0.0]
    rotation: list[float] = [0.0, 0.0, 0.0]
    fixed: bool = False


class ComponentPatch(BaseModel):
    name: Optional[str] = None
    position: Optional[list[float]] = None
    rotation: Optional[list[float]] = None
    fixed: Optional[bool] = None


class MateIn(BaseModel):
    type: str
    comp_a: int
    comp_b: int
    params: dict[str, Any] = {}


class CamSetupIn(BaseModel):
    name: str = "Setup 1"
    stock: dict[str, Any] = {}
    tools: Optional[list[dict[str, Any]]] = None
    operations: Optional[list[dict[str, Any]]] = None
    post: dict[str, Any] = {}


class CamSetupPatch(BaseModel):
    name: Optional[str] = None
    stock: Optional[dict[str, Any]] = None
    tools: Optional[list[dict[str, Any]]] = None
    operations: Optional[list[dict[str, Any]]] = None
    post: Optional[dict[str, Any]] = None


class SimulationIn(BaseModel):
    kind: str  # static | thermal | modal
    config: dict[str, Any] = {}


class DrawingIn(BaseModel):
    name: str = "Drawing"
    sheet: dict[str, Any] = {}
    part_id: Optional[int] = None  # create the standard views of this part
    title: dict[str, Any] = {}


class DrawingPatch(BaseModel):
    name: Optional[str] = None
    sheet: Optional[dict[str, Any]] = None
    views: Optional[list[dict[str, Any]]] = None
    dimensions: Optional[list[dict[str, Any]]] = None
    notes: Optional[list[dict[str, Any]]] = None
    title: Optional[dict[str, Any]] = None


class DrawingViewIn(BaseModel):
    part_id: int
    kind: str = "front"
    scale: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None


class DimensionIn(BaseModel):
    view: str
    type: str  # horizontal | vertical | aligned | radius | diameter
    a: dict[str, Any]  # snap point from /render: {edge, end, p}
    b: Optional[dict[str, Any]] = None
    offset: float = 10.0
    angle: float = 45.0
    text: Optional[str] = None
    tolerance: Optional[float] = None
