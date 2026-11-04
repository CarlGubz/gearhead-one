"""
The Gearhead project file: one JSON file for all three workspaces.

    {
      "format": "gearhead-project", "version": 3,
      "name": ..., "description": ...,
      <FEA: the active model's keys at the top level + "layers": the whole model stack>   (as in version 2)
      "cfd": {"cases": [{name, description, template, config}]},
      "cad": {"name", "description", "sketches": [...], "parts": [...], "assemblies": [...], "drawings": [...],
              "cam_setups": [...], "uploads": {"<file>.step": "<base64>"}}
    }

Files carry *definitions*, not results (as FEA files always did): FEA jobs, CFD runs and CAD simulation jobs are
recomputed. CAD rows keep their original ids in the file; on import they get new ids and every reference is remapped
(feature params of type sketch / sketchlist / part, drawing views, assembly components and mates, CAM setups).
Uploaded STEP files that parts were built from travel inside the file, so a part opens identically elsewhere.

Readers: version 1 / 2 FEA files ("gearhead-fea-project") still open (FEA only). The FEA keys of a version 3 file are
laid out exactly like version 2, so the FEA part of the format did not change.

This module handles the CFD and CAD sections; the FEA model stack is read / written by app.main (it reuses the
FEA validation code there).
"""
from __future__ import annotations

import base64
import binascii
import re
import uuid
from typing import Any, Dict

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import models, workspaces

FORMAT = "gearhead-project"
VERSION = 3
LEGACY_FEA_FORMAT = "gearhead-fea-project"
FORMATS = (FORMAT, LEGACY_FEA_FORMAT)
MAX_CFD_CASES = 50
MAX_CAD_ROWS = 500
_UPLOAD_NAME = re.compile(r"[0-9a-f]{32}\.step")


def check_format(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict) or data.get("format") not in FORMATS:
        raise HTTPException(422, "This is not a Gearhead project file")


# ============================================================================================ export
def export_sections(db: Session, project: models.Project) -> Dict[str, Any]:
    return {"cfd": _export_cfd(db, project), "cad": _export_cad(db, project)}


def _export_cfd(db: Session, project: models.Project) -> Dict[str, Any]:
    return {"cases": [{"name": c.name, "description": c.description or "", "template": c.template, "config": c.config}
                      for c in reversed(workspaces.cfd_cases_of(db, project))]}   # oldest first


def _export_cad(db: Session, project: models.Project) -> Dict[str, Any]:
    from .cad import models as cm
    from .cad.config import settings as cad_settings

    cp = workspaces.cad_project_for(db, project, create=False)
    if cp is None:
        return {"name": project.name, "description": "", "sketches": [], "parts": [], "assemblies": [], "drawings": [],
                "cam_setups": [], "uploads": {}}
    parts = sorted(cp.parts, key=lambda p: p.id)
    uploads = {}
    for p in parts:
        for f in p.features or []:
            name = str((f.get("params") or {}).get("file") or "") if f.get("type") == "import_step" else ""
            path = cad_settings.uploads_dir / name
            if _UPLOAD_NAME.fullmatch(name) and path.is_file() and name not in uploads:
                uploads[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    setups = (db.query(cm.CamSetup).filter(cm.CamSetup.part_id.in_([p.id for p in parts])).order_by(cm.CamSetup.id).all()
              if parts else [])
    drawings = db.query(cm.Drawing).filter(cm.Drawing.project_id == cp.id).order_by(cm.Drawing.id).all()
    return {
        "name": cp.name, "description": cp.description or "",
        "sketches": [{"id": s.id, "name": s.name, "plane": s.plane, "offset": s.offset, "entities": s.entities or [],
                      "constraints": s.constraints or [], "layers": s.layers or []}
                     for s in sorted(cp.sketches, key=lambda s: s.id)],
        "parts": [{"id": p.id, "name": p.name, "material": p.material, "color": p.color,
                   "parameters": p.parameters or {}, "features": p.features or []} for p in parts],
        "assemblies": [{"id": a.id, "name": a.name,
                        "components": [{"id": c.id, "part_id": c.part_id, "name": c.name, "position": c.position,
                                        "rotation": c.rotation, "fixed": bool(c.fixed)} for c in a.components],
                        "mates": [{"type": m.type, "comp_a": m.comp_a, "comp_b": m.comp_b, "params": m.params or {}}
                                  for m in a.mates]}
                       for a in sorted(cp.assemblies, key=lambda a: a.id)],
        "drawings": [{"name": d.name, "sheet": d.sheet or {}, "views": d.views or [], "dimensions": d.dimensions or [],
                      "notes": d.notes or [], "title": d.title or {}} for d in drawings],
        "cam_setups": [{"part_id": s.part_id, "name": s.name, "stock": s.stock or {}, "tools": s.tools or [],
                        "operations": s.operations or [], "post": s.post or {}} for s in setups],
        "uploads": uploads,
    }


# ============================================================================================ import
def import_sections(db: Session, project: models.Project, data: Dict[str, Any], cfd: bool = True, cad: bool = True) -> dict:
    """Add the CFD cases and CAD documents of a project file to `project`. Returns counts of what was added."""
    added = {"cfd_cases": 0, "cad_sketches": 0, "cad_parts": 0, "cad_assemblies": 0, "cad_drawings": 0, "cad_cam_setups": 0}
    if cfd and isinstance(data.get("cfd"), dict):
        added["cfd_cases"] = _import_cfd(db, project, data["cfd"])
    if cad and isinstance(data.get("cad"), dict):
        added.update(_import_cad(db, project, data["cad"]))
    return added


def _import_cfd(db: Session, project: models.Project, section: Dict[str, Any]) -> int:
    from .cfd.models import CfdCase
    from .cfd.schemas import CaseConfig

    cases = section.get("cases") or []
    if not isinstance(cases, list) or len(cases) > MAX_CFD_CASES:
        raise HTTPException(422, f"Invalid project file: at most {MAX_CFD_CASES} CFD cases")
    for k, c in enumerate(cases):
        if not isinstance(c, dict):
            raise HTTPException(422, f"Invalid project file: CFD case {k + 1} is not an object")
        try:
            cfg = CaseConfig.model_validate(c.get("config") or {})
        except ValidationError as exc:
            raise HTTPException(422, f"Invalid project file: CFD case {k + 1}: {exc.errors()[0].get('msg')}")
        db.add(CfdCase(project_id=project.id, name=str(c.get("name") or f"Case {k + 1}")[:200],
                       description=str(c.get("description") or "")[:4000],
                       template=(str(c["template"])[:64] if c.get("template") else None),
                       config=cfg.model_dump(mode="json")))
    db.flush()
    return len(cases)


def _remap_feature(feat: dict, sketch_map: dict, part_map: dict, schemas: dict) -> dict:
    """New ids in the params that point at other CAD rows (types sketch / sketchlist / part)."""
    feat = {**feat, "params": dict(feat.get("params") or {})}
    for spec in (schemas.get(feat.get("type")) or {}).get("params", []):
        key, kind = spec.get("name") or spec.get("key"), spec.get("type")
        if key not in feat["params"]:
            continue
        val = feat["params"][key]
        try:
            if kind == "sketch" and val not in (None, ""):
                feat["params"][key] = sketch_map.get(int(val), val)
            elif kind == "sketchlist" and isinstance(val, list):
                feat["params"][key] = [sketch_map.get(int(v), v) for v in val]
            elif kind == "part" and val not in (None, ""):
                feat["params"][key] = part_map.get(int(val), val)
        except (TypeError, ValueError):
            pass  # an expression or a broken reference: left as is, the feature reports it
    return feat


def _import_cad(db: Session, project: models.Project, sec: Dict[str, Any]) -> dict:
    from .cad import models as cm
    from .cad.config import settings as cad_settings
    from .cad.services import modeling3d

    lists = {k: sec.get(k) or [] for k in ("sketches", "parts", "assemblies", "drawings", "cam_setups")}
    if any(not isinstance(v, list) for v in lists.values()) or sum(len(v) for v in lists.values()) > MAX_CAD_ROWS:
        raise HTTPException(422, f"Invalid project file: the CAD section must hold lists of at most {MAX_CAD_ROWS} rows")
    cp = workspaces.cad_project_for(db, project)
    # uploaded STEP files: stored under fresh names (never overwrite), features are pointed at them
    file_map = {}
    for name, b64 in (sec.get("uploads") or {}).items():
        if not _UPLOAD_NAME.fullmatch(str(name)):
            continue
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, TypeError, ValueError):
            raise HTTPException(422, f"Invalid project file: upload {name} is not base64")
        new = f"{uuid.uuid4().hex}.step"
        (cad_settings.uploads_dir / new).write_bytes(raw)
        file_map[name] = new
    try:
        sketch_map = {}
        for s in lists["sketches"]:
            row = cm.Sketch(project_id=cp.id, name=str(s.get("name") or "Sketch")[:200], plane=str(s.get("plane") or "XY")[:16],
                            offset=float(s.get("offset") or 0.0), entities=list(s.get("entities") or []),
                            constraints=list(s.get("constraints") or []), layers=list(s.get("layers") or []))
            db.add(row)
            db.flush()
            if s.get("id") is not None:
                sketch_map[int(s["id"])] = row.id
        part_map, part_rows = {}, []
        for p in lists["parts"]:
            row = cm.Part(project_id=cp.id, name=str(p.get("name") or "Part")[:200], material=str(p.get("material") or "steel")[:64],
                          color=str(p.get("color") or "#8aa4c8")[:16], parameters=dict(p.get("parameters") or {}), features=[])
            db.add(row)
            db.flush()
            if p.get("id") is not None:
                part_map[int(p["id"])] = row.id
            part_rows.append((row, p))
        for row, p in part_rows:
            feats = []
            for f in p.get("features") or []:
                f = _remap_feature(dict(f), sketch_map, part_map, modeling3d.FEATURE_SCHEMAS)
                if f.get("type") == "import_step":
                    old = str((f.get("params") or {}).get("file") or "")
                    f["params"]["file"] = file_map.get(old, old)
                feats.append(f)
            row.features = feats
        for a in lists["assemblies"]:
            arow = cm.Assembly(project_id=cp.id, name=str(a.get("name") or "Assembly")[:200])
            db.add(arow)
            db.flush()
            comp_map = {}
            for c in a.get("components") or []:
                pid = part_map.get(int(c.get("part_id")))
                if pid is None:
                    continue
                crow = cm.AssemblyComponent(assembly_id=arow.id, part_id=pid, name=str(c.get("name") or "Component")[:200],
                                            position=list(c.get("position") or [0.0, 0.0, 0.0]),
                                            rotation=list(c.get("rotation") or [0.0, 0.0, 0.0]), fixed=bool(c.get("fixed")))
                db.add(crow)
                db.flush()
                if c.get("id") is not None:
                    comp_map[int(c["id"])] = crow.id
            for m in a.get("mates") or []:
                ca, cb = comp_map.get(int(m.get("comp_a", -1))), comp_map.get(int(m.get("comp_b", -1)))
                if ca and cb:
                    db.add(cm.Mate(assembly_id=arow.id, type=str(m.get("type") or "flush")[:32], comp_a=ca, comp_b=cb,
                                   params=dict(m.get("params") or {})))
        n_dw = 0
        for d in lists["drawings"]:
            views = [{**v, "part_id": part_map.get(int(v["part_id"]), v["part_id"])} if isinstance(v, dict) and v.get("part_id") is not None
                     else v for v in d.get("views") or []]
            db.add(cm.Drawing(project_id=cp.id, name=str(d.get("name") or "Drawing")[:200], sheet=dict(d.get("sheet") or {}),
                              views=views, dimensions=list(d.get("dimensions") or []), notes=list(d.get("notes") or []),
                              title=dict(d.get("title") or {})))
            n_dw += 1
        n_cam = 0
        for s in lists["cam_setups"]:
            pid = part_map.get(int(s.get("part_id", -1)))
            if pid is None:
                continue
            db.add(cm.CamSetup(part_id=pid, name=str(s.get("name") or "Setup 1")[:200], stock=dict(s.get("stock") or {}),
                               tools=list(s.get("tools") or []), operations=list(s.get("operations") or []),
                               post=dict(s.get("post") or {})))
            n_cam += 1
        db.flush()
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(422, f"Invalid project file: CAD section: {exc}")
    return {"cad_sketches": len(lists["sketches"]), "cad_parts": len(part_rows), "cad_assemblies": len(lists["assemblies"]),
            "cad_drawings": n_dw, "cad_cam_setups": n_cam}
