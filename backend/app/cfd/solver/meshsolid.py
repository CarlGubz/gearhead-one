"""Inside / outside test of a closed triangle surface - the ``mesh`` obstacle shape.

An FEA model or a CAD part sent to CFD becomes an obstacle whose surface is a triangle mesh. Like every other obstacle
it is rasterised with the CFDFlow rule *cell centre inside the solid*, so all backends (native, OpenFOAM, FEniCSx, PyFR,
Python fallbacks) and the geometry canvas in the browser see the same staircase body.

Method: ray parity. For every grid row (fixed y, z) a ray is cast along +x; the triangles it crosses are found from
their y-z bounding boxes, the crossing abscissae are sorted, and a cell centre is inside when an odd number of
crossings lies to its left. The ray is offset by a tiny irrational fraction of the body size so that it does not hit
edges / vertices exactly (which would count a crossing twice). Cost: O(triangles x rows they cover + cells).
"""
from __future__ import annotations

import numpy as np

_JITTER = (1.2345678e-7, 2.3456789e-7)


def _as_arrays(vertices, triangles) -> tuple[np.ndarray, np.ndarray]:
    V = np.asarray(vertices, dtype=float).reshape(-1, 3)
    T = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    return V, T


def inside_axes(vertices, triangles, xs, ys, zs) -> np.ndarray:
    """Boolean mask of shape (len(xs), len(ys), len(zs)): grid point (xs[i], ys[j], zs[k]) inside the surface."""
    V, T = _as_arrays(vertices, triangles)
    xs, ys, zs = (np.asarray(a, dtype=float).ravel() for a in (xs, ys, zs))
    nx, ny, nz = len(xs), len(ys), len(zs)
    out = np.zeros((nx, ny, nz), dtype=bool)
    if len(T) == 0 or nx == 0:
        return out
    span = float(np.ptp(V, axis=0).max()) or 1.0
    yq = ys + _JITTER[0] * span
    zq = zs + _JITTER[1] * span
    y_order, z_order = np.argsort(yq), np.argsort(zq)
    ys_sorted, zs_sorted = yq[y_order], zq[z_order]
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    ylo = np.minimum(np.minimum(A[:, 1], B[:, 1]), C[:, 1])
    yhi = np.maximum(np.maximum(A[:, 1], B[:, 1]), C[:, 1])
    zlo = np.minimum(np.minimum(A[:, 2], B[:, 2]), C[:, 2])
    zhi = np.maximum(np.maximum(A[:, 2], B[:, 2]), C[:, 2])
    j0s, j1s = np.searchsorted(ys_sorted, ylo, "left"), np.searchsorted(ys_sorted, yhi, "right")
    k0s, k1s = np.searchsorted(zs_sorted, zlo, "left"), np.searchsorted(zs_sorted, zhi, "right")
    rays, xcross = [], []
    for t in np.flatnonzero((j1s > j0s) & (k1s > k0s)):
        jj = y_order[j0s[t]:j1s[t]]
        kk = z_order[k0s[t]:k1s[t]]
        Y, Z = np.meshgrid(yq[jj], zq[kk], indexing="ij")
        a, b, c = A[t], B[t], C[t]
        # barycentric coordinates in the y-z projection
        d = (b[1] - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (b[2] - a[2])
        if abs(d) < 1e-300:
            continue  # triangle seen edge-on by the rays
        w1 = ((Y - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (Z - a[2])) / d
        w2 = ((b[1] - a[1]) * (Z - a[2]) - (Y - a[1]) * (b[2] - a[2])) / d
        w0 = 1.0 - w1 - w2
        hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not hit.any():
            continue
        J, K = np.meshgrid(jj, kk, indexing="ij")
        rays.append((J[hit] * nz + K[hit]).astype(np.int64))
        xcross.append(w0[hit] * a[0] + w1[hit] * b[0] + w2[hit] * c[0])
    if not rays:
        return out
    ray = np.concatenate(rays)
    xc = np.concatenate(xcross)
    order = np.lexsort((xc, ray))
    ray, xc = ray[order], xc[order]
    starts = np.flatnonzero(np.r_[True, ray[1:] != ray[:-1]])
    ends = np.r_[starts[1:], len(ray)]
    for s, e in zip(starts, ends):
        r = int(ray[s])
        count_left = np.searchsorted(xc[s:e], xs, "left")
        out[:, r // nz, r % nz] = (count_left % 2) == 1
    return out


def inside_points(vertices, triangles, points) -> np.ndarray:
    """Boolean per point (N, 3) - for scattered points (slower than `inside_axes`; use that for grids)."""
    V, T = _as_arrays(vertices, triangles)
    P = np.asarray(points, dtype=float).reshape(-1, 3)
    if len(P) == 0 or len(T) == 0:
        return np.zeros(len(P), dtype=bool)
    span = float(np.ptp(V, axis=0).max()) or 1.0
    py = P[:, 1] + _JITTER[0] * span
    pz = P[:, 2] + _JITTER[1] * span
    parity = np.zeros(len(P), dtype=bool)
    for a, b, c in zip(V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]):
        sel = np.flatnonzero((py >= min(a[1], b[1], c[1])) & (py <= max(a[1], b[1], c[1]))
                             & (pz >= min(a[2], b[2], c[2])) & (pz <= max(a[2], b[2], c[2])))
        if not len(sel):
            continue
        d = (b[1] - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (b[2] - a[2])
        if abs(d) < 1e-300:
            continue
        Y, Z = py[sel], pz[sel]
        w1 = ((Y - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (Z - a[2])) / d
        w2 = ((b[1] - a[1]) * (Z - a[2]) - (Y - a[1]) * (b[2] - a[2])) / d
        w0 = 1.0 - w1 - w2
        x = w0 * a[0] + w1 * b[0] + w2 * c[0]
        hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0) & (x > P[sel, 0])
        parity[sel[hit]] ^= True
    return parity


def obstacle_vertices(ob, ndim: int, depth: float) -> np.ndarray:
    """Absolute vertex coordinates of a mesh obstacle. In 2-D the section plane is z = 0 (through the centre)."""
    V = np.asarray(ob.vertices if not isinstance(ob, dict) else ob["vertices"], dtype=float).reshape(-1, 3)
    get = (lambda k: ob[k]) if isinstance(ob, dict) else (lambda k: getattr(ob, k))
    if ndim == 3:
        zc = get("z") if get("z") is not None else depth / 2
    else:
        zc = 0.0
    return V + np.array([get("x"), get("y"), zc])


def mask_on_grid(ob, coords: list[np.ndarray], depth: float) -> np.ndarray:
    """Mask of a mesh obstacle on meshgrid (indexing="ij") coordinates [X, Y(, Z)], or on scattered points."""
    nd = len(coords)
    V = obstacle_vertices(ob, nd, depth)
    tris = ob.triangles if not isinstance(ob, dict) else ob["triangles"]
    X = np.asarray(coords[0])
    if X.ndim == nd and nd in (2, 3):
        xs = X[(slice(None),) + (0,) * (nd - 1)]
        ys = np.asarray(coords[1])[(0, slice(None)) + (0,) * (nd - 2)]
        zs = np.asarray(coords[2])[0, 0, :] if nd == 3 else np.array([0.0])
        m = inside_axes(V, tris, xs, ys, zs)
        return m if nd == 3 else m[:, :, 0]
    pts = np.stack([np.ravel(c) for c in coords] + ([np.zeros(X.size)] if nd == 2 else []), axis=1)
    return inside_points(V, tris, pts).reshape(X.shape)


def bounds(ob, ndim: int, depth: float) -> tuple[list[float], list[float]]:
    V = obstacle_vertices(ob, ndim, depth)
    return V.min(axis=0)[:ndim].tolist(), V.max(axis=0)[:ndim].tolist()


def solid_runs_boxes(mask: np.ndarray, h: tuple[float, ...]) -> list[tuple[tuple[float, ...], tuple[float, ...]]]:
    """Cover the True cells of a (nx, ny, nz) mask with axis-aligned boxes whose faces lie on cell faces.

    Runs along x are found per (y, z) row and identical runs of neighbouring rows in y are merged. Used to express a
    mesh obstacle as OpenFOAM `boxToCell` boxes (geometric, so it survives the renumbering by earlier `subsetMesh` runs).
    """
    nx, ny, nz = mask.shape
    hx, hy, hz = h
    boxes = []
    for k in range(nz):
        open_runs: dict[tuple[int, int], int] = {}  # (i0, i1) -> j where the run started
        for j in range(ny + 1):
            row = mask[:, j, k] if j < ny else np.zeros(nx, dtype=bool)
            d = np.diff(np.r_[0, row.astype(np.int8), 0])
            runs = set(zip(np.flatnonzero(d == 1).tolist(), (np.flatnonzero(d == -1) - 1).tolist()))
            for run in list(open_runs):
                if run not in runs:
                    j0 = open_runs.pop(run)
                    lo = (run[0] * hx, j0 * hy, k * hz)
                    hi = ((run[1] + 1) * hx, j * hy, (k + 1) * hz)
                    boxes.append((lo, hi))
            for run in runs:
                open_runs.setdefault(run, j)
    return boxes


def decimate(vertices, triangles, max_triangles: int) -> tuple[np.ndarray, np.ndarray]:
    """Vertex-clustering decimation to at most ~`max_triangles` (keeps a closed surface closed in practice).

    The clustering cell shrinks until the result fits; degenerate and duplicate triangles are dropped. A surface that
    is already small enough is returned unchanged.
    """
    V, T = _as_arrays(vertices, triangles)
    if len(T) <= max_triangles:
        return V, T
    span = np.ptp(V, axis=0)
    size = float(span.max()) or 1.0
    best = None
    for cells in (160, 120, 90, 70, 55, 42, 32, 24, 18, 14, 10):
        q = size / cells
        key = np.floor((V - V.min(axis=0)) / q).astype(np.int64)
        uniq, inv = np.unique(key, axis=0, return_inverse=True)
        inv = inv.ravel()
        cnt = np.bincount(inv)
        NV = np.zeros((len(uniq), 3))
        for a in range(3):
            NV[:, a] = np.bincount(inv, weights=V[:, a]) / cnt
        NT = inv[T]
        ok = (NT[:, 0] != NT[:, 1]) & (NT[:, 1] != NT[:, 2]) & (NT[:, 0] != NT[:, 2])
        NT = NT[ok]
        if len(NT):
            # collapsed triangles that now coincide: for the parity test an even number of copies cancels out,
            # an odd number counts once - keep one copy of each triangle that occurs an odd number of times
            _, first, inverse, counts = np.unique(np.sort(NT, axis=1), axis=0, return_index=True,
                                                  return_inverse=True, return_counts=True)
            keep = np.sort(first[(counts % 2) == 1])
            NT = NT[keep]
        best = (NV, NT)
        if len(NT) <= max_triangles:
            break
    used = np.unique(best[1])
    remap = np.full(len(best[0]), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return best[0][used], remap[best[1]]
