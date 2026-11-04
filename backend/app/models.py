"""SQLAlchemy ORM models for projects, meshes, materials, BCs and jobs."""
import datetime as dt
import uuid

from sqlalchemy import (
    Boolean, Column, String, Float, Integer, Text, DateTime, ForeignKey, JSON, LargeBinary,
)
from sqlalchemy.orm import relationship

from .database import Base


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> dt.datetime:
    """Naive UTC timestamp (all DateTime columns here are naive UTC)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------ Accounts
class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    email = Column(String(320), nullable=False, unique=True, index=True)  # stored lower-case
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(120), nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=True)
    failed_logins = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    last_login_at = Column(DateTime, nullable=True)

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="user", uselist=False,
                                cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="owner")


class UserSession(Base):
    """Server-side session. `token_hash` is SHA-256 of the cookie value."""
    __tablename__ = "user_sessions"

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    user_agent = Column(String(255), default="")
    ip = Column(String(64), default="")

    user = relationship("User", back_populates="sessions")


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


# ------------------------------------------------------------------- Billing
class Subscription(Base):
    """
    One row per user. The user is Pro while `current_period_end` is in the
    future - there is no background job to "expire" anything.
    """
    __tablename__ = "subscriptions"

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    plan_id = Column(String(32), nullable=False)
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    user = relationship("User", back_populates="subscription")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(String(32), nullable=False)
    amount_centavos = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default="PHP")
    provider = Column(String(20), nullable=False)
    provider_ref = Column(String(100), nullable=True, unique=True)  # e.g. PayMongo cs_...
    status = Column(String(12), nullable=False, default="pending")  # pending|paid|failed
    checkout_url = Column(Text, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    paid_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="payments")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_id)
    # Nullable only so pre-accounts rows survive the migration; such legacy
    # projects belong to nobody and are not served by the API.
    owner_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    # Mesh data stored as JSON blobs (nodes: [[x,y,z],...], elements: [[n1..n4],...])
    nodes = Column(JSON, default=list)
    elements = Column(JSON, default=list)
    surface_faces = Column(JSON, default=list)  # boundary triangles for picking/rendering

    material = Column(JSON, default=dict)  # {name, E, nu, density, conductivity, specific_heat}

    boundary_conditions = Column(JSON, default=list)  # list of BC dicts
    loads = Column(JSON, default=list)  # list of load dicts

    analysis_type = Column(String, default="static")  # see fea.runner.ANALYSIS_TYPES
    n_modes = Column(Integer, default=6)  # number of modes for modal analysis
    gravity = Column(JSON, default=lambda: {"enabled": False, "x": 0.0, "y": 0.0, "z": -9.81})

    # --- v3 (standalone FE engine) ---
    element_type = Column(String, default="C3D4")   # C3D4 | C3D10 | C3D8 | C3D20
    settings = Column(JSON, default=dict)            # analysis parameters (time, increments, amplitude, ...)
    reference_points = Column(JSON, default=list)    # [{id, name, coords}]
    constraints = Column(JSON, default=list)         # rigid / tie
    node_sets = Column(JSON, default=list)           # named selections [{id, name, kind, ids}]

    # --- v4 (parts & materials) ---
    materials = Column(JSON, default=list)           # extra material library entries; "default" = `material`
    parts = Column(JSON, default=list)               # [{id, name, elem_start, elem_end, material_id}]
    surface_parts = Column(JSON, default=list)       # part index of every render triangle
    surface_mid = Column(JSON, default=list)         # quadratic meshes: mid-side node of each render triangle's 3 edges (-1 = none)

    # --- undo / redo (see history.py) ---
    history_pos = Column(Integer, default=-1)        # index of the current state, -1 = no history yet
    history_labels = Column(JSON, default=list)      # label of the edit that produced each state

    # --- v5 (model stack, see layers.py): the columns above always hold the *active* model ---
    active_layer_id = Column(String(32), nullable=True)

    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")
    owner = relationship("User", back_populates="projects")
    history = relationship("ProjectHistory", cascade="all, delete-orphan")
    layers = relationship("ModelLayer", cascade="all, delete-orphan", order_by="ModelLayer.position")

    @property
    def layer_stack(self):
        """The model stack as the UI shows it (top of the stack first); live numbers for the active model."""
        from .layers import stack_out
        return stack_out(self)


class ModelLayer(Base):
    """
    One model of a project's model stack (like a layer in an image editor). The active model lives in the project's own
    columns; every other model keeps a compressed snapshot of the same fields in `data`.
    """
    __tablename__ = "model_layers"

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)       # 0 = bottom of the stack
    name = Column(String(80), nullable=False, default="Model")
    process = Column(String(8), nullable=False, default="cae")   # cad | cam | cae - workspace the model belongs to
    visible = Column(Boolean, nullable=False, default=True)
    summary = Column(JSON, default=dict)                         # n_nodes, n_elements, element_type, analysis_type, n_parts
    data = Column(LargeBinary, nullable=True)                    # packed snapshot; NULL while the model is active
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class ProjectHistory(Base):
    """One compressed snapshot of a project's model (see history.py)."""
    __tablename__ = "project_history"

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    layer_id = Column(String(32), nullable=False, default="", index=True)   # model of the stack the snapshot belongs to
    seq = Column(Integer, nullable=False)
    label = Column(String(80), default="")
    data = Column(LargeBinary, nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    layer_id = Column(String(32), nullable=True, index=True)  # model of the stack that was solved
    status = Column(String, default="pending")  # pending, running, completed, failed
    solver_used = Column(String, default="")  # always the built-in engine ("gearhead-fea")
    log = Column(Text, default="")
    progress = Column(Float, default=0.0)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    # Results stored as JSON: displacements [[ux,uy,uz],...], von_mises [...], per node
    results = Column(JSON, default=dict)

    project = relationship("Project", back_populates="jobs")
