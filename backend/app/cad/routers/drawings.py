from fastapi import APIRouter, Depends, HTTPException, Response
from ...scope import bind_user

from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import DimensionIn, DrawingIn, DrawingPatch, DrawingViewIn
from ..services import drawing as dw
from ..services import store
from .deps import get_or_404

router = APIRouter(prefix="/api/cad", tags=["cad-drawings"], dependencies=[Depends(bind_user)])


def drawing_out(d: models.Drawing) -> dict:
    return {
        "id": d.id, "project_id": d.project_id, "name": d.name, "sheet": {**dw.default_sheet(), **(d.sheet or {})},
        "views": d.views or [], "dimensions": d.dimensions or [], "notes": d.notes or [],
        "title": {**dw.default_title(d.name), **(d.title or {})},
    }


def _shape(db: Session, project_id: int, part_id: int):
    part = db.get(models.Part, part_id)
    if not part or part.project_id != project_id:
        raise HTTPException(422, f"part {part_id} is not in this project")
    res = store.build_part(db, part)
    if res.shape is None:
        raise HTTPException(422, f"part '{part.name}' has no geometry")
    return res.shape, res.prov, part


def _shapes(db: Session, d: models.Drawing) -> dict:
    out = {}
    for pid in {v.get("part_id") for v in (d.views or [])}:
        part = db.get(models.Part, pid) if pid is not None else None
        if part:
            res = store.build_part(db, part)
            if res.shape is not None:
                out[pid] = (res.shape, res.prov)
    return out


@router.get("/projects/{pid}/drawings")
def list_drawings(pid: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    rows = db.query(models.Drawing).filter(models.Drawing.project_id == pid).order_by(models.Drawing.id).all()
    return [drawing_out(d) for d in rows]


@router.post("/projects/{pid}/drawings", status_code=201)
def create_drawing(pid: int, body: DrawingIn, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    sheet = {**dw.default_sheet(), **body.sheet}
    if sheet["size"] not in dw.SHEETS:
        raise HTTPException(422, f"sheet size must be one of {', '.join(dw.SHEETS)}")
    d = models.Drawing(project_id=pid, name=body.name, sheet=sheet, views=[], dimensions=[], notes=[], title=dict(body.title))
    if body.part_id is not None:
        shape, _, part = _shape(db, pid, body.part_id)
        d.views = dw.standard_views(shape, part.id, sheet)
        d.title = {"title": part.name, "material": part.material, **d.title}
    db.add(d)
    db.commit()
    return drawing_out(d)


@router.get("/drawings/{did}")
def get_drawing(did: int, db: Session = Depends(get_db)):
    return drawing_out(get_or_404(db, models.Drawing, did, "drawing"))


@router.patch("/drawings/{did}")
@router.put("/drawings/{did}")
def update_drawing(did: int, body: DrawingPatch, db: Session = Depends(get_db)):
    d = get_or_404(db, models.Drawing, did, "drawing")
    if body.sheet is not None and body.sheet.get("size", "A3") not in dw.SHEETS:
        raise HTTPException(422, f"sheet size must be one of {', '.join(dw.SHEETS)}")
    for k in ("name", "sheet", "views", "dimensions", "notes", "title"):
        v = getattr(body, k)
        if v is not None:
            setattr(d, k, v)
    db.commit()
    return drawing_out(d)


@router.delete("/drawings/{did}", status_code=204)
def delete_drawing(did: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Drawing, did, "drawing"))
    db.commit()


@router.post("/drawings/{did}/views", status_code=201)
def add_view(did: int, body: DrawingViewIn, db: Session = Depends(get_db)):
    d = get_or_404(db, models.Drawing, did, "drawing")
    if body.kind not in dw.VIEW_DIRS:
        raise HTTPException(422, f"view must be one of {', '.join(dw.VIEW_DIRS)}")
    shape, _, part = _shape(db, d.project_id, body.part_id)
    W, H = dw.sheet_size({**dw.default_sheet(), **(d.sheet or {})})
    scale = body.scale or next((float(v["scale"]) for v in (d.views or []) if v.get("kind") != "iso"), 1.0)
    x = body.x if body.x is not None else W / 2
    y = body.y if body.y is not None else H / 2
    v = dw.new_view(shape, part.id, body.kind, scale, x, y, {v["id"] for v in (d.views or [])})
    d.views = list(d.views or []) + [v]
    db.commit()
    return drawing_out(d)


@router.post("/drawings/{did}/standard-views")
def standard_views(did: int, body: DrawingViewIn, db: Session = Depends(get_db)):
    """Replace the views with front / top / right / iso of a part (dimensions of removed views are dropped)."""
    d = get_or_404(db, models.Drawing, did, "drawing")
    shape, _, part = _shape(db, d.project_id, body.part_id)
    d.views = dw.standard_views(shape, part.id, {**dw.default_sheet(), **(d.sheet or {})})
    ids = {v["id"] for v in d.views}
    d.dimensions = [x for x in (d.dimensions or []) if x.get("view") in ids]
    db.commit()
    return drawing_out(d)


@router.post("/drawings/{did}/dimensions", status_code=201)
def add_dimension(did: int, body: DimensionIn, db: Session = Depends(get_db)):
    d = get_or_404(db, models.Drawing, did, "drawing")
    v = next((x for x in (d.views or []) if x["id"] == body.view), None)
    if v is None:
        raise HTTPException(404, f"view {body.view} not found")
    if body.type not in dw.DIM_TYPES:
        raise HTTPException(422, f"dimension type must be one of {', '.join(dw.DIM_TYPES)}")
    if body.type in ("radius", "diameter") and body.a.get("end") != "c":
        raise HTTPException(422, "radius / diameter dimensions need a circle (pick a circle centre)")
    if body.type not in ("radius", "diameter") and body.b is None:
        raise HTTPException(422, "linear dimensions need two points")
    shape, prov, _ = _shape(db, d.project_id, v["part_id"])
    used = {x.get("id") for x in (d.dimensions or [])}
    n = 1
    while f"d{n}" in used:
        n += 1
    try:
        dim = {"id": f"d{n}", "view": body.view, "type": body.type, "a": dw.make_anchor(shape, prov, body.a),
               "offset": body.offset, "angle": body.angle}
        if body.b is not None and body.type not in ("radius", "diameter"):
            dim["b"] = dw.make_anchor(shape, prov, body.b)
    except (KeyError, IndexError, ValueError) as exc:
        raise HTTPException(422, f"invalid anchor: {exc}")
    if body.text:
        dim["text"] = body.text
    if body.tolerance:
        dim["tolerance"] = body.tolerance
    d.dimensions = list(d.dimensions or []) + [dim]
    db.commit()
    return drawing_out(d)


@router.post("/drawings/{did}/auto-dimensions")
def auto_dimensions(did: int, db: Session = Depends(get_db)):
    d = get_or_404(db, models.Drawing, did, "drawing")
    new = dw.auto_dimensions(d.views or [], _shapes(db, d), d.dimensions or [])
    d.dimensions = list(d.dimensions or []) + new
    db.commit()
    return {**drawing_out(d), "added": len(new)}


@router.get("/drawings/{did}/render")
def render_drawing(did: int, db: Session = Depends(get_db)):
    d = get_or_404(db, models.Drawing, did, "drawing")
    return dw.render(drawing_out(d), _shapes(db, d))


@router.get("/drawings/{did}/export")
def export_drawing(did: int, format: str = "pdf", db: Session = Depends(get_db)):
    d = get_or_404(db, models.Drawing, did, "drawing")
    r = dw.render(drawing_out(d), _shapes(db, d))
    fmt = format.lower()
    name = d.name.replace('"', "")
    if fmt == "pdf":
        return Response(dw.to_pdf(r), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    if fmt == "svg":
        return Response(dw.to_svg(r), media_type="image/svg+xml", headers={"Content-Disposition": f'attachment; filename="{name}.svg"'})
    raise HTTPException(422, "format must be pdf or svg")
