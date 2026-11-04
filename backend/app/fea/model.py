"""FE mesh container: connectivity, geometry, boundary faces and quality."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from .elements import ELEMENT_TYPES, FACE_TYPES, ElementType, element_type, tabulate

CHUNK = 4000  # elements per batch in vectorised loops (bounds memory)

_TET10_FLIP = np.array([0, 2, 1, 3, 6, 5, 4, 7, 9, 8])


def _reflect_hex(nat: np.ndarray) -> np.ndarray:
    """Node permutation that mirrors a brick through its mid-plane (zeta -> -zeta): turns an inside-out brick right way round."""
    key = {tuple(np.round(p, 6)): i for i, p in enumerate(nat)}
    return np.array([key[(p[0], p[1], -p[2])] for p in np.round(nat, 6)])


@dataclass
class Boundary:
    face_nodes: np.ndarray      # (nf, n_face_nodes) full node lists
    elem: np.ndarray            # (nf,) owning element
    tris: np.ndarray            # (nt, 3) outward-wound render triangles (corner nodes)
    tri_face: np.ndarray        # (nt,) index into face_nodes for each triangle
    face_type: str
    tri_mid: Optional[np.ndarray] = None   # (nt, 3) mid-side node of triangle edges (c0-c1, c1-c2, c2-c0); -1 = none (linear mesh, quad diagonal)
    _lookup: Optional[Dict[Tuple[int, int, int], int]] = field(default=None, repr=False)

    def faces_for_triangles(self, tris) -> np.ndarray:
        """Map render triangles (as picked in the UI) to boundary-face indices (deduplicated)."""
        if self._lookup is None:
            self._lookup = {tuple(sorted(int(x) for x in t)): int(f) for t, f in zip(self.tris, self.tri_face)}
        out = []
        for t in tris:
            key = tuple(sorted(int(x) for x in t))
            if key not in self._lookup:
                raise ValueError(f"Selected face {list(key)} is not on the model surface")
            out.append(self._lookup[key])
        return np.unique(np.array(out, dtype=np.int64))


class FEMesh:
    def __init__(self, nodes, elems, etype: str = "C3D4"):
        self.nodes = np.ascontiguousarray(nodes, dtype=np.float64)
        self.elems = np.ascontiguousarray(elems, dtype=np.int64)
        self.et: ElementType = element_type(etype)
        if self.elems.ndim != 2 or self.elems.shape[1] != self.et.n_nodes:
            raise ValueError(f"{self.et.name} elements need {self.et.n_nodes} nodes each, got shape {self.elems.shape}")
        if self.elems.size and (self.elems.min() < 0 or self.elems.max() >= len(self.nodes)):
            raise ValueError("Element connectivity references a node that does not exist")
        self._boundary: Optional[Boundary] = None

    # ----------------------------------------------------------- basics
    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_elems(self) -> int:
        return len(self.elems)

    def chunks(self, size: int = CHUNK, idx=None):
        """Element batches: slices over all elements, or index arrays when `idx` selects a subset."""
        if idx is None:
            for a in range(0, self.n_elems, size):
                yield slice(a, min(a + size, self.n_elems))
        else:
            for a in range(0, len(idx), size):
                yield idx[a:a + size]

    def coords(self, sl: slice = slice(None)) -> np.ndarray:
        return self.nodes[self.elems[sl]]  # (ne, nn, 3)

    # --------------------------------------------------------- geometry
    def jacobian(self, sl: slice, rule=None):
        """
        Per-chunk geometry at the Gauss points.
        Returns (N[ng,nn], dNdx[ne,ng,nn,3], wdet[ne,ng], detJ[ne,ng], xg[ne,ng,3]).
        """
        et = self.et
        pts, wts, N, dN = tabulate(et, rule)
        X = self.coords(sl)
        J = np.einsum("gnk,enj->egjk", dN, X)  # J[j,k] = dx_j/dxi_k
        detJ = np.linalg.det(J)
        invJ = np.linalg.inv(J)  # dxi/dx: invJ[k,j]
        dNdx = np.einsum("gnk,egkj->egnj", dN, invJ)
        xg = np.einsum("gn,enj->egj", N, X)
        return N, dNdx, wts[None, :] * detJ, detJ, xg

    def volumes(self) -> np.ndarray:
        out = np.zeros(self.n_elems)
        for sl in self.chunks():
            _, _, wdet, _, _ = self.jacobian(sl)
            out[sl] = wdet.sum(axis=1)
        return out

    def ensure_positive_orientation(self) -> int:
        """
        Flip inside-out elements in place and return how many were fixed. Tetrahedra swap two corners; a brick is
        mirrored through its mid-plane. A brick whose Jacobian changes sign inside is genuinely distorted: error.
        """
        fixed = 0
        if self.et.kind == "tet":
            perm = _TET10_FLIP if self.et.n_nodes == 10 else np.array([0, 2, 1, 3])
        else:
            perm = _reflect_hex(self.et.natural_nodes)
        for sl in self.chunks():
            _, _, _, detJ, _ = self.jacobian(sl)
            bad = np.where(detJ.min(axis=1) <= 0)[0] + (sl.start or 0)
            if len(bad) == 0:
                continue
            if self.et.kind != "tet":
                mixed = (detJ.max(axis=1) > 0)[bad - (sl.start or 0)]
                if mixed.any():
                    raise ValueError("Mesh contains inverted or degenerate hexahedra")
            self.elems[bad] = self.elems[bad][:, perm]
            fixed += len(bad)
        if fixed:
            for sl in self.chunks():
                _, _, _, detJ, _ = self.jacobian(sl)
                if (detJ <= 0).any():
                    raise ValueError("Mesh contains degenerate (zero-volume) elements")
            self._boundary = None
        return fixed

    def quality(self) -> Dict[str, float]:
        vol = self.volumes()
        ratio = np.ones(self.n_elems)
        edge_ratio = np.ones(self.n_elems)
        for sl in self.chunks():
            _, _, _, detJ, _ = self.jacobian(sl)
            ratio[sl] = detJ.min(axis=1) / np.maximum(detJ.max(axis=1), 1e-300)
            X = self.coords(sl)[:, : self.et.n_corner, :]
            d = np.linalg.norm(X[:, :, None, :] - X[:, None, :, :], axis=3)
            d = np.where(d > 0, d, np.nan)
            edge_ratio[sl] = np.nanmax(d, axis=(1, 2)) / np.nanmin(d, axis=(1, 2))
        return {
            "n_nodes": int(self.n_nodes),
            "n_elements": int(self.n_elems),
            "element_type": self.et.name,
            "total_volume": float(vol.sum()),
            "min_element_volume": float(vol.min()) if len(vol) else 0.0,
            "max_element_volume": float(vol.max()) if len(vol) else 0.0,
            "min_jacobian_ratio": float(ratio.min()) if len(ratio) else 1.0,
            "max_edge_ratio": float(edge_ratio.max()) if len(edge_ratio) else 1.0,
            "inverted_elements": int((ratio <= 0).sum()),
        }

    # --------------------------------------------------------- boundary
    def boundary(self) -> Boundary:
        if self._boundary is not None:
            return self._boundary
        et = self.et
        ft = FACE_TYPES[et.face_type]
        local = np.array(et.faces)  # (nfl, nfn)
        nfl = len(local)
        corner_local = local[:, : ft.n_corner]
        keys = np.sort(self.elems[:, corner_local].reshape(-1, ft.n_corner), axis=1)
        _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        mask = counts[inv.reshape(-1)] == 1
        face_nodes = self.elems[:, local].reshape(-1, local.shape[1])[mask]
        elem = np.repeat(np.arange(self.n_elems), nfl)[mask]

        # orient: reverse faces whose normal points into the element
        p = self.nodes[face_nodes[:, :3]]
        normal = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        fc = self.nodes[face_nodes[:, : ft.n_corner]].mean(axis=1)
        ec = self.nodes[self.elems[elem][:, : et.n_corner]].mean(axis=1)
        flip = np.einsum("fj,fj->f", normal, fc - ec) < 0
        corners = face_nodes[:, : ft.n_corner].copy()
        corners[flip] = corners[flip][:, ::-1]
        if ft.n_corner == 3:
            tris, tri_face = corners, np.arange(len(corners))
        else:
            tris = np.vstack([corners[:, [0, 1, 2]], corners[:, [0, 2, 3]]])
            tri_face = np.concatenate([np.arange(len(corners))] * 2)
        tris = tris.astype(np.int64)
        self._boundary = Boundary(face_nodes, elem, tris, tri_face, ft.name,
                                  tri_mid=self._triangle_midnodes(face_nodes, tris, ft.n_corner))
        return self._boundary

    def _triangle_midnodes(self, face_nodes: np.ndarray, tris: np.ndarray, nc: int) -> np.ndarray:
        """
        Mid-side node on each edge of every render triangle, (nt, 3) = edges c0-c1, c1-c2, c2-c0; -1 where there is none.
        The viewport uses it so the pick cursor can land on the centre of an edge of a quadratic mesh. Quad-face
        diagonals carry no node, so those entries stay -1.
        """
        out = np.full(tris.shape, -1, dtype=np.int64)
        if self.et.n_nodes == self.et.n_corner or len(tris) == 0:
            return out
        n = np.int64(self.n_nodes)
        a = face_nodes[:, :nc]
        b = np.roll(a, -1, axis=1)
        m = face_nodes[:, nc:2 * nc]                      # edge k joins corner k and k+1 and owns column nc + k
        keys = (np.minimum(a, b) * n + np.maximum(a, b)).ravel()
        order = np.argsort(keys, kind="stable")
        keys, mids = keys[order], m.ravel()[order]
        for e in range(3):
            u, v = tris[:, e], tris[:, (e + 1) % 3]
            q = np.minimum(u, v) * n + np.maximum(u, v)
            pos = np.clip(np.searchsorted(keys, q), 0, len(keys) - 1)
            hit = keys[pos] == q
            out[hit, e] = mids[pos[hit]]
        return out

    def surface_nodes(self) -> np.ndarray:
        return np.unique(self.boundary().face_nodes)

    # --------------------------------------------------------- selection
    def expand_selection(self, node_ids) -> np.ndarray:
        """
        Add the mid-side nodes lying on boundary-face edges whose two corner nodes are selected.
        The UI picks corner nodes of surface triangles; quadratic elements also need their mid-side
        nodes constrained/loaded or the face would not be held evenly.
        """
        sel = np.unique(np.asarray(list(node_ids), dtype=np.int64))
        if self.et.n_nodes == self.et.n_corner or len(sel) == 0:
            return sel
        b = self.boundary()
        ft = FACE_TYPES[b.face_type]
        nc = ft.n_corner
        fn = b.face_nodes
        mask = np.zeros(self.n_nodes, dtype=bool)
        mask[sel] = True
        extra = []
        for k in range(nc):  # edge k joins corner k and corner (k+1) % nc; its mid-node is column nc + k
            a, c, m = fn[:, k], fn[:, (k + 1) % nc], fn[:, nc + k]
            extra.append(m[mask[a] & mask[c]])
        return np.unique(np.concatenate([sel] + extra))

    def nodes_where(self, pred) -> np.ndarray:
        """Node ids for which pred(coords[N,3]) -> bool[N] is true."""
        return np.where(pred(self.nodes))[0]

    def faces_where(self, pred) -> np.ndarray:
        """Boundary-face indices whose corner nodes ALL satisfy pred."""
        b = self.boundary()
        nc = FACE_TYPES[b.face_type].n_corner
        pts = self.nodes[b.face_nodes[:, :nc]].reshape(-1, 3)
        return np.where(pred(pts).reshape(-1, nc).all(axis=1))[0]
