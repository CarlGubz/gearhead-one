"""Shared router helpers.

Every CAD route looks its rows up through `get_or_404`, so that is where ownership is enforced: the row is walked up
to its CAD project, whose Gearhead project must belong to the signed-in user (bound by `app.scope.bind_user`, a
dependency of every CAD router). Rows of other users answer 404, exactly like missing rows.
"""
from typing import Optional, Type, TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...scope import owns_project
from .. import models

T = TypeVar("T")


def cad_project_of(db: Session, obj) -> Optional[models.CadProject]:
    """The CAD project a CAD row belongs to (None when the chain is broken)."""
    if obj is None:
        return None
    if isinstance(obj, models.CadProject):
        return obj
    if isinstance(obj, (models.Sketch, models.Part, models.Assembly, models.Drawing)):
        return db.get(models.CadProject, obj.project_id)
    if isinstance(obj, (models.AssemblyComponent, models.Mate)):
        return cad_project_of(db, db.get(models.Assembly, obj.assembly_id))
    if isinstance(obj, models.CamSetup):
        return cad_project_of(db, db.get(models.Part, obj.part_id))
    if isinstance(obj, models.SimulationJob):
        if obj.part_id is not None:
            return cad_project_of(db, db.get(models.Part, obj.part_id))
        return cad_project_of(db, db.get(models.Assembly, obj.assembly_id))
    return None


def is_owned(db: Session, obj) -> bool:
    cp = cad_project_of(db, obj)
    return cp is not None and owns_project(db, cp.project_id)


def get_or_404(db: Session, model: Type[T], id_: int, what: str = "") -> T:
    obj = db.get(model, id_)
    if obj is None or not is_owned(db, obj):
        raise HTTPException(404, f"{what or model.__name__} {id_} not found")
    return obj


def apply_patch(obj, patch, fields: list[str]):
    data = patch.model_dump(exclude_unset=True)
    for f in fields:
        if f in data and data[f] is not None:
            setattr(obj, f, data[f])
