"""
Gmsh meshing from the API: CAD import (STEP / IGES / BREP / STL), primitives and existing `.msh` files, all through
`app.fea.gmsh_worker` (Gmsh) in a subprocess with a timeout.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

import numpy as np

from . import gmsh_options as go

FORMATS = (".step", ".stp", ".iges", ".igs", ".brep", ".brp", ".stl")
UNITS = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "in": 0.0254}
TIMEOUT_S = 180
BACKEND_DIR = Path(__file__).resolve().parents[2]


class CadError(ValueError):
    """A problem with the uploaded file or the requested meshing parameters (user-facing)."""


class GmshResult(NamedTuple):
    nodes: np.ndarray          # (n, 3) metres
    elems: np.ndarray          # (m, 4|8|10|20) Abaqus node order
    ends: np.ndarray           # cumulative element count per solid
    element_type: str          # C3D4 | C3D10 | C3D8 | C3D20
    info: Dict[str, Any]


def available() -> bool:
    return importlib.util.find_spec("gmsh") is not None


def version() -> Optional[str]:
    """Installed Gmsh version (read from package metadata: importing gmsh here would load the kernel into the API process)."""
    try:
        from importlib import metadata
        return metadata.version("gmsh")
    except Exception:  # noqa: BLE001
        return None


def _options(options: Optional[Dict[str, Any]], **forced) -> Dict[str, Any]:
    try:
        return go.normalize({**(options or {}), **forced})
    except go.OptionError as exc:
        raise CadError(str(exc)) from None


def _run(job: Dict[str, Any], files: Dict[str, bytes]) -> GmshResult:
    if not available():
        raise RuntimeError("Gmsh is not installed on the server, so this mesher is unavailable. Install it with: pip install gmsh")
    with tempfile.TemporaryDirectory(prefix="gfea_gmsh_") as tmp:
        for name, raw in files.items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(raw)
        if "path" in job["source"]:
            job["source"]["path"] = os.path.join(tmp, job["source"]["path"])
        spec, out = os.path.join(tmp, "job.json"), os.path.join(tmp, "mesh.npz")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump(job, f)
        cmd = [sys.executable, "-m", "app.fea.gmsh_worker", spec, out]
        try:
            res = subprocess.run(cmd, cwd=str(BACKEND_DIR), capture_output=True, text=True, timeout=TIMEOUT_S,
                                 env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)})
        except subprocess.TimeoutExpired:
            raise CadError(f"Meshing took longer than {TIMEOUT_S} s. Use a larger element size or a simpler model.") from None
        if res.returncode == 2:
            msg = (res.stderr.strip().splitlines() or ["Gmsh meshing failed."])[-1]
            raise CadError(msg)
        if res.returncode != 0 or not os.path.exists(out):
            raise CadError("The Gmsh kernel stopped unexpectedly while meshing. The input may be corrupt or too complex.")
        with np.load(out) as data:                      # close the file before the temp directory is removed (Windows)
            return GmshResult(data["nodes"].copy(), data["elems"].copy(), data["part_ends"].copy(),
                              str(data["etype"]), json.loads(str(data["info"])))


def mesh_cad(raw: bytes, filename: str, size: float, order: int, unit: str,
             options: Optional[Dict[str, Any]] = None) -> GmshResult:
    """
    Mesh a STEP / IGES / BREP / STL file. `size` (metres, 0 = automatic) and `order` (1 | 2) are the classic parameters; `options`
    carries the rest of the Gmsh options (see gmsh_options.py) and may also set 'family' to 'hex' for bricks.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in FORMATS:
        raise CadError("Unsupported CAD format. Use .step/.stp, .iges/.igs, .brep or .stl.")
    if order not in (1, 2):
        raise CadError("order must be 1 (linear) or 2 (quadratic)")
    if unit not in UNITS:
        raise CadError(f"unit must be one of: {', '.join(UNITS)}")
    if size < 0 or not np.isfinite(size):
        raise CadError("The element size must be positive (or 0 for automatic).")
    if not raw:
        raise CadError("The file is empty.")
    opts = _options(options, order=order, **({"size": size} if size else {}))
    job = {"source": {"kind": "file", "path": "model" + ext, "scale": UNITS[unit]}, "options": opts}
    return _run(job, {"model" + ext: raw})


def mesh_primitive(shape: str, dims: Dict[str, float], divisions: int, options: Optional[Dict[str, Any]] = None) -> GmshResult:
    """Mesh one of the built-in primitives with Gmsh (OpenCASCADE solid) using the given options."""
    from .meshgen import SHAPES
    if shape not in SHAPES:
        raise CadError(f"Unknown primitive shape '{shape}'. Available: {', '.join(SHAPES)}")
    for k, v in dims.items():
        if not np.isfinite(float(v)) or float(v) <= 0:
            raise CadError(f"Dimension '{k}' must be positive")
    job = {"source": {"kind": "primitive", "shape": shape, "dims": {k: float(v) for k, v in dims.items()},
                      "divisions": int(divisions)}, "options": _options(options)}
    return _run(job, {})


def mesh_msh(raw: bytes, unit: str = "m") -> GmshResult:
    """Read an existing Gmsh mesh file (any format version, ASCII or binary): tetrahedra and hexahedra of order 1 or 2."""
    if unit not in UNITS:
        raise CadError(f"unit must be one of: {', '.join(UNITS)}")
    if not raw:
        raise CadError("The file is empty.")
    job = {"source": {"kind": "msh", "path": "model.msh", "scale": UNITS[unit]}, "options": _options(None)}
    return _run(job, {"model.msh": raw})
