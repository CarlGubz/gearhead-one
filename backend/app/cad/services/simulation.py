"""Finite-element simulations: gmsh (tetrahedral meshing) + scikit-fem (assembly) + SciPy (solvers).

Unit system (consistent "N-mm-tonne-s"):
    length mm | force N | stress / modulus MPa | density tonne/mm^3 | frequency from rad/s directly

Analyses
--------
static   linear elasticity, P1 tetrahedra; fixed / roller supports, face forces, pressures, gravity
thermal  steady-state conduction; fixed temperatures, heat flux, convection
modal    natural frequencies and mode shapes (generalised eigenproblem K x = w^2 M x)

Boundary conditions reference B-rep faces through the same selectors the modeller uses
(``">Z"``, ``"near:x,y,z"``, ``"idx:3"`` ...), so a setup survives re-meshing.
"""
from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from typing import Any, Optional

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from . import modeling3d

G_MM_S2 = 9806.65
_GMSH_LOCK = threading.Lock()  # gmsh keeps global state: one mesh job at a time


# ---------------------------------------------------------------------- meshing
class MeshData:
    def __init__(self, P, T, surfaces, T10=None):
        self.P = P  # (N,3)
        self.T = T  # (M,4) corner nodes
        self.surfaces = surfaces  # list of dict(center, area, tris(K,3) compact node indices[, tris6(K,6)])
        self.T10 = T10  # (M,10) quadratic tets (gmsh node order) when meshed with order=2


def _gmsh_mesh(shape, size: float, order: int = 1) -> MeshData:
    import gmsh

    with _GMSH_LOCK, tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "part.step")
        shape.exportStep(path)
        gmsh.initialize(interruptible=False)  # required when called from a worker thread
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("part")
            gmsh.model.occ.importShapes(path)
            gmsh.model.occ.synchronize()
            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.25)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 10)
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)
            if order == 2:
                gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)  # no slivers: they pollute eigen / buckling results
            gmsh.model.mesh.generate(3)
            if order == 2:
                gmsh.model.mesh.setOrder(2)
            tags, coords, _ = gmsh.model.mesh.getNodes()
            coords = np.asarray(coords).reshape(-1, 3)
            tag_to_idx = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
            tag_to_idx[np.asarray(tags, dtype=np.int64)] = np.arange(len(tags))
            tet_type, tri_type, nt, ns = (11, 9, 10, 6) if order == 2 else (4, 2, 4, 3)
            etypes, _, enodes = gmsh.model.mesh.getElements(3)
            tets = None
            for et, en in zip(etypes, enodes):
                if et == tet_type:
                    tets = tag_to_idx[np.asarray(en, dtype=np.int64)].reshape(-1, nt)
            if tets is None or len(tets) == 0:
                raise RuntimeError("gmsh produced no tetrahedra (is the solid valid / closed?)")
            surfaces = []
            for dim, tag in gmsh.model.getEntities(2):
                com = gmsh.model.occ.getCenterOfMass(2, tag)
                area = gmsh.model.occ.getMass(2, tag)
                ets, _, ens = gmsh.model.mesh.getElements(2, tag)
                tris = np.zeros((0, ns), dtype=np.int64)
                for et, en in zip(ets, ens):
                    if et == tri_type:
                        tris = tag_to_idx[np.asarray(en, dtype=np.int64)].reshape(-1, ns)
                surfaces.append({"center": np.array(com), "area": float(area), "tris": tris})
        finally:
            gmsh.finalize()
    used = np.unique(tets)
    remap = np.full(len(coords), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    P = coords[used]
    T_all = remap[tets]
    for s in surfaces:
        t = remap[s["tris"]]
        t = t[np.all(t >= 0, axis=1)]
        s["tris"] = t[:, :3]
        if order == 2:
            s["tris6"] = t
    if order == 2:
        return MeshData(P, T_all[:, :4], surfaces, T10=T_all)
    return MeshData(P, T_all, surfaces)


def mesh_shape(shape, size: Optional[float], max_elements: int) -> tuple[MeshData, float]:
    diag = shape.BoundingBox().DiagonalLength
    auto = not size
    size = size or diag / 14.0
    for _ in range(5):
        md = _gmsh_mesh(shape, size)
        if len(md.T) <= max_elements:
            return md, size
        if not auto:
            raise ValueError(f"mesh has {len(md.T)} elements (> {max_elements}); increase the mesh size")
        size *= 1.35
    raise ValueError("could not generate a mesh within the element limit; simplify the part")


def _surface_nodes_for(shape, md: MeshData, selector: str) -> tuple[list[dict], np.ndarray]:
    """Mesh surfaces (and union of their nodes) matching the B-rep faces picked by ``selector``."""
    faces = modeling3d.resolve_faces(shape, selector)
    picked: list[dict] = []
    for f in faces:
        c = f.Center()
        cv = np.array([c.x, c.y, c.z])
        a = f.Area()
        best, bd = None, 1e18
        for s in md.surfaces:
            d = np.linalg.norm(s["center"] - cv) + abs(s["area"] - a) / max(a, 1e-9)
            if d < bd:
                best, bd = s, d
        if best is None or bd > 0.05 * max(shape.BoundingBox().DiagonalLength, 1.0):
            raise ValueError(f"could not match face '{selector}' to the mesh")
        if not any(best is p for p in picked):
            picked.append(best)
    if not picked:
        raise ValueError(f"selector '{selector}' matched no face")
    nodes = np.unique(np.concatenate([p["tris"].ravel() for p in picked]))
    return picked, nodes


def _tri_areas_normals(P, tris):
    a, b, c = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    cr = np.cross(b - a, c - a)
    ar = 0.5 * np.linalg.norm(cr, axis=1)
    return ar, cr, (a + b + c) / 3


def _outward_normals(md: MeshData, tris: np.ndarray) -> np.ndarray:
    """Unit outward normals of boundary triangles, oriented with the adjacent tetrahedron."""
    face_map: dict[tuple, int] = {}
    T = md.T
    for ti, t in enumerate(T):
        for tri in ((t[0], t[1], t[2]), (t[0], t[1], t[3]), (t[0], t[2], t[3]), (t[1], t[2], t[3])):
            face_map[tuple(sorted(tri))] = ti
    _, cr, cen = _tri_areas_normals(md.P, tris)
    out = np.zeros_like(cr)
    for k, tri in enumerate(tris):
        n = cr[k] / (np.linalg.norm(cr[k]) + 1e-300)
        ti = face_map.get(tuple(sorted(tri)))
        if ti is not None:
            tc = md.P[T[ti]].mean(axis=0)
            if np.dot(n, cen[k] - tc) < 0:
                n = -n
        out[k] = n
    return out


# ------------------------------------------------------------------ element math
def _tet_geometry(P, T):
    X = P[T]  # (M,4,3)
    A = np.concatenate([np.ones((len(T), 4, 1)), X], axis=2)  # rows [1 x y z]
    inv = np.linalg.inv(A)  # (M,4,4)
    grads = inv[:, 1:, :]  # (M,3,4): d phi_i / d x_k
    vol = np.abs(np.linalg.det(A)) / 6.0
    return grads, vol


def _nodal_average(T, N, vol, values):
    """Volume-weighted element -> node averaging."""
    out = np.zeros((N,) + values.shape[1:])
    w = np.zeros(N)
    for k in range(4):
        np.add.at(w, T[:, k], vol)
        if values.ndim == 1:
            np.add.at(out, T[:, k], vol * values)
        else:
            np.add.at(out, T[:, k], vol[:, None] * values)
    w = np.where(w > 0, w, 1.0)
    return out / (w if values.ndim == 1 else w[:, None])


def _surface_payload(md: MeshData, fields: dict[str, np.ndarray], vec: Optional[np.ndarray] = None) -> dict:
    tris = np.concatenate([s["tris"] for s in md.surfaces if len(s["tris"])], axis=0)
    used = np.unique(tris)
    remap = np.full(len(md.P), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    out: dict[str, Any] = {
        "positions": np.round(md.P[used], 4).ravel().tolist(),
        "indices": remap[tris].ravel().tolist(),
        "fields": {},
    }
    for name, arr in fields.items():
        a = arr[used]
        out["fields"][name] = {"values": np.round(a, 5).tolist(), "min": float(arr.min()), "max": float(arr.max())}
    if vec is not None:
        out["displacements"] = np.round(vec[used], 6).ravel().tolist()
    return out


# ----------------------------------------------------------- P2 helper functions
def _facet_lookup(mesh) -> dict:
    return {tuple(sorted(col)): i for i, col in enumerate(mesh.facets.T.tolist())}


def _facets_of(mesh, lookup: dict, tris: np.ndarray) -> np.ndarray:
    out = []
    for t in tris.tolist():
        i = lookup.get(tuple(sorted(t)))
        if i is not None:
            out.append(i)
    return np.array(sorted(set(out)), dtype=np.int64)


def _dofs_on_facets(basis, facets: np.ndarray, flags) -> np.ndarray:
    """All DOFs (vertex + mid-edge) of the requested components on the given facets."""
    dv = basis.get_dofs(facets=facets)
    out = []
    for a in range(3):
        if not flags[a]:
            continue
        key = f"u^{a + 1}"
        for group in (dv.nodal, dv.facet, dv.edge, dv.interior):
            try:
                if key in group:
                    out.append(np.asarray(group[key]).ravel())
            except Exception:
                pass
    return np.unique(np.concatenate(out)) if out else np.array([], dtype=np.int64)


# ------------------------------------------------------------------------ solvers
def _material_si(name: str) -> dict:
    m = modeling3d.material(name)
    return {
        "E": m["E"] * 1000.0,  # GPa -> MPa
        "nu": m["nu"],
        "rho": m["density"] * 1e-12,  # kg/m3 -> tonne/mm3
        "yield": m["yield_strength"],
        "k": m["k"] * 1e-3,  # W/mK -> W/(mm K)
        "label": m["label"],
    }


def run_static(shape, md: MeshData, mat: dict, cfg: dict) -> dict:
    from skfem import Basis, ElementTetP2, ElementVector, FacetBasis, LinearForm, MeshTet, asm, condense, solve
    from skfem.helpers import dot
    from skfem.models.elasticity import lame_parameters, linear_elasticity

    mesh = MeshTet(np.ascontiguousarray(md.P.T), np.ascontiguousarray(md.T.T))
    elem = ElementVector(ElementTetP2())
    basis = Basis(mesh, elem)
    lam, mu = lame_parameters(mat["E"], mat["nu"])
    K = asm(linear_elasticity(lam, mu), basis)
    f = np.zeros(basis.N)
    lookup = _facet_lookup(mesh)

    fixed_parts = []
    for sup in cfg.get("fixed", []):
        picked, _ = _surface_nodes_for(shape, md, sup["face"])
        facets = _facets_of(mesh, lookup, np.concatenate([p["tris"] for p in picked]))
        fixed_parts.append(_dofs_on_facets(basis, facets, sup.get("dofs", [True, True, True])))
    fixed = np.unique(np.concatenate(fixed_parts)) if fixed_parts else np.array([], dtype=np.int64)
    if len(fixed) == 0:
        raise ValueError("add at least one fixed support")

    total_load = np.zeros(3)
    for ld in cfg.get("forces", []):
        picked, _ = _surface_nodes_for(shape, md, ld["face"])
        tris = np.concatenate([p["tris"] for p in picked])
        area = _tri_areas_normals(md.P, tris)[0].sum()
        t = np.array(ld["vector"], dtype=float) / area  # traction N/mm^2
        facets = _facets_of(mesh, lookup, tris)
        fb = FacetBasis(mesh, elem, facets=facets)

        @LinearForm
        def traction(v, w, t=t):
            return t[0] * v[0] + t[1] * v[1] + t[2] * v[2]

        f += asm(traction, fb)
        total_load += np.array(ld["vector"], dtype=float)
    for pr in cfg.get("pressures", []):
        picked, _ = _surface_nodes_for(shape, md, pr["face"])
        tris = np.concatenate([p["tris"] for p in picked])
        facets = _facets_of(mesh, lookup, tris)
        fb = FacetBasis(mesh, elem, facets=facets)
        pval = float(pr["value"])

        @LinearForm
        def pressure(v, w, p=pval):
            return -p * dot(w.n, v)  # positive pressure acts into the body

        f += asm(pressure, fb)
        total_load += -pval * (fb.normals * fb.dx).sum(axis=(1, 2))  # resultant force vector
    grav = cfg.get("gravity") or {}
    if grav.get("enabled"):
        g = np.array(grav.get("vector", [0, 0, -G_MM_S2]), dtype=float)

        @LinearForm
        def body(v, w, g=g, rho=mat["rho"]):
            return rho * (g[0] * v[0] + g[1] * v[1] + g[2] * v[2])

        f += asm(body, basis)
        vol_total = float(np.sum(basis.dx))
        total_load += mat["rho"] * vol_total * g
    if not np.any(f):
        raise ValueError("no loads applied: add a force, pressure or enable gravity")

    u = solve(*condense(K, f, D=fixed))
    resid = K @ u - f  # at fixed dofs this is the constraint (reaction) force
    reaction = np.zeros(3)
    for c in range(3):
        idx = basis.nodal_dofs[c]
        edge_idx = basis.edge_dofs[c] if basis.edge_dofs is not None and basis.edge_dofs.size else np.array([], dtype=np.int64)
        all_c = np.concatenate([idx, np.asarray(edge_idx).ravel()])
        reaction[c] = resid[all_c[np.isin(all_c, fixed)]].sum()
    U = u[basis.nodal_dofs].T  # vertex displacements (N,3)
    disp = np.linalg.norm(U, axis=1)

    # stress recovery at quadrature points -> element average -> volume weighted nodal average
    gu = basis.interpolate(u).grad  # (3,3,M,nqp): d u_i / d x_k
    eps = 0.5 * (gu + gu.transpose(1, 0, 2, 3))
    tr = eps[0, 0] + eps[1, 1] + eps[2, 2]
    sig = 2 * mu * eps
    for i in range(3):
        sig[i, i] = sig[i, i] + lam * tr
    sxx, syy, szz = sig[0, 0], sig[1, 1], sig[2, 2]
    vm_q = np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sig[0, 1] ** 2 + sig[1, 2] ** 2 + sig[0, 2] ** 2)
    )
    wq = basis.dx  # (M,nqp)
    vol = wq.sum(axis=1)
    vm_e = (vm_q * wq).sum(axis=1) / vol
    vm = _nodal_average(md.T, len(md.P), vol, vm_e)
    vm_max = float(vm.max())
    diag = shape.BoundingBox().DiagonalLength
    dmax = float(disp.max())
    payload = _surface_payload(md, {"von_mises": vm, "displacement": disp}, U)
    payload["suggested_deformation_scale"] = float(0.05 * diag / dmax) if dmax > 0 else 1.0
    return {
        "kind": "static",
        "summary": {
            "max_von_mises_mpa": vm_max,
            "max_displacement_mm": dmax,
            "yield_strength_mpa": mat["yield"],
            "safety_factor": float(mat["yield"] / vm_max) if vm_max > 1e-12 else None,
            "applied_load_n": [float(x) for x in total_load],
            "reaction_force_n": [float(x) for x in reaction],
            "equilibrium_error_n": float(np.linalg.norm(reaction + total_load)),
        },
        "surface": payload,
    }


def run_thermal(shape, md: MeshData, mat: dict, cfg: dict) -> dict:
    from skfem import Basis, ElementTetP1, MeshTet, asm, condense, solve
    from skfem.models.poisson import laplace

    mesh = MeshTet(np.ascontiguousarray(md.P.T), np.ascontiguousarray(md.T.T))
    basis = Basis(mesh, ElementTetP1())
    K = asm(laplace, basis) * mat["k"]
    N = len(md.P)
    f = np.zeros(N)
    diag = np.zeros(N)
    for fl in cfg.get("heat_flux", []):  # W/m^2 -> W/mm^2
        picked, _ = _surface_nodes_for(shape, md, fl["face"])
        tris = np.concatenate([p["tris"] for p in picked])
        ar, _, _ = _tri_areas_normals(md.P, tris)
        q = float(fl["value"]) * 1e-6
        for k in range(3):
            np.add.at(f, tris[:, k], q * ar / 3.0)
    for cv in cfg.get("convection", []):
        picked, _ = _surface_nodes_for(shape, md, cv["face"])
        tris = np.concatenate([p["tris"] for p in picked])
        ar, _, _ = _tri_areas_normals(md.P, tris)
        h = float(cv["h"]) * 1e-6  # W/m^2K -> W/mm^2K
        t_inf = float(cv.get("ambient", 20.0))
        for k in range(3):
            np.add.at(diag, tris[:, k], h * ar / 3.0)
            np.add.at(f, tris[:, k], h * t_inf * ar / 3.0)
    K = K + sp.diags(diag)
    D_nodes: list[int] = []
    D_vals: list[float] = []
    for ft in cfg.get("fixed_temps", []):
        _, nodes = _surface_nodes_for(shape, md, ft["face"])
        D_nodes += nodes.tolist()
        D_vals += [float(ft["temperature"])] * len(nodes)
    if not D_nodes and not np.any(diag):
        raise ValueError("add a fixed temperature or a convection boundary condition")
    if not np.any(f) and not D_nodes:
        raise ValueError("no thermal loads")
    x = np.zeros(N)
    D = np.array(D_nodes, dtype=np.int64)
    if len(D):
        x[D] = np.array(D_vals)
        T = solve(*condense(K, f, x=x, D=D))
    else:
        T = solve(K, f)
    return {
        "kind": "thermal",
        "summary": {"min_temperature_c": float(T.min()), "max_temperature_c": float(T.max())},
        "surface": _surface_payload(md, {"temperature": T}),
    }


def run_modal(shape, md: MeshData, mat: dict, cfg: dict) -> dict:
    from skfem import Basis, BilinearForm, ElementTetP2, ElementVector, MeshTet, asm
    from skfem.helpers import dot
    from skfem.models.elasticity import lame_parameters, linear_elasticity

    mesh = MeshTet(np.ascontiguousarray(md.P.T), np.ascontiguousarray(md.T.T))
    basis = Basis(mesh, ElementVector(ElementTetP2()))
    lam, mu = lame_parameters(mat["E"], mat["nu"])
    K = asm(linear_elasticity(lam, mu), basis)

    @BilinearForm
    def mass(u, v, w):
        return mat["rho"] * dot(u, v)

    M = asm(mass, basis)
    lookup = _facet_lookup(mesh)
    fixed_parts = []
    for sup in cfg.get("fixed", []):
        picked, _ = _surface_nodes_for(shape, md, sup["face"])
        facets = _facets_of(mesh, lookup, np.concatenate([p["tris"] for p in picked]))
        fixed_parts.append(_dofs_on_facets(basis, facets, sup.get("dofs", [True, True, True])))
    fixed = np.unique(np.concatenate(fixed_parts)) if fixed_parts else np.array([], dtype=np.int64)
    free = np.setdiff1d(np.arange(basis.N), fixed)
    n_modes = int(cfg.get("modes", 6))
    Kf, Mf = K[free][:, free].tocsc(), M[free][:, free].tocsc()
    k = min(n_modes + (0 if len(fixed) else 6), Kf.shape[0] - 2)
    vals, vecs = spla.eigsh(Kf, k=k, M=Mf, sigma=-1.0, which="LM")
    order = np.argsort(vals)
    vals, vecs = vals[order], vecs[:, order]
    freqs = np.sqrt(np.maximum(vals, 0.0)) / (2 * math.pi)
    keep = [i for i, fr in enumerate(freqs) if fr > 1.0][:n_modes]
    modes = []
    diag = shape.BoundingBox().DiagonalLength
    for i in keep[: min(len(keep), 6)]:
        full = np.zeros(basis.N)
        full[free] = vecs[:, i]
        U = full[basis.nodal_dofs].T
        mag = np.linalg.norm(U, axis=1)
        scale = 0.08 * diag / (mag.max() or 1.0)
        modes.append({"frequency_hz": float(freqs[i]), "surface": _surface_payload(md, {"displacement": mag / (mag.max() or 1.0)}, U * scale)})
    return {
        "kind": "modal",
        "summary": {"frequencies_hz": [float(freqs[i]) for i in keep], "rigid_body_modes_skipped": int(k - len(keep)) if not len(fixed) else 0},
        "modes": modes,
    }


# --------------------------------------------------------------------------- API
def run_simulation(shape, material_name: str, kind: str, cfg: dict, max_elements: int) -> dict:
    t0 = time.time()
    mat = _material_si(material_name)
    md, size = mesh_shape(shape, float(cfg.get("mesh_size") or 0) or None, max_elements)
    if kind == "static":
        res = run_static(shape, md, mat, cfg)
    elif kind == "thermal":
        res = run_thermal(shape, md, mat, cfg)
    elif kind == "modal":
        res = run_modal(shape, md, mat, cfg)
    else:
        raise ValueError(f"unknown simulation kind '{kind}'")
    res["mesh"] = {"nodes": int(len(md.P)), "elements": int(len(md.T)), "element_size_mm": float(size)}
    res["material"] = {"name": material_name, **{k: mat[k] for k in ("E", "nu", "yield", "label")}}
    res["solve_time_s"] = round(time.time() - t0, 3)
    return res
