"""Glue between the database and the geometry kernels (with a small build cache)."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Optional

import cadquery as cq
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from . import modeling3d

_CACHE: "OrderedDict[str, modeling3d.BuildResult]" = OrderedDict()
_CACHE_MAX = 48


def part_dict(p: models.Part) -> dict:
    return {
        "id": p.id, "project_id": p.project_id, "name": p.name, "material": p.material, "color": p.color,
        "parameters": p.parameters or {}, "features": p.features or [], "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def project_sketches(db: Session, project_id: int) -> dict[int, dict]:
    rows = db.query(models.Sketch).filter(models.Sketch.project_id == project_id).all()
    return {s.id: {"entities": s.entities or [], "plane": s.plane, "offset": s.offset, "name": s.name} for s in rows}


def _others(db: Session, part: models.Part) -> dict[int, dict]:
    return {
        p.id: {"features": p.features or [], "parameters": p.parameters or {}}
        for p in db.query(models.Part).filter(models.Part.project_id == part.project_id).all()
    }


def build_context(db: Session, part: models.Part) -> modeling3d.BuildContext:
    others = _others(db, part)
    return modeling3d.BuildContext(
        sketches=project_sketches(db, part.project_id), parameters=part.parameters or {},
        part_lookup=lambda pid: others.get(pid), uploads_dir=settings.uploads_dir,
    )


def build_part(db: Session, part: models.Part) -> modeling3d.BuildResult:
    sketches = project_sketches(db, part.project_id)
    others = _others(db, part)
    key = hashlib.sha1(
        json.dumps({"part": part.id, "sk": sketches, "parts": others}, sort_keys=True, default=str).encode()
    ).hexdigest()
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    ctx = modeling3d.BuildContext(
        sketches=sketches,
        parameters=part.parameters or {},
        part_lookup=lambda pid: others.get(pid),
        uploads_dir=settings.uploads_dir,
    )
    res = modeling3d.rebuild(part.features or [], ctx)
    _CACHE[key] = res
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return res


def part_shape_or_none(db: Session, part: models.Part) -> Optional[cq.Shape]:
    return build_part(db, part).shape


def require_shape(db: Session, part: models.Part) -> cq.Shape:
    from fastapi import HTTPException

    res = build_part(db, part)
    if res.shape is None:
        errs = [s["error"] for s in res.statuses if s.get("error")]
        raise HTTPException(422, "part has no geometry" + (f" ({errs[0]})" if errs else ""))
    return res.shape
