"""
Structural models (truss / beam / shell / spring): project dict -> assembly -> analysis -> JSON-serialisable results.

Supported analyses: static, modal, linear buckling (truss and beam), transient dynamic (Newmark).
Entry point: run(project, progress) -> {"results": ..., "log": ...}   (called from runner.run).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy import linalg as dla
from scipy import sparse
from scipy.sparse import linalg as spla

from . import structural as S
from .analyses import SolverError, amplitude, solve_partitioned
from .linsolve import SolverOptions
from .materials import Material
from .structural import STRUCT_TYPES, StructuralError

Progress = Optional[Callable[[float, str], None]]
STRUCT_ANALYSES = ("static", "modal", "buckling", "dynamic")
CHUNK = 4000


def _sig(values, digits: int = 7) -> List[float]:
    fmt = f"%.{digits}g"
    return [float(fmt % v) for v in np.asarray(values, dtype=float).ravel()]


def _sig_array(arr, digits: int = 7):
    a = np.asarray(arr, dtype=float)
    return np.array(_sig(a, digits)).reshape(a.shape).tolist()


def _scatter(Ke: np.ndarray, dofs: np.ndarray):
    r = np.broadcast_to(dofs[:, :, None], Ke.shape).ravel()
    c = np.broadcast_to(dofs[:, None, :], Ke.shape).ravel()
    return r, c, Ke.ravel()


def _csr(chunks, n) -> sparse.csr_matrix:
    if not chunks:
        return sparse.csr_matrix((n, n))
    r = np.concatenate([c[0] for c in chunks])
    c = np.concatenate([c[1] for c in chunks])
    v = np.concatenate([c[2] for c in chunks])
    return sparse.coo_matrix((v, (r, c)), shape=(n, n)).tocsr()


def von_mises_plane(sx, sy, txy):
    return np.sqrt(np.maximum(sx ** 2 - sx * sy + sy ** 2 + 3.0 * txy ** 2, 0.0))


class StructModel:
    """Everything needed to solve a structural project."""

    def __init__(self, project: Dict[str, Any]):
        self.p = project
        self.etype = str(project.get("element_type") or "")
        if self.etype not in STRUCT_TYPES:
            raise StructuralError(f"'{self.etype}' is not a structural element type")
        nn = STRUCT_TYPES[self.etype]
        if not project.get("elements"):
            raise StructuralError("The project has no mesh yet.")
        self.nodes = np.asarray(project["nodes"], dtype=float)
        self.elems = np.asarray(project["elements"], dtype=np.int64)
        if self.elems.ndim != 2 or self.elems.shape[1] != nn:
            raise StructuralError(f"{self.etype} elements need {nn} nodes each.")
        if self.elems.min() < 0 or self.elems.max() >= len(self.nodes):
            raise StructuralError("Element connectivity references a node that does not exist.")
        self.N, self.ne = len(self.nodes), len(self.elems)
        self.ndof = 6 * self.N
        self.settings = dict(project.get("settings") or {})
        self.notes: List[str] = []
        self._materials_and_sections(project)
        self._geometry()
        self.f_ext = np.zeros(self.ndof)
        self.point_mass = np.zeros(self.N)
        self.beam_eq = np.zeros((self.ne, 12)) if self.etype == "B31" else None   # equivalent loads (local) for recovery
        self.fixed: Dict[int, float] = {}
        self._build_bcs(project.get("boundary_conditions") or [])
        self._build_loads(project.get("loads") or [], project.get("gravity") or {})

    # ------------------------------------------------------------- properties
    def _materials_and_sections(self, project: Dict[str, Any]) -> None:
        lib = {"default": Material.from_dict(project.get("material"))}
        for m in project.get("materials") or []:
            lib[str(m["id"])] = Material.from_dict(m)
        parts = [dict(p) for p in (project.get("parts") or [])]
        if not parts:
            parts = [{"id": "p0", "name": "Body", "elem_start": 0, "elem_end": self.ne, "material_id": "default"}]
        parts.sort(key=lambda p: p["elem_start"])
        edge = 0
        for p in parts:
            if p["elem_start"] != edge or p["elem_end"] <= p["elem_start"]:
                raise StructuralError("The part definitions do not cover the mesh. Re-create the parts.")
            edge = p["elem_end"]
        if edge != self.ne:
            raise StructuralError("The part definitions do not cover the mesh. Re-create the parts.")
        self.parts = parts
        ne = self.ne
        self.E, self.nu, self.rho = np.zeros(ne), np.zeros(ne), np.zeros(ne)
        self.part_of = np.zeros(ne, dtype=np.int64)
        self.sec: Dict[str, np.ndarray] = {}
        keys = {"T3D2": ["area"], "S4": ["thickness"], "SPRING2": ["k", "c"],
                "B31": ["A", "Iy", "Iz", "J", "Asy", "Asz", "cy", "cz", "rt"]}[self.etype]
        for k in keys:
            self.sec[k] = np.zeros(ne)
        self.beam_ref = np.zeros((ne, 3)) if self.etype == "B31" else None
        self.beam_has_ref = np.zeros(ne, dtype=bool)
        for k, p in enumerate(parts):
            sl = slice(p["elem_start"], p["elem_end"])
            mid = str(p.get("material_id") or "default")
            if mid not in lib:
                raise StructuralError(f"Part '{p.get('name', p['id'])}' uses a material that no longer exists.")
            mat = lib[mid]
            if mat.plastic:
                self.notes.append(f"Note: material '{mid}' has a yield stress, but structural elements are linear elastic.")
            self.E[sl], self.nu[sl], self.rho[sl] = mat.E, mat.nu, mat.density
            self.part_of[sl] = k
            raw = p.get("section") or S.default_section(self.etype)
            try:
                clean = S.validate_section(self.etype, raw)
            except StructuralError as exc:
                raise StructuralError(f"Part '{p.get('name', p['id'])}': {exc}") from exc
            if self.etype == "B31":
                props = S.beam_section(clean)
                for key in keys:
                    self.sec[key][sl] = props[key]
                if clean.get("orientation"):
                    self.beam_ref[sl] = clean["orientation"]
                    self.beam_has_ref[sl] = True
            else:
                for key in keys:
                    self.sec[key][sl] = clean.get(key, 0.0)
        self.G = self.E / (2.0 * (1.0 + self.nu))

    # --------------------------------------------------------------- geometry
    def _geometry(self) -> None:
        X = self.nodes[self.elems]                      # (ne,nn,3)
        if self.etype == "S4":
            self.R, self.xy, self.center = S.shell_frames(X)
            self.kin = S.shell_kinematics(self.xy)
            self.shell_K_local, self.alpha_map = S.shell_local_K(self.kin, self.E, self.nu, self.sec["thickness"])
            return
        d = X[:, 1] - X[:, 0]
        self.L = np.linalg.norm(d, axis=1)
        if np.any(self.L <= 1e-12):
            raise StructuralError("An element has zero length (two nodes at the same place).")
        self.ex = d / self.L[:, None]
        if self.etype == "B31":
            ref = self.beam_ref.copy()
            ref[~self.beam_has_ref] = [0.0, 1.0, 0.0]
            self.R, _ = S.beam_frames(X[:, 0], X[:, 1], ref)

    # ---------------------------------------------------------------- loading
    def _build_bcs(self, bcs: List[dict]) -> None:
        for bc in bcs:
            if bc.get("bc_type") == "temperature":
                raise StructuralError("Temperature boundary conditions are not available for structural elements.")
            if bc.get("ref_id"):
                raise StructuralError("Reference points are not available for structural elements.")
            dofs = bc.get("dofs") or []
            vals = bc.get("values")
            vals = vals if vals is not None and len(vals) == len(dofs) else [0.0] * len(dofs)
            for nid in bc.get("node_ids", []):
                for d, v in zip(dofs, vals):
                    if not 0 <= int(d) <= 5:
                        raise StructuralError("Structural DOFs must be 0..5 (ux uy uz rx ry rz).")
                    self.fixed[6 * int(nid) + int(d)] = float(v)

    def active_mask(self) -> np.ndarray:
        """DOFs that carry stiffness: unused nodes have none, truss/spring nodes only translations."""
        m = np.zeros(self.ndof, dtype=bool)
        for node in np.unique(self.elems):
            if self.etype in ("T3D2", "SPRING2"):
                m[6 * node:6 * node + 3] = True
            else:
                m[6 * node:6 * node + 6] = True
        return m

    def _tri_lookup(self) -> Dict[Tuple[int, int, int], int]:
        lk = {}
        for e, q in enumerate(self.elems):
            for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
                lk[tuple(sorted(int(x) for x in tri))] = e
        return lk

    def _faces_to_elements(self, tris) -> np.ndarray:
        lk = self._tri_lookup()
        out = set()
        for t in tris or []:
            key = tuple(sorted(int(x) for x in t))
            if key not in lk:
                raise StructuralError(f"Selected face {list(key)} is not part of the shell.")
            out.add(lk[key])
        return np.array(sorted(out), dtype=np.int64)

    def _shell_traction(self, elems: np.ndarray, t_local: np.ndarray) -> None:
        """Add a uniform local-axes traction (len(elems),3) [Pa] on shell elements to f_ext."""
        sub = S.ShellKin(self.kin.Bc[elems], self.kin.Bi[elems], self.kin.Bb[elems], self.kin.Bs[elems],
                         self.kin.wdet[elems], self.kin.area[elems])
        N, _ = S._q4(S._SHELL_GAUSS)
        w = np.einsum("eg,gi->ei", sub.wdet, N)
        f = np.zeros((len(elems), 24))
        for c in range(3):
            f[:, c::6] = w * t_local[:, c][:, None]
        T = S._blockdiag_T(self.R[elems], 8)
        fg = np.einsum("eji,ej->ei", T, f)
        dofs = (self.elems[elems][:, :, None] * 6 + np.arange(6)).reshape(len(elems), -1)
        np.add.at(self.f_ext, dofs.ravel(), fg.ravel())

    def _beam_line_load(self, elems: np.ndarray, q_global: np.ndarray) -> None:
        """Uniform line load (N/m, global vector, same for each element in `elems`) on beams."""
        q_loc = np.einsum("eij,j->ei", self.R[elems], q_global)
        feq = S.beam_equivalent_load(q_loc, self.L[elems])
        self.beam_eq[elems] += feq
        T = S._blockdiag_T(self.R[elems], 4)
        fg = np.einsum("eji,ej->ei", T, feq)
        dofs = (self.elems[elems][:, :, None] * 6 + np.arange(6)).reshape(len(elems), -1)
        np.add.at(self.f_ext, dofs.ravel(), fg.ravel())

    def _build_loads(self, loads: List[dict], gravity: dict) -> None:
        shell = self.etype == "S4"
        for ld in loads:
            kind = ld.get("load_type", "force")
            if kind == "force":
                ids = [int(n) for n in (ld.get("node_ids") or [])]
                if not ids:
                    continue
                vec = np.array(ld.get("vector") or [0, 0, 0], dtype=float) / len(ids)
                mom = np.array(ld.get("moment") or [0, 0, 0], dtype=float) / len(ids)
                for n in ids:
                    self.f_ext[6 * n:6 * n + 3] += vec
                    self.f_ext[6 * n + 3:6 * n + 6] += mom
            elif kind in ("pressure", "traction"):
                if not shell:
                    raise StructuralError("Pressure and traction loads need shell elements.")
                el = self._faces_to_elements(ld.get("faces"))
                if kind == "pressure":
                    p = float(ld.get("magnitude") or 0.0)
                    t_local = np.tile([0.0, 0.0, -p], (len(el), 1))              # positive = against the element normal
                else:
                    vec = np.array(ld.get("vector") or [0, 0, 0], dtype=float)
                    t_local = np.einsum("eij,j->ei", self.R[el], vec)
                self._shell_traction(el, t_local)
            elif kind == "line_load":
                if self.etype != "B31":
                    raise StructuralError("Line loads need beam elements.")
                el = self._part_elements(ld.get("part_id"))
                self._beam_line_load(el, np.array(ld.get("vector") or [0, 0, 0], dtype=float))
            elif kind == "point_mass":
                for n in ld.get("node_ids") or []:
                    self.point_mass[int(n)] += float(ld.get("magnitude") or 0.0)
            elif kind == "ref_force":
                raise StructuralError("Reference points are not available for structural elements.")
            else:
                raise StructuralError(f"Load type '{kind}' is not available for structural elements.")
        if gravity.get("enabled"):
            g = np.array([gravity.get("x", 0.0), gravity.get("y", 0.0), gravity.get("z", -9.81)], dtype=float)
            allel = np.arange(self.ne)
            if self.etype == "B31":
                w = (self.rho * self.sec["A"])[:, None] * g[None, :]                       # weight per unit length, per element
                q_loc = np.einsum("eij,ej->ei", self.R, w)
                feq = S.beam_equivalent_load(q_loc, self.L)
                self.beam_eq += feq
                T = S._blockdiag_T(self.R, 4)
                fg = np.einsum("eji,ej->ei", T, feq)
                dofs = (self.elems[:, :, None] * 6 + np.arange(6)).reshape(self.ne, -1)
                np.add.at(self.f_ext, dofs.ravel(), fg.ravel())
            elif self.etype == "T3D2":
                m = self.rho * self.sec["area"] * self.L / 2.0
                for k in range(2):
                    for c in range(3):
                        np.add.at(self.f_ext, 6 * self.elems[:, k] + c, m * g[c])
            elif shell:
                t_local = np.einsum("eij,j->ei", self.R, g) * (self.rho * self.sec["thickness"])[:, None]
                self._shell_traction(allel, t_local)
            for n in np.nonzero(self.point_mass)[0]:
                self.f_ext[6 * n:6 * n + 3] += self.point_mass[n] * g

    def _part_elements(self, part_id) -> np.ndarray:
        if part_id in (None, "", "all"):
            return np.arange(self.ne)
        for p in self.parts:
            if str(p["id"]) == str(part_id):
                return np.arange(p["elem_start"], p["elem_end"])
        raise StructuralError("A line load refers to a part that no longer exists.")

    # --------------------------------------------------------------- assembly
    def _dofs(self, sl) -> np.ndarray:
        e = self.elems[sl]
        if self.etype in ("T3D2", "SPRING2"):
            return np.concatenate([(e[:, 0:1] * 6 + np.arange(3)), (e[:, 1:2] * 6 + np.arange(3))], axis=1)
        return (e[:, :, None] * 6 + np.arange(6)).reshape(len(e), -1)

    def assemble(self, want=("K", "M", "C")) -> Dict[str, sparse.csr_matrix]:
        Kc, Mc, Cc = [], [], []
        for a in range(0, self.ne, CHUNK):
            sl = slice(a, min(a + CHUNK, self.ne))
            dofs = self._dofs(sl)
            if self.etype == "B31":
                T = S._blockdiag_T(self.R[sl], 4)
                Kl = S.beam_local_K(self.E[sl], self.G[sl], self.sec["A"][sl], self.sec["Iy"][sl], self.sec["Iz"][sl],
                                    self.sec["J"][sl], self.sec["Asy"][sl], self.sec["Asz"][sl], self.L[sl])
                Kc.append(_scatter(np.einsum("eji,ejk,ekl->eil", T, Kl, T), dofs))
                if "M" in want:
                    Ml = S.beam_local_M(self.rho[sl], self.sec["A"][sl], self.sec["Iy"][sl], self.sec["Iz"][sl], self.L[sl])
                    Mc.append(_scatter(np.einsum("eji,ejk,ekl->eil", T, Ml, T), dofs))
            elif self.etype == "T3D2":
                k = self.E[sl] * self.sec["area"][sl] / self.L[sl]
                Kc.append(_scatter(S.axial_global(k, self.ex[sl]), dofs))
                if "M" in want:
                    m = self.rho[sl] * self.sec["area"][sl] * self.L[sl] / 2.0
                    Me = np.zeros((len(m), 6, 6))
                    for i in range(6):
                        Me[:, i, i] = m
                    Mc.append(_scatter(Me, dofs))
            elif self.etype == "SPRING2":
                Kc.append(_scatter(S.axial_global(self.sec["k"][sl], self.ex[sl]), dofs))
                if "C" in want:
                    Cc.append(_scatter(S.axial_global(self.sec["c"][sl], self.ex[sl]), dofs))
            else:  # S4
                T = S._blockdiag_T(self.R[sl], 8)
                Kc.append(_scatter(np.einsum("eji,ejk,ekl->eil", T, self.shell_K_local[sl], T), dofs))
                if "M" in want:
                    sub = S.ShellKin(self.kin.Bc[sl], self.kin.Bi[sl], self.kin.Bb[sl], self.kin.Bs[sl],
                                     self.kin.wdet[sl], self.kin.area[sl])
                    Ml = S.shell_local_M(self.xy[sl], sub, self.rho[sl], self.sec["thickness"][sl])
                    Mc.append(_scatter(np.einsum("eji,ejk,ekl->eil", T, Ml, T), dofs))
        out = {"K": _csr(Kc, self.ndof)}
        if "M" in want:
            M = _csr(Mc, self.ndof)
            if self.point_mass.any():
                d = np.zeros(self.ndof)
                for c in range(3):
                    d[c::6] = self.point_mass
                M = M + sparse.diags(d)
            out["M"] = M
        if "C" in want:
            out["C"] = _csr(Cc, self.ndof) if Cc else sparse.csr_matrix((self.ndof, self.ndof))
        return out

    def fixed_all(self) -> Dict[int, float]:
        """User supports plus every inactive DOF (no stiffness) pinned at zero."""
        fixed = dict(self.fixed)
        for d in np.nonzero(~self.active_mask())[0]:
            fixed.setdefault(int(d), 0.0)
        return fixed

    # --------------------------------------------------------------- recovery
    def element_forces_beam(self, u: np.ndarray) -> np.ndarray:
        """Local end forces (ne,12): section forces N, Vy, Vz, T, My, Mz at both ends (end-1 values sign-flipped)."""
        dofs = self._dofs(slice(None))
        T = S._blockdiag_T(self.R, 4)
        ul = np.einsum("eij,ej->ei", T, u[dofs])
        Kl = S.beam_local_K(self.E, self.G, self.sec["A"], self.sec["Iy"], self.sec["Iz"], self.sec["J"],
                            self.sec["Asy"], self.sec["Asz"], self.L)
        f = np.einsum("eij,ej->ei", Kl, ul) - self.beam_eq
        out = f.copy()
        out[:, :6] *= -1.0
        return out

    def recover(self, u: np.ndarray) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
        """Nodal result fields and extra (element-level) results from the displacement vector u (6N)."""
        N = self.N
        ut = u.reshape(N, 6)
        trans, rot = ut[:, :3], ut[:, 3:]
        fields: Dict[str, np.ndarray] = {
            "displacement_magnitude": np.linalg.norm(trans, axis=1),
            "ux": trans[:, 0], "uy": trans[:, 1], "uz": trans[:, 2],
            "rotation_magnitude": np.linalg.norm(rot, axis=1),
            "rx": rot[:, 0], "ry": rot[:, 1], "rz": rot[:, 2],
        }
        extra: Dict[str, Any] = {}
        count = np.zeros(N)

        def acc(name: str, nodes: np.ndarray, vals: np.ndarray, how: str = "max") -> None:
            arr = fields.setdefault(name, np.zeros(N))
            if how == "max":
                np.maximum.at(arr, nodes, vals)
            else:
                np.add.at(arr, nodes, vals)

        if self.etype == "T3D2":
            dofs = self._dofs(slice(None))
            du = u[dofs]
            ext = np.einsum("ei,ei->e", du[:, 3:] - du[:, :3], self.ex)
            force = self.E * self.sec["area"] * ext / self.L
            sigma = force / self.sec["area"]
            for k in range(2):
                acc("axial_force", self.elems[:, k], np.abs(force))
                acc("von_mises", self.elems[:, k], np.abs(sigma))
            extra["element_forces"] = [[int(e), float(f)] for e, f in enumerate(force)]
            extra["element_force_columns"] = ["element", "axial force N"]
        elif self.etype == "SPRING2":
            dofs = self._dofs(slice(None))
            du = u[dofs]
            ext = np.einsum("ei,ei->e", du[:, 3:] - du[:, :3], self.ex)
            force = self.sec["k"] * ext
            for k in range(2):
                acc("axial_force", self.elems[:, k], np.abs(force))
            extra["element_forces"] = [[int(e), float(f)] for e, f in enumerate(force)]
            extra["element_force_columns"] = ["element", "spring force N"]
        elif self.etype == "B31":
            ef = self.element_forces_beam(u)
            A, Iy, Iz, J = self.sec["A"], self.sec["Iy"], self.sec["Iz"], self.sec["J"]
            for end, off in enumerate((0, 6)):
                Nn, Vy, Vz, Tt, My, Mz = (ef[:, off + i] for i in range(6))
                sig = np.abs(Nn) / A + np.abs(My) * self.sec["cz"] / Iy + np.abs(Mz) * self.sec["cy"] / Iz
                tau = np.abs(Tt) * self.sec["rt"] / J
                nd = self.elems[:, end]
                acc("axial_force", nd, np.abs(Nn))
                acc("shear_force", nd, np.hypot(Vy, Vz))
                acc("torsion", nd, np.abs(Tt))
                acc("bending_moment", nd, np.hypot(My, Mz))
                acc("bending_stress", nd, sig)
                acc("von_mises", nd, np.sqrt(sig ** 2 + 3.0 * tau ** 2))
            extra["element_forces"] = [[int(e)] + [float(x) for x in ef[e]] for e in range(self.ne)] if self.ne <= 20000 else []
            extra["element_force_columns"] = ["element", "N1", "Vy1", "Vz1", "T1", "My1", "Mz1", "N2", "Vy2", "Vz2", "T2", "My2", "Mz2"]
        else:  # S4
            dofs = self._dofs(slice(None))
            T = S._blockdiag_T(self.R, 8)
            ul = np.einsum("eij,ej->ei", T, u[dofs])
            n, m, q = S.shell_stress_resultants(self.kin, self.E, self.nu, self.sec["thickness"], ul, self.alpha_map)
            t = self.sec["thickness"][:, None, None]
            sm = n / t
            sb = 6.0 * m / t ** 2
            top, bot = sm + sb, sm - sb
            vm_top = von_mises_plane(top[..., 0], top[..., 1], top[..., 2])
            vm_bot = von_mises_plane(bot[..., 0], bot[..., 1], bot[..., 2])
            per = {
                "von_mises_top": vm_top, "von_mises_bottom": vm_bot, "von_mises": np.maximum(vm_top, vm_bot),
                "shell_nx": n[..., 0], "shell_ny": n[..., 1], "shell_nxy": n[..., 2],
                "shell_mx": m[..., 0], "shell_my": m[..., 1], "shell_mxy": m[..., 2],
                "shell_shear": np.hypot(q[..., 0], q[..., 1]),
            }
            for name, val in per.items():
                cor = S.shell_gauss_to_nodes(val)                                  # (ne,4)
                arr = np.zeros(N)
                np.add.at(arr, self.elems.ravel(), cor.ravel())
                cnt = np.zeros(N)
                np.add.at(cnt, self.elems.ravel(), 1.0)
                nodal = arr / np.maximum(cnt, 1.0)
                # extrapolating non-negative Gauss values can overshoot below zero: clamp the non-negative quantities
                fields[name] = np.maximum(nodal, 0.0) if name.startswith("von_mises") or name == "shell_shear" else nodal
        return fields, extra


# ------------------------------------------------------------------- analyses
def _free_set(model: StructModel, fixed: Dict[int, float]) -> np.ndarray:
    mask = np.ones(model.ndof, dtype=bool)
    mask[list(fixed)] = False
    return np.nonzero(mask)[0]


def check_mechanism(K: sparse.csr_matrix, free: np.ndarray) -> None:
    """Raise if the supported structure can still move as a rigid body (a zero-energy mode of the free stiffness matrix)."""
    n = len(free)
    if n < 3 or n > 60000:
        return
    Kf = K[free][:, free].tocsc()
    d = np.sqrt(np.maximum(Kf.diagonal(), 1e-300))
    Ks = sparse.diags(1.0 / d) @ Kf @ sparse.diags(1.0 / d)
    try:
        lam = spla.eigsh(Ks.tocsc(), k=1, sigma=-1e-8, which="LM", return_eigenvectors=False)[0]
    except (RuntimeError, spla.ArpackNoConvergence):
        return
    if lam < 1e-10:
        raise StructuralError(
            "The structure is not fully supported: it can still move as a rigid body (or has a mechanism). "
            "Add supports that stop every translation and rotation.")


def _static_solve(model: StructModel, K: sparse.csr_matrix, f: np.ndarray, fixed: Dict[int, float]) -> np.ndarray:
    check_mechanism(K, _free_set(model, fixed))
    return solve_partitioned(K, f, fixed, SolverOptions(mode="direct"), None)


def _need_supports(model: StructModel) -> None:
    if not model.fixed:
        raise StructuralError("Add at least one support (boundary condition) before running this analysis.")


def _eig_dense_or_sparse(Kf, Mf, n_modes: int):
    """Smallest eigenvalues of K x = w M x (M may have massless DOFs: they are condensed out exactly)."""
    n = Kf.shape[0]
    dM = np.asarray(Mf.diagonal()).ravel()
    scale = dM.max() if dM.size else 1.0
    massive = np.nonzero(dM > 1e-12 * scale)[0]
    if len(massive) == 0:
        raise StructuralError("The model has no mass. Give the material a density or add a point mass.")
    if len(massive) < n:
        massless = np.nonzero(dM <= 1e-12 * scale)[0]
        Kmm = Kf[massive][:, massive].toarray()
        Kms = Kf[massive][:, massless].toarray()
        Kss = Kf[massless][:, massless].tocsc()
        try:
            X = spla.splu(Kss).solve(Kms.T)
        except RuntimeError as exc:
            raise StructuralError("A massless part of the structure is a mechanism (singular stiffness). Add supports or mass.") from exc
        Kc = Kmm - Kms @ X
        Mm = Mf[massive][:, massive].toarray()
    else:
        Kc, Mm = Kf.toarray(), Mf.toarray()
    nm = Kc.shape[0]
    k = min(n_modes, nm)
    Kc = 0.5 * (Kc + Kc.T)
    Mm = 0.5 * (Mm + Mm.T)
    if nm <= 3000:
        vals, vecs = dla.eigh(Kc, Mm, subset_by_index=[0, k - 1])
    else:
        shift = -1e-6 * (np.abs(Kc.diagonal()).max() / max(Mm.diagonal().max(), 1e-300))
        vals, vecs = spla.eigsh(sparse.csc_matrix(Kc), k=k, M=sparse.csc_matrix(Mm), sigma=shift, which="LM")
        order = np.argsort(vals)
        vals, vecs = vals[order], vecs[:, order]
    return vals, vecs, massive, (Kms, X) if len(massive) < n else None, n


def _modal(model: StructModel, n_modes: int, progress: Progress):
    mats = model.assemble(("K", "M"))
    fixed = model.fixed_all()
    free = _free_set(model, fixed)
    Kf, Mf = mats["K"][free][:, free], mats["M"][free][:, free]
    vals, vecs, massive, cond, n = _eig_dense_or_sparse(Kf, Mf, n_modes)
    shapes = []
    for j in range(vecs.shape[1]):
        full_f = np.zeros(len(free))
        full_f[massive] = vecs[:, j]
        if cond is not None:
            Kms, X = cond
            massless = np.setdiff1d(np.arange(len(free)), massive)
            full_f[massless] = -(X @ vecs[:, j])
        u = np.zeros(model.ndof)
        u[free] = full_f
        t = u.reshape(-1, 6)[:, :3]
        mx = np.abs(t).max()
        shapes.append(t / mx if mx > 0 else t)
    freqs = np.sqrt(np.maximum(vals, 0.0)) / (2.0 * np.pi)
    return freqs, shapes


def _beam_axial_forces(model: StructModel, u: np.ndarray) -> np.ndarray:
    if model.etype == "B31":
        ef = model.element_forces_beam(u)
        return 0.5 * (ef[:, 0] + ef[:, 6])
    dofs = model._dofs(slice(None))
    du = u[dofs]
    ext = np.einsum("ei,ei->e", du[:, 3:] - du[:, :3], model.ex)
    return model.E * model.sec["area"] * ext / model.L


def _buckling(model: StructModel, n_modes: int):
    if model.etype not in ("B31", "T3D2"):
        raise StructuralError("Linear buckling is available for truss and beam models only.")
    _need_supports(model)
    if not model.f_ext.any():
        raise StructuralError("Buckling needs a reference load (the load that is scaled to find the buckling load).")
    mats = model.assemble(("K",))
    K = mats["K"]
    fixed = model.fixed_all()
    u = _static_solve(model, K, model.f_ext, fixed)
    Nax = _beam_axial_forces(model, u)
    # geometric stiffness for the reference load state
    Kg_chunks = []
    for a in range(0, model.ne, CHUNK):
        sl = slice(a, min(a + CHUNK, model.ne))
        dofs = model._dofs(sl)
        if model.etype == "B31":
            T = S._blockdiag_T(model.R[sl], 4)
            ipa = (model.sec["Iy"][sl] + model.sec["Iz"][sl]) / model.sec["A"][sl]
            Kgl = S.beam_local_Kg(Nax[sl], ipa, model.L[sl])
            Kg_chunks.append(_scatter(np.einsum("eji,ejk,ekl->eil", T, Kgl, T), dofs))
        else:
            Kg_chunks.append(_scatter(S.axial_geometric(Nax[sl], model.L[sl], model.ex[sl]), dofs))
    Kg = _csr(Kg_chunks, model.ndof)
    free = _free_set(model, fixed)
    Kf, Kgf = K[free][:, free].toarray(), Kg[free][:, free].toarray()
    mu = dla.eigh(-0.5 * (Kgf + Kgf.T), 0.5 * (Kf + Kf.T), eigvals_only=False)
    vals, vecs = mu
    pos = np.nonzero(vals > 1e-12)[0]
    order = pos[np.argsort(-vals[pos])][:n_modes]                     # largest 1/lambda = smallest positive lambda
    lam = 1.0 / vals[order]
    shapes = []
    for j in order:
        v = np.zeros(model.ndof)
        v[free] = vecs[:, j]
        t = v.reshape(-1, 6)[:, :3]
        mx = np.abs(t).max()
        shapes.append(t / mx if mx > 0 else t)
    return lam, shapes


def _dynamic(model: StructModel, t_end: float, n_steps: int, amp, alpha: float, beta: float, history: List[int],
             progress: Progress):
    mats = model.assemble(("K", "M", "C"))
    K, M, Cd = mats["K"], mats["M"], mats["C"]
    C = alpha * M + beta * K + Cd
    fixed = model.fixed_all()
    if any(v != 0 for v in fixed.values()):
        raise StructuralError("Dynamic analysis assumes zero prescribed displacement.")
    free = _free_set(model, fixed)
    Kf, Mf, Cf = K[free][:, free].tocsc(), M[free][:, free].tocsc(), C[free][:, free].tocsc()
    f = model.f_ext[free]
    dt = t_end / n_steps
    a0, a1, a2 = 4.0 / dt ** 2, 2.0 / dt, 4.0 / dt            # Newmark average acceleration (beta = 1/4, gamma = 1/2)
    Keff = (Kf + a0 * Mf + a1 * Cf).tocsc()
    try:
        lu = spla.splu(Keff)
    except RuntimeError as exc:
        raise StructuralError("The effective stiffness is singular: the model is not fully supported or has massless mechanisms.") from exc
    u = np.zeros(len(free))
    v = np.zeros(len(free))
    acc = np.zeros(len(free))
    try:
        acc = spla.splu(Mf.tocsc()).solve(f * amp(0.0) - Kf @ u - Cf @ v)
    except RuntimeError:
        acc = np.zeros(len(free))
    times = [0.0]
    frames = [np.zeros((model.N, 3))]
    hist = {int(h): [0.0] for h in history}
    stride = max(1, n_steps // 50)
    peak_u, peak_val = np.zeros(model.ndof), 0.0
    for k in range(1, n_steps + 1):
        t = k * dt
        rhs = f * amp(t) + Mf @ (a0 * u + a2 * v + acc) + Cf @ (a1 * u + v)
        un = lu.solve(rhs)
        accn = a0 * (un - u) - a2 * v - acc
        vn = v + (dt / 2.0) * (acc + accn)
        u, v, acc = un, vn, accn
        full = np.zeros(model.ndof)
        full[free] = u
        tr = full.reshape(-1, 6)[:, :3]
        mag = float(np.linalg.norm(tr, axis=1).max())
        if mag > peak_val:
            peak_val, peak_u = mag, full
        for h in hist:
            hist[h].append(float(np.linalg.norm(tr[h])))
        if k % stride == 0 or k == n_steps:
            frames.append(tr.copy())
            times.append(t)
        if progress and k % max(1, n_steps // 20) == 0:
            progress(0.2 + 0.7 * k / n_steps, f"Time step {k}/{n_steps}")
    return times, frames, hist, peak_u


# ------------------------------------------------------------------ entry point
def run(project: Dict[str, Any], progress: Progress = None) -> Dict[str, Any]:
    kind = project.get("analysis_type") or "static"
    if kind not in STRUCT_ANALYSES:
        raise StructuralError(
            f"'{kind}' is not available for structural elements. Use static, frequency, buckling (truss/beam) or dynamic analysis.")
    st = dict(project.get("settings") or {})
    if st.get("nlgeom"):
        raise StructuralError("Large deformation (NLGEOM) is available for solid elements only. Switch it off for structural models.")
    if (project.get("constraints") or []) or (project.get("reference_points") or []):
        raise StructuralError("Ties, contact and rigid bodies are available for solid elements only. Remove the constraints.")
    model = StructModel(project)
    log = [f"Structural model: {model.N} nodes, {model.ne} {model.etype} elements, {len(model.parts)} part(s), "
           f"{int(model.active_mask().sum())} active DOFs, {len(model.fixed)} constrained"]
    log.extend(model.notes)

    if kind == "static":
        _need_supports(model)
        if not (model.f_ext.any() or any(v != 0 for v in model.fixed.values())):
            raise StructuralError("Add at least one load or prescribed displacement before running a static analysis.")
        if progress:
            progress(0.2, "Assembling stiffness matrix")
        K = model.assemble(("K",))["K"]
        if progress:
            progress(0.5, f"Solving {int(model.active_mask().sum()) - len(model.fixed)} equations")
        fixed = model.fixed_all()
        u = _static_solve(model, K, model.f_ext, fixed)
        R = np.zeros(model.ndof)
        res_vec = K @ u - model.f_ext
        for d in model.fixed:
            R[d] = res_vec[d]
        fields, extra = model.recover(u)
        ut = u.reshape(-1, 6)
        Rt = R.reshape(-1, 6)
        energy = 0.5 * float(u @ (K @ u))
        res = {
            "analysis_type": "static", "model_class": "structural", "element_type": model.etype,
            "displacements": _sig_array(ut[:, :3]), "rotations": _sig_array(ut[:, 3:]),
            "reaction_forces": _sig_array(Rt[:, :3]), "reaction_moments": _sig_array(Rt[:, 3:]),
            "fields": {k: _sig(v) for k, v in fields.items()},
            "von_mises_nodal": _sig(fields.get("von_mises", np.zeros(model.N))),
            "max_displacement": float(fields["displacement_magnitude"].max()),
            "max_von_mises": float(fields.get("von_mises", np.zeros(1)).max()),
            "max_rotation": float(fields["rotation_magnitude"].max()),
            "strain_energy": energy,
            "reaction_total": [float(x) for x in Rt[:, :3].sum(axis=0)],
            "reaction_moment_total": [float(x) for x in Rt[:, 3:].sum(axis=0)],
            "reference_points": {},
        }
        res.update(extra)
        log.append("Linear static solution (direct sparse LU)")
        return {"results": res, "log": "\n".join(log)}

    if kind == "modal":
        n_modes = int(st.get("n_modes") or project.get("n_modes") or 6)
        freqs, shapes = _modal(model, n_modes, progress)
        res = {"analysis_type": "modal", "model_class": "structural", "frequencies_hz": _sig(freqs),
               "mode_shapes": [_sig_array(s) for s in shapes], "mode_shape_nodes": None}
        log.append(f"{len(freqs)} natural frequencies" + ("" if model.fixed else " (free-free: the first six are rigid-body modes)"))
        return {"results": res, "log": "\n".join(log)}

    if kind == "buckling":
        lam, shapes = _buckling(model, min(int(st.get("n_modes") or 6), 20))
        res = {"analysis_type": "buckling", "model_class": "structural", "load_factors": _sig(lam),
               "mode_shapes": [_sig_array(s) for s in shapes], "mode_shape_nodes": None}
        log.append(f"{len(lam)} buckling load factor(s) for the applied reference load")
        return {"results": res, "log": "\n".join(log)}

    # dynamic
    _need_supports(model)
    if not model.f_ext.any():
        raise StructuralError("A dynamic analysis needs a load.")
    t_end, n_steps = float(st.get("t_end", 0.1)), int(st.get("n_steps", 200))
    if t_end <= 0 or n_steps < 1 or n_steps > 20000:
        raise StructuralError("Duration must be positive and there must be 1..20000 time steps.")
    times, frames, hist, peak_u = _dynamic(
        model, t_end, n_steps, amplitude(st.get("amplitude")), float(st.get("rayleigh_alpha", 0.0)),
        float(st.get("rayleigh_beta", 0.0)), [int(i) for i in st.get("history_nodes", [])], progress)
    fields, extra = model.recover(peak_u)
    mags = [np.linalg.norm(fr, axis=1) for fr in frames]
    peak = int(np.argmax([m.max() for m in mags]))
    res = {
        "analysis_type": "dynamic", "model_class": "structural", "times": _sig(times), "frame_times": _sig(times),
        "frame_nodes": list(range(model.N)), "frames": [_sig_array(fr, 6) for fr in frames],
        "history": {f"node {k}": _sig(v) for k, v in hist.items()}, "peak_frame": peak,
        "fields": {k: _sig(v) for k, v in fields.items()},
        "displacements": _sig_array(peak_u.reshape(-1, 6)[:, :3]),
        "von_mises_nodal": _sig(fields.get("von_mises", np.zeros(model.N))),
        "max_displacement": float(max(m.max() for m in mags)),
        "max_von_mises": float(fields.get("von_mises", np.zeros(1)).max()),
    }
    log.append(f"Newmark average acceleration, {n_steps} steps of {t_end / n_steps:.4g} s")
    return {"results": res, "log": "\n".join(log)}
