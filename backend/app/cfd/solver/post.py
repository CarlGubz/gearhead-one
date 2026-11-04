"""Post-processing helpers: result files, derived fields, slices, probes, exports.

Two result formats exist:

* **staggered** (2-D native solver): ``u`` (nx+1, ny), ``v`` (nx, ny+1), ``p``, ``T`` (nx, ny) per frame.
* **cell-centred** ``meta["format"] == "cc"`` (3-D native solver and every external backend:
  OpenFOAM, FEniCSx, PyFR and their Python fallbacks): ``u, v, w, p, T`` of shape (nx, ny, nz) per
  frame (nz = 1 for 2-D). External solvers are sampled onto the structured grid of the case, so
  the viewer, probes and exports work identically whatever produced the result.

3-D results are viewed as planar slices (``axis`` 0/1/2 = normal x/y/z, ``index`` = cell layer).
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path

import numpy as np

FIELD_NAMES = ["speed", "u", "v", "w", "p", "T", "vorticity"]
AXES = "xyz"


def save_frames(path: Path, meta: dict, solid: np.ndarray, times, frames: list[dict], extra: dict | None = None) -> None:
    """Atomically write a result file with one or more frames."""
    tmp = Path(str(path) + ".tmp.npz")
    arrays = {k: np.stack([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(
        tmp,
        meta=np.array(json.dumps({**meta, **(extra or {})})),
        solid=solid,
        times=np.asarray(times, dtype=float),
        **arrays,
    )
    os.replace(tmp, path)


def load_result(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        d = {k: z[k] for k in z.files}
    d["meta"] = json.loads(str(d["meta"]))
    return d


def is_cc(res: dict) -> bool:
    return res["meta"].get("format") == "cc"


def n_frames(res: dict) -> int:
    return int(res["u"].shape[0])


def _frame_index(res: dict, frame: int) -> int:
    nf = n_frames(res)
    k = frame if frame >= 0 else nf + frame
    return min(max(k, 0), nf - 1)


def derive_fields(u, v, p_pa, T, solid, dx, dy) -> dict[str, np.ndarray]:
    """Cell-centred derived fields from staggered u (nx+1,ny), v (nx,ny+1), p (nx,ny), T (nx,ny)."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    uc = np.where(solid, 0.0, 0.5 * (u[:-1] + u[1:]))
    vc = np.where(solid, 0.0, 0.5 * (v[:, :-1] + v[:, 1:]))
    vort = np.gradient(vc, dx, axis=0) - np.gradient(uc, dy, axis=1)
    return {
        "speed": np.hypot(uc, vc),
        "u": uc,
        "v": vc,
        "p": np.where(solid, 0.0, np.asarray(p_pa, dtype=float)),
        "T": np.asarray(T, dtype=float),
        "vorticity": np.where(solid, 0.0, vort),
    }


def default_slice(res: dict) -> tuple[int, int]:
    meta = res["meta"]
    if is_cc(res) and meta.get("dim") == 3:
        return 2, meta["nz"] // 2
    return 2, 0


def plane_view(res: dict, frame: int, axis: int | None = None, index: int | None = None) -> dict:
    """A 2-D view of a result: the whole field (2-D) or a slice normal to ``axis`` (3-D).

    Returns ``fields`` (2-D cell arrays), ``solid``, plane sizes ``n``/``L``, ``axes`` (names of the
    two in-plane axes), the in-plane velocity components ``vectors`` and plane information.
    """
    meta = res["meta"]
    k = _frame_index(res, frame)
    t = float(res["times"][k])
    if not is_cc(res):
        nx, ny = meta["nx"], meta["ny"]
        f = derive_fields(res["u"][k], res["v"][k], res["p"][k], res["T"][k], res["solid"], meta["lx"] / nx, meta["ly"] / ny)
        return {"fields": f, "solid": res["solid"], "n": (nx, ny), "L": (meta["lx"], meta["ly"]), "axes": ("x", "y"),
                "vectors": ("u", "v"), "time": t, "frame": k, "plane": None}
    dim = meta.get("dim", 3)
    n3 = (meta["nx"], meta["ny"], meta["nz"])
    L3 = (meta["lx"], meta["ly"], meta["lz"])
    if dim == 2:
        axis, index = 2, 0
    else:
        d_axis, d_index = default_slice(res)
        axis = d_axis if axis is None else int(axis)
        index = (n3[axis] // 2 if axis != d_axis else d_index) if index is None else int(index)
    axis = min(max(axis, 0), 2)
    index = min(max(index, 0), n3[axis] - 1)
    inplane = [a for a in range(3) if a != axis]
    comps = {c: np.asarray(res[c][k], dtype=float).take(index, axis=axis) for c in ("u", "v", "w", "p", "T") if c in res}
    solid3 = res["solid"] if res["solid"].ndim == 3 else res["solid"][..., None]
    solid = solid3.take(index, axis=axis)
    names = ("u", "v", "w")
    ca, cb = names[inplane[0]], names[inplane[1]]
    ha, hb = L3[inplane[0]] / n3[inplane[0]], L3[inplane[1]] / n3[inplane[1]]

    def grad(f, ax, h):
        return np.gradient(f, h, axis=ax) if f.shape[ax] > 1 else np.zeros_like(f)

    vel = [comps.get(c, np.zeros_like(comps["u"])) for c in names]
    speed = np.sqrt(sum(c**2 for c in vel))
    vort = grad(comps[cb], 0, ha) - grad(comps[ca], 1, hb)
    fields = {
        "speed": np.where(solid, 0.0, speed),
        "u": np.where(solid, 0.0, vel[0]),
        "v": np.where(solid, 0.0, vel[1]),
        "p": np.where(solid, 0.0, comps["p"]),
        "T": comps["T"],
        "vorticity": np.where(solid, 0.0, vort),
    }
    if dim == 3:
        fields["w"] = np.where(solid, 0.0, vel[2])
    plane = None
    if dim == 3:
        h_axis = L3[axis] / n3[axis]
        plane = {"axis": axis, "normal": AXES[axis], "index": index, "count": n3[axis],
                 "position": (index + 0.5) * h_axis, "axes": [AXES[a] for a in inplane]}
    return {"fields": fields, "solid": solid, "n": (n3[inplane[0]], n3[inplane[1]]),
            "L": (L3[inplane[0]], L3[inplane[1]]), "axes": (AXES[inplane[0]], AXES[inplane[1]]),
            "vectors": (ca, cb), "time": t, "frame": k, "plane": plane}


def frame_fields(res: dict, frame: int) -> tuple[dict[str, np.ndarray], float]:
    pv = plane_view(res, frame)
    return pv["fields"], pv["time"]


def encode(a: np.ndarray) -> str:
    """(n1, n2) array -> base64 float32 little-endian, row-major in (n2, n1) order."""
    return base64.b64encode(np.ascontiguousarray(a.T, dtype="<f4").tobytes()).decode("ascii")


def fields_payload(res: dict, frame: int, axis: int | None = None, index: int | None = None) -> dict:
    meta = res["meta"]
    pv = plane_view(res, frame, axis, index)
    f, solid = pv["fields"], pv["solid"]
    fluid = ~solid
    ranges = {}
    for k, a in f.items():
        vals = a[fluid] if fluid.any() else a.ravel()
        ranges[k] = [float(vals.min()), float(vals.max())]
    return {
        "nx": pv["n"][0],
        "ny": pv["n"][1],
        "lx": pv["L"][0],
        "ly": pv["L"][1],
        "axes": list(pv["axes"]),
        "vectors": list(pv["vectors"]),
        "dim": meta.get("dim", 2),
        "plane": pv["plane"],
        "backend": meta.get("backend", "native"),
        "energy": meta.get("energy", False),
        "time": pv["time"],
        "frame": pv["frame"],
        "n_frames": n_frames(res),
        "iteration": meta.get("iteration"),
        "encoding": "float32-le-base64, row-major (ny rows of nx)",
        "mask": base64.b64encode(np.ascontiguousarray(solid.T, dtype=np.uint8).tobytes()).decode("ascii"),
        "fields": {k: encode(a) for k, a in f.items()},
        "ranges": ranges,
    }


def bilinear(field: np.ndarray, meta: dict, x: float, y: float) -> float:
    nx, ny = meta["nx"], meta["ny"]
    dx, dy = meta["lx"] / nx, meta["ly"] / ny
    fx = min(max((x - dx / 2) / dx, 0), nx - 1)
    fy = min(max((y - dy / 2) / dy, 0), ny - 1)
    i0, j0 = int(math.floor(fx)), int(math.floor(fy))
    i1, j1 = min(i0 + 1, nx - 1), min(j0 + 1, ny - 1)
    a, b = fx - i0, fy - j0
    return float(
        field[i0, j0] * (1 - a) * (1 - b) + field[i1, j0] * a * (1 - b) + field[i0, j1] * (1 - a) * b + field[i1, j1] * a * b
    )


def probe_line(res: dict, frame: int, p0, p1, n: int, axis: int | None = None, index: int | None = None) -> dict:
    """Profile along a straight line given in the coordinates of the viewed plane."""
    pv = plane_view(res, frame, axis, index)
    pm = {"nx": pv["n"][0], "ny": pv["n"][1], "lx": pv["L"][0], "ly": pv["L"][1]}
    (x0, y0), (x1, y1) = p0, p1
    s = np.linspace(0.0, 1.0, n)
    xs = x0 + (x1 - x0) * s
    ys = y0 + (y1 - y0) * s
    dist = s * math.hypot(x1 - x0, y1 - y0)
    out = {k: [bilinear(a, pm, x, y) for x, y in zip(xs, ys)] for k, a in pv["fields"].items()}
    return {"distance": dist.tolist(), "x": xs.tolist(), "y": ys.tolist(), "axes": list(pv["axes"]),
            "plane": pv["plane"], "time": pv["time"], "values": out}


def _cc_frame(res: dict, frame: int) -> dict[str, np.ndarray]:
    k = _frame_index(res, frame)
    return {c: np.asarray(res[c][k], dtype=float) for c in ("u", "v", "w", "p", "T") if c in res}


def export_vtu(res: dict, frame: int, path: Path) -> None:
    import meshio

    meta = res["meta"]
    if not is_cc(res) or meta.get("dim", 3) == 2:
        _export_vtu_2d(res, frame, path)
        return
    nx, ny, nz = meta["nx"], meta["ny"], meta["nz"]
    h = (meta["lx"] / nx, meta["ly"] / ny, meta["lz"] / nz)
    f = _cc_frame(res, frame)
    X, Y, Z = np.meshgrid(np.arange(nx + 1) * h[0], np.arange(ny + 1) * h[1], np.arange(nz + 1) * h[2], indexing="ij")
    points = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

    def pid(i, j, k):
        return (i * (ny + 1) + j) * (nz + 1) + k

    I, J, K = (a.ravel() for a in np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij"))
    hexes = np.column_stack([pid(I, J, K), pid(I + 1, J, K), pid(I + 1, J + 1, K), pid(I, J + 1, K),
                             pid(I, J, K + 1), pid(I + 1, J, K + 1), pid(I + 1, J + 1, K + 1), pid(I, J + 1, K + 1)])
    vel = np.column_stack([f["u"].ravel(), f["v"].ravel(), f["w"].ravel()])
    cell_data = {
        "velocity": [vel],
        "speed": [np.linalg.norm(vel, axis=1)],
        "pressure": [f["p"].ravel()],
        "solid": [res["solid"].ravel().astype(np.int8)],
    }
    if meta.get("energy"):
        cell_data["temperature"] = [f["T"].ravel()]
    meshio.Mesh(points, [("hexahedron", hexes)], cell_data=cell_data).write(str(path), file_format="vtu")


def _export_vtu_2d(res: dict, frame: int, path: Path) -> None:
    import meshio

    pv = plane_view(res, frame)
    meta = res["meta"]
    nx, ny = pv["n"]
    dx, dy = pv["L"][0] / nx, pv["L"][1] / ny
    f = pv["fields"]
    xs = np.arange(nx + 1) * dx
    ys = np.arange(ny + 1) * dy
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    points = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])

    def pid(i, j):
        return i * (ny + 1) + j

    I, J = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    quads = np.column_stack([pid(I, J).ravel(), pid(I + 1, J).ravel(), pid(I + 1, J + 1).ravel(), pid(I, J + 1).ravel()])
    cell_data = {
        "velocity": [np.column_stack([f["u"].ravel(), f["v"].ravel(), np.zeros(nx * ny)])],
        "speed": [f["speed"].ravel()],
        "pressure": [f["p"].ravel()],
        "vorticity": [f["vorticity"].ravel()],
        "solid": [pv["solid"].ravel().astype(np.int8)],
    }
    if meta.get("energy"):
        cell_data["temperature"] = [f["T"].ravel()]
    meshio.Mesh(points, [("quad", quads)], cell_data=cell_data).write(str(path), file_format="vtu")


def export_csv(res: dict, frame: int) -> str:
    meta = res["meta"]
    energy = meta.get("energy")
    if is_cc(res) and meta.get("dim", 3) == 3:
        nx, ny, nz = meta["nx"], meta["ny"], meta["nz"]
        h = (meta["lx"] / nx, meta["ly"] / ny, meta["lz"] / nz)
        f = _cc_frame(res, frame)
        f["speed"] = np.sqrt(f["u"] ** 2 + f["v"] ** 2 + f["w"] ** 2)
        cols = ["x", "y", "z", "solid", "u", "v", "w", "speed", "p"] + (["T"] if energy else [])
        lines = [",".join(cols)]
        solid = res["solid"]
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    row = [f"{(i + .5) * h[0]:.6g}", f"{(j + .5) * h[1]:.6g}", f"{(k + .5) * h[2]:.6g}", str(int(solid[i, j, k]))]
                    row += [f"{f[c][i, j, k]:.8g}" for c in cols[4:]]
                    lines.append(",".join(row))
        return "\n".join(lines) + "\n"
    pv = plane_view(res, frame)
    nx, ny = pv["n"]
    dx, dy = pv["L"][0] / nx, pv["L"][1] / ny
    f = pv["fields"]
    cols = ["x", "y", "solid", "u", "v", "speed", "p", "vorticity"] + (["T"] if energy else [])
    lines = [",".join(cols)]
    for j in range(ny):
        for i in range(nx):
            row = [f"{(i + .5) * dx:.6g}", f"{(j + .5) * dy:.6g}", str(int(pv["solid"][i, j]))]
            for k in cols[3:]:
                row.append(f"{f[k][i, j]:.8g}")
            lines.append(",".join(row))
    return "\n".join(lines) + "\n"
