import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from ...scope import bind_user

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models
from ..config import settings
from ..database import get_db
from ..schemas import FeatureIn, FeatureMove, FeaturePatch, PartIn, PartPatch
from ..services import modeling3d, store
from .deps import apply_patch, get_or_404

router = APIRouter(prefix="/api/cad", tags=["cad-parts"], dependencies=[Depends(bind_user)])


def part_out(p: models.Part, statuses=None) -> dict:
    out = store.part_dict(p)
    if statuses is not None:
        out["statuses"] = statuses
    return out


@router.get("/projects/{pid}/parts")
def list_parts(pid: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    rows = db.query(models.Part).filter(models.Part.project_id == pid).order_by(models.Part.id).all()
    return [part_out(p) for p in rows]


@router.post("/projects/{pid}/parts", status_code=201)
def create_part(pid: int, body: PartIn, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    color = body.color or modeling3d.material(body.material)["color"]
    p = models.Part(project_id=pid, name=body.name, material=body.material, color=color, parameters=dict(body.parameters), features=[])
    db.add(p)
    db.commit()
    return part_out(p)


@router.post("/projects/{pid}/parts/import-step", status_code=201)
async def import_step(pid: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    data = await file.read()
    if b"ISO-10303" not in data[:2000].upper():
        raise HTTPException(422, "file does not look like a STEP file")
    name = f"{uuid.uuid4().hex}.step"
    (settings.uploads_dir / name).write_bytes(data)
    feat = {"id": "f1", "type": "import_step", "name": file.filename, "params": {"file": name}}
    p = models.Part(project_id=pid, name=(file.filename or "Imported").rsplit(".", 1)[0], features=[feat], parameters={})
    db.add(p)
    db.commit()
    res = store.build_part(db, p)
    return part_out(p, res.statuses)


@router.get("/parts/{part_id}")
def get_part(part_id: int, db: Session = Depends(get_db)):
    return part_out(get_or_404(db, models.Part, part_id, "part"))


@router.put("/parts/{part_id}")
@router.patch("/parts/{part_id}")
def update_part(part_id: int, body: PartPatch, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    apply_patch(p, body, ["name", "material", "color"])
    if body.parameters is not None:
        p.parameters = dict(body.parameters)
    db.commit()
    return part_out(p)


@router.delete("/parts/{part_id}", status_code=204)
def delete_part(part_id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Part, part_id, "part"))
    db.commit()


@router.post("/parts/{part_id}/duplicate", status_code=201)
def duplicate_part(part_id: int, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    q = models.Part(
        project_id=p.project_id, name=p.name + " copy", material=p.material, color=p.color,
        parameters=dict(p.parameters or {}), features=[{**f, "params": dict(f["params"])} for f in (p.features or [])],
    )
    db.add(q)
    db.commit()
    return part_out(q)


# ------------------------------------------------------------------ feature tree
def _feature(p: models.Part, fid: str) -> tuple[int, dict]:
    for i, f in enumerate(p.features or []):
        if f.get("id") == fid:
            return i, f
    raise HTTPException(404, f"feature {fid} not found")


@router.post("/parts/{part_id}/features", status_code=201)
def add_feature(part_id: int, body: FeatureIn, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    if body.type not in modeling3d.FEATURE_SCHEMAS:
        raise HTTPException(422, f"unknown feature type '{body.type}'")
    feats = [dict(f) for f in (p.features or [])]
    params = {**modeling3d.default_params(body.type), **body.params}
    feat = {
        "id": modeling3d.new_feature_id(feats), "type": body.type, "params": params,
        "name": body.name or f"{modeling3d.FEATURE_SCHEMAS[body.type]['label']} {len(feats) + 1}", "suppressed": False,
    }
    if body.index is None or body.index >= len(feats):
        feats.append(feat)
    else:
        feats.insert(max(body.index, 0), feat)
    p.features = feats
    db.commit()
    res = store.build_part(db, p)
    return part_out(p, res.statuses)


@router.put("/parts/{part_id}/features/{fid}")
@router.patch("/parts/{part_id}/features/{fid}")
def update_feature(part_id: int, fid: str, body: FeaturePatch, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    feats = [dict(f) for f in (p.features or [])]
    i, f = next(((i, f) for i, f in enumerate(feats) if f.get("id") == fid), (None, None))
    if f is None:
        raise HTTPException(404, f"feature {fid} not found")
    if body.params is not None:
        f["params"] = {**f["params"], **body.params}
    if body.name is not None:
        f["name"] = body.name
    if body.suppressed is not None:
        f["suppressed"] = body.suppressed
    feats[i] = f
    p.features = feats
    db.commit()
    res = store.build_part(db, p)
    return part_out(p, res.statuses)


@router.delete("/parts/{part_id}/features/{fid}")
def delete_feature(part_id: int, fid: str, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    _feature(p, fid)
    p.features = [f for f in (p.features or []) if f.get("id") != fid]
    db.commit()
    res = store.build_part(db, p)
    return part_out(p, res.statuses)


@router.post("/parts/{part_id}/features/{fid}/move")
def move_feature(part_id: int, fid: str, body: FeatureMove, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    i, f = _feature(p, fid)
    feats = [dict(x) for x in (p.features or [])]
    feats.pop(i)
    feats.insert(max(0, min(body.index, len(feats))), f)
    p.features = feats
    db.commit()
    res = store.build_part(db, p)
    return part_out(p, res.statuses)


# ----------------------------------------------------------- persistent references
@router.get("/parts/{part_id}/reference")
def part_reference(part_id: int, indices: str, kind: str = "face", db: Session = Depends(get_db)):
    """Persistent `ref:` selector for faces / edges of the current body (what a viewport pick should store)."""
    if kind not in ("face", "edge"):
        raise HTTPException(422, "kind must be face or edge")
    p = get_or_404(db, models.Part, part_id, "part")
    res = store.build_part(db, p)
    if res.shape is None:
        raise HTTPException(422, "part has no geometry")
    try:
        idx = [int(t) for t in indices.split(",") if t.strip()]
        n = len(res.shape.Faces() if kind == "face" else res.shape.Edges())
        if not idx or any(not 0 <= i < n for i in idx):
            raise ValueError(f"{kind} indices must be in 0..{n - 1}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    from ..services import toponaming
    return {"selector": toponaming.make_reference(res.shape, kind, idx, res.prov)}


@router.post("/parts/{part_id}/stabilize-references")
def stabilize_references(part_id: int, db: Session = Depends(get_db)):
    """Rewrite every idx:/near: selector in the feature tree as a persistent ref: selector."""
    p = get_or_404(db, models.Part, part_id, "part")
    res = modeling3d.rebuild(p.features or [], store.build_context(db, p), upgrade_refs=True)
    new = res.upgraded or []
    changed = sum(1 for a, b in zip(p.features or [], new) if a != b)
    if changed and len(new) == len(p.features or []):
        p.features = new
        db.commit()
    res = store.build_part(db, p)
    return {**part_out(p, res.statuses), "changed": changed}


# -------------------------------------------------------------- geometry output
@router.get("/parts/{part_id}/mesh")
def part_mesh(part_id: int, tolerance: float = 0.0, db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    res = store.build_part(db, p)
    out = {"part": part_out(p, res.statuses), "mesh": None, "properties": None}
    if res.shape is not None:
        out["mesh"] = modeling3d.tessellate(res.shape, tolerance or None)
        out["properties"] = modeling3d.mass_properties(res.shape, p.material)
    return out


@router.get("/parts/{part_id}/export")
def export_part(part_id: int, format: str = "step", db: Session = Depends(get_db)):
    p = get_or_404(db, models.Part, part_id, "part")
    shape = store.require_shape(db, p)
    try:
        data, mime = modeling3d.export_shape(shape, format)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{p.name}.{format.lower()}"'})
