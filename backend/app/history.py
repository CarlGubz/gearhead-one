"""
Undo / redo for project edits.

A project keeps up to MAX_STATES compressed snapshots of its model (mesh, parts, materials, supports, loads, constraints,
sets, settings...). `projects.history_pos` points at the current state and `projects.history_labels` names every state
(label i = the edit that produced state i), so project responses carry what the UI needs without extra queries.

History belongs to one model of the project's model stack (see layers.py): rows carry the active model's `layer_id`, and
switching models swaps `history_pos` / `history_labels` together with the rest of the model.
"""
from __future__ import annotations

import json
import zlib
from typing import Any, Dict, List

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from . import models

MAX_STATES = 20

SNAP_FIELDS = [
    "nodes", "elements", "element_type", "surface_faces", "surface_parts", "surface_mid", "parts", "materials", "material",
    "boundary_conditions", "loads", "constraints", "reference_points", "node_sets", "settings",
    "analysis_type", "n_modes", "gravity",
]


def _layer_key(project: models.Project) -> str:
    return project.active_layer_id or ""


def _rows(db: Session, project: models.Project):
    return db.query(models.ProjectHistory).filter(models.ProjectHistory.project_id == project.id,
                                                  models.ProjectHistory.layer_id == _layer_key(project))


def _pack(state: Dict[str, Any]) -> bytes:
    return zlib.compress(json.dumps(state, separators=(",", ":")).encode("utf-8"), 3)


def _unpack(blob: bytes) -> Dict[str, Any]:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def capture(project: models.Project, before: bool = False) -> Dict[str, Any]:
    """Current model state; with before=True attributes changed in this transaction report their previous value."""
    insp = sa_inspect(project)
    state: Dict[str, Any] = {}
    for f in SNAP_FIELDS:
        val = getattr(project, f)
        if before:
            h = insp.attrs[f].history
            if h.has_changes() and h.deleted:
                val = h.deleted[0]
        state[f] = val
    return state


def info(project: models.Project) -> Dict[str, Any]:
    labels = list(project.history_labels or [])
    pos = project.history_pos if project.history_pos is not None else -1
    can_undo = pos > 0
    can_redo = 0 <= pos < len(labels) - 1
    return {
        "position": pos, "count": len(labels), "can_undo": can_undo, "can_redo": can_redo,
        "undo_label": labels[pos] if can_undo else None,
        "redo_label": labels[pos + 1] if can_redo else None,
    }


def record(db: Session, project: models.Project, label: str, before_state: Dict[str, Any]) -> None:
    """Append the project's current (post-edit) state to its history, dropping any redo branch."""
    labels: List[str] = list(project.history_labels or [])
    pos = project.history_pos if project.history_pos is not None else -1
    q = _rows(db, project)
    q.filter(models.ProjectHistory.seq > pos).delete(synchronize_session=False)
    labels = labels[: pos + 1]
    if pos < 0:  # first edit ever: the state before it becomes the first history entry
        db.add(models.ProjectHistory(project_id=project.id, layer_id=_layer_key(project), seq=0, label="Initial",
                                     data=_pack(before_state)))
        labels, pos = ["Initial"], 0
    db.add(models.ProjectHistory(project_id=project.id, layer_id=_layer_key(project), seq=pos + 1, label=label[:80],
                                 data=_pack(capture(project))))
    labels.append(label[:80])
    pos += 1
    db.flush()  # rows must be in the database before pruning renumbers them
    while len(labels) > MAX_STATES:  # forget the oldest state
        q.filter(models.ProjectHistory.seq == 0).delete(synchronize_session=False)
        q.update({models.ProjectHistory.seq: models.ProjectHistory.seq - 1}, synchronize_session=False)
        labels.pop(0)
        pos -= 1
    project.history_labels = labels
    project.history_pos = pos


def _restore(db: Session, project: models.Project, seq: int) -> None:
    row = _rows(db, project).filter(models.ProjectHistory.seq == seq).first()
    if row is None:
        raise LookupError("History entry missing")
    state = _unpack(row.data)
    for f in SNAP_FIELDS:
        if f in state:
            setattr(project, f, state[f])
        elif f == "surface_mid":          # snapshots from before quadratic picking: rebuilt on demand (see schemas.ProjectOut)
            project.surface_mid = []
    project.history_pos = seq


def goto(db: Session, project: models.Project, seq: int) -> None:
    """Jump straight to history state `seq` (several undo / redo steps at once)."""
    if not 0 <= seq < len(project.history_labels or []):
        raise ValueError("No such history state")
    _restore(db, project, seq)


def undo(db: Session, project: models.Project) -> None:
    pos = project.history_pos if project.history_pos is not None else -1
    if pos <= 0:
        raise ValueError("Nothing to undo")
    _restore(db, project, pos - 1)


def redo(db: Session, project: models.Project) -> None:
    pos = project.history_pos if project.history_pos is not None else -1
    if pos < 0 or pos >= len(project.history_labels or []) - 1:
        raise ValueError("Nothing to redo")
    _restore(db, project, pos + 1)
