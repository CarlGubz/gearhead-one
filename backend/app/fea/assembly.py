"""
Vectorised assembly of global matrices and load vectors.

DOF layout: node n has displacement DOFs 3n..3n+2 (ux,uy,uz) and one
temperature DOF n in thermal problems. Reference-point DOFs are appended
later by constraints.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import numpy as np
from scipy import sparse

from .elements import FACE_TYPES, tabulate_face
from .materials import M_VEC, Material, MaterialMap, as_map, j2_update
from .model import FEMesh


# ------------------------------------------------------------------ helpers
def dof_map(elems: np.ndarray) -> np.ndarray:
    """(ne, nn) node connectivity -> (ne, 3*nn) displacement DOF numbers."""
    return (elems[:, :, None] * 3 + np.arange(3)[None, None, :]).reshape(len(elems), -1)


def build_B(dNdx: np.ndarray) -> np.ndarray:
    """Strain-displacement matrices (ne,ng,6,3nn) from shape-function gradients (ne,ng,nn,3)."""
    ne, ng, nn, _ = dNdx.shape
    B = np.zeros((ne, ng, 6, 3 * nn))
    dx, dy, dz = dNdx[..., 0], dNdx[..., 1], dNdx[..., 2]
    B[:, :, 0, 0::3] = dx
    B[:, :, 1, 1::3] = dy
    B[:, :, 2, 2::3] = dz
    B[:, :, 3, 0::3] = dy
    B[:, :, 3, 1::3] = dx
    B[:, :, 4, 1::3] = dz
    B[:, :, 4, 2::3] = dy
    B[:, :, 5, 0::3] = dz
    B[:, :, 5, 2::3] = dx
    return B


def _chunk_size(mesh: FEMesh) -> int:
    ng = len(mesh.et.rule[1])
    return int(np.clip(4e6 / (ng * 18 * mesh.et.n_nodes), 100, 20000))


def _csr(rows, cols, vals, shape) -> sparse.csr_matrix:
    return sparse.coo_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=shape).tocsr()


def _scatter(Ke: np.ndarray, dofs: np.ndarray):
    r = np.broadcast_to(dofs[:, :, None], Ke.shape).ravel()
    c = np.broadcast_to(dofs[:, None, :], Ke.shape).ravel()
    return r, c, Ke.ravel()


@dataclass
class PlasticState:
    eps_p: np.ndarray  # (ne, ng, 6)
    alpha: np.ndarray  # (ne, ng)

    @classmethod
    def zeros(cls, mesh: FEMesh) -> "PlasticState":
        ng = len(mesh.et.rule[1])
        return cls(np.zeros((mesh.n_elems, ng, 6)), np.zeros((mesh.n_elems, ng)))

    def copy(self) -> "PlasticState":
        return PlasticState(self.eps_p.copy(), self.alpha.copy())


@dataclass
class MechOut:
    K: Optional[sparse.csr_matrix]
    fint: np.ndarray
    stress: np.ndarray        # (ne, ng, 6) at Gauss points
    strain: np.ndarray        # (ne, ng, 6) total strain
    state: Optional[PlasticState]
    energy: float = 0.0


# --------------------------------------------------------------- mechanics
def mech_assemble(mesh: FEMesh, mat, u: np.ndarray, state: Optional[PlasticState] = None,
                  T: Optional[np.ndarray] = None, Tref: float = 0.0, want_K: bool = True) -> MechOut:
    """
    Internal force and (tangent) stiffness for displacement field `u` (3N).
    `mat` is a Material or a MaterialMap (per-element materials). Handles thermal strain
    (alpha * (T - Tref)) and J2 plasticity (for plastic materials when `state` is given).
    """
    mats = as_map(mat, mesh)
    n3 = 3 * mesh.n_nodes
    ng = len(mesh.et.rule[1])
    fint = np.zeros(n3)
    stress = np.zeros((mesh.n_elems, ng, 6))
    strain = np.zeros((mesh.n_elems, ng, 6))
    new_state = state.copy() if state is not None else None
    rows, cols, vals = [], [], []
    energy = 0.0
    D_cache = {}

    for gmat, sl in mats.chunks(mesh, _chunk_size(mesh)):
        D0 = D_cache.setdefault(id(gmat), gmat.D())
        plastic = gmat.plastic and state is not None
        N, dNdx, wdet, _, _ = mesh.jacobian(sl)
        B = build_B(dNdx)
        dofs = dof_map(mesh.elems[sl])
        eps = np.einsum("egin,en->egi", B, u[dofs])
        eps_m = eps
        if T is not None and gmat.alpha != 0.0:
            dT = np.einsum("gn,en->eg", N, T[mesh.elems[sl]]) - Tref
            eps_m = eps - (gmat.alpha * dT)[..., None] * M_VEC
        if plastic:
            sig, ep_new, al_new, Dt = j2_update(eps_m, state.eps_p[sl], state.alpha[sl], gmat)
            new_state.eps_p[sl], new_state.alpha[sl] = ep_new, al_new
        else:
            sig = np.einsum("ij,egj->egi", D0, eps_m)
            Dt = D0[None, None]
        stress[sl], strain[sl] = sig, eps
        fe = np.einsum("eg,egia,egi->ea", wdet, B, sig)
        np.add.at(fint, dofs.ravel(), fe.ravel())
        energy += 0.5 * float(np.einsum("eg,egi,egi->", wdet, sig, eps_m))
        if want_K:
            DB = np.einsum("egij,egjb->egib", np.broadcast_to(Dt, (len(dofs), ng, 6, 6)), B)
            Ke = np.einsum("eg,egia,egib->eab", wdet, B, DB)
            r, c, v = _scatter(Ke, dofs)
            rows.append(r)
            cols.append(c)
            vals.append(v)
    K = _csr(rows, cols, vals, (n3, n3)) if want_K else None
    return MechOut(K, fint, stress, strain, new_state, energy)


def assemble_geometric(mesh: FEMesh, stress_gp: np.ndarray) -> sparse.csr_matrix:
    """Initial-stress (geometric) stiffness K_sigma from Gauss-point stresses (ne, ng, 6)."""
    n3 = 3 * mesh.n_nodes
    rows, cols, vals = [], [], []
    for sl in mesh.chunks(_chunk_size(mesh)):
        _, dNdx, wdet, _, _ = mesh.jacobian(sl)
        s = stress_gp[sl]
        S = np.zeros(s.shape[:2] + (3, 3))
        S[..., 0, 0], S[..., 1, 1], S[..., 2, 2] = s[..., 0], s[..., 1], s[..., 2]
        S[..., 0, 1] = S[..., 1, 0] = s[..., 3]
        S[..., 1, 2] = S[..., 2, 1] = s[..., 4]
        S[..., 0, 2] = S[..., 2, 0] = s[..., 5]
        # scalar block  g_ab = dN_a^T S dN_b  ->  K[3a+i, 3b+i] = g_ab
        g = np.einsum("egaj,egjk,egbk,eg->eab", dNdx, S, dNdx, wdet)
        ne, nn = g.shape[0], g.shape[1]
        Ke = np.zeros((ne, 3 * nn, 3 * nn))
        for i in range(3):
            Ke[:, i::3, i::3] = g
        r, c, v = _scatter(Ke, dof_map(mesh.elems[sl]))
        rows.append(r)
        cols.append(c)
        vals.append(v)
    return _csr(rows, cols, vals, (n3, n3))


# ------------------------------------------------------------ mass matrices
def _hrz_weights(mesh: FEMesh, vol: np.ndarray) -> np.ndarray:
    """Lumped nodal masses (per unit density) for C3D10 via the HRZ scheme: corners V/36, mids 4V/27."""
    w = np.empty((mesh.n_elems, 10))
    w[:, :4] = vol[:, None] / 36.0
    w[:, 4:] = vol[:, None] * 4.0 / 27.0
    return w


def _per_element(value, mesh: FEMesh) -> np.ndarray:
    """Scalar or per-element array -> array of shape (n_elems,)."""
    return np.broadcast_to(np.asarray(value, dtype=float), (mesh.n_elems,))


def assemble_mass(mesh: FEMesh, rho, lumped: Optional[bool] = None) -> sparse.csr_matrix:
    """
    Mass matrix (3N x 3N). Consistent for C3D4/C3D8/C3D20; HRZ-lumped for C3D10
    (the 4-point rule cannot integrate N_i*N_j exactly). `lumped=True` forces diagonal lumping
    (row-sum for C3D4/C3D8, HRZ for C3D10/C3D20).
    """
    n3 = 3 * mesh.n_nodes
    et = mesh.et
    rho_e = _per_element(rho, mesh)
    use_lumped = (et.name == "C3D10") if lumped is None else lumped
    if use_lumped:
        diag = np.zeros(mesh.n_nodes)
        vol = mesh.volumes()
        if et.name == "C3D10":
            np.add.at(diag, mesh.elems.ravel(), (rho_e[:, None] * _hrz_weights(mesh, vol)).ravel())
        else:
            # HRZ: scale the diagonal of the consistent matrix to conserve total mass
            for sl in mesh.chunks(_chunk_size(mesh)):
                N, _, wdet, _, _ = mesh.jacobian(sl)
                d = np.einsum("gn,gn,eg->en", N, N, wdet)
                d = d / d.sum(axis=1, keepdims=True) * wdet.sum(axis=1, keepdims=True)
                np.add.at(diag, mesh.elems[sl].ravel(), (rho_e[sl][:, None] * d).ravel())
        return sparse.diags(np.repeat(diag, 3)).tocsr()
    rows, cols, vals = [], [], []
    for sl in mesh.chunks(_chunk_size(mesh)):
        N, _, wdet, _, _ = mesh.jacobian(sl)
        m = rho_e[sl][:, None, None] * np.einsum("gn,gm,eg->enm", N, N, wdet)
        ne, nn = m.shape[0], m.shape[1]
        Me = np.zeros((ne, 3 * nn, 3 * nn))
        for i in range(3):
            Me[:, i::3, i::3] = m
        r, c, v = _scatter(Me, dof_map(mesh.elems[sl]))
        rows.append(r)
        cols.append(c)
        vals.append(v)
    return _csr(rows, cols, vals, (n3, n3))


# ------------------------------------------------------------ thermal (scalar)
def assemble_conductivity(mesh: FEMesh, k) -> sparse.csr_matrix:
    n = mesh.n_nodes
    k_e = _per_element(k, mesh)
    rows, cols, vals = [], [], []
    for sl in mesh.chunks(_chunk_size(mesh)):
        _, dNdx, wdet, _, _ = mesh.jacobian(sl)
        Ke = k_e[sl][:, None, None] * np.einsum("egaj,egbj,eg->eab", dNdx, dNdx, wdet)
        r, c, v = _scatter(Ke, mesh.elems[sl])
        rows.append(r)
        cols.append(c)
        vals.append(v)
    return _csr(rows, cols, vals, (n, n))


def assemble_capacity(mesh: FEMesh, rho_c, lumped: bool = False) -> sparse.csr_matrix:
    """Heat capacity matrix rho*c*N^T N (consistent) or its lumped diagonal. `rho_c` may be per element."""
    n = mesh.n_nodes
    rc = _per_element(rho_c, mesh)
    if lumped or mesh.et.name == "C3D10":
        diag = np.zeros(n)
        vol = mesh.volumes()
        if mesh.et.name == "C3D10":
            np.add.at(diag, mesh.elems.ravel(), (rc[:, None] * _hrz_weights(mesh, vol)).ravel())
        else:
            for sl in mesh.chunks(_chunk_size(mesh)):
                N, _, wdet, _, _ = mesh.jacobian(sl)
                d = np.einsum("gn,gn,eg->en", N, N, wdet)
                d = d / d.sum(axis=1, keepdims=True) * wdet.sum(axis=1, keepdims=True)
                np.add.at(diag, mesh.elems[sl].ravel(), (rc[sl][:, None] * d).ravel())
        return sparse.diags(diag).tocsr()
    rows, cols, vals = [], [], []
    for sl in mesh.chunks(_chunk_size(mesh)):
        N, _, wdet, _, _ = mesh.jacobian(sl)
        Ce = rc[sl][:, None, None] * np.einsum("gn,gm,eg->enm", N, N, wdet)
        r, c, v = _scatter(Ce, mesh.elems[sl])
        rows.append(r)
        cols.append(c)
        vals.append(v)
    return _csr(rows, cols, vals, (n, n))


# ------------------------------------------------------------- body loads
def body_force_vector(mesh: FEMesh, fn: Callable[[np.ndarray], np.ndarray], scale=None) -> np.ndarray:
    """
    Consistent nodal forces for a body-force field fn(xg[ne,ng,3]) -> [ne,ng,3].
    With `scale` (scalar or per-element array, e.g. density) the field is per unit scale: force = scale * fn.
    """
    f = np.zeros(3 * mesh.n_nodes)
    scale_e = None if scale is None else _per_element(scale, mesh)
    for sl in mesh.chunks(_chunk_size(mesh)):
        N, _, wdet, _, xg = mesh.jacobian(sl)
        b = fn(xg)
        if scale_e is not None:
            b = b * scale_e[sl][:, None, None]
        fe = np.einsum("gn,egi,eg->eni", N, b, wdet).reshape(len(xg), -1)
        np.add.at(f, dof_map(mesh.elems[sl]).ravel(), fe.ravel())
    return f


def body_scalar_vector(mesh: FEMesh, q: float) -> np.ndarray:
    """Nodal heat for a uniform volumetric source q (W/m^3)."""
    f = np.zeros(mesh.n_nodes)
    for sl in mesh.chunks(_chunk_size(mesh)):
        N, _, wdet, _, _ = mesh.jacobian(sl)
        fe = q * np.einsum("gn,eg->en", N, wdet)
        np.add.at(f, mesh.elems[sl].ravel(), fe.ravel())
    return f


# ------------------------------------------------------------- face loads
def _face_geometry(mesh: FEMesh, face_idx: np.ndarray):
    """N, weights*|J_s|, gauss-point positions and outward unit normals for boundary faces."""
    b = mesh.boundary()
    ft = FACE_TYPES[b.face_type]
    pts, wts, N, dN = tabulate_face(ft)
    fn = b.face_nodes[face_idx]
    X = mesh.nodes[fn]  # (nf, nfn, 3)
    t1 = np.einsum("gn,fnj->fgj", dN[:, :, 0], X)
    t2 = np.einsum("gn,fnj->fgj", dN[:, :, 1], X)
    nrm = np.cross(t1, t2)
    jac = np.linalg.norm(nrm, axis=2)
    unit = nrm / np.maximum(jac, 1e-300)[..., None]
    # orient outward using the owning element
    ec = mesh.nodes[mesh.elems[b.elem[face_idx]][:, : mesh.et.n_corner]].mean(axis=1)
    fc = X[:, : ft.n_corner].mean(axis=1)
    sign = np.sign(np.einsum("fj,fj->f", unit[:, 0], fc - ec))
    sign[sign == 0] = 1.0
    unit = unit * sign[:, None, None]
    xg = np.einsum("gn,fnj->fgj", N, X)
    return fn, N, wts[None, :] * jac, xg, unit


def face_vector(mesh: FEMesh, face_idx: np.ndarray, fn: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> np.ndarray:
    """Consistent nodal forces for a traction fn(xg, outward_normal) -> [nf,ng,3] (Pa) over boundary faces."""
    f = np.zeros(3 * mesh.n_nodes)
    if len(face_idx) == 0:
        return f
    nodes, N, wj, xg, unit = _face_geometry(mesh, face_idx)
    t = fn(xg, unit)
    fe = np.einsum("gn,fgi,fg->fni", N, t, wj).reshape(len(nodes), -1)
    dofs = (nodes[:, :, None] * 3 + np.arange(3)).reshape(len(nodes), -1)
    np.add.at(f, dofs.ravel(), fe.ravel())
    return f


def pressure_vector(mesh: FEMesh, face_idx: np.ndarray, p: float) -> np.ndarray:
    """Pressure p (Pa), positive = pushing INTO the surface (compressive), like CalculiX/PrePoMax."""
    return face_vector(mesh, face_idx, lambda xg, n: -p * n)


def face_scalar_vector(mesh: FEMesh, face_idx: np.ndarray, value: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """Nodal heat for a surface flux value(xg)->[nf,ng] (W/m^2) over boundary faces."""
    f = np.zeros(mesh.n_nodes)
    if len(face_idx) == 0:
        return f
    nodes, N, wj, xg, _ = _face_geometry(mesh, face_idx)
    q = value(xg)
    fe = np.einsum("gn,fg,fg->fn", N, q, wj)
    np.add.at(f, nodes.ravel(), fe.ravel())
    return f


def face_scalar_matrix(mesh: FEMesh, face_idx: np.ndarray, coef: float) -> sparse.csr_matrix:
    """Surface matrix  coef * N^T N  (film convection h)."""
    n = mesh.n_nodes
    if len(face_idx) == 0:
        return sparse.csr_matrix((n, n))
    nodes, N, wj, _, _ = _face_geometry(mesh, face_idx)
    Ke = coef * np.einsum("gn,gm,fg->fnm", N, N, wj)
    r, c, v = _scatter(Ke, nodes)
    return _csr([r], [c], [v], (n, n))


def face_scalar_nonlinear(mesh: FEMesh, face_idx: np.ndarray, T: np.ndarray,
                          law: Callable[[np.ndarray], Tuple[np.ndarray, np.ndarray]]):
    """
    Nonlinear surface term (radiation). `law(T_gp) -> (flux, dflux/dT)` in W/m^2.
    Returns (nodal outflow vector, tangent matrix).
    """
    n = mesh.n_nodes
    if len(face_idx) == 0:
        return np.zeros(n), sparse.csr_matrix((n, n))
    nodes, N, wj, _, _ = _face_geometry(mesh, face_idx)
    Tg = np.einsum("gn,fn->fg", N, T[nodes])
    q, dq = law(Tg)
    fe = np.einsum("gn,fg,fg->fn", N, q, wj)
    Ke = np.einsum("gn,gm,fg,fg->fnm", N, N, dq, wj)
    f = np.zeros(n)
    np.add.at(f, nodes.ravel(), fe.ravel())
    r, c, v = _scatter(Ke, nodes)
    return f, _csr([r], [c], [v], (n, n))
