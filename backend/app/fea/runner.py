"""
Project JSON -> FE model -> analysis -> JSON-serialisable results.

This is the single entry point the web API calls (see app/jobs.py).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from . import analyses as A
from . import assembly as asm
from .linsolve import SolverOptions
from .constraints import ConstraintError, ConstraintSystem, RefPoint
from .contact import ContactError, ContactSystem, build_pair
from .materials import Material, MaterialMap
from .model import FEMesh
from .recovery import heat_flux_fields, stress_fields

Progress = Optional[Callable[[float, str], None]]

MECHANICAL = {"static", "nonlinear", "modal", "buckling", "dynamic", "thermomech"}
THERMAL = {"thermal", "thermal_transient"}
ANALYSIS_TYPES = sorted(MECHANICAL | THERMAL)
FREE_ANALYSES = {"static"}
COMPONENT_FIELD_LIMIT = 30_000  # drop stress/strain components above this node count


class ModelError(ValueError):
    """Problem with the user's model definition (not a solver failure)."""


def _sig(values, digits: int = 7) -> List[float]:
    """Round to significant digits for compact JSON."""
    fmt = f"%.{digits}g"
    return [float(fmt % v) for v in np.asarray(values, dtype=float).ravel()]


def _sig_array(arr: np.ndarray, digits: int = 7):
    a = np.asarray(arr, dtype=float)
    return np.array(_sig(a, digits)).reshape(a.shape).tolist()


class Model:
    """Everything needed to solve, built from the stored project dict."""

    def __init__(self, project: Dict[str, Any]):
        self.p = project
        self.settings: Dict[str, Any] = dict(project.get("settings") or {})
        self.analysis = project.get("analysis_type") or "static"
        if self.analysis not in ANALYSIS_TYPES:
            raise ModelError(f"Unknown analysis type '{self.analysis}'")
        if not project.get("elements"):
            raise ModelError("The project has no mesh yet.")
        self.mesh = FEMesh(project["nodes"], project["elements"], project.get("element_type") or "C3D4")
        self.mesh.ensure_positive_orientation()
        self._build_materials(project)
        self.refs = [RefPoint(str(r["id"]), np.array(r["coords"], dtype=float), r.get("name", ""))
                     for r in (project.get("reference_points") or [])]
        all_cons = self._resolve_constraints(project.get("constraints") or [])
        self.contact_raw = [c for c in all_cons if c.get("type") == "contact"]
        self.cons_raw = [c for c in all_cons if c.get("type") != "contact"]
        try:
            self.cs = ConstraintSystem(self.mesh, self.refs, self.cons_raw)
        except ConstraintError as exc:
            raise ModelError(str(exc)) from exc
        self.bcs = [self._expanded(b) for b in (project.get("boundary_conditions") or [])]
        self.loads = [self._expanded(l) for l in (project.get("loads") or [])]

    # ------------------------------------------------------- parts & materials
    def _build_materials(self, project: Dict[str, Any]) -> None:
        """Material library + parts (contiguous element ranges) -> per-element MaterialMap."""
        lib = {"default": Material.from_dict(project.get("material"))}
        for m in project.get("materials") or []:
            lib[str(m["id"])] = Material.from_dict(m)
        n = self.mesh.n_elems
        parts = [dict(p) for p in (project.get("parts") or [])]
        if not parts:
            parts = [{"id": "p0", "name": "Body", "elem_start": 0, "elem_end": n, "material_id": "default"}]
        parts.sort(key=lambda p: p["elem_start"])
        edge = 0
        for p in parts:
            if p["elem_start"] != edge or p["elem_end"] <= p["elem_start"]:
                raise ModelError("The part definitions do not cover the mesh. Re-create the parts.")
            edge = p["elem_end"]
        if edge != n:
            raise ModelError("The part definitions do not cover the mesh. Re-create the parts.")
        by_mat: Dict[str, List[np.ndarray]] = {}
        self.elem_part = np.empty(n, dtype=np.int64)
        for k, p in enumerate(parts):
            mid = str(p.get("material_id") or "default")
            if mid not in lib:
                raise ModelError(f"Part '{p.get('name', p['id'])}' uses a material that no longer exists.")
            by_mat.setdefault(mid, []).append(np.arange(p["elem_start"], p["elem_end"]))
            self.elem_part[p["elem_start"]:p["elem_end"]] = k
        groups = [(lib[mid], None if len(by_mat) == 1 else np.concatenate(chunks)) for mid, chunks in by_mat.items()]
        self.parts = parts
        self.materials = lib
        self.mats = MaterialMap(n, groups)
        self.mat = self.mats.default  # representative material (messages, export header)

    def _part_index(self, pid: str) -> int:
        for k, p in enumerate(self.parts):
            if str(p["id"]) == str(pid):
                return k
        raise ModelError("A constraint refers to a part that no longer exists.")

    # ---------------------------------------------------------- selections
    def _expanded(self, item: dict) -> dict:
        item = dict(item)
        if item.get("node_ids"):
            item["node_ids"] = [int(n) for n in self.mesh.expand_selection(item["node_ids"])]
        return item

    def _faces(self, tris) -> np.ndarray:
        return self.mesh.boundary().faces_for_triangles(tris or [])

    def _face_nodes(self, tris) -> List[int]:
        idx = self._faces(tris)
        return sorted({int(n) for n in self.mesh.boundary().face_nodes[idx].ravel()})

    def _resolve_constraints(self, raw: List[dict]) -> List[dict]:
        out = []
        for c in raw:
            c = dict(c)
            if c.get("type") == "rigid" and c.get("faces") and not c.get("node_ids"):
                c["node_ids"] = self._face_nodes(c["faces"])
            elif c.get("type") == "rigid" and c.get("node_ids"):
                c["node_ids"] = [int(n) for n in self.mesh.expand_selection(c["node_ids"])]
            if c.get("type") == "tie" and c.get("master_part") and c.get("slave_part"):
                mi, si = self._part_index(c["master_part"]), self._part_index(c["slave_part"])
                if mi == si:
                    raise ModelError("A tie needs two different parts.")
                b = self.mesh.boundary()
                tri_part = self.elem_part[b.elem[b.tri_face]]
                c["master_faces"] = b.tris[tri_part == mi].tolist()
                face_part = self.elem_part[b.elem]
                c["slave_node_ids"] = sorted({int(x) for x in b.face_nodes[face_part == si].ravel()})
                c["strict"] = False
            if c.get("type") == "tie" and c.get("slave_faces") and not c.get("slave_node_ids"):
                c["slave_node_ids"] = self._face_nodes(c["slave_faces"])
            out.append(c)
        return out

    # ------------------------------------------------------------ mechanics
    def _is_temperature_bc(self, bc: dict) -> bool:
        kind = bc.get("bc_type")
        if kind:
            return kind == "temperature"
        return self.analysis in THERMAL

    def fixed_dofs(self) -> Dict[int, float]:
        fixed: Dict[int, float] = {}
        for bc in self.bcs:
            if self._is_temperature_bc(bc):
                continue
            dofs = bc.get("dofs") or []
            vals = bc.get("values")
            vals = vals if vals is not None and len(vals) == len(dofs) else [0.0] * len(dofs)
            if bc.get("ref_id"):
                try:
                    for d, v in zip(dofs, vals):
                        fixed[self.cs.ref_dof(str(bc["ref_id"]), int(d))] = float(v)
                except ConstraintError as exc:
                    raise ModelError(str(exc)) from exc
                continue
            for nid in bc.get("node_ids", []):
                for d, v in zip(dofs, vals):
                    fixed[3 * int(nid) + int(d)] = float(v)
        return fixed

    def mech_loads(self) -> np.ndarray:
        mesh, cs = self.mesh, self.cs
        f = np.zeros(cs.n_full)
        n3 = 3 * mesh.n_nodes
        for ld in self.loads:
            kind = ld.get("load_type", "force")
            if kind == "force":
                ids = ld.get("node_ids") or []
                if ids:
                    share = np.array(ld.get("vector", [0, 0, 0]), dtype=float) / len(ids)
                    for nid in ids:
                        f[3 * int(nid): 3 * int(nid) + 3] += share
            elif kind == "pressure":
                f[:n3] += asm.pressure_vector(mesh, self._faces(ld.get("faces")), float(ld.get("magnitude") or 0.0))
            elif kind == "traction":
                vec = np.array(ld.get("vector", [0, 0, 0]), dtype=float)
                f[:n3] += asm.face_vector(mesh, self._faces(ld.get("faces")),
                                          lambda xg, n, v=vec: np.broadcast_to(v, xg.shape).copy())
            elif kind == "ref_force":
                try:
                    base = cs.ref_dof(str(ld.get("ref_id")), 0)
                except ConstraintError as exc:
                    raise ModelError(str(exc)) from exc
                f[base: base + 3] += np.array(ld.get("vector", [0, 0, 0]), dtype=float)
                f[base + 3: base + 6] += np.array(ld.get("moment", [0, 0, 0]), dtype=float)
        g = self.p.get("gravity") or {}
        if g.get("enabled"):
            gvec = np.array([g.get("x", 0.0), g.get("y", 0.0), g.get("z", -9.81)], dtype=float)
            f[:n3] += asm.body_force_vector(mesh, lambda xg: np.broadcast_to(gvec, xg.shape).copy(),
                                            scale=self.mats.prop("density"))
        cf = self.settings.get("centrifugal") or {}
        if cf.get("enabled"):
            omega = float(cf.get("omega") or 0.0) or float(cf.get("rpm") or 0.0) * 2 * np.pi / 60.0
            axis = np.array(cf.get("axis", [0, 0, 1]), dtype=float)
            if np.linalg.norm(axis) == 0:
                raise ModelError("Centrifugal axis cannot be the zero vector")
            axis = axis / np.linalg.norm(axis)
            origin = np.array(cf.get("origin", [0, 0, 0]), dtype=float)

            def body(xg):
                r = xg - origin
                perp = r - np.einsum("egj,j->eg", r, axis)[..., None] * axis
                return omega ** 2 * perp

            f[:n3] += asm.body_force_vector(mesh, body, scale=self.mats.prop("density"))
        return f

    def contact_system(self) -> Optional[ContactSystem]:
        """Frictionless contact pairs between parts (None when the model has none)."""
        if not self.contact_raw:
            return None
        b = self.mesh.boundary()
        face_part = self.elem_part[b.elem]
        E = self.mats.prop("E")
        pairs = []
        for c in self.contact_raw:
            mi, si = self._part_index(c.get("master_part")), self._part_index(c.get("slave_part"))
            if mi == si:
                raise ModelError("A contact pair needs two different parts.")
            e_ref = float(E[(self.elem_part == mi) | (self.elem_part == si)].max())
            try:
                pairs.append(build_pair(
                    self.mesh, self.mats, c.get("name") or "Contact", np.where(face_part == mi)[0], np.where(face_part == si)[0],
                    float(c.get("penalty_factor") or 10.0), None, e_ref))
            except ContactError as exc:
                raise ModelError(str(exc)) from exc
        return ContactSystem(self.mesh, pairs)

    def solver_options(self) -> SolverOptions:
        mode = str(self.settings.get("linear_solver", "auto"))
        if mode not in ("auto", "direct", "iterative"):
            raise ModelError("linear_solver must be auto, direct or iterative")
        o = SolverOptions(mode=mode)
        if self.settings.get("auto_threshold"):
            o.auto_threshold = int(self.settings["auto_threshold"])
        if self.settings.get("solver_tolerance"):
            o.tol = float(self.settings["solver_tolerance"])
        return o

    def mech_problem(self, T: Optional[np.ndarray] = None, Tref: float = 0.0) -> A.MechProblem:
        prob = A.MechProblem(self.mesh, self.mats, self.cs, self.fixed_dofs(), self.mech_loads(), T, Tref,
                             self.solver_options())
        prob.nlgeom = bool(self.settings.get("nlgeom"))
        prob.finite_model = str(self.settings.get("finite_strain_model") or "svk")
        if prob.nlgeom and prob.finite_model not in ("svk", "neohookean"):
            raise ModelError("finite_strain_model must be svk or neohookean")
        return prob

    # -------------------------------------------------------------- thermal
    def thermal_problem(self) -> A.ThermalProblem:
        mesh = self.mesh
        n = mesh.n_nodes
        T_fixed: Dict[int, float] = {}
        for bc in self.bcs:
            if not self._is_temperature_bc(bc):
                continue
            vals = bc.get("values") or [0.0]
            for nid in bc.get("node_ids", []):
                T_fixed[int(nid)] = float(vals[0])
        Q = np.zeros(n)
        Kh = None
        qh = np.zeros(n)
        radiation: List[dict] = []
        for ld in self.loads:
            kind = ld.get("load_type")
            if kind == "heat_flux":  # concentrated nodal heat (W)
                ids = ld.get("node_ids") or []
                if ids:
                    for nid in ids:
                        Q[int(nid)] += float(ld.get("magnitude") or 0.0) / len(ids)
            elif kind == "surface_flux":
                mag = float(ld.get("magnitude") or 0.0)
                Q += asm.face_scalar_vector(mesh, self._faces(ld.get("faces")), lambda xg, m=mag: np.full(xg.shape[:2], m))
            elif kind == "body_heat":
                Q += asm.body_scalar_vector(mesh, float(ld.get("magnitude") or 0.0))
            elif kind == "convection":
                h, Tinf = float(ld.get("h") or 0.0), float(ld.get("T_inf") or 0.0)
                faces = self._faces(ld.get("faces"))
                Km = asm.face_scalar_matrix(mesh, faces, h)
                Kh = Km if Kh is None else Kh + Km
                qh += asm.face_scalar_vector(mesh, faces, lambda xg, v=h * Tinf: np.full(xg.shape[:2], v))
            elif kind == "radiation":
                radiation.append({"faces": self._faces(ld.get("faces")),
                                  "emissivity": float(ld.get("emissivity", 0.8)), "T_inf": float(ld.get("T_inf", 0.0))})
        return A.ThermalProblem(mesh, self.mats, T_fixed, Q, Kh, qh if Kh is not None else None, radiation,
                                float(self.settings.get("initial_temperature", 0.0)),
                                float(self.settings.get("absolute_zero", -273.15)), self.cs, self.solver_options())

    # -------------------------------------------------------------- packing
    def surface_ids(self) -> np.ndarray:
        return np.unique(self.mesh.boundary().tris)


def _fields_json(fields: Dict[str, np.ndarray], n_nodes: int) -> Dict[str, List[float]]:
    drop = set()
    if n_nodes > COMPONENT_FIELD_LIMIT:
        drop = {"sxx", "syy", "szz", "sxy", "syz", "szx", "exx", "eyy", "ezz", "exy", "eyz", "ezx"}
    return {k: _sig(v) for k, v in fields.items() if k not in drop}


def _static_results(model: Model, out: A.StaticOut, label: str) -> Dict[str, Any]:
    mesh = model.mesh
    fields = stress_fields(mesh, out.stress, out.strain, model.mats, out.alpha_p)
    u = out.u.reshape(-1, 3)
    mag = np.linalg.norm(u, axis=1)
    fields["displacement_magnitude"] = mag
    for i, ax in enumerate("xyz"):
        fields[f"u{ax}"] = u[:, i]
    res = {
        "analysis_type": label,
        "displacements": _sig_array(u),
        "reaction_forces": _sig_array(out.reactions.reshape(-1, 3)),
        "fields": _fields_json(fields, mesh.n_nodes),
        "von_mises_nodal": _sig(fields["von_mises"]),
        "principal_stress_max_nodal": _sig(fields["principal_max"]),
        "principal_stress_min_nodal": _sig(fields["principal_min"]),
        "max_displacement": float(mag.max()),
        "max_von_mises": float(fields["von_mises"].max()),
        "strain_energy": out.energy,
        "reaction_total": [float(x) for x in out.reactions.reshape(-1, 3).sum(axis=0)],
        "reference_points": {k: [float(x) for x in v] for k, v in out.ref_motion.items()},
    }
    if out.alpha_p is not None:
        res["max_plastic_strain"] = float(fields["plastic_strain"].max())
    if out.contact is not None:
        c = out.contact
        gap = np.nan_to_num(c["gap"], nan=0.0, posinf=0.0, neginf=0.0)
        res["fields"]["contact_pressure"] = _sig(c["pressure"])
        res["fields"]["contact_gap"] = _sig(gap)
        res["contact"] = {"active_nodes": c["active"], "max_penetration": float(c["max_penetration"]),
                          "total_force": float(c["total_force"]), "max_pressure": float(np.max(c["pressure"]))}
    return res


def _thermal_fields(model: Model, T: np.ndarray) -> Dict[str, np.ndarray]:
    fields = {"temperature": T}
    fields.update(heat_flux_fields(model.mesh, T, model.mats.prop("conductivity")))
    return fields


def run(project: Dict[str, Any], progress: Progress = None) -> Dict[str, Any]:
    """Run the project's analysis. Returns {"results": dict, "log": str}."""
    from .structural import is_structural
    if is_structural(project.get("element_type")):
        from . import struct_runner
        return struct_runner.run(project, progress)
    model = Model(project)
    mesh, st = model.mesh, model.settings
    kind = model.analysis
    log: List[str] = [f"Mesh: {mesh.n_nodes} nodes, {mesh.n_elems} {mesh.et.name} elements, "
                      f"{len(model.parts)} part(s), {len(model.mats.groups)} material(s), "
                      f"{len(model.cs.expr)} constrained DOFs"]

    def need_supports(fixed: Dict[int, float]) -> None:
        if not fixed:
            raise ModelError("Add at least one support (boundary condition) before running this analysis.")

    nlgeom = bool(st.get("nlgeom"))
    if nlgeom and kind not in ("static", "nonlinear"):
        raise ModelError("Large deformation (NLGEOM) is supported in static and nonlinear analyses only. Switch it off or change the analysis type.")
    contact = model.contact_system()
    if contact is not None and kind not in ("static", "nonlinear"):
        raise ModelError("Contact is supported in static and nonlinear analyses only. Remove the contact pair or change the analysis type.")

    if kind == "static" and contact is None and not nlgeom:
        prob = model.mech_problem(*_uniform_T(model))
        need_supports(prob.fixed)
        if not (prob.f_ext.any() or any(v != 0 for v in prob.fixed.values()) or prob.T is not None):
            raise ModelError("Add at least one load, prescribed displacement or temperature before running a static analysis.")
        out = A.static(prob, progress)
        log.append(out.log)
        return {"results": _static_results(model, out, "static"), "log": "\n".join(log)}

    if kind == "nonlinear" or (kind == "static" and (contact is not None or nlgeom)):
        if kind == "nonlinear" and not model.mats.plastic and contact is None and not nlgeom:
            log.append("Note: the material has no yield stress, so the response is linear elastic.")
        prob = model.mech_problem(*_uniform_T(model))
        prob.contact = contact
        need_supports(prob.fixed)
        if not (prob.f_ext.any() or any(v != 0 for v in prob.fixed.values()) or prob.T is not None):
            raise ModelError("Add at least one load or prescribed displacement before running the analysis.")
        out = A.nonlinear_static(prob, int(st.get("increments", 10)), progress=progress)
        log.append(out.log)
        return {"results": _static_results(model, out, kind), "log": "\n".join(log)}

    if kind == "thermomech":
        tp = model.thermal_problem()
        _report(progress, 0.05, "Solving heat transfer")
        if st.get("thermal_mode") == "transient":
            tr = A.thermal_transient(tp, float(st.get("t_end", 1.0)), int(st.get("n_steps", 50)),
                                     float(st.get("theta", 1.0)), A.amplitude(st.get("amplitude")),
                                     progress=progress)
            T = tr.final
        else:
            T = A.thermal_steady(tp, progress)
        Tref = float(st.get("reference_temperature", 20.0))
        prob = model.mech_problem(T, Tref)
        need_supports(prob.fixed)
        out = A.static(prob, progress)
        res = _static_results(model, out, "thermomech")
        res["fields"].update(_fields_json(_thermal_fields(model, T), mesh.n_nodes))
        res["max_temperature"], res["min_temperature"] = float(T.max()), float(T.min())
        res["temperatures"] = _sig(T)
        log.append(f"Sequential thermo-mechanical: T in [{T.min():.4g}, {T.max():.4g}], reference {Tref:g}")
        return {"results": res, "log": "\n".join(log)}

    if kind in ("modal", "buckling"):
        prob = model.mech_problem()
        if kind == "buckling":
            need_supports(prob.fixed)
            if not prob.f_ext.any():
                raise ModelError("Buckling needs a reference load (the load that is scaled to find the buckling load).")
        n_modes = int(st.get("n_modes") or project.get("n_modes") or 6)
        out = A.frequency(prob, n_modes, progress) if kind == "modal" else A.buckling(prob, min(n_modes, 20), progress)
        ids = model.surface_ids()
        res = {
            "analysis_type": kind,
            ("frequencies_hz" if kind == "modal" else "load_factors"): _sig(out.values),
            "mode_shapes": [_sig_array(s) for s in out.shapes] if mesh.n_nodes <= 20000 else
                           [_sig_array(s[ids]) for s in out.shapes],
            "mode_shape_nodes": None if mesh.n_nodes <= 20000 else [int(i) for i in ids],
        }
        log.append(out.log)
        return {"results": res, "log": "\n".join(log)}

    if kind == "dynamic":
        prob = model.mech_problem()
        need_supports(prob.fixed)
        if not prob.f_ext.any():
            raise ModelError("A dynamic analysis needs a load.")
        t_end, n_steps = float(st.get("t_end", 0.1)), int(st.get("n_steps", 200))
        _check_time(t_end, n_steps)
        out = A.dynamic(prob, t_end, n_steps, A.amplitude(st.get("amplitude")),
                        float(st.get("rayleigh_alpha", 0.0)), float(st.get("rayleigh_beta", 0.0)),
                        [int(i) for i in st.get("history_nodes", [])], progress=progress)
        ids = model.surface_ids()
        mags = [np.linalg.norm(fr, axis=1) for fr in out.frames]
        peak = int(np.argmax([m.max() for m in mags]))
        peak_stress = asm.mech_assemble(mesh, model.mats, out.frames[peak].ravel(), None, None, 0.0, want_K=False)
        fields = stress_fields(mesh, peak_stress.stress, peak_stress.strain, model.mats)
        fields["displacement_magnitude"] = mags[peak]
        res = {
            "analysis_type": "dynamic",
            "times": _sig(out.times),
            "frame_nodes": [int(i) for i in ids],
            "frames": [_sig_array(fr[ids], 6) for fr in out.frames],
            "history": {k: _sig(v) for k, v in out.history.items()},
            "peak_frame": peak,
            "fields": _fields_json(fields, mesh.n_nodes),
            "displacements": _sig_array(out.frames[peak]),
            "von_mises_nodal": _sig(fields["von_mises"]),
            "max_displacement": float(max(m.max() for m in mags)),
            "max_von_mises": float(fields["von_mises"].max()),
        }
        res["frame_times"] = _sig(_frame_times(t_end, n_steps, len(out.frames)))
        log.append(out.log)
        return {"results": res, "log": "\n".join(log)}

    if kind in ("thermal", "thermal_transient"):
        tp = model.thermal_problem()
        if kind == "thermal":
            T = A.thermal_steady(tp, progress)
            res = {"analysis_type": "thermal", "temperatures": _sig(T), "max_temperature": float(T.max()),
                   "min_temperature": float(T.min()), "fields": _fields_json(_thermal_fields(model, T), mesh.n_nodes)}
            log.append("Steady-state heat transfer")
            return {"results": res, "log": "\n".join(log)}
        t_end, n_steps = float(st.get("t_end", 100.0)), int(st.get("n_steps", 100))
        _check_time(t_end, n_steps)
        tr = A.thermal_transient(tp, t_end, n_steps, float(st.get("theta", 1.0)), A.amplitude(st.get("amplitude")),
                                 [int(i) for i in st.get("history_nodes", [])], progress=progress)
        ids = model.surface_ids()
        T = tr.final
        res = {
            "analysis_type": "thermal_transient",
            "temperatures": _sig(T), "max_temperature": float(T.max()), "min_temperature": float(T.min()),
            "times": _sig(tr.times),
            "frame_times": _sig(_frame_times(t_end, n_steps, len(tr.frames))),
            "frame_nodes": [int(i) for i in ids],
            "frames": [_sig(fr[ids], 6) for fr in tr.frames],
            "history": {k: _sig(v) for k, v in tr.history.items()},
            "fields": _fields_json(_thermal_fields(model, T), mesh.n_nodes),
        }
        log.append(tr.log)
        return {"results": res, "log": "\n".join(log)}

    raise ModelError(f"Analysis '{kind}' is not implemented")


# -------------------------------------------------------------------- helpers
def _report(progress: Progress, frac: float, msg: str) -> None:
    if progress:
        progress(frac, msg)


def _uniform_T(model: Model) -> Tuple[Optional[np.ndarray], float]:
    u = model.settings.get("uniform_temperature")
    if u is None or u == "":
        return None, 0.0
    return np.full(model.mesh.n_nodes, float(u)), float(model.settings.get("reference_temperature", 20.0))


def _check_time(t_end: float, n_steps: int) -> None:
    if t_end <= 0 or n_steps < 1:
        raise ModelError("Duration must be positive and there must be at least one time step.")
    if n_steps > 20000:
        raise ModelError("Too many time steps (limit 20000).")


def _frame_times(t_end: float, n_steps: int, n_frames: int) -> List[float]:
    stride = max(1, n_steps // 50)
    t = [0.0] + [min(k * stride, n_steps) * t_end / n_steps for k in range(1, n_frames)]
    if n_frames > 1:
        t[-1] = t_end
    return t

