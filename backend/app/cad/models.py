"""SQLAlchemy ORM models.

Geometry is stored as *parametric definitions* (JSON), never as baked meshes:
sketch entities + constraints, ordered feature trees, assembly transforms/mates,
CAM operation lists and simulation setups.  The B-rep is rebuilt on demand by the
geometry services, which keeps the database small and the models fully editable.

Tables are prefixed ``cad_`` in the shared Gearhead database and classes are prefixed ``Cad`` where the original
name would clash with a platform class (`Project`). The CAD project of a Gearhead project is linked by
`CadProject.project_id` (one CAD project per Gearhead project, created on first use - see app/workspaces.py), which
makes it owned by that project's user. The original class names stay available as aliases for the routers/services.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .. import models as _core  # noqa: F401  (registers `projects` for the foreign key)
from .database import Base

# JSONB on PostgreSQL (indexable, binary), plain JSON elsewhere (tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CadProject(Base):
    __tablename__ = "cad_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True,
                                                   nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    sketches = relationship("Sketch", cascade="all, delete-orphan", back_populates="project")
    parts = relationship("Part", cascade="all, delete-orphan", back_populates="project")
    assemblies = relationship("Assembly", cascade="all, delete-orphan", back_populates="project")
    drawings = relationship("Drawing", cascade="all, delete-orphan")


Project = CadProject  # name used by the original CADWeb routers / services


class Sketch(Base):
    __tablename__ = "cad_sketches"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("cad_projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Sketch")
    plane: Mapped[str] = mapped_column(String(16), default="XY")  # XY | XZ | YZ
    offset: Mapped[float] = mapped_column(Float, default=0.0)  # along plane normal
    entities: Mapped[list] = mapped_column(JSONType, default=list)
    constraints: Mapped[list] = mapped_column(JSONType, default=list)
    layers: Mapped[list | None] = mapped_column(JSONType, default=list, nullable=True)  # [{name,color,visible,locked}]
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    project = relationship("CadProject", back_populates="sketches")


class Part(Base):
    __tablename__ = "cad_parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("cad_projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Part")
    material: Mapped[str] = mapped_column(String(64), default="steel")
    color: Mapped[str] = mapped_column(String(16), default="#8aa4c8")
    parameters: Mapped[dict] = mapped_column(JSONType, default=dict)  # named user parameters
    features: Mapped[list] = mapped_column(JSONType, default=list)  # ordered feature tree
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    project = relationship("CadProject", back_populates="parts")


class Assembly(Base):
    __tablename__ = "cad_assemblies"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("cad_projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Assembly")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    project = relationship("CadProject", back_populates="assemblies")
    components = relationship(
        "AssemblyComponent", cascade="all, delete-orphan", back_populates="assembly", order_by="AssemblyComponent.id"
    )
    mates = relationship("Mate", cascade="all, delete-orphan", back_populates="assembly", order_by="Mate.id")


class AssemblyComponent(Base):
    __tablename__ = "cad_assembly_components"
    id: Mapped[int] = mapped_column(primary_key=True)
    assembly_id: Mapped[int] = mapped_column(ForeignKey("cad_assemblies.id", ondelete="CASCADE"), index=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("cad_parts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), default="Component")
    position: Mapped[list] = mapped_column(JSONType, default=lambda: [0.0, 0.0, 0.0])
    rotation: Mapped[list] = mapped_column(JSONType, default=lambda: [0.0, 0.0, 0.0])  # Euler XYZ, degrees
    fixed: Mapped[bool] = mapped_column(Boolean, default=False)

    assembly = relationship("Assembly", back_populates="components")


class Mate(Base):
    __tablename__ = "cad_mates"
    id: Mapped[int] = mapped_column(primary_key=True)
    assembly_id: Mapped[int] = mapped_column(ForeignKey("cad_assemblies.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32))  # flush | concentric | point
    comp_a: Mapped[int] = mapped_column(Integer)
    comp_b: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)

    assembly = relationship("Assembly", back_populates="mates")


class CamSetup(Base):
    __tablename__ = "cad_cam_setups"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("cad_parts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Setup 1")
    stock: Mapped[dict] = mapped_column(JSONType, default=dict)
    tools: Mapped[list] = mapped_column(JSONType, default=list)
    operations: Mapped[list] = mapped_column(JSONType, default=list)
    post: Mapped[dict] = mapped_column(JSONType, default=dict)
    gcode: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SimulationJob(Base):
    __tablename__ = "cad_simulation_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    # a job analyses either one part or (CalculiX contact) a whole assembly
    part_id: Mapped[int | None] = mapped_column(ForeignKey("cad_parts.id", ondelete="CASCADE"), index=True, nullable=True)
    assembly_id: Mapped[int | None] = mapped_column(ForeignKey("cad_assemblies.id", ondelete="CASCADE"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # static | thermal | modal | ccx_* (see calculix.KINDS)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Drawing(Base):
    """2D drawing sheet: views of parts, dimensions, notes and a title block (definitions only)."""

    __tablename__ = "cad_drawings"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("cad_projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Drawing")
    sheet: Mapped[dict] = mapped_column(JSONType, default=dict)  # size, orientation, projection, decimals
    views: Mapped[list] = mapped_column(JSONType, default=list)
    dimensions: Mapped[list] = mapped_column(JSONType, default=list)
    notes: Mapped[list] = mapped_column(JSONType, default=list)
    title: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
