from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from ...scope import bind_user

from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import InferIn, SketchEdit, SketchIn, SketchOp, SketchPatch
from ..services import sketch2d, sketch_edit
from .deps import apply_patch, get_or_404

router = APIRouter(prefix="/api/cad", tags=["cad-sketches"], dependencies=[Depends(bind_user)])


def sketch_out(s: models.Sketch) -> dict:
    return {
        "id": s.id, "project_id": s.project_id, "name": s.name, "plane": s.plane, "offset": s.offset,
        "entities": s.entities or [], "constraints": s.constraints or [],
        "layers": sketch2d.normalise_layers(s.layers, s.entities),
    }


def _check_plane(plane: str):
    if plane.upper() not in sketch2d.PLANES:
        raise HTTPException(422, f"unknown plane '{plane}' (XY, YZ, XZ, ZX)")


@router.get("/projects/{pid}/sketches")
def list_sketches(pid: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    rows = db.query(models.Sketch).filter(models.Sketch.project_id == pid).order_by(models.Sketch.id).all()
    return [sketch_out(s) for s in rows]


@router.post("/projects/{pid}/sketches", status_code=201)
def create_sketch(pid: int, body: SketchIn, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    _check_plane(body.plane)
    s = models.Sketch(
        project_id=pid, name=body.name, plane=body.plane.upper(), offset=body.offset,
        entities=sketch2d.ensure_ids([dict(e) for e in body.entities]), constraints=list(body.constraints),
        layers=sketch2d.normalise_layers(body.layers, body.entities),
    )
    db.add(s)
    db.commit()
    return sketch_out(s)


@router.post("/projects/{pid}/sketches/import-dxf", status_code=201)
async def import_dxf(pid: int, file: UploadFile = File(...), plane: str = "XY", db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    _check_plane(plane)
    data = await file.read()
    try:
        entities, layers = sketch2d.import_dxf(data, with_layers=True)
    except Exception as exc:
        raise HTTPException(422, f"could not read DXF: {exc}")
    s = models.Sketch(project_id=pid, name=(file.filename or "Imported DXF").rsplit(".", 1)[0], plane=plane.upper(),
                      entities=entities, constraints=[], layers=layers)
    db.add(s)
    db.commit()
    return sketch_out(s)


@router.get("/sketches/{sid}")
def get_sketch(sid: int, db: Session = Depends(get_db)):
    return sketch_out(get_or_404(db, models.Sketch, sid, "sketch"))


@router.put("/sketches/{sid}")
@router.patch("/sketches/{sid}")
def update_sketch(sid: int, body: SketchPatch, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Sketch, sid, "sketch")
    if body.plane is not None:
        _check_plane(body.plane)
        s.plane = body.plane.upper()
    apply_patch(s, body, ["name", "offset"])
    if body.entities is not None:
        s.entities = sketch2d.ensure_ids([dict(e) for e in body.entities])
    if body.constraints is not None:
        s.constraints = list(body.constraints)
    if body.layers is not None:
        s.layers = sketch2d.normalise_layers(body.layers, s.entities)
    db.commit()
    return sketch_out(s)


@router.delete("/sketches/{sid}", status_code=204)
def delete_sketch(sid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Sketch, sid, "sketch"))
    db.commit()


@router.post("/sketches/{sid}/solve")
def solve_sketch(sid: int, apply: bool = True, db: Session = Depends(get_db)):
    """Run the constraint solver. Returns dof / status; with apply=true the solved geometry is saved."""
    s = get_or_404(db, models.Sketch, sid, "sketch")
    res = sketch2d.solve_constraints(s.entities or [], s.constraints or [])
    if apply and res["status"] in ("solved", "fully_constrained"):
        s.entities = res["entities"]
        db.commit()
    return {**res, "sketch": sketch_out(s)}


@router.post("/solve-preview")
def solve_preview(body: SketchIn):
    """Stateless solve used by the editor for live dragging (nothing is stored)."""
    return sketch2d.solve_constraints(sketch2d.ensure_ids([dict(e) for e in body.entities]), list(body.constraints))


@router.post("/sketches/{sid}/edit")
def sketch_edit_op(sid: int, body: SketchEdit, db: Session = Depends(get_db)):
    """Trim / extend / fillet / explode. Re-solves when the sketch has constraints."""
    s = get_or_404(db, models.Sketch, sid, "sketch")
    try:
        ents, cons = sketch_edit.edit(s.entities or [], s.constraints or [], body.op, body.params, body.ids)
    except (KeyError, ValueError, StopIteration) as exc:
        raise HTTPException(422, f"{body.op} failed: {exc or 'entity not found'}")
    solve = None
    if cons:
        solve = sketch2d.solve_constraints(ents, cons)
        if solve["status"] in ("solved", "fully_constrained"):
            ents = solve["entities"]
    s.entities, s.constraints = ents, cons
    db.commit()
    return {**sketch_out(s), "solve": {k: solve[k] for k in ("status", "dof")} if solve else None}


@router.post("/sketch-infer")
def infer_preview(body: InferIn):
    """Constraints implied by the geometry of `ids` (stateless; used while drawing)."""
    ents = sketch2d.ensure_ids([dict(e) for e in body.entities])
    return {"constraints": sketch_edit.infer_constraints(ents, list(body.constraints), body.ids)}


@router.post("/sketches/{sid}/infer")
def infer_sketch(sid: int, db: Session = Depends(get_db)):
    """Auto-constrain the whole sketch (e.g. after a DXF import) and save."""
    s = get_or_404(db, models.Sketch, sid, "sketch")
    new = sketch_edit.infer_constraints(s.entities or [], s.constraints or [])
    s.constraints = list(s.constraints or []) + new
    db.commit()
    return {**sketch_out(s), "added": len(new)}


@router.get("/sketches/{sid}/analysis")
def sketch_analysis(sid: int, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Sketch, sid, "sketch")
    return sketch2d.analyse(s.entities or [])


@router.get("/sketches/{sid}/render")
def sketch_render(sid: int, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Sketch, sid, "sketch")
    return {"polylines": sketch2d.render_polylines(s.entities or [])}


@router.post("/sketches/{sid}/ops")
def sketch_ops(sid: int, body: SketchOp, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Sketch, sid, "sketch")
    ents = [dict(e) for e in (s.entities or [])]
    try:
        new = sketch2d.transform_entities(ents, {"op": body.op, "ids": body.ids, **body.params})
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"sketch op failed: {exc}")
    if body.replace and body.op in ("translate", "rotate", "scale", "mirror"):
        src = set(body.ids) if body.ids else {e["id"] for e in ents}
        ents = [e for e in ents if e["id"] not in src]
    s.entities = ents + new
    db.commit()
    return sketch_out(s)


@router.get("/sketches/{sid}/export")
def export_sketch(sid: int, format: str = "dxf", db: Session = Depends(get_db)):
    s = get_or_404(db, models.Sketch, sid, "sketch")
    fmt = format.lower()
    if fmt == "dxf":
        body, mime = sketch2d.export_dxf(s.entities or [], s.layers), "application/dxf"
    elif fmt == "svg":
        body, mime = sketch2d.export_svg(s.entities or []), "image/svg+xml"
    else:
        raise HTTPException(422, "format must be dxf or svg")
    return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{s.name}.{fmt}"'})
