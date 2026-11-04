from fastapi import APIRouter, Depends, HTTPException, Response
from ...scope import bind_user

from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import AssemblyIn, ComponentIn, ComponentPatch, MateIn
from ..services import assembly as asm
from ..services import mate_solver, modeling3d, store
from .deps import apply_patch, get_or_404

router = APIRouter(prefix="/api/cad", tags=["cad-assemblies"], dependencies=[Depends(bind_user)])


def comp_dict(c: models.AssemblyComponent) -> dict:
    return {
        "id": c.id, "assembly_id": c.assembly_id, "part_id": c.part_id, "name": c.name,
        "position": c.position, "rotation": c.rotation, "fixed": c.fixed,
    }


def mate_dict(m: models.Mate) -> dict:
    return {"id": m.id, "assembly_id": m.assembly_id, "type": m.type, "comp_a": m.comp_a, "comp_b": m.comp_b, "params": m.params or {}}


def assembly_out(a: models.Assembly) -> dict:
    return {
        "id": a.id, "project_id": a.project_id, "name": a.name,
        "components": [comp_dict(c) for c in a.components], "mates": [mate_dict(m) for m in a.mates],
    }


def _shapes(db: Session, a: models.Assembly) -> dict:
    out = {}
    for pid in {c.part_id for c in a.components}:
        part = db.get(models.Part, pid)
        if part:
            shp = store.part_shape_or_none(db, part)
            if shp is not None:
                out[pid] = shp
    return out


@router.get("/projects/{pid}/assemblies")
def list_assemblies(pid: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    rows = db.query(models.Assembly).filter(models.Assembly.project_id == pid).order_by(models.Assembly.id).all()
    return [assembly_out(a) for a in rows]


@router.post("/projects/{pid}/assemblies", status_code=201)
def create_assembly(pid: int, body: AssemblyIn, db: Session = Depends(get_db)):
    get_or_404(db, models.Project, pid, "project")
    a = models.Assembly(project_id=pid, name=body.name)
    db.add(a)
    db.commit()
    return assembly_out(a)


@router.get("/assemblies/{aid}")
def get_assembly(aid: int, db: Session = Depends(get_db)):
    return assembly_out(get_or_404(db, models.Assembly, aid, "assembly"))


@router.patch("/assemblies/{aid}")
def rename_assembly(aid: int, body: AssemblyIn, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    a.name = body.name
    db.commit()
    return assembly_out(a)


@router.delete("/assemblies/{aid}", status_code=204)
def delete_assembly(aid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Assembly, aid, "assembly"))
    db.commit()


# ---------------------------------------------------------------- components
@router.post("/assemblies/{aid}/components", status_code=201)
def add_component(aid: int, body: ComponentIn, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    part = get_or_404(db, models.Part, body.part_id, "part")
    if part.project_id != a.project_id:
        raise HTTPException(422, "part belongs to a different project")
    n = sum(1 for c in a.components if c.part_id == part.id) + 1
    c = models.AssemblyComponent(
        assembly_id=aid, part_id=part.id, name=body.name or (part.name if n == 1 else f"{part.name} <{n}>"),
        position=list(body.position), rotation=list(body.rotation), fixed=body.fixed or (len(a.components) == 0),
    )
    db.add(c)
    db.commit()
    return comp_dict(c)


@router.patch("/components/{cid}")
@router.put("/components/{cid}")
def update_component(cid: int, body: ComponentPatch, db: Session = Depends(get_db)):
    c = get_or_404(db, models.AssemblyComponent, cid, "component")
    apply_patch(c, body, ["name", "fixed"])
    if body.position is not None:
        c.position = list(body.position)
    if body.rotation is not None:
        c.rotation = list(body.rotation)
    db.commit()
    return comp_dict(c)


@router.delete("/components/{cid}", status_code=204)
def delete_component(cid: int, db: Session = Depends(get_db)):
    c = get_or_404(db, models.AssemblyComponent, cid, "component")
    for m in db.query(models.Mate).filter(models.Mate.assembly_id == c.assembly_id).all():
        if cid in (m.comp_a, m.comp_b):
            db.delete(m)
    db.delete(c)
    db.commit()


# --------------------------------------------------------------------- mates
@router.post("/assemblies/{aid}/mates", status_code=201)
def add_mate(aid: int, body: MateIn, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    ids = {c.id for c in a.components}
    if body.comp_a not in ids or body.comp_b not in ids or body.comp_a == body.comp_b:
        raise HTTPException(422, "comp_a and comp_b must be two different components of this assembly")
    if body.type not in mate_solver.MATE_TYPES:
        raise HTTPException(422, f"mate type must be one of: {', '.join(mate_solver.MATE_TYPES)}")
    m = models.Mate(assembly_id=aid, type=body.type, comp_a=body.comp_a, comp_b=body.comp_b, params=dict(body.params))
    db.add(m)
    db.commit()
    return mate_dict(m)


@router.put("/mates/{mid}")
def update_mate(mid: int, body: MateIn, db: Session = Depends(get_db)):
    m = get_or_404(db, models.Mate, mid, "mate")
    if body.type not in mate_solver.MATE_TYPES:
        raise HTTPException(422, f"mate type must be one of: {', '.join(mate_solver.MATE_TYPES)}")
    m.type, m.comp_a, m.comp_b, m.params = body.type, body.comp_a, body.comp_b, dict(body.params)
    db.commit()
    return mate_dict(m)


@router.delete("/mates/{mid}", status_code=204)
def delete_mate(mid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Mate, mid, "mate"))
    db.commit()


# ------------------------------------------------------------------ solve & analysis
def _components_for_kernel(a: models.Assembly) -> list[dict]:
    return [comp_dict(c) for c in a.components]


@router.post("/assemblies/{aid}/solve")
def solve_assembly(aid: int, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    shapes = _shapes(db, a)
    missing = [c.name for c in a.components if c.part_id not in shapes]
    if missing:
        raise HTTPException(422, f"parts without geometry: {', '.join(missing)}")
    res = asm.solve(_components_for_kernel(a), [mate_dict(m) for m in a.mates], shapes)
    for c in a.components:
        pose = res["components"][c.id]
        c.position, c.rotation = pose["position"], pose["rotation"]
    db.commit()
    return {"mate_status": res["mate_status"], "dof": res["dof"], "status": res["status"], "assembly": assembly_out(a)}


@router.get("/assemblies/{aid}/mesh")
def assembly_mesh(aid: int, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    shapes = _shapes(db, a)
    meshes = {pid: modeling3d.tessellate(shp, max(shp.BoundingBox().DiagonalLength * 0.002, 0.02)) for pid, shp in shapes.items()}
    comps = []
    for c in a.components:
        part = db.get(models.Part, c.part_id)
        M = asm.euler_matrix(c.position, c.rotation)
        comps.append({**comp_dict(c), "color": part.color if part else "#888888", "matrix": M.ravel().tolist(), "has_geometry": c.part_id in shapes})
    return {"components": comps, "meshes": meshes, "assembly": assembly_out(a)}


@router.get("/assemblies/{aid}/interference")
def assembly_interference(aid: int, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    hits = asm.interference(_components_for_kernel(a), _shapes(db, a))
    return {"count": len(hits), "interferences": hits}


@router.get("/assemblies/{aid}/bom")
def assembly_bom(aid: int, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    parts = {pid: store.part_dict(db.get(models.Part, pid)) for pid in {c.part_id for c in a.components}}
    rows = asm.bill_of_materials(_components_for_kernel(a), parts, _shapes(db, a))
    return {"rows": rows, "total_mass_kg": sum(r["total_mass_kg"] for r in rows), "total_parts": sum(r["quantity"] for r in rows)}


@router.get("/assemblies/{aid}/export")
def export_assembly(aid: int, format: str = "step", db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assembly, aid, "assembly")
    if format.lower() not in ("step", "stp"):
        raise HTTPException(422, "assembly export supports step")
    data = asm.export_assembly_step(_components_for_kernel(a), _shapes(db, a))
    return Response(data, media_type="application/step", headers={"Content-Disposition": f'attachment; filename="{a.name}.step"'})
