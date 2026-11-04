"""Seeds a demo project so a fresh install has something to explore in every workspace."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models
from . import cam as camsvc
from . import gcode, modeling3d


def _feat(fid: str, ftype: str, name: str, **params) -> dict:
    return {"id": fid, "type": ftype, "name": name, "suppressed": False, "params": {**modeling3d.default_params(ftype), **params}}


DEMO_NAME = "Demo - Mounting plate & pins"
DEMO_DESCRIPTION = "Parametric plate, bolts, assembly mates, CAM setup and FEA-ready model."


def create_demo(db: Session, proj: models.Project | None = None, project_id: str | None = None) -> models.Project:
    """Seed the demo into `proj` (an existing CAD project), or into a new CAD project linked to Gearhead project
    `project_id`."""
    if proj is None:
        proj = models.Project(project_id=project_id, name=DEMO_NAME, description=DEMO_DESCRIPTION)
        db.add(proj)
        db.flush()

    sk_plate = models.Sketch(
        project_id=proj.id, name="Plate outline", plane="XY",
        entities=[{"id": "e1", "type": "rect", "p1": [0, 0], "p2": [80, 50]}],
        constraints=[
            {"type": "fixed", "a": "e1.p1", "value": [0, 0]},
            {"type": "horizontal_distance", "a": "e1.p1", "b": "e1.p2", "value": 80},
            {"type": "vertical_distance", "a": "e1.p1", "b": "e1.p2", "value": 50},
        ],
    )
    sk_profile = models.Sketch(
        project_id=proj.id, name="Gusset profile", plane="XZ",
        entities=[{"id": "e1", "type": "polyline", "points": [[0, 0], [20, 0], [0, 20]], "closed": True}],
        constraints=[],
    )
    db.add_all([sk_plate, sk_profile])
    db.flush()

    plate = models.Part(
        project_id=proj.id, name="Mounting plate", material="aluminum", color=modeling3d.material("aluminum")["color"],
        parameters={"t": 12, "hole_d": 8, "pocket_depth": 5},
        features=[
            _feat("f1", "extrude", "Base plate", sketch_id=sk_plate.id, distance="=t"),
            _feat("f2", "fillet", "Corner radius", radius=4, edges="|Z"),
            _feat("f3", "hole", "Mounting holes", face=">Z", diameter="=hole_d", depth=0,
                  points=[[10, 10, "=t"], [70, 10, "=t"], [10, 40, "=t"], [70, 40, "=t"]], positions=[]),
            _feat("f4", "box", "Pocket", length=30, width=20, height="=pocket_depth", position=[25, 15, "=t-pocket_depth"], op="cut"),
            _feat("f5", "hole", "Centre bore", face=">Z", diameter=10, depth=0, points=[[40, 25, "=t"]], positions=[]),
            _feat("f6", "chamfer", "Edge break", length=0.8, edges=">Z"),
        ],
    )
    pin = models.Part(
        project_id=proj.id, name="Pin", material="steel", color=modeling3d.material("steel")["color"], parameters={},
        features=[
            _feat("f1", "cylinder", "Shaft", radius=4, height=30, position=[0, 0, 0]),
            _feat("f2", "cylinder", "Head", radius=6, height=4, position=[0, 0, 30]),
            _feat("f3", "chamfer", "Lead-in", length=0.8, edges="<Z"),
        ],
    )
    db.add_all([plate, pin])
    db.flush()

    asm = models.Assembly(project_id=proj.id, name="Plate with pins")
    db.add(asm)
    db.flush()
    c_plate = models.AssemblyComponent(assembly_id=asm.id, part_id=plate.id, name="Mounting plate", fixed=True)
    c_pin1 = models.AssemblyComponent(assembly_id=asm.id, part_id=pin.id, name="Pin <1>", position=[10, 10, 60], rotation=[0, 0, 0])
    c_pin2 = models.AssemblyComponent(assembly_id=asm.id, part_id=pin.id, name="Pin <2>", position=[70, 40, 60], rotation=[0, 0, 0])
    db.add_all([c_plate, c_pin1, c_pin2])
    db.flush()
    for comp, hole_pt in ((c_pin1, "near:14,10,6"), (c_pin2, "near:74,40,6")):
        db.add(models.Mate(assembly_id=asm.id, type="concentric", comp_a=c_plate.id, comp_b=comp.id,
                           params={"a_face": hole_pt, "b_face": "near:4,0,15"}))
        db.add(models.Mate(assembly_id=asm.id, type="flush", comp_a=c_plate.id, comp_b=comp.id,
                           params={"a_face": ">Z", "b_face": "near:5,0,30", "offset": 0}))

    setup = models.CamSetup(
        part_id=plate.id, name="Plate - 3 axis", stock={"margin": [3, 3], "top_extra": 2},
        tools=[dict(t) for t in camsvc.DEFAULT_TOOLS], post={**gcode.DEFAULT_POST, "program_name": "MOUNTING_PLATE"},
        operations=[
            {"id": "op1", "type": "face", "name": "Face top", "enabled": True,
             "params": {**{p["name"]: p["default"] for p in camsvc.OP_SCHEMAS["face"]["params"]}, "tool": 1, "z_final": 12.0, "stepdown": 1.0}},
            {"id": "op2", "type": "pocket", "name": "Pocket", "enabled": True,
             "params": {**{p["name"]: p["default"] for p in camsvc.OP_SCHEMAS["pocket"]["params"]}, "tool": 1}},
            {"id": "op3", "type": "drill", "name": "Drill holes", "enabled": True,
             "params": {**{p["name"]: p["default"] for p in camsvc.OP_SCHEMAS["drill"]["params"]}, "tool": 4, "min_diameter": 7.5, "max_diameter": 8.5, "peck": 4.0}},
            {"id": "op4", "type": "drill", "name": "Mill centre bore", "enabled": True,
             "params": {**{p["name"]: p["default"] for p in camsvc.OP_SCHEMAS["drill"]["params"]}, "tool": 1, "min_diameter": 9.5, "max_diameter": 10.5}},
            {"id": "op5", "type": "profile", "name": "Outline", "enabled": True,
             "params": {**{p["name"]: p["default"] for p in camsvc.OP_SCHEMAS["profile"]["params"]}, "tool": 1, "side": "outside", "stepdown": 4.0}},
        ],
    )
    db.add(setup)
    db.commit()
    return proj
