from fastapi import APIRouter, Depends, HTTPException, Response
from ...scope import bind_user

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import get_db
from ..schemas import CamSetupIn, CamSetupPatch
from ..services import cam as camsvc
from ..services import gcode, modeling3d, store
from ..services.geometry_mesh import TriMesh
from .deps import get_or_404

router = APIRouter(prefix="/api/cad", tags=["cad-cam"], dependencies=[Depends(bind_user)])


def setup_out(s: models.CamSetup, with_gcode: bool = False) -> dict:
    out = {
        "id": s.id, "part_id": s.part_id, "name": s.name, "stock": s.stock or {}, "tools": s.tools or [],
        "operations": s.operations or [], "post": s.post or {}, "summary": s.summary or {},
        "has_gcode": bool(s.gcode),
    }
    if with_gcode:
        out["gcode"] = s.gcode
    return out


def _prepare(db: Session, setup: models.CamSetup):
    part = get_or_404(db, models.Part, setup.part_id, "part")
    shape = store.require_shape(db, part)
    diag = shape.BoundingBox().DiagonalLength
    md = modeling3d.tessellate(shape, max(diag * 0.0004, 0.01), 0.15)
    mesh = TriMesh(md["positions"], md["indices"])
    holes = camsvc.detect_holes(shape)
    sketches = store.project_sketches(db, part.project_id)
    return part, shape, md, mesh, holes, sketches


def _setup_dict(s: models.CamSetup) -> dict:
    return {"stock": s.stock or {}, "tools": s.tools or camsvc.DEFAULT_TOOLS, "operations": s.operations or [], "post": s.post or {}}


@router.get("/parts/{part_id}/cam")
def list_setups(part_id: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Part, part_id, "part")
    rows = db.query(models.CamSetup).filter(models.CamSetup.part_id == part_id).order_by(models.CamSetup.id).all()
    return [setup_out(s) for s in rows]


@router.post("/parts/{part_id}/cam", status_code=201)
def create_setup(part_id: int, body: CamSetupIn, db: Session = Depends(get_db)):
    get_or_404(db, models.Part, part_id, "part")
    s = models.CamSetup(
        part_id=part_id, name=body.name, stock=dict(body.stock), tools=body.tools or [dict(t) for t in camsvc.DEFAULT_TOOLS],
        operations=body.operations or [], post={**gcode.DEFAULT_POST, **body.post},
    )
    db.add(s)
    db.commit()
    return setup_out(s)


@router.get("/cam/{sid}")
def get_setup(sid: int, db: Session = Depends(get_db)):
    return setup_out(get_or_404(db, models.CamSetup, sid, "CAM setup"), with_gcode=True)


@router.patch("/cam/{sid}")
@router.put("/cam/{sid}")
def update_setup(sid: int, body: CamSetupPatch, db: Session = Depends(get_db)):
    s = get_or_404(db, models.CamSetup, sid, "CAM setup")
    d = body.model_dump(exclude_unset=True)
    if "name" in d and d["name"]:
        s.name = d["name"]
    if d.get("stock") is not None:
        s.stock = dict(d["stock"])
    if d.get("tools") is not None:
        s.tools = list(d["tools"])
    if d.get("operations") is not None:
        s.operations = list(d["operations"])
    if d.get("post") is not None:
        s.post = dict(d["post"])
    db.commit()
    return setup_out(s)


@router.delete("/cam/{sid}", status_code=204)
def delete_setup(sid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.CamSetup, sid, "CAM setup"))
    db.commit()


@router.post("/cam/{sid}/auto-operations")
def auto_operations(sid: int, db: Session = Depends(get_db)):
    """Feature recognition: propose face / pocket / drill / profile operations for the part."""
    s = get_or_404(db, models.CamSetup, sid, "CAM setup")
    part, shape, md, mesh, holes, sketches = _prepare(db, s)
    tools = {int(t["id"]): t for t in (s.tools or camsvc.DEFAULT_TOOLS)}
    flat = next((t for t in tools.values() if t["type"] == "flat"), None)
    drills = [t for t in tools.values() if t["type"] == "drill"]
    if flat is None:
        raise HTTPException(422, "add a flat end mill to the tool list first")

    def defaults(kind: str, tool_id: int, **over) -> dict:
        base = {p["name"]: p["default"] for p in camsvc.OP_SCHEMAS[kind]["params"]}
        base.update(over)
        base["tool"] = tool_id
        return base

    stock = camsvc.compute_stock(s.stock or {}, mesh)
    ops: list[dict] = []

    def add(kind: str, tool_id: int, **over):
        ops.append({"id": f"op{len(ops) + 1}", "type": kind, "enabled": True, "name": camsvc.OP_SCHEMAS[kind]["label"], "params": defaults(kind, tool_id, **over)})

    if stock.zmax > mesh.zmax + 1e-6:
        add("face", flat["id"], z_final=mesh.zmax)
    floors = [f for f in md["faces"] if f["type"] == "PLANE" and f["normal"][2] > 0.98 and mesh.zmin + 0.01 < f["center"][2] < stock.zmax - 0.01]
    if floors:
        add("pocket", flat["id"], allowance=0.2)
    if holes:
        for d in sorted({round(h["diameter"], 3) for h in holes}):
            fit = next((t for t in drills if abs(t["diameter"] - d) < 0.05), None)
            if fit:
                add("drill", fit["id"], min_diameter=d - 0.05, max_diameter=d + 0.05)
            elif d > flat["diameter"] * 1.05:
                add("drill", flat["id"], min_diameter=d - 0.05, max_diameter=d + 0.05)
    add("profile", flat["id"], side="outside", stepdown=min(flat["diameter"], 4.0))
    s.operations = ops
    db.commit()
    return setup_out(s)


def _run(db: Session, s: models.CamSetup):
    part, shape, md, mesh, holes, sketches = _prepare(db, s)
    setup = _setup_dict(s)
    result = camsvc.generate(setup, mesh, md["faces"], holes, sketches, settings.max_toolpath_points)
    return mesh, result


@router.post("/cam/{sid}/generate")
def generate(sid: int, db: Session = Depends(get_db)):
    s = get_or_404(db, models.CamSetup, sid, "CAM setup")
    try:
        mesh, result = _run(db, s)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    s.gcode = result["gcode"]
    s.summary = {**result["summary"], "stock": result["stock"], "lines": result["gcode"].count("\n")}
    db.commit()
    return {
        "summary": s.summary, "toolpaths": result["toolpaths"], "warnings": result["warnings"], "errors": result["errors"],
        "collisions": result.get("collisions"),
        "stock": result["stock"], "gcode": result["gcode"],
    }


@router.post("/cam/{sid}/simulate")
def simulate(sid: int, resolution: float = 0.0, db: Session = Depends(get_db)):
    """Material-removal simulation of the current operations (height-map / dexel model)."""
    s = get_or_404(db, models.CamSetup, sid, "CAM setup")
    try:
        mesh, result = _run(db, s)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    tools = {int(t["id"]): t for t in (s.tools or camsvc.DEFAULT_TOOLS)}
    stock = camsvc.compute_stock(s.stock or {}, mesh)
    sim = camsvc.simulate(result["events"], tools, stock, mesh, resolution)
    return {**sim, "warnings": result["warnings"], "errors": result["errors"], "stock": result["stock"]}


@router.get("/cam/{sid}/gcode")
def download_gcode(sid: int, db: Session = Depends(get_db)):
    s = get_or_404(db, models.CamSetup, sid, "CAM setup")
    if not s.gcode:
        raise HTTPException(404, "no G-code yet: generate toolpaths first")
    return Response(
        s.gcode, media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{s.name.replace(" ", "_")}.nc"'},
    )
