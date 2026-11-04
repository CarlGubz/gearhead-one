"""
Gmsh meshing options: validation and the catalogue the UI builds its form from.

This module deliberately does not import gmsh, so the API process can validate a request and describe the options
without loading the geometry kernel. The work itself happens in `gmsh_worker` (a separate process).

Every option maps onto a Gmsh option or API call:

  family            tet | hex          tetrahedra, or bricks (swept where the shape allows it, otherwise tet -> 4 bricks)
  order             1 | 2              Mesh.ElementOrder (hex: serendipity C3D20 via Mesh.SecondOrderIncomplete)
  size, min_size    metres             Mesh.MeshSizeMax / Mesh.MeshSizeMin
  size_factor       > 0                Mesh.MeshSizeFactor
  curvature_points  0..60              Mesh.MeshSizeFromCurvature (elements per 2 pi of curvature, 0 = off)
  size_from_points  bool               Mesh.MeshSizeFromPoints
  extend_from_boundary bool            Mesh.MeshSizeExtendFromBoundary
  algorithm_2d      1 2 5 6 8          Mesh.Algorithm       (surface mesher)
  algorithm_3d      1 4 7 9 10         Mesh.Algorithm3D     (volume mesher)
  recombine_algorithm 0..3             Mesh.RecombinationAlgorithm (triangles -> quadrilaterals, used by the brick sweep)
  hex_method        auto|sweep|subdivide
  smoothing_steps   0..20              Mesh.Smoothing
  optimize          none|netgen|relocate|untangle      gmsh.model.mesh.optimize after the volume mesh
  refine            0..2               gmsh.model.mesh.refine (uniform subdivision)
  straight_midside  bool               Mesh.SecondOrderLinear (mid-side nodes stay on the straight edge)
  curve_optimize    none|highorder|elastic|fast        Mesh.HighOrderOptimize for curved quadratic elements
  regions           [{shape ball|box, ...}]            local size fields (gmsh.model.mesh.field Ball / Box + Min)
"""
from __future__ import annotations

from typing import Any, Dict, List

# BAMG (7) fails on every curved OpenCASCADE surface and Packing of parallelograms (9) crashes the kernel: both are left out.
ALGORITHMS_2D = {1: "MeshAdapt", 2: "Automatic", 5: "Delaunay", 6: "Frontal-Delaunay", 8: "Frontal-Delaunay for quads"}
ALGORITHMS_3D = {1: "Delaunay", 4: "Frontal", 7: "MMG3D", 9: "R-tree", 10: "HXT"}
RECOMBINE_ALGORITHMS = {0: "Simple", 1: "Blossom", 2: "Simple full-quad", 3: "Blossom full-quad"}
HEX_METHODS = {"auto": "Automatic (sweep when the shape allows it)", "sweep": "Sweep a quad mesh along the axis",
               "subdivide": "Subdivide tetrahedra into 4 bricks each"}
OPTIMIZERS = {"none": None, "netgen": "Netgen", "relocate": "Relocate3D", "untangle": "UntangleMeshGeometry"}
CURVE_OPTIMIZERS = {"none": 0, "highorder": 1, "elastic": 2, "fast": 4}

MAX_REGIONS = 8

DEFAULTS: Dict[str, Any] = {
    "family": "tet", "order": 2, "size": 0.0, "min_size": 0.0, "size_factor": 1.0, "curvature_points": 0,
    "size_from_points": True, "extend_from_boundary": True, "algorithm_2d": 6, "algorithm_3d": 1,
    "recombine_algorithm": 3, "hex_method": "auto", "smoothing_steps": 1, "optimize": "none", "refine": 0,
    "straight_midside": False, "curve_optimize": "none", "regions": [],
}


class OptionError(ValueError):
    """An invalid Gmsh option (user-facing message)."""


def _num(opts: Dict[str, Any], key: str, lo: float, hi: float, integer: bool = False, allow_zero: bool = True):
    v = opts.get(key, DEFAULTS[key])
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise OptionError(f"Option '{key}' must be a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise OptionError(f"Option '{key}' must be a finite number")
    if integer and v != int(v):
        raise OptionError(f"Option '{key}' must be a whole number")
    if v < lo or v > hi or (not allow_zero and v == 0):
        raise OptionError(f"Option '{key}' must be between {lo:g} and {hi:g}")
    return int(v) if integer else v


def _choice(opts: Dict[str, Any], key: str, allowed) -> Any:
    v = opts.get(key, DEFAULTS[key])
    if isinstance(allowed, dict):
        allowed = list(allowed)
    if v not in allowed:
        raise OptionError(f"Option '{key}' must be one of: {', '.join(str(a) for a in allowed)}")
    return v


def _vec(v, n: int, what: str) -> List[float]:
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        raise OptionError(f"{what} must be a list of {n} numbers") from None
    if len(out) != n or any(x != x or abs(x) == float("inf") for x in out):
        raise OptionError(f"{what} must be a list of {n} finite numbers")
    return out


def _region(r: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(r, dict):
        raise OptionError("A refinement region must be an object")
    shape = r.get("shape")
    if shape not in ("ball", "box"):
        raise OptionError("A refinement region 'shape' must be 'ball' or 'box'")
    size_in = r.get("size_in")
    try:
        size_in = float(size_in)
    except (TypeError, ValueError):
        raise OptionError("A refinement region needs 'size_in' (element size inside it, metres)") from None
    if not size_in > 0:
        raise OptionError("A refinement region's 'size_in' must be positive")
    size_out = float(r.get("size_out") or 0.0)
    thick = float(r.get("thickness") or 0.0)
    if size_out < 0 or thick < 0:
        raise OptionError("'size_out' and 'thickness' must not be negative")
    out = {"shape": shape, "size_in": size_in, "size_out": size_out, "thickness": thick}
    if shape == "ball":
        out["center"] = _vec(r.get("center"), 3, "A ball region's 'center'")
        out["radius"] = float(r.get("radius") or 0.0)
        if not out["radius"] > 0:
            raise OptionError("A ball region needs a positive 'radius'")
    elif shape == "box":
        lo, hi = _vec(r.get("min"), 3, "A box region's 'min'"), _vec(r.get("max"), 3, "A box region's 'max'")
        if any(a > b for a, b in zip(lo, hi)):
            raise OptionError("A box region's 'min' must not exceed its 'max'")
        out["min"], out["max"] = lo, hi
    else:
        raise OptionError("A refinement region 'shape' must be 'ball' or 'box'")
    return out


def normalize(options: Dict[str, Any] | None) -> Dict[str, Any]:
    """Validate a request's options and fill in the defaults. Raises OptionError."""
    opts = dict(options or {})
    unknown = set(opts) - set(DEFAULTS)
    if unknown:
        raise OptionError(f"Unknown Gmsh option(s): {', '.join(sorted(unknown))}")
    out = {
        "family": _choice(opts, "family", ("tet", "hex")),
        "order": _num(opts, "order", 1, 2, integer=True),
        "size": _num(opts, "size", 0.0, 1e6),
        "min_size": _num(opts, "min_size", 0.0, 1e6),
        "size_factor": _num(opts, "size_factor", 0.05, 20.0, allow_zero=False),
        "curvature_points": _num(opts, "curvature_points", 0, 60, integer=True),
        "size_from_points": bool(opts.get("size_from_points", DEFAULTS["size_from_points"])),
        "extend_from_boundary": bool(opts.get("extend_from_boundary", DEFAULTS["extend_from_boundary"])),
        "algorithm_2d": _choice(opts, "algorithm_2d", ALGORITHMS_2D),
        "algorithm_3d": _choice(opts, "algorithm_3d", ALGORITHMS_3D),
        "recombine_algorithm": _choice(opts, "recombine_algorithm", RECOMBINE_ALGORITHMS),
        "hex_method": _choice(opts, "hex_method", HEX_METHODS),
        "smoothing_steps": _num(opts, "smoothing_steps", 0, 20, integer=True),
        "optimize": _choice(opts, "optimize", OPTIMIZERS),
        "refine": _num(opts, "refine", 0, 2, integer=True),
        "straight_midside": bool(opts.get("straight_midside", DEFAULTS["straight_midside"])),
        "curve_optimize": _choice(opts, "curve_optimize", CURVE_OPTIMIZERS),
    }
    if out["min_size"] and out["size"] and out["min_size"] > out["size"]:
        raise OptionError("'min_size' must not exceed 'size'")
    regions = opts.get("regions") or []
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise OptionError(f"'regions' must be a list of at most {MAX_REGIONS} refinement regions")
    out["regions"] = [_region(r) for r in regions]
    return out


def element_type_of(options: Dict[str, Any]) -> str:
    """Solver element type a set of (normalised) options produces."""
    return {("tet", 1): "C3D4", ("tet", 2): "C3D10", ("hex", 1): "C3D8", ("hex", 2): "C3D20"}[(options["family"], options["order"])]


def catalogue() -> Dict[str, Any]:
    """Machine-readable description of the options for the UI (and the API docs)."""
    return {
        "defaults": DEFAULTS,
        "algorithms_2d": [{"id": k, "label": v} for k, v in ALGORITHMS_2D.items()],
        "algorithms_3d": [{"id": k, "label": v} for k, v in ALGORITHMS_3D.items()],
        "recombine_algorithms": [{"id": k, "label": v} for k, v in RECOMBINE_ALGORITHMS.items()],
        "hex_methods": [{"id": k, "label": v} for k, v in HEX_METHODS.items()],
        "optimizers": [{"id": k, "label": {"none": "None", "netgen": "Netgen (tetrahedra)", "relocate": "Relocate 3-D",
                                           "untangle": "Untangle geometry"}[k]} for k in OPTIMIZERS],
        "curve_optimizers": [{"id": k, "label": {"none": "None", "highorder": "High-order optimisation",
                                                  "elastic": "Elastic + optimisation", "fast": "Fast curving"}[k]} for k in CURVE_OPTIMIZERS],
        "max_regions": MAX_REGIONS,
    }
