"""
Linear multi-point constraints by master/slave elimination.

full DOFs  = 3N node DOFs + 6 per reference point (ux,uy,uz,rx,ry,rz)
u_full = T @ u_reduced      (T is sparse; slave rows hold the constraint weights)

Supported constraints
  rigid : slave nodes follow a reference point as a rigid body  (u = u_ref + theta x r)
  tie   : slave nodes are glued to the nearest master surface triangles (linear interpolation)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree

from .elements import FACE_TYPES
from .model import FEMesh


class ConstraintError(ValueError):
    pass


@dataclass
class RefPoint:
    id: str
    coords: np.ndarray  # (3,)
    name: str = ""


def _closest_point_on_triangle(p, a, b, c):
    """Closest point on triangle abc to p; returns barycentric weights (wa, wb, wc)."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return np.array([1.0, 0.0, 0.0])
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return np.array([0.0, 1.0, 0.0])
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return np.array([1 - v, v, 0.0])
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return np.array([0.0, 0.0, 1.0])
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return np.array([1 - w, 0.0, w])
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return np.array([0.0, 1 - w, w])
    denom = 1.0 / (va + vb + vc)
    v, w = vb * denom, vc * denom
    return np.array([1 - v - w, v, w])


def _project_on_face(ft, p: np.ndarray, X: np.ndarray):
    """
    Closest point of the (flat) master face to p. X holds the face's node coordinates in element order.
    Returns (distance, weights over ALL face nodes) evaluated with the face's shape functions.
    """
    nc = ft.n_corner
    if nc == 3:
        w = _closest_point_on_triangle(p, X[0], X[1], X[2])
        xi = np.array([[w[1], w[2]]])
    else:
        xi = np.zeros((1, 2))
        for _ in range(12):  # Newton on the bilinear corner map
            N4 = 0.25 * np.array([(1 - xi[0, 0]) * (1 - xi[0, 1]), (1 + xi[0, 0]) * (1 - xi[0, 1]),
                                  (1 + xi[0, 0]) * (1 + xi[0, 1]), (1 - xi[0, 0]) * (1 + xi[0, 1])])
            dN = 0.25 * np.array([[-(1 - xi[0, 1]), -(1 - xi[0, 0])], [(1 - xi[0, 1]), -(1 + xi[0, 0])],
                                  [(1 + xi[0, 1]), (1 + xi[0, 0])], [-(1 + xi[0, 1]), (1 - xi[0, 0])]])
            x = N4 @ X[:4]
            J = X[:4].T @ dN  # (3, 2)
            d, *_ = np.linalg.lstsq(J, p - x, rcond=None)
            xi = np.clip(xi + d[None, :], -1.0, 1.0)
            if np.linalg.norm(d) < 1e-12:
                break
    N, _ = ft.shape(xi)
    w = N[0]
    q = w @ X
    return float(np.linalg.norm(p - q)), w


class ConstraintSystem:
    def __init__(self, mesh: FEMesh, ref_points: Sequence[RefPoint] = (), constraints: Sequence[dict] = ()):
        self.mesh = mesh
        self.n_node_dofs = 3 * mesh.n_nodes
        self.refs: Dict[str, RefPoint] = {r.id: r for r in ref_points}
        self.ref_order: List[str] = []
        self.expr: Dict[int, List[Tuple[int, float]]] = {}
        used_refs: set = set()

        for c in constraints:
            kind = c.get("type")
            if kind == "rigid":
                self._rigid(c, used_refs)
            elif kind == "tie":
                self._tie(c)
            else:
                raise ConstraintError(f"Unknown constraint type '{kind}'")

        # only reference points that take part in a constraint get DOFs
        self.ref_order = [rid for rid in self.refs if rid in used_refs]
        self.ref_slot = {rid: k for k, rid in enumerate(self.ref_order)}
        self.n_full = self.n_node_dofs + 6 * len(self.ref_order)
        self._resolve_chains()
        self._build_T()

    # ----------------------------------------------------------- builders
    def ref_dof(self, ref_id: str, local: int) -> int:
        if ref_id not in self.refs:
            raise ConstraintError(f"Unknown reference point '{ref_id}'")
        if ref_id not in self.ref_slot:
            raise ConstraintError(
                f"Reference point '{self.refs[ref_id].name or ref_id}' is not attached to a rigid-body "
                "constraint, so it has no stiffness. Attach it to a surface first.")
        return self.n_node_dofs + 6 * self.ref_slot[ref_id] + local

    def _rigid(self, c: dict, used_refs: set) -> None:
        rid = c.get("ref_id")
        if rid not in self.refs:
            raise ConstraintError("Rigid-body constraint needs an existing reference point")
        used_refs.add(rid)
        for nid in c.get("node_ids", []):
            self._set_rigid(int(nid), rid)

    def _set_rigid(self, nid: int, rid: str) -> None:
        r = self.mesh.nodes[nid] - self.refs[rid].coords
        # store symbolic: dof -> [(('ref', rid, local) | int node dof, weight)]
        ux, uy, uz, rx, ry, rz = (("ref", rid, k) for k in range(6))
        rows = {
            0: [(ux, 1.0), (ry, r[2]), (rz, -r[1])],
            1: [(uy, 1.0), (rz, r[0]), (rx, -r[2])],
            2: [(uz, 1.0), (rx, r[1]), (ry, -r[0])],
        }
        for k, terms in rows.items():
            d = 3 * nid + k
            if d in self.expr:
                raise ConstraintError(f"Node {nid} is constrained twice")
            self.expr[d] = [(t, w) for t, w in terms if w != 0.0]

    def _tie(self, c: dict) -> None:
        """
        Surface tie: every slave node follows the master surface through the master face's own shape functions
        (tri3/tri6/quad4/quad8), so quadratic faces transfer force consistently and a uniform state passes exactly.
        """
        slaves = [int(n) for n in c.get("slave_node_ids", [])]
        tris = np.array(c.get("master_faces") or [], dtype=np.int64).reshape(-1, 3)
        if len(slaves) == 0 or len(tris) == 0:
            raise ConstraintError("A tie needs slave nodes and a master surface")
        nodes = self.mesh.nodes
        bnd = self.mesh.boundary()
        ft = FACE_TYPES[bnd.face_type]
        try:
            fidx = bnd.faces_for_triangles(tris)
        except ValueError as exc:
            raise ConstraintError(str(exc)) from exc
        fnodes = bnd.face_nodes[fidx]
        cent = nodes[fnodes[:, : ft.n_corner]].mean(axis=1)
        tree = cKDTree(cent)
        diag = np.linalg.norm(nodes.max(axis=0) - nodes.min(axis=0))
        tol = float(c.get("tolerance") or 1e-3 * diag)
        strict = bool(c.get("strict", True))  # part-level ties skip nodes that are not on the interface
        far = 0
        tied = 0
        for s_node in slaves:
            p = nodes[s_node]
            _, cand = tree.query(p, k=min(12, len(fnodes)))
            best = (np.inf, None, None)
            for fi in np.atleast_1d(cand):
                dist, w = _project_on_face(ft, p, nodes[fnodes[fi]])
                if dist < best[0]:
                    best = (dist, fi, w)
            dist, fi, w = best
            if dist > tol:
                far += 1
                continue
            if s_node in fnodes[fi]:
                continue  # already part of the master surface
            tied += 1
            for k in range(3):
                d = 3 * s_node + k
                if d in self.expr:
                    raise ConstraintError(f"Node {s_node} is constrained twice")
                self.expr[d] = [(3 * int(m) + k, float(wi)) for m, wi in zip(fnodes[fi], w) if abs(wi) > 1e-12]
        if not strict:
            if tied == 0:
                raise ConstraintError(
                    f"No slave node lies within {tol:.3g} of the master surface. Move the parts together "
                    "or increase the tolerance.")
            return
        if far:
            raise ConstraintError(
                f"{far} tie node(s) are farther than the tolerance ({tol:.3g}) from the master surface. "
                "Move the surfaces together or increase the tolerance.")

    # ------------------------------------------------------------ assembly
    def _full_index(self, term) -> int:
        if isinstance(term, tuple):
            _, rid, k = term
            return self.ref_dof(rid, k)
        return int(term)

    def _resolve_chains(self) -> None:
        resolved: Dict[int, List[Tuple[int, float]]] = {}
        for d, terms in self.expr.items():
            resolved[d] = [(self._full_index(t), w) for t, w in terms]
        for _ in range(20):
            changed = False
            for d, terms in resolved.items():
                out: Dict[int, float] = {}
                for m, w in terms:
                    if m in resolved:
                        changed = True
                        for m2, w2 in resolved[m]:
                            out[m2] = out.get(m2, 0.0) + w * w2
                    else:
                        out[m] = out.get(m, 0.0) + w
                resolved[d] = list(out.items())
            if not changed:
                break
        else:
            raise ConstraintError("Constraint chains are circular")
        self.expr = resolved

    def _build_T(self) -> None:
        n = self.n_full
        slave = np.zeros(n, dtype=bool)
        for d in self.expr:
            slave[d] = True
        kept = np.where(~slave)[0]
        self.red_index = -np.ones(n, dtype=np.int64)
        self.red_index[kept] = np.arange(len(kept))
        self.n_red = len(kept)
        rows = list(kept)
        cols = list(range(len(kept)))
        vals = [1.0] * len(kept)
        for d, terms in self.expr.items():
            for m, w in terms:
                rows.append(d)
                cols.append(int(self.red_index[m]))
                vals.append(w)
        self.T = sparse.coo_matrix((vals, (rows, cols)), shape=(n, self.n_red)).tocsr()
        self.slave = slave

    # ------------------------------------------------- scalar (thermal) view
    def scalar_T(self):
        """
        Temperature-DOF version of the tie constraints: T_slave = sum(w_i * T_master_i).
        Returns (Ts[N x n_red], red_index[N], kept_nodes) or None when there is nothing to tie.
        Rigid-body constraints do not couple temperatures and are ignored.
        """
        n = self.mesh.n_nodes
        slaves = {}
        for d, terms in self.expr.items():
            if d < self.n_node_dofs and d % 3 == 0 and all(m < self.n_node_dofs and m % 3 == 0 for m, _ in terms):
                slaves[d // 3] = [(m // 3, w) for m, w in terms]
        if not slaves:
            return None
        is_slave = np.zeros(n, dtype=bool)
        is_slave[list(slaves)] = True
        kept = np.where(~is_slave)[0]
        red_index = -np.ones(n, dtype=np.int64)
        red_index[kept] = np.arange(len(kept))
        rows, cols, vals = list(kept), list(range(len(kept))), [1.0] * len(kept)
        for s_node, terms in slaves.items():
            for m, w in terms:
                rows.append(s_node)
                cols.append(int(red_index[m]))
                vals.append(w)
        Ts = sparse.coo_matrix((vals, (rows, cols)), shape=(n, len(kept))).tocsr()
        return Ts, red_index, kept

    # ------------------------------------------------------------- helpers
    @property
    def trivial(self) -> bool:
        return not self.expr and not self.ref_order

    def expand(self, u_red: np.ndarray) -> np.ndarray:
        return self.T @ u_red

    def pad(self, mat: sparse.spmatrix) -> sparse.csr_matrix:
        """Zero-pad a 3N x 3N matrix to the full DOF count."""
        n = self.n_full
        if mat.shape[0] == n:
            return mat.tocsr()
        m = mat.tocoo()
        return sparse.coo_matrix((m.data, (m.row, m.col)), shape=(n, n)).tocsr()

    def reduce_matrix(self, mat: sparse.spmatrix) -> sparse.csr_matrix:
        if self.trivial:
            return mat.tocsr()
        return (self.T.T @ self.pad(mat) @ self.T).tocsr()

    def reduce_vector(self, vec: np.ndarray) -> np.ndarray:
        if len(vec) < self.n_full:
            vec = np.concatenate([vec, np.zeros(self.n_full - len(vec))])
        return self.T.T @ vec

    def reduced_fixed(self, fixed_full: Dict[int, float]) -> Dict[int, float]:
        """
        Map prescribed full-space DOFs to reduced indices. A support on a slaved DOF is accepted when it is
        redundant - every master DOF it depends on is prescribed with a consistent value (typical for symmetry
        planes running across tied parts); otherwise it is rejected with an explanation.
        """
        out = {}
        for d, v in fixed_full.items():
            if self.slave[d]:
                terms = self.expr.get(d, [])
                if terms and all(m in fixed_full for m, _ in terms):
                    implied = sum(w * fixed_full[m] for m, w in terms)
                    if abs(implied - v) <= 1e-9 * max(1.0, abs(v)):
                        continue  # already guaranteed by the masters
                raise ConstraintError(
                    "A boundary condition is applied to a node that is slaved by a rigid body or tie "
                    "constraint. Apply it to the reference point / master surface instead (a support that "
                    "is also applied to all of its master nodes is fine).")
            out[int(self.red_index[d])] = v
        return out
