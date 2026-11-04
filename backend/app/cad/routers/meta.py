from fastapi import APIRouter, Depends
from ...scope import bind_user


from ..services import calculix, cam, gcode, mate_solver, modeling3d, sketch2d

router = APIRouter(prefix="/api/cad", tags=["cad-meta"], dependencies=[Depends(bind_user)])

CONSTRAINT_TYPES = [
    {"type": "horizontal", "refs": ["a"], "help": "line, or two points {a,b}"},
    {"type": "vertical", "refs": ["a"], "help": "line, or two points {a,b}"},
    {"type": "coincident", "refs": ["a", "b"], "help": "point refs like 'e1.p2'"},
    {"type": "distance", "refs": ["a"], "value": True, "help": "line length, or {a,b} point distance"},
    {"type": "horizontal_distance", "refs": ["a", "b"], "value": True},
    {"type": "vertical_distance", "refs": ["a", "b"], "value": True},
    {"type": "fixed", "refs": ["a"], "value": True, "help": "value=[x,y]"},
    {"type": "radius", "refs": ["a"], "value": True},
    {"type": "diameter", "refs": ["a"], "value": True},
    {"type": "parallel", "refs": ["a", "b"]},
    {"type": "perpendicular", "refs": ["a", "b"]},
    {"type": "equal", "refs": ["a", "b"]},
    {"type": "angle", "refs": ["a", "b"], "value": True, "help": "degrees between two lines"},
    {"type": "tangent", "refs": ["a", "b"]},
    {"type": "concentric", "refs": ["a", "b"]},
    {"type": "point_on_line", "refs": ["a", "b"]},
    {"type": "point_on_circle", "refs": ["a", "b"]},
    {"type": "midpoint", "refs": ["a", "b"]},
    {"type": "symmetric", "refs": ["a", "b", "c"], "help": "points a,b symmetric about line c"},
]

SKETCH_OPS = ["translate", "rotate", "scale", "mirror", "linear_pattern", "circular_pattern", "offset"]
SKETCH_EDIT_OPS = ["trim", "extend", "fillet", "explode"]
MATE_TYPES = [{"type": t, "params": p} for t, p in mate_solver.MATE_TYPES.items()]


@router.get("/meta")
def meta():
    return {
        "features": modeling3d.FEATURE_SCHEMAS,
        "materials": modeling3d.MATERIALS,
        "planes": ["XY", "YZ", "XZ", "ZX"],
        "sketch_entity_types": ["line", "circle", "arc", "rect", "polyline", "polygon", "slot", "ellipse", "spline", "point"],
        "constraint_types": CONSTRAINT_TYPES,
        "sketch_ops": SKETCH_OPS,
        "sketch_edit_ops": SKETCH_EDIT_OPS,
        "mate_types": MATE_TYPES,
        "cam_operations": cam.OP_SCHEMAS,
        "cam_default_tools": cam.DEFAULT_TOOLS,
        "post_defaults": gcode.DEFAULT_POST,
        "post_dialects": list(gcode.DIALECTS),
        "simulation_kinds": ["static", "thermal", "modal"] + [k for k in calculix.KINDS if k not in calculix.ASSEMBLY_KINDS],
        "calculix": calculix.status(),
    }
