"""
Frictionless node-to-surface penalty contact between two parts.

Every Newton iteration the slave surface nodes are projected onto the master surface in the CURRENT configuration (closest
point on the master face, evaluated with the face's own shape functions). A node is in contact when its signed gap to the
master face is negative (or within a touching tolerance). The normal penalty force

    F = eps * A_s * max(0, -g)

acts on the slave node along the master outward normal and is shared (with opposite sign) over the master face nodes by the
shape-function weights, so the contact forces are in exact equilibrium. The tangent is the normal penalty stiffness
`eps*A_s * b b^T` with `b = [n ; -w_i n]` (small-sliding consistent linearisation; frictionless).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree

from . import assembly as asm
from .constraints import _project_on_face
from .elements import FACE_TYPES, tabulate_face
from .materials import as_map
from .model import FEMesh


class ContactError(ValueError):
    pass


@dataclass
class ContactResult:
    force: np.ndarray                 # (3N,) nodal contact forces (acting on the structure)
    K: sparse.csr_matrix              # (3N,3N) penalty tangent (positive semi-definite)
    active: np.ndarray                # sorted ids of slave nodes in contact
    pressure: np.ndarray              # (N,) contact pressure at slave nodes (Pa), 0 elsewhere
    gap: np.ndarray                   # (N,) signed gap at slave nodes (m), NaN where not evaluated
    max_penetration: float = 0.0
    total_force: float = 0.0          # sum of normal force magnitudes


@dataclass
class ContactPair:
    name: str
    master_faces: np.ndarray          # (nm, nfn) boundary-face node ids of the master surface
    master_sign: np.ndarray           # (nm,) +1/-1 so that cross(X1-X0, X2-X0)*sign is the outward normal
    slave_nodes: np.ndarray           # (ns,)
    slave_area: np.ndarray            # (ns,) tributary area per slave node
    slave_normal: np.ndarray          # (ns,3) outward unit normal of the slave body at each slave node (reference config)
    penalty: float                    # eps, Pa/m
    face_type: str
    search: float                     # candidate search radius around face centroids


def _hrz_node_areas(mesh: FEMesh, face_idx: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Lumped (HRZ) tributary area of every node on a set of boundary faces. Returns (nodes, areas)."""
    b = mesh.boundary()
    ft = FACE_TYPES[b.face_type]
    nodes, N, wj, _, _ = asm._face_geometry(mesh, face_idx)           # noqa: SLF001 - shared surface quadrature
    diag = np.einsum("gn,gn,fg->fn", N, N, wj)                         # \int N_i^2
    area = wj.sum(axis=1, keepdims=True)
    diag = diag / diag.sum(axis=1, keepdims=True) * area               # HRZ scaling keeps the face area
    acc = np.zeros(mesh.n_nodes)
    np.add.at(acc, nodes.ravel(), diag.ravel())
    used = np.unique(nodes)
    return used, acc[used]


def build_pair(mesh: FEMesh, mats, name: str, master_faces_idx: np.ndarray, slave_faces_idx: np.ndarray,
               penalty_factor: float = 10.0, penalty: Optional[float] = None, E_ref: Optional[float] = None) -> ContactPair:
    b = mesh.boundary()
    ft = FACE_TYPES[b.face_type]
    if len(master_faces_idx) == 0 or len(slave_faces_idx) == 0:
        raise ContactError("A contact pair needs a surface on both parts.")
    mf = b.face_nodes[master_faces_idx]
    X = mesh.nodes[mf]
    n = np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0])
    fc = X[:, : ft.n_corner].mean(axis=1)
    ec = mesh.nodes[mesh.elems[b.elem[master_faces_idx]][:, : mesh.et.n_corner]].mean(axis=1)
    sign = np.where(np.einsum("fj,fj->f", n, fc - ec) >= 0, 1.0, -1.0)

    # keep only the slave faces that look at the master surface: nearby and with opposing normals. Lateral faces of the slave
    # part (which would also add tributary area to edge nodes) and far-away faces are not part of the contact surface.
    sf0 = b.face_nodes[slave_faces_idx]
    Xs0 = mesh.nodes[sf0]
    nsl = np.cross(Xs0[:, 1] - Xs0[:, 0], Xs0[:, 2] - Xs0[:, 0])
    scen = Xs0[:, : ft.n_corner].mean(axis=1)
    secen = mesh.nodes[mesh.elems[b.elem[slave_faces_idx]][:, : mesh.et.n_corner]].mean(axis=1)
    nsl = nsl * np.where(np.einsum("fj,fj->f", nsl, scen - secen) >= 0, 1.0, -1.0)[:, None]
    nsl = nsl / np.maximum(np.linalg.norm(nsl, axis=1, keepdims=True), 1e-300)
    nm = n * sign[:, None]
    nm = nm / np.maximum(np.linalg.norm(nm, axis=1, keepdims=True), 1e-300)
    mdiam0 = np.linalg.norm(X[:, : ft.n_corner] - fc[:, None, :], axis=2).max()
    sdiam0 = np.linalg.norm(Xs0[:, : ft.n_corner] - scen[:, None, :], axis=2).max()
    reach = 3.0 * max(mdiam0, sdiam0)
    kq = min(6, len(fc))
    dq, iq = cKDTree(fc).query(scen, k=kq, distance_upper_bound=reach)
    dq, iq = np.atleast_2d(dq.T).T if kq == 1 else dq, np.atleast_2d(iq.T).T if kq == 1 else iq
    facing = np.zeros(len(scen), dtype=bool)
    for j in range(kq):
        ok = np.isfinite(dq[:, j]) & (iq[:, j] < len(fc))
        idx = np.where(ok, iq[:, j], 0)
        facing |= ok & (np.einsum("fj,fj->f", nm[idx], nsl) < -0.3)
    if not facing.any():
        raise ContactError("The two parts are too far apart (or do not face each other) to form a contact pair.")
    slave_faces_idx = np.asarray(slave_faces_idx)[facing]
    s_nodes, s_area = _hrz_node_areas(mesh, slave_faces_idx)
    # outward slave-body normal at every slave node (area weighted): breaks ties between master faces at edges/corners
    sf = b.face_nodes[slave_faces_idx]
    Xs = mesh.nodes[sf]
    ns_ = np.cross(Xs[:, 1] - Xs[:, 0], Xs[:, 2] - Xs[:, 0])
    sc = Xs[:, : ft.n_corner].mean(axis=1)
    se = mesh.nodes[mesh.elems[b.elem[slave_faces_idx]][:, : mesh.et.n_corner]].mean(axis=1)
    ns_ = ns_ * np.where(np.einsum("fj,fj->f", ns_, sc - se) >= 0, 1.0, -1.0)[:, None]
    ns_ = ns_ / np.maximum(np.linalg.norm(ns_, axis=1, keepdims=True), 1e-300)
    acc_n = np.zeros((mesh.n_nodes, 3))
    for k in range(sf.shape[1]):
        np.add.at(acc_n, sf[:, k], ns_)
    s_normal = acc_n[s_nodes]
    s_normal = s_normal / np.maximum(np.linalg.norm(s_normal, axis=1, keepdims=True), 1e-300)
    # characteristic sizes -> penalty stiffness and search radius
    sface_area = float(np.mean(asm._face_geometry(mesh, slave_faces_idx)[2].sum(axis=1)))  # noqa: SLF001
    h = max(np.sqrt(sface_area), 1e-12)
    if penalty is None:
        # reference modulus: the stiffest material in the model's parts touching this contact
        if E_ref is None:
            E_ref = float(as_map(mats, mesh).prop("E").max())
        penalty = penalty_factor * E_ref / h
    mdiam = np.linalg.norm(X[:, : ft.n_corner] - fc[:, None, :], axis=2).max()
    return ContactPair(name, mf, sign, s_nodes, s_area, s_normal, float(penalty), b.face_type, search=3.0 * max(mdiam, h))


class ContactSystem:
    def __init__(self, mesh: FEMesh, pairs: List[ContactPair]):
        self.mesh = mesh
        self.pairs = pairs

    def evaluate(self, u: np.ndarray) -> ContactResult:
        """Contact forces/tangent for nodal displacements u (3N or (N,3))."""
        mesh = self.mesh
        n = mesh.n_nodes
        xd = mesh.nodes + np.asarray(u, dtype=float).reshape(n, 3)
        force = np.zeros(3 * n)
        rows, cols, vals = [], [], []
        pressure = np.zeros(n)
        gap_out = np.full(n, np.nan)
        active_ids: List[int] = []
        max_pen = 0.0
        total = 0.0
        for pair in self.pairs:
            ft = FACE_TYPES[pair.face_type]
            Xf = xd[pair.master_faces]                       # (nm, nfn, 3) deformed master faces
            cent = Xf[:, : ft.n_corner].mean(axis=1)
            tree = cKDTree(cent)
            ps = xd[pair.slave_nodes]
            k = min(12, len(cent))
            dist, cand = tree.query(ps, k=k, distance_upper_bound=pair.search)
            dist = np.atleast_2d(dist.T).T if k == 1 else dist
            cand = np.atleast_2d(cand.T).T if k == 1 else cand
            gtol = 1e-11 * pair.search
            dtol = 1e-6 * pair.search
            for i in range(len(ps)):
                # Only master faces that look at the slave body can be touched: their outward normal opposes the slave
                # body's outward normal at this node. (A node that sank below the master's edge lies on the plane of the
                # neighbouring side face with zero gap; without this rule it would be wrongly released.)
                cands = []
                for c, fi in enumerate(np.atleast_1d(cand[i])):
                    if fi >= len(cent) or not np.isfinite(np.atleast_1d(dist[i])[c]):
                        continue
                    F = Xf[int(fi)]
                    nrm = np.cross(F[1] - F[0], F[2] - F[0]) * pair.master_sign[int(fi)]
                    ln = np.linalg.norm(nrm)
                    if ln == 0:
                        continue
                    nrm = nrm / ln
                    if float(nrm @ pair.slave_normal[i]) > -0.2:
                        continue
                    d, w = _project_on_face(ft, ps[i], F)
                    cands.append((d, int(fi), w, nrm, float((ps[i] - w @ F) @ nrm)))
                if not cands:
                    continue
                dmin = min(c[0] for c in cands)
                near = [c for c in cands if c[0] <= dmin * (1 + 1e-6) + dtol]
                gmax = max(c[4] for c in near)
                _, fi, w, nrm, g = min((c for c in near if c[4] >= gmax - gtol), key=lambda c: float(c[3] @ pair.slave_normal[i]))
                s = int(pair.slave_nodes[i])
                gap_out[s] = g
                if g >= gtol:
                    continue
                active_ids.append(s)
                A = float(pair.slave_area[i])
                eps_a = pair.penalty * A
                pen = max(0.0, -g)
                Fn = eps_a * pen
                if pen > max_pen:
                    max_pen = pen
                total += Fn
                pressure[s] = pair.penalty * pen
                mnodes = pair.master_faces[fi]
                dofs = np.concatenate([[3 * s, 3 * s + 1, 3 * s + 2],
                                       (3 * mnodes[:, None] + np.arange(3)).ravel()])
                b = np.concatenate([nrm, (-w[:, None] * nrm[None, :]).ravel()])
                force[dofs] += Fn * b                       # slave pushed out, master pushed in
                Kloc = eps_a * np.outer(b, b)
                rows.append(np.repeat(dofs, len(dofs)))
                cols.append(np.tile(dofs, len(dofs)))
                vals.append(Kloc.ravel())
        if vals:
            K = sparse.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                                  shape=(3 * n, 3 * n)).tocsr()
        else:
            K = sparse.csr_matrix((3 * n, 3 * n))
        return ContactResult(force, K, np.array(sorted(set(active_ids)), dtype=np.int64), pressure, gap_out, max_pen, total)
