"""
AI engineering agent: turns a plain-language problem statement into a solved FE model.

The agent is a tool-calling loop over an LLM API: Google Gemini (default, `gemini-2.5-pro`), the Anthropic
Messages API or OpenRouter (last resort), chosen by `settings.agent_provider`. Its tools are thin wrappers over the same
handlers the UI uses (mesh generation, material, analysis type, supports, loads, gravity), so everything it
builds is a normal project edit: it is undoable (Ctrl+Z), respects plan limits and shows up in the model tree.

The agent never runs the solver itself. `run_simulation` only validates the model and raises a flag; the
browser then starts the job through the ordinary "Solve" path, so progress, results and errors look exactly
like a manual run.

The HTTP call is isolated in `_post_messages` so tests can replace it without a network.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx
import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from . import models, schemas
from .config import settings
from .fea import meshgen, structmesh
from .fea.runner import ANALYSIS_TYPES

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_HISTORY = 30
MAX_MESSAGE_CHARS = 6000
NOT_CONFIGURED = "The AI assistant is not configured on this server (set GEMINI_API_KEY, ANTHROPIC_API_KEY or OPENROUTER_API_KEY)."

MATERIAL_PRESETS: Dict[str, Dict[str, Any]] = {
    "steel": dict(name="Structural steel", E=210e9, nu=0.30, density=7850, conductivity=50, specific_heat=490,
                  alpha=12e-6, yield_stress=250e6, hardening=2e9),
    "stainless_steel": dict(name="Stainless steel 304", E=193e9, nu=0.29, density=8000, conductivity=16,
                            specific_heat=500, alpha=17.3e-6, yield_stress=215e6, hardening=2e9),
    "aluminium": dict(name="Aluminium 6061-T6", E=69e9, nu=0.33, density=2700, conductivity=167, specific_heat=896,
                      alpha=23.6e-6, yield_stress=276e6, hardening=1e9),
    "titanium": dict(name="Titanium Ti-6Al-4V", E=114e9, nu=0.34, density=4430, conductivity=6.7, specific_heat=526,
                     alpha=8.6e-6, yield_stress=880e6, hardening=2e9),
    "copper": dict(name="Copper", E=117e9, nu=0.34, density=8960, conductivity=390, specific_heat=385,
                   alpha=16.5e-6, yield_stress=70e6, hardening=1e9),
    "cast_iron": dict(name="Cast iron", E=110e9, nu=0.28, density=7200, conductivity=50, specific_heat=460,
                      alpha=10.5e-6),
    "concrete": dict(name="Concrete", E=30e9, nu=0.20, density=2400, conductivity=1.7, specific_heat=880,
                     alpha=10e-6),
}

SYSTEM_PROMPT = """You are the Gearhead FEA engineering agent. A user describes a mechanical or thermal problem in \
plain language; you build the finite-element model in their project with the tools, check it, and start the \
simulation. You work inside a FEA workspace, so everything you do appears in the model tree and can be \
undone by the user.

UNITS: the solver is SI only: metres, newtons, pascals, kilograms, seconds, kelvin/°C. Convert every value the user \
gives (mm, MPa, kN, ...) before calling a tool. Example: 50 mm = 0.05 m, 250 MPa = 250e6 Pa.
AXES: Z is up and gravity acts along -Z. Box dims are length (x), width (y), height (z); cylinders, spheres and tubes stand along z. Every solid primitive is centred on the origin (a 1 m long box spans x = -0.5 to 0.5), so use face regions (x_min, x_max, ...) rather than guessing coordinates. Structural line/plate models start at the origin or the given points.

WORKFLOW
1. Read the problem. The user expects the whole job to run without further input, so fill every gap yourself with a \
reasonable engineering default (steel if no material is named, static analysis, C3D10 mesh, one end fixed for a \
cantilever, ...) and state those assumptions in the reply. Only if a dimension or load that decides the whole \
answer is completely absent and cannot be assumed, ask ONE short clarifying question and stop; do not call tools.
2. Build in order: geometry+mesh -> material -> analysis type/settings -> supports -> loads -> run_simulation.
3. Mesh choice: solids use C3D10 (quadratic tets, good for bending) by default; use C3D20 (quadratic bricks, any \
shape) when accuracy matters; C3D4/C3D8 only for quick checks. 4 to 8 divisions is normally enough. Use structural models (B31 beam, \
T3D2 truss, S4 shell, SPRING2) for slender members, frames, trusses and thin plates/shells.
4. Select where supports and loads go with a `region`: {"type":"face","side":"x_min|x_max|y_min|y_max|z_min|z_max"}, \
{"type":"box","min":[x,y,z],"max":[x,y,z]} (use null for an open side), {"type":"near_point","point":[x,y,z],\
"radius":r} or {"type":"all"}. Call inspect_model if you need the bounding box.
5. Supports: for solids dofs are 0,1,2 (ux,uy,uz); for structural models 0..5 (adds rotations). Fixed = all of them. \
A symmetry plane normal to X fixes only dof 0.
6. Loads: force vectors are TOTAL newtons split over the region's nodes; pressure is Pa, positive pushing INTO the \
surface (use a negative value to pull away); gravity via set_gravity.
7. Analysis types: static (default, available on the Free plan), nonlinear (needs a yield stress), modal (natural \
frequencies, no loads needed), buckling (needs a compressive reference load), dynamic, thermal, thermal_transient, \
thermomech. Everything except static needs the Pro plan; if a tool reports that, tell the user and offer the static \
alternative.
8. Finish by calling run_simulation once the model is complete. If a tool returns an error, read it, fix the model \
and continue; do not give up after one failure. Never invent results: the numbers appear only after the simulation \
runs, so do not quote deflections or stresses you have not seen. You may give a hand-calculation estimate if you \
label it as such.

REPLY STYLE: after the tools, answer in 3 to 6 short lines: what was modelled (geometry, material, supports, \
loads, analysis), the assumptions you made, and that the simulation has been started. No markdown headings."""


# ------------------------------------------------------------------ request model
class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(min_length=1, max_length=MAX_HISTORY)


def available() -> bool:
    return settings.agent_configured


# ------------------------------------------------------------------ geometry helpers
def _bbox(project: models.Project):
    pts = np.asarray(project.nodes or [], dtype=float)
    if pts.size == 0:
        return None, None
    return pts.min(axis=0), pts.max(axis=0)


def _surface_node_ids(project: models.Project) -> np.ndarray:
    """Nodes a user could click: the boundary of a solid, every node of a structural model."""
    if project.surface_faces:
        return np.unique(np.asarray(project.surface_faces, dtype=int).ravel())
    return np.arange(len(project.nodes or []))


def _face_axis(side: str):
    if not isinstance(side, str) or len(side) != 5 or side[0] not in "xyz" or side[1:] not in ("_min", "_max"):
        raise ValueError("face side must be one of x_min, x_max, y_min, y_max, z_min, z_max")
    return "xyz".index(side[0]), side.endswith("max")


def _point_mask(pts: np.ndarray, region: Dict[str, Any], lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Boolean mask over `pts` (n,3) for a region."""
    kind = (region or {}).get("type")
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    tol = max(1e-9, 1e-4 * diag)
    if kind == "all":
        return np.ones(len(pts), bool)
    if kind == "face":
        ax, is_max = _face_axis(region.get("side"))
        target = hi[ax] if is_max else lo[ax]
        return np.abs(pts[:, ax] - target) <= tol
    if kind == "box":
        mn = region.get("min") or [None] * 3
        mx = region.get("max") or [None] * 3
        if len(mn) != 3 or len(mx) != 3:
            raise ValueError("box min and max need 3 numbers each (null = unbounded)")
        mask = np.ones(len(pts), bool)
        for i in range(3):
            if mn[i] is not None:
                mask &= pts[:, i] >= float(mn[i]) - tol
            if mx[i] is not None:
                mask &= pts[:, i] <= float(mx[i]) + tol
        return mask
    if kind == "near_point":
        c = region.get("point")
        r = float(region.get("radius", 0.0))
        if not c or len(c) != 3 or r <= 0:
            raise ValueError("near_point needs point [x,y,z] and a positive radius")
        return np.linalg.norm(pts - np.asarray(c, float), axis=1) <= r + tol
    raise ValueError("region.type must be one of: face, box, near_point, all")


def region_nodes(project: models.Project, region: Dict[str, Any]) -> List[int]:
    lo, hi = _bbox(project)
    if lo is None:
        raise ValueError("The project has no mesh yet. Create one first.")
    pts = np.asarray(project.nodes, dtype=float)
    ids = _surface_node_ids(project)
    ids = ids[_point_mask(pts[ids], region, lo, hi)]
    if not len(ids):
        raise ValueError(f"The region {json.dumps(region)} contains no nodes. Check the coordinates with inspect_model.")
    return [int(i) for i in ids]


def region_faces(project: models.Project, region: Dict[str, Any]) -> List[List[int]]:
    """Surface triangles whose three corners all lie in the region."""
    lo, hi = _bbox(project)
    if lo is None:
        raise ValueError("The project has no mesh yet. Create one first.")
    if not project.surface_faces:
        raise ValueError("This model has no surface faces (beam / truss / spring models): use a node-based load instead.")
    pts = np.asarray(project.nodes, dtype=float)
    tris = np.asarray(project.surface_faces, dtype=int)
    mask = _point_mask(pts, region, lo, hi)
    keep = mask[tris].all(axis=1)
    if not keep.any():
        raise ValueError(f"The region {json.dumps(region)} contains no whole surface faces. For a curved or partial "
                         "area use a box region that fully covers the faces.")
    return tris[keep].tolist()


def _fmt(v: float) -> float:
    return float(f"{v:.6g}")


def describe_project(project: models.Project) -> Dict[str, Any]:
    lo, hi = _bbox(project)
    out: Dict[str, Any] = {
        "name": project.name,
        "has_mesh": bool(project.elements),
        "analysis_type": project.analysis_type or "static",
        "material": {k: project.material.get(k) for k in ("name", "E", "nu", "density", "yield_stress")}
        if project.material else None,
        "gravity": project.gravity,
        "settings": project.settings or {},
        "supports": [{"name": b.get("name"), "type": b.get("bc_type") or "default", "nodes": len(b.get("node_ids") or []),
                      "dofs": b.get("dofs"), "values": b.get("values")} for b in project.boundary_conditions or []],
        "loads": [{"name": l.get("name"), "type": l.get("load_type"), "nodes": len(l.get("node_ids") or []),
                   "faces": len(l.get("faces") or []), "vector": l.get("vector"), "magnitude": l.get("magnitude")}
                  for l in project.loads or []],
    }
    if lo is not None:
        out.update({
            "element_type": project.element_type, "nodes": len(project.nodes), "elements": len(project.elements),
            "bounding_box_min": [_fmt(v) for v in lo], "bounding_box_max": [_fmt(v) for v in hi],
            "parts": [p.get("name") for p in project.parts or []],
        })
    return out


# ------------------------------------------------------------------ tool schemas
_REGION = {
    "type": "object",
    "description": 'Where to apply it. {"type":"face","side":"x_min"} | {"type":"box","min":[x,y,z],"max":[x,y,z]} '
                   '(null = open side) | {"type":"near_point","point":[x,y,z],"radius":r} | {"type":"all"}',
    "properties": {
        "type": {"type": "string", "enum": ["face", "box", "near_point", "all"]},
        "side": {"type": "string", "enum": ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]},
        "min": {"type": "array", "items": {"type": ["number", "null"]}},
        "max": {"type": "array", "items": {"type": ["number", "null"]}},
        "point": {"type": "array", "items": {"type": "number"}},
        "radius": {"type": "number"},
    },
    "required": ["type"],
}
_VEC = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}

TOOLS: List[Dict[str, Any]] = [
    {"name": "inspect_model", "description": "Return the current model: mesh size, bounding box, material, analysis "
     "type, supports and loads. Cheap; call it whenever you need coordinates or want to verify your work.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "create_solid_mesh",
     "description": "Replace the model with a meshed solid primitive (clears supports/loads). Dims in metres. "
     "box: length,width,height | cylinder: radius,height | sphere: radius | tube: outer_radius,inner_radius,height | "
     "plate_hole: length,width,thickness,hole_radius | lbracket: length,height,thickness,depth | "
     "cone: bottom_radius,top_radius,height | torus: major_radius,minor_radius. Every shape accepts every element type "
     "(bricks C3D8/C3D20 included). Set mesher='gmsh' to mesh with Gmsh instead (optional 'gmsh' options: size in metres, "
     "algorithm_3d, optimize, refine, regions for local refinement).",
     "input_schema": {"type": "object", "properties": {
         "shape": {"type": "string", "enum": list(meshgen.SHAPES)},
         "dims": {"type": "object", "additionalProperties": {"type": "number"}},
         "divisions": {"type": "integer", "minimum": 1, "maximum": 24},
         "element_type": {"type": "string", "enum": ["C3D4", "C3D10", "C3D8", "C3D20"]},
         "mesher": {"type": "string", "enum": ["builtin", "gmsh"]},
         "gmsh": {"type": "object"}},
         "required": ["shape", "dims"]}},
    {"name": "create_structural_model",
     "description": "Replace the model with a beam / truss / shell / spring model. kind: line (x1,y1,z1,x2,y2,z2) | "
     "portal (width,height) | plate (length,width) | cylinder (radius,length,angle). B31 and T3D2 for line/portal, "
     "S4 for plate/cylinder, SPRING2 for line. section by element type: B31 {type:'rect',b,h} or {type:'circ',d} "
     "or {type:'pipe',d,t}; T3D2 {area}; S4 {thickness}; SPRING2 {k}. Metres.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(structmesh.KINDS)},
         "element_type": {"type": "string", "enum": ["B31", "T3D2", "S4", "SPRING2"]},
         "dims": {"type": "object", "additionalProperties": {"type": "number"}},
         "divisions": {"type": "integer", "minimum": 1, "maximum": 200},
         "section": {"type": "object"}},
         "required": ["kind", "element_type"]}},
    {"name": "set_material",
     "description": "Set the model's material. Pass a preset and/or explicit properties (SI: E Pa, density kg/m3, "
     "conductivity W/mK, specific_heat J/kgK, alpha 1/K, yield_stress Pa, hardening Pa). Explicit values override "
     "the preset.",
     "input_schema": {"type": "object", "properties": {
         "preset": {"type": "string", "enum": list(MATERIAL_PRESETS)},
         "name": {"type": "string"}, "E": {"type": "number"}, "nu": {"type": "number"},
         "density": {"type": "number"}, "conductivity": {"type": "number"}, "specific_heat": {"type": "number"},
         "alpha": {"type": "number"}, "yield_stress": {"type": "number"}, "hardening": {"type": "number"}}}},
    {"name": "set_analysis",
     "description": "Choose the analysis type and optional settings. settings may contain: n_modes, increments, "
     "t_end, n_steps, theta, initial_temperature, reference_temperature, nlgeom (large deformation), "
     "linear_solver (auto|direct|iterative).",
     "input_schema": {"type": "object", "properties": {
         "analysis_type": {"type": "string", "enum": list(ANALYSIS_TYPES)},
         "settings": {"type": "object"}}, "required": ["analysis_type"]}},
    {"name": "add_support",
     "description": "Constrain the nodes of a region. dofs: solids 0,1,2 = ux,uy,uz; structural 0..5 adds rx,ry,rz. "
     "values = prescribed displacement per dof (default 0). For a thermal fixed temperature use add_temperature.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "region": _REGION,
         "dofs": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 5}},
         "values": {"type": "array", "items": {"type": "number"}}},
         "required": ["region", "dofs"]}},
    {"name": "add_temperature",
     "description": "Fix the temperature (°C) of the nodes in a region (thermal and thermo-mechanical analyses).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "region": _REGION, "temperature": {"type": "number"}},
         "required": ["region", "temperature"]}},
    {"name": "add_load",
     "description": "Add a load. force: total `vector` [N] (and optional `moment` [N m] on structural models) over the "
     "region's nodes | pressure: `magnitude` Pa on the region's faces, positive pushes INTO the surface | traction: "
     "`vector` Pa on faces | heat_flux: `magnitude` W over nodes | surface_flux: `magnitude` W/m2 on faces | "
     "body_heat: `magnitude` W/m3 (no region) | convection: `h` and `T_inf` on faces | radiation: `emissivity` and "
     "`T_inf` on faces | line_load: `vector` N/m on all beams (structural, no region) | point_mass: `magnitude` kg "
     "at the region's nodes (structural).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "load_type": {"type": "string", "enum": ["force", "pressure", "traction", "heat_flux", "surface_flux",
                                                  "body_heat", "convection", "radiation", "line_load", "point_mass"]},
         "region": _REGION, "vector": _VEC, "moment": _VEC, "magnitude": {"type": "number"},
         "h": {"type": "number"}, "T_inf": {"type": "number"}, "emissivity": {"type": "number"}},
         "required": ["load_type"]}},
    {"name": "set_gravity", "description": "Switch gravity on/off. Default direction is -Z at 9.81 m/s2.",
     "input_schema": {"type": "object", "properties": {
         "enabled": {"type": "boolean"}, "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
         "required": ["enabled"]}},
    {"name": "clear_supports_and_loads",
     "description": "Remove every support and load (the mesh and material stay). Use to start the setup over.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_simulation",
     "description": "Validate the model and start the analysis. Call once, as the last step, when the model is "
     "complete. Returns an error describing what is missing if the model is not ready.",
     "input_schema": {"type": "object", "properties": {}}},
]


# ------------------------------------------------------------------ tool execution
class ToolBox:
    """Executes tool calls against one project, through the same handlers the REST API uses."""

    def __init__(self, db: Session, user: models.User, project: models.Project):
        from . import main  # late import: main imports this module's router hook
        self.m = main
        self.db, self.user, self.project = db, user, project
        self.run_requested = False

    # -- dispatch
    def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"ok": False, "error": f"Unknown tool '{name}'."}
        try:
            result = fn(**(args or {}))
            return {"ok": True, **result}
        except HTTPException as exc:
            self.db.rollback()
            return {"ok": False, "error": str(exc.detail)}
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            self.db.rollback()
            msg = str(exc)
            return {"ok": False, "error": msg[:600] if msg else exc.__class__.__name__}

    # -- tools
    def t_inspect_model(self):
        return {"model": describe_project(self.project)}

    def t_create_solid_mesh(self, shape, dims, divisions=5, element_type="C3D10", mesher="builtin", gmsh=None):
        req = schemas.PrimitiveMeshRequest(shape=shape, dims=dims, divisions=divisions, element_type=element_type,
                                           mesher=mesher, gmsh=gmsh)
        self.project = self.m.generate_primitive(req, self.project, self.db)
        return {"summary": f"{shape} meshed: {len(self.project.nodes)} nodes, {len(self.project.elements)} "
                           f"{self.project.element_type} elements", "model": describe_project(self.project)}

    def t_create_structural_model(self, kind, element_type, dims=None, divisions=8, section=None):
        req = schemas.StructuralMeshRequest(kind=kind, dims=dims or {}, divisions=divisions,
                                            element_type=element_type, section=section)
        self.project = self.m.generate_structural(req, self.project, self.db)
        return {"summary": f"{kind} created: {len(self.project.nodes)} nodes, {len(self.project.elements)} "
                           f"{element_type} elements", "model": describe_project(self.project)}

    def t_set_material(self, preset=None, **props):
        base = dict(MATERIAL_PRESETS.get(preset, {})) if preset else dict(self.project.material or {})
        if preset and preset not in MATERIAL_PRESETS:
            raise ValueError(f"Unknown preset '{preset}'. Use one of: {', '.join(MATERIAL_PRESETS)}")
        base.update({k: v for k, v in props.items() if v is not None})
        mat = schemas.MaterialUpdate(**{k: v for k, v in base.items() if k in schemas.MaterialUpdate.model_fields})
        self.project = self.m.set_material(mat, self.project, self.db)
        return {"summary": f"Material: {mat.name} (E = {mat.E:.3g} Pa, nu = {mat.nu}, yield = {mat.yield_stress:.3g} Pa)"}

    def t_set_analysis(self, analysis_type, settings=None):
        n_modes = int((settings or {}).get("n_modes") or self.project.n_modes or 6)
        self.project = self.m.set_analysis_type(schemas.AnalysisTypeUpdate(analysis_type=analysis_type, n_modes=n_modes),
                                                self.project, self.user, self.db)
        if settings:
            self.project = self.m.set_settings(schemas.SettingsUpdate(**settings), self.project, self.db)
        return {"summary": f"Analysis: {analysis_type}" + (f" with {json.dumps(settings)}" if settings else "")}

    def t_add_support(self, region, dofs, values=None, name="Support"):
        ids = region_nodes(self.project, region)
        vals = list(values) if values else [0.0] * len(dofs)
        if len(vals) != len(dofs):
            raise ValueError("values must have one number per dof")
        bc = schemas.BoundaryCondition(name=name, bc_type="displacement", node_ids=ids, dofs=list(dofs), values=vals)
        self.project = self.m.add_bc(bc, self.project, self.db)
        return {"summary": f"Support '{name}' on {len(ids)} nodes, dofs {list(dofs)}"}

    def t_add_temperature(self, region, temperature, name="Fixed temperature"):
        ids = region_nodes(self.project, region)
        bc = schemas.BoundaryCondition(name=name, bc_type="temperature", node_ids=ids, dofs=[0], values=[temperature])
        self.project = self.m.add_bc(bc, self.project, self.db)
        return {"summary": f"{temperature} °C fixed on {len(ids)} nodes"}

    def t_add_load(self, load_type, region=None, vector=None, moment=None, magnitude=None, h=None, T_inf=None,
                   emissivity=None, name=None):
        data: Dict[str, Any] = {"name": name or load_type.replace("_", " ").title(), "load_type": load_type}
        for key, val in (("vector", vector), ("moment", moment), ("magnitude", magnitude), ("h", h),
                         ("T_inf", T_inf), ("emissivity", emissivity)):
            if val is not None:
                data[key] = val
        where = "model"
        if load_type in ("force", "heat_flux", "point_mass"):
            data["node_ids"] = region_nodes(self.project, region or {"type": "all"})
            where = f"{len(data['node_ids'])} nodes"
        elif load_type in ("pressure", "traction", "surface_flux", "convection", "radiation"):
            if not region:
                raise ValueError(f"{load_type} needs a region")
            data["faces"] = region_faces(self.project, region)
            where = f"{len(data['faces'])} faces"
        load = schemas.Load(**data)
        self.project = self.m.add_load(load, self.project, self.db)
        return {"summary": f"Load '{data['name']}' ({load_type}) on {where}"}

    def t_set_gravity(self, enabled, x=0.0, y=0.0, z=-9.81):
        self.project = self.m.set_gravity(schemas.GravityUpdate(enabled=enabled, x=x, y=y, z=z), self.project, self.db)
        return {"summary": "Gravity on" if enabled else "Gravity off"}

    def t_clear_supports_and_loads(self):
        self.project.boundary_conditions = []
        self.project.loads = []
        self.project = self.m._commit(self.db, self.project, "Clear supports and loads")
        return {"summary": "Supports and loads cleared"}

    def t_run_simulation(self):
        p = self.project
        if not p.elements:
            raise ValueError("There is no mesh yet.")
        self.m.require_pro_for(p.analysis_type or "static", self.user)
        kind = p.analysis_type or "static"
        thermal = kind in ("thermal", "thermal_transient")
        if not p.boundary_conditions:
            raise ValueError("Add a fixed temperature (add_temperature) first." if thermal else
                             "Add at least one support first; the model would otherwise move as a rigid body.")
        if thermal and not any(b.get("bc_type") == "temperature" or not b.get("bc_type") for b in p.boundary_conditions):
            raise ValueError("A thermal analysis needs a fixed temperature (add_temperature) on some region.")
        driven = p.loads or (p.gravity or {}).get("enabled") or any(
            any(v for v in (b.get("values") or [])) for b in p.boundary_conditions)
        if kind not in ("modal", "thermal", "thermal_transient") and not driven:
            raise ValueError("Add at least one load, gravity or a prescribed displacement.")
        self.run_requested = True
        return {"summary": "Model complete. The simulation is being started in the workspace."}


# ------------------------------------------------------------------ LLM transport
# The loop in `run_agent` speaks one dialect (Anthropic-style: `tool_use` / `tool_result` content blocks). Gemini is
# supported by translating each request into `generateContent` and each reply back into that dialect, so the tools,
# the loop and the tests are provider-independent.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MIN_OUTPUT_TOKENS = 8192  # Gemini 2.5 Pro "thinks"; thinking tokens count against maxOutputTokens
log = logging.getLogger("webfea.agent")


def _gemini_contents(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    names = {b["id"]: b["name"] for m in messages if isinstance(m["content"], list)
             for b in m["content"] if b.get("type") == "tool_use"}
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        if isinstance(m["content"], str):
            out.append({"role": role, "parts": [{"text": m["content"]}]})
            continue
        parts: List[Dict[str, Any]] = []
        for b in m["content"]:
            if b.get("type") == "text":
                if b.get("text"):
                    parts.append({"text": b["text"]})
            elif b.get("type") == "tool_use":
                part: Dict[str, Any] = {"functionCall": {"name": b["name"], "args": b.get("input") or {}}}
                if b.get("thought_signature"):
                    part["thoughtSignature"] = b["thought_signature"]
                parts.append(part)
            elif b.get("type") == "tool_result":
                try:
                    payload = json.loads(b["content"])
                except (TypeError, ValueError):
                    payload = {"result": b.get("content")}
                parts.append({"functionResponse": {"name": names.get(b["tool_use_id"], "tool"),
                                                   "response": payload if isinstance(payload, dict) else {"result": payload}}})
        if parts:
            out.append({"role": role, "parts": parts})
    return out


def _gemini_request(body: Dict[str, Any]) -> Dict[str, Any]:
    decls = [{"name": t["name"], "description": t["description"],
              **({"parametersJsonSchema": t["input_schema"]} if t["input_schema"].get("properties") else {})}
             for t in body.get("tools") or []]
    req: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": body["system"]}]},
        "contents": _gemini_contents(body["messages"]),
        "generationConfig": {"maxOutputTokens": max(int(body.get("max_tokens") or 0), GEMINI_MIN_OUTPUT_TOKENS),
                             "temperature": 0.2},
    }
    if decls:
        req["tools"] = [{"functionDeclarations": decls}]
        req["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    return req


def _gemini_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Gemini generateContent reply -> Anthropic-style {stop_reason, content}."""
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        text = "The AI service declined to answer this request." + (f" ({reason})" if reason else "")
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}
    blocks: List[Dict[str, Any]] = []
    for i, part in enumerate((cands[0].get("content") or {}).get("parts") or []):
        if part.get("thought"):
            continue  # reasoning summary, not an answer
        if "functionCall" in part:
            fc = part["functionCall"]
            blk = {"type": "tool_use", "id": f"call_{uuid.uuid4().hex[:12]}_{i}", "name": fc.get("name", ""),
                   "input": fc.get("args") or {}}
            if part.get("thoughtSignature"):
                blk["thought_signature"] = part["thoughtSignature"]
            blocks.append(blk)
        elif part.get("text"):
            blocks.append({"type": "text", "text": part["text"]})
    if not blocks and cands[0].get("finishReason") not in (None, "STOP"):
        blocks.append({"type": "text", "text": f"The AI service stopped without an answer ({cands[0]['finishReason']})."})
    used = any(b["type"] == "tool_use" for b in blocks)
    return {"stop_reason": "tool_use" if used else "end_turn", "content": blocks}


def _post_gemini(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = httpx.post(GEMINI_URL.format(model=settings.gemini_model), json=_gemini_request(body),
                       timeout=httpx.Timeout(120.0, connect=10.0),
                       headers={"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"})
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach the AI service. Try again in a moment.")
    if r.status_code >= 400:
        log.warning("Gemini %s: %s", r.status_code, r.text[:800])
    if r.status_code in (401, 403) or (r.status_code == 400 and "API key" in r.text):
        raise HTTPException(503, "The AI service rejected the server's API key. Ask the administrator to check GEMINI_API_KEY.")
    if r.status_code == 429:
        raise HTTPException(429, "The AI service is busy. Try again in a moment.")
    if r.status_code >= 400:
        raise HTTPException(502, "The AI service returned an error. Try again in a moment.")
    return _gemini_response(r.json())


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _openrouter_messages(system: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if isinstance(m["content"], str):
            out.append({"role": m["role"], "content": m["content"]})
        elif m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in m["content"] if b.get("type") == "tool_use"]
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
        else:
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
    return out


def _openrouter_request(body: Dict[str, Any]) -> Dict[str, Any]:
    req: Dict[str, Any] = {"model": body["model"], "max_tokens": max(int(body.get("max_tokens") or 0), 4096),
                           "temperature": 0.2, "messages": _openrouter_messages(body["system"], body["messages"])}
    if body.get("tools"):
        req["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                          "parameters": t["input_schema"]}} for t in body["tools"]]
    return req


def _openrouter_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI-style chat completion -> Anthropic-style {stop_reason, content}."""
    choices = data.get("choices") or []
    if not choices:
        msg = (data.get("error") or {}).get("message")
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": msg or "The AI service returned no answer."}]}
    message = choices[0].get("message") or {}
    blocks: List[Dict[str, Any]] = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        blocks.append({"type": "tool_use", "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}_{i}",
                       "name": fn.get("name", ""), "input": args if isinstance(args, dict) else {}})
    used = any(b["type"] == "tool_use" for b in blocks)
    return {"stop_reason": "tool_use" if used else "end_turn", "content": blocks}


def _post_openrouter(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = httpx.post(OPENROUTER_URL, json=_openrouter_request(body), timeout=httpx.Timeout(120.0, connect=10.0),
                       headers={"Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": "Gearhead FEA",
                                "HTTP-Referer": settings.app_base_url, "content-type": "application/json"})
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach the AI service. Try again in a moment.")
    if r.status_code >= 400:
        log.warning("OpenRouter %s: %s", r.status_code, r.text[:800])
    if r.status_code in (401, 403):
        raise HTTPException(503, "The AI service rejected the server's API key. Ask the administrator to check OPENROUTER_API_KEY.")
    if r.status_code == 429:
        raise HTTPException(429, "The AI service is busy. Try again in a moment.")
    if r.status_code >= 400:
        raise HTTPException(502, "The AI service returned an error. Try again in a moment.")
    return _openrouter_response(r.json())


def _post_messages(body: Dict[str, Any]) -> Dict[str, Any]:
    """Send one LLM request through the configured provider. Replaced by tests."""
    send = {"gemini": _post_gemini, "anthropic": _post_anthropic, "openrouter": _post_openrouter}[settings.agent_provider]
    return send(body)


def _post_anthropic(body: Dict[str, Any]) -> Dict[str, Any]:
    """POST one Messages API request."""
    try:
        r = httpx.post(API_URL, json=body, timeout=httpx.Timeout(90.0, connect=10.0), headers={
            "x-api-key": settings.anthropic_api_key, "anthropic-version": API_VERSION,
            "content-type": "application/json"})
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach the AI service. Try again in a moment.")
    if r.status_code == 401:
        raise HTTPException(503, "The AI service rejected the server's API key. Ask the administrator to check ANTHROPIC_API_KEY.")
    if r.status_code == 429:
        raise HTTPException(429, "The AI service is busy. Try again in a moment.")
    if r.status_code >= 400:
        raise HTTPException(502, "The AI service returned an error. Try again in a moment.")
    return r.json()


def _step_label(name: str, args: Dict[str, Any]) -> str:
    return {
        "inspect_model": "Inspect model", "create_solid_mesh": "Create mesh", "create_structural_model": "Create structure",
        "set_material": "Set material", "set_analysis": "Set analysis", "add_support": "Add support",
        "add_temperature": "Add temperature", "add_load": "Add load", "set_gravity": "Set gravity",
        "clear_supports_and_loads": "Clear supports and loads", "run_simulation": "Run simulation",
    }.get(name, name)


def run_agent(db: Session, user: models.User, project: models.Project, messages: List[Dict[str, str]],
              post: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Run the tool loop. Returns {reply, steps, run_simulation}; the project is edited in place."""
    if not available():
        raise HTTPException(503, NOT_CONFIGURED)
    post = post or _post_messages
    box = ToolBox(db, user, project)
    system = f"{SYSTEM_PROMPT}\n\nCURRENT MODEL (live): {json.dumps(describe_project(project))}"
    convo: List[Dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in messages]
    steps: List[Dict[str, Any]] = []
    reply = ""
    for _ in range(max(1, settings.agent_max_turns)):
        resp = post({"model": settings.agent_active_model, "max_tokens": 2048, "system": system, "tools": TOOLS,
                     "messages": convo})
        blocks = resp.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if text:
            reply = text
        if resp.get("stop_reason") != "tool_use" or not uses:
            break
        convo.append({"role": "assistant", "content": blocks})
        results = []
        for u in uses:
            out = box.call(u.get("name", ""), u.get("input") or {})
            steps.append({"tool": u["name"], "label": _step_label(u["name"], u.get("input") or {}), "ok": out["ok"],
                          "detail": out.get("summary") or out.get("error") or ""})
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out),
                            **({} if out["ok"] else {"is_error": True})})
        convo.append({"role": "user", "content": results})
    else:
        reply = reply or "I ran out of steps before finishing. Tell me to continue and I will pick up from the current model."
    if not reply:
        reply = "Done." if steps else "I did not get a response. Please try again."
    return {"reply": reply, "steps": steps, "run_simulation": box.run_requested}
