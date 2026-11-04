"""Triangle-mesh helpers used by CAM (slicing, silhouettes, height fields).

Working on a tessellation instead of the exact B-rep keeps toolpath generation fast and
robust: a horizontal slice is a vectorised numpy operation and the result is a shapely
region that the offsetting code can consume directly.
"""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import MultiLineString, Polygon
from shapely.ops import polygonize, unary_union


class TriMesh:
    def __init__(self, positions, indices):
        self.P = np.asarray(positions, dtype=float).reshape(-1, 3)
        self.T = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
        self.zmin = float(self.P[:, 2].min()) if len(self.P) else 0.0
        self.zmax = float(self.P[:, 2].max()) if len(self.P) else 0.0
        self.xmin, self.ymin = (float(self.P[:, 0].min()), float(self.P[:, 1].min())) if len(self.P) else (0.0, 0.0)
        self.xmax, self.ymax = (float(self.P[:, 0].max()), float(self.P[:, 1].max())) if len(self.P) else (0.0, 0.0)
        self._tri = self.P[self.T] if len(self.T) else np.zeros((0, 3, 3))
        self._cache: dict[float, object] = {}

    # ------------------------------------------------------------------ slicing
    def section(self, z: float):
        """Cross-section (even-odd filled region) of the solid at height z."""
        key = round(z, 6)
        if key in self._cache:
            return self._cache[key]
        tri = self._tri
        if len(tri) == 0:
            return Polygon()
        zt = tri[:, :, 2]
        sel = (zt.min(axis=1) < z) & (zt.max(axis=1) > z)
        tris = tri[sel]
        if len(tris) == 0:
            self._cache[key] = Polygon()
            return self._cache[key]
        d = tris[:, :, 2] - z
        segs = np.zeros((len(tris), 2, 2))
        count = np.zeros(len(tris), dtype=int)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            cross = (d[:, i] * d[:, j]) < 0
            denom = np.where(cross, d[:, i] - d[:, j], 1.0)
            t = d[:, i] / denom
            pt = tris[:, i, :2] + (tris[:, j, :2] - tris[:, i, :2]) * t[:, None]
            for k in np.nonzero(cross)[0]:
                if count[k] < 2:
                    segs[k, count[k]] = pt[k]
                count[k] += 1
        ok = count == 2
        lines = [((round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))) for a, b in segs[ok] if np.linalg.norm(a - b) > 1e-6]
        if not lines:
            self._cache[key] = Polygon()
            return self._cache[key]
        noded = unary_union(MultiLineString(lines))
        polys = list(polygonize(noded))
        region = Polygon()
        for pg in polys:  # even-odd fill over the loops: nested loops become holes / islands
            # (polygonize already returns faces *with* their holes; XOR-ing those would refill every hole)
            region = region.symmetric_difference(Polygon(pg.exterior))
        region = region.buffer(0)
        self._cache[key] = region
        return region

    def silhouette(self, samples: int = 14):
        """Approximate XY shadow of the part: union of sections at several heights."""
        if self.zmax - self.zmin < 1e-6:
            return Polygon()
        key = ("sil", samples)
        if key in self._cache:  # type: ignore[comparison-overlap]
            return self._cache[key]  # type: ignore[index]
        zs = np.linspace(self.zmin, self.zmax, samples + 2)[1:-1]
        regs = [self.section(float(z)) for z in zs]
        out = unary_union([r for r in regs if not r.is_empty]) if regs else Polygon()
        self._cache[key] = out  # type: ignore[index]
        return out

    # ------------------------------------------------------------ face polygons
    def triangles_region(self, tri_start: int, tri_count: int):
        tris = self._tri[tri_start : tri_start + tri_count]
        polys = [Polygon(t[:, :2]) for t in tris if abs(_area2(t)) > 1e-12]
        if not polys:
            return Polygon()
        return unary_union([p.buffer(1e-6) for p in polys]).buffer(-1e-6)

    # --------------------------------------------------------------- height field
    def heightfield(self, x0: float, y0: float, nx: int, ny: int, res: float) -> np.ndarray:
        """Highest surface z over each XY cell (-inf where the part does not cover the cell)."""
        H = np.full((ny, nx), -np.inf)
        tri = self._tri
        if len(tri) == 0:
            return H
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        keep = np.abs(n[:, 2]) > 1e-9  # skip vertical triangles
        for a, b, c in tri[keep]:
            minx, maxx = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
            miny, maxy = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
            i0 = max(0, int(math.floor((minx - x0) / res - 0.5)))
            i1 = min(nx - 1, int(math.ceil((maxx - x0) / res - 0.5)))
            j0 = max(0, int(math.floor((miny - y0) / res - 0.5)))
            j1 = min(ny - 1, int(math.ceil((maxy - y0) / res - 0.5)))
            if i1 < i0 or j1 < j0:
                continue
            xs = x0 + (np.arange(i0, i1 + 1) + 0.5) * res
            ys = y0 + (np.arange(j0, j1 + 1) + 0.5) * res
            X, Y = np.meshgrid(xs, ys)
            det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(det) < 1e-14:
                continue
            l1 = ((b[1] - c[1]) * (X - c[0]) + (c[0] - b[0]) * (Y - c[1])) / det
            l2 = ((c[1] - a[1]) * (X - c[0]) + (a[0] - c[0]) * (Y - c[1])) / det
            l3 = 1 - l1 - l2
            eps = -1e-9
            inside = (l1 >= eps) & (l2 >= eps) & (l3 >= eps)
            if not inside.any():
                continue
            Z = l1 * a[2] + l2 * b[2] + l3 * c[2]
            win = H[j0 : j1 + 1, i0 : i1 + 1]
            np.maximum(win, np.where(inside, Z, -np.inf), out=win)
        return H


def _area2(t: np.ndarray) -> float:
    return (t[1, 0] - t[0, 0]) * (t[2, 1] - t[0, 1]) - (t[2, 0] - t[0, 0]) * (t[1, 1] - t[0, 1])
