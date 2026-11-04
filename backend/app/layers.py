"""
Model stack: several models (meshes + their whole setup) in one project file, ordered like layers in an image editor.

The project's own columns (nodes, elements, parts, materials, supports, loads, settings, ... - see history.SNAP_FIELDS)
always hold the *active* model, so every existing endpoint, the solver and the AI assistant keep working on "the model"
unchanged. Each other model of the stack is a `ModelLayer` row holding a compressed snapshot of the same fields plus its
own undo history position; activating a model swaps the snapshots. Undo history rows and jobs are tagged with the model
they belong to, so undo / redo and "latest results" stay per model.

`process` tags the workspace a model belongs to (cad | cam | cae); it is metadata the UI uses to pick its toolset.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import history, models

MAX_LAYERS = 20
PROCESSES = ("cad", "cam", "cae")


def blank_model() -> Dict[str, Any]:
    """Every model field of an empty model (a new project, or "New model" in the stack)."""
    return {
        "nodes": [], "elements": [], "surface_faces": [], "surface_mid": [], "surface_parts": [], "element_type": "C3D4",
        "material": {"name": "Steel", "E": 210e9, "nu": 0.3, "density": 7850.0, "conductivity": 50.0,
                     "specific_heat": 490.0, "alpha": 12e-6, "yield_stress": 0.0, "hardening": 0.0},
        "boundary_conditions": [], "loads": [], "constraints": [], "reference_points": [], "node_sets": [],
        "materials": [], "parts": [], "analysis_type": "static", "n_modes": 6, "settings": {},
        "gravity": {"enabled": False, "x": 0.0, "y": 0.0, "z": -9.81},
    }


def summary(project: models.Project) -> Dict[str, Any]:
    """Small numbers shown on a model's row in the stack."""
    elems = project.elements or []
    return {
        "n_nodes": len(project.nodes or []), "n_elements": len(elems), "element_type": project.element_type or "C3D4",
        "analysis_type": project.analysis_type or "static", "n_parts": len(project.parts or []) or (1 if elems else 0),
        "n_setup": len(project.boundary_conditions or []) + len(project.loads or []),
    }


def _out(layer: models.ModelLayer, active: bool, live: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"id": layer.id, "name": layer.name, "process": layer.process or "cae", "visible": bool(layer.visible),
            "position": layer.position, "active": active, "summary": live if live is not None else (layer.summary or {}),
            "updated_at": layer.updated_at.isoformat() if layer.updated_at else None}


def stack_out(project: models.Project) -> List[Dict[str, Any]]:
    """The stack, top first. A project that predates the stack is served as a one-model stack."""
    rows = sorted(project.layers or [], key=lambda l: l.position, reverse=True)
    if not rows:
        return [{"id": project.active_layer_id or "", "name": "Model 1", "process": "cae", "visible": True,
                 "position": 0, "active": True, "summary": summary(project)}]
    return [_out(l, l.id == project.active_layer_id, summary(project) if l.id == project.active_layer_id else None)
            for l in rows]


# -------------------------------------------------------------- snapshots
def _state(project: models.Project) -> Dict[str, Any]:
    state = history.capture(project)
    state["history_pos"] = project.history_pos if project.history_pos is not None else -1
    state["history_labels"] = list(project.history_labels or [])
    return state


def _apply(project: models.Project, state: Dict[str, Any]) -> None:
    base = blank_model()
    for f in history.SNAP_FIELDS:
        setattr(project, f, state.get(f, base.get(f)))
    project.history_pos = state.get("history_pos", -1)
    project.history_labels = list(state.get("history_labels") or [])


def unpack(layer: models.ModelLayer) -> Dict[str, Any]:
    return history._unpack(layer.data) if layer.data else {}


def model_state(project: models.Project, layer: models.ModelLayer) -> Dict[str, Any]:
    """Model fields of any layer: live for the active one, from its snapshot otherwise."""
    return _state(project) if layer.id == project.active_layer_id else unpack(layer)


def _stash(project: models.Project, layer: models.ModelLayer) -> None:
    layer.data = history._pack(_state(project))
    layer.summary = summary(project)
    layer.updated_at = models.utcnow()


def _rows(project: models.Project) -> List[models.ModelLayer]:
    return sorted(project.layers or [], key=lambda l: l.position)


def _get(project: models.Project, layer_id: str) -> models.ModelLayer:
    layer = next((l for l in project.layers or [] if l.id == layer_id), None)
    if layer is None:
        raise KeyError(layer_id)
    return layer


# ------------------------------------------------------------- operations
def ensure(db: Session, project: models.Project, name: str = "Model 1", process: str = "cae") -> models.ModelLayer:
    """Give a project its first model row (new projects, and projects created before the stack existed)."""
    if project.layers:
        return next((l for l in project.layers if l.id == project.active_layer_id), _rows(project)[-1])
    if project.id is None:
        db.flush()
    layer = models.ModelLayer(id=models.gen_id(), project_id=project.id, position=0, name=name[:80],
                              process=process if process in PROCESSES else "cae", summary=summary(project))
    project.layers.append(layer)
    project.active_layer_id = layer.id
    # history and results recorded before the stack existed belong to this model
    db.query(models.ProjectHistory).filter(models.ProjectHistory.project_id == project.id,
                                           models.ProjectHistory.layer_id == "").update(
        {models.ProjectHistory.layer_id: layer.id}, synchronize_session=False)
    db.query(models.Job).filter(models.Job.project_id == project.id, models.Job.layer_id.is_(None)).update(
        {models.Job.layer_id: layer.id}, synchronize_session=False)
    return layer


def activate(db: Session, project: models.Project, layer_id: str) -> None:
    ensure(db, project)
    target = _get(project, layer_id)
    if target.id == project.active_layer_id:
        return
    current = _get(project, project.active_layer_id)
    _stash(project, current)
    _apply(project, unpack(target))
    target.data = None
    target.visible = True
    project.active_layer_id = target.id


def add(db: Session, project: models.Project, name: str, process: str = "cae",
        duplicate_of: Optional[str] = None) -> models.ModelLayer:
    """Put a new model on top of the stack (empty, or a copy of another model without its undo history) and activate it."""
    ensure(db, project)
    if len(project.layers) >= MAX_LAYERS:
        raise ValueError(f"A project holds at most {MAX_LAYERS} models")
    if process not in PROCESSES:
        raise ValueError(f"process must be one of: {', '.join(PROCESSES)}")
    if duplicate_of is not None:
        state = dict(model_state(project, _get(project, duplicate_of)))
    else:
        state = blank_model()
    state["history_pos"], state["history_labels"] = -1, []
    _stash(project, _get(project, project.active_layer_id))
    layer = models.ModelLayer(id=models.gen_id(), project_id=project.id, name=name.strip()[:80] or "Model",
                              position=max(l.position for l in project.layers) + 1, process=process)
    project.layers.append(layer)
    _apply(project, state)
    project.active_layer_id = layer.id
    layer.summary = summary(project)
    return layer


def update(project: models.Project, layer_id: str, name: Optional[str] = None, process: Optional[str] = None,
           visible: Optional[bool] = None) -> None:
    layer = _get(project, layer_id)
    if name is not None:
        if not name.strip():
            raise ValueError("A model needs a name")
        layer.name = name.strip()[:80]
    if process is not None:
        if process not in PROCESSES:
            raise ValueError(f"process must be one of: {', '.join(PROCESSES)}")
        layer.process = process
    if visible is not None:
        layer.visible = bool(visible) or layer.id == project.active_layer_id   # the active model is always shown
    layer.updated_at = models.utcnow()


def move(project: models.Project, layer_id: str, direction: str) -> None:
    """Move a model one place up (towards the top of the stack) or down."""
    rows = _rows(project)
    k = next((i for i, l in enumerate(rows) if l.id == layer_id), None)
    if k is None:
        raise KeyError(layer_id)
    j = k + 1 if direction == "up" else k - 1
    if direction not in ("up", "down") or not 0 <= j < len(rows):
        raise ValueError("The model cannot move further in that direction")
    rows[k].position, rows[j].position = rows[j].position, rows[k].position


def delete(db: Session, project: models.Project, layer_id: str) -> None:
    """Remove a model, its undo history and its results. The model below it (or above) becomes active if needed."""
    ensure(db, project)
    rows = _rows(project)
    layer = _get(project, layer_id)
    if len(rows) < 2:
        raise ValueError("A project needs at least one model")
    if layer.id == project.active_layer_id:
        k = rows.index(layer)
        activate(db, project, rows[k - 1].id if k > 0 else rows[k + 1].id)
    db.query(models.ProjectHistory).filter(models.ProjectHistory.project_id == project.id,
                                           models.ProjectHistory.layer_id == layer.id).delete(synchronize_session=False)
    db.query(models.Job).filter(models.Job.project_id == project.id, models.Job.layer_id == layer.id).delete(
        synchronize_session=False)
    project.layers.remove(layer)
    db.delete(layer)
