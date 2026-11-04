"""PyFR backend: high-order flux reconstruction with PyFR's artificial-compressibility
incompressible Navier-Stokes system (``ac-navier-stokes``).

* Mesh: the fluid cells of the structured CFDFlow grid written as a Gmsh 2.2 mesh (``mesh.msh``,
  no Gmsh installation needed); physical groups ``fluid``, one per domain side and one per
  obstacle (``obs_<id>``).
* ``config.ini``: order ``p`` = solver order, dual time stepping (backward Euler in physical time,
  RK45 pseudo-stepping with a local PI pseudo-controller), Rusanov interface fluxes, LDG viscous
  fluxes. Boundary conditions: ``ac-in-fv`` (uniform or parabolic expression), ``ac-out-fp`` (p = 0),
  ``no-slp-wall`` (optionally moving), ``slp-wall`` (symmetry).
* Steady cases are integrated in time to 20 flow-through times (PyFR has no steady mode for AC).
* ``run_pyfr.sh`` imports the mesh, runs PyFR (``-b openmp`` by default, ``CFD_PYFR_DEVICE``) and
  exports every solution to ``.vtu``; CFDFlow averages the high-order output onto its grid.

PyFR's AC system is isothermal: energy / buoyancy are not available on this backend.
"""
from __future__ import annotations

import csv
import math
import re
import shutil
import time
from pathlib import Path

import numpy as np

from ...config import get_settings
from ..grid3d import AXIS_SIDES, build_grid_nd
from .base import (Availability, BackendFailed, BackendResult, CheckMsg, SolverBackend, frame_from_cc, full_fields,
                   meta_for, summary_from_cc)
from .launcher import resolve_launcher, run_streaming
from .openfoam import patch_name

INWARD = {"left": 1.0, "right": -1.0, "bottom": 1.0, "top": -1.0, "back": 1.0, "front": -1.0}
TANGENT = {"left": 1, "right": 1, "bottom": 0, "top": 0, "back": 0, "front": 0}


def _u_ref(case) -> float:
    sides = [getattr(case.boundaries, s) for s in case.sides()]
    speeds = [b.velocity for b in sides if b.type == "inlet"] + [abs(b.wall_velocity) for b in sides if b.type == "wall"]
    ini = case.initial
    return max(speeds + [abs(ini.velocity_x), abs(ini.velocity_y), abs(ini.velocity_z), 1e-3])


def write_gmsh(case, path: Path) -> dict:
    """Write the fluid cells as a Gmsh 2.2 ASCII mesh. Returns the boundary names used."""
    g = build_grid_nd(case)
    nd, n = g.ndim, g.n
    nn = tuple(k + 1 for k in n)
    fluid = g.fluid
    cells = np.argwhere(fluid)
    offs = [(0, 0), (1, 0), (1, 1), (0, 1)] if nd == 2 else \
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    conn = np.stack([np.ravel_multi_index(tuple(cells[:, a] + o[a] for a in range(nd)), nn) for o in offs], axis=1)
    faces: dict[str, list[np.ndarray]] = {}
    for a in range(nd):
        t_axes = [b for b in range(nd) if b != a]
        # cyclic node offsets of a face normal to axis a (in the tangential axes)
        cyc = [(0,), (1,)] if nd == 2 else [(0, 0), (1, 0), (1, 1), (0, 1)]
        for side, step in ((AXIS_SIDES[a][0], -1), (AXIS_SIDES[a][1], +1)):
            nb = cells.copy()
            nb[:, a] += step
            outside = (nb[:, a] < 0) | (nb[:, a] >= n[a])
            inside = ~outside
            solid_nb = np.zeros(len(cells), bool)
            sid = np.full(len(cells), -1)
            solid_nb[inside] = g.solid[tuple(nb[inside].T)]
            sid[inside] = g.solid_id[tuple(nb[inside].T)]
            face_off = 0 if step < 0 else 1
            for name, sel in [(side, outside)] + [(patch_name(ob.id), solid_nb & (sid == k)) for k, ob in enumerate(case.obstacles)]:
                if not sel.any():
                    continue
                c = cells[sel]
                nodes = []
                for co in cyc:
                    idx = [None] * nd
                    idx[a] = c[:, a] + face_off
                    for t, o in zip(t_axes, co):
                        idx[t] = c[:, t] + o
                    nodes.append(np.ravel_multi_index(tuple(idx), nn))
                faces.setdefault(name, []).append(np.stack(nodes, axis=1))
    used = np.unique(conn)
    renum = np.zeros(int(np.prod(nn)), dtype=np.int64)
    renum[used] = np.arange(1, used.size + 1)
    coords = np.stack([c.ravel() for c in np.meshgrid(*[np.arange(k) * h for k, h in zip(nn, g.h)], indexing="ij")], axis=1)
    names = sorted(faces)
    phys = {name: i + 2 for i, name in enumerate(names)}
    bdim, vdim = nd - 1, nd
    btype, vtype = (1, 3) if nd == 2 else (3, 5)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(names) + 1), f'{vdim} 1 "fluid"']
    lines += [f'{bdim} {phys[nm]} "{nm}"' for nm in names]
    lines += ["$EndPhysicalNames", "$Nodes", str(used.size)]
    for i, nid in enumerate(used, start=1):
        x = coords[nid]
        lines.append(f"{i} {x[0]:.12g} {x[1]:.12g} {x[2] if nd == 3 else 0.0:.12g}")
    lines += ["$EndNodes", "$Elements"]
    elems = []
    eid = 1
    for nm in names:
        for arr in faces[nm]:
            for row in renum[arr]:
                elems.append(f"{eid} {btype} 2 {phys[nm]} {phys[nm]} " + " ".join(map(str, row)))
                eid += 1
    for row in renum[conn]:
        elems.append(f"{eid} {vtype} 2 1 1 " + " ".join(map(str, row)))
        eid += 1
    lines += [str(len(elems))] + elems + ["$EndElements"]
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"boundaries": names, "elements": int(len(cells))}


def _inlet_expr(case, side: str, comp: int, nd: int) -> str:
    b = getattr(case.boundaries, side)
    a = next(i for i, pair in enumerate(AXIS_SIDES) if side in pair)
    if comp != a:
        return "0"
    val = f"{INWARD[side] * b.velocity:.10g}"
    if b.profile != "parabolic":
        return val
    L = (case.domain.width, case.domain.height, case.domain.depth)
    terms = [f"6*({'xyz'[t]}/{L[t]:.10g})*(1-{'xyz'[t]}/{L[t]:.10g})" for t in range(nd) if t != a]
    return val + "*" + "*".join(terms)


def write_ini(case, path: Path, names: list[str], device: str = "openmp") -> dict:
    g = build_grid_nd(case)
    nd = g.ndim
    m, rs = case.material, case.run
    nu = m.viscosity / m.density
    U = _u_ref(case)
    zeta = 2.5 * U**2
    order = case.solver.order
    hmin = min(g.h)
    t_ref = min(g.L) / U
    if rs.mode == "steady":
        tend = 20.0 * t_ref
        dt = max(rs.cfl * hmin / U, tend / 400.0)
        nframes = 2
    else:
        tend = rs.end_time
        dt = rs.fixed_dt or rs.cfl * hmin / U
        nframes = max(rs.snapshot_count, 2)
    dt_out = tend / (nframes - 1)
    dt = dt_out / math.ceil(dt_out / dt - 1e-9)  # PyFR: output interval must be a multiple of dt
    c = U + math.sqrt(U**2 + zeta)  # artificial acoustic speed
    pdt = 0.5 * hmin / ((order + 1) ** 2 * c)  # initial pseudo step; grown point-wise by the local-PI controller
    down = list(range(order, -1, -1))
    cycle = [(k, 1) for k in down[:-1]] + [(0, 2)] + [(k, 1) for k in reversed(down[1:-1])] + ([(order, 3)] if order else [])
    elem = "quad" if nd == 2 else "hex"
    iface = "line" if nd == 2 else "quad"
    comps = "uvw"[:nd]
    ini = case.initial
    v0 = (ini.velocity_x, ini.velocity_y, ini.velocity_z)
    out = [
        "; Generated by CFDFlow for PyFR (ac-navier-stokes)",
        "[backend]", "precision = double", "rank-allocator = linear", "",
        "[backend-openmp]", "cc = gcc", "",
        "[constants]", f"nu = {nu:.10g}", f"ac-zeta = {zeta:.10g}", "",
        "[solver]", "system = ac-navier-stokes", f"order = {order}", "",
        "[solver-time-integrator]", "formulation = dual", "scheme = backward-euler", "pseudo-scheme = rk45",
        "controller = none", "pseudo-controller = local-pi", "tstart = 0.0", f"tend = {tend!r}", f"dt = {dt!r}",
        f"pseudo-dt = {pdt:.10g}", "pseudo-niters-min = 3", "pseudo-niters-max = 20", "pseudo-resid-norm = l2",
        "pseudo-resid-tol = 5e-5", "atol = 1e-6", "pseudo-dt-max-mult = 2.5", "",
    ]
    if order > 0:  # p-multigrid acceleration of the pseudo-time iterations
        out += ["[solver-dual-time-integrator-multip]", "pseudo-dt-fact = 1.75",
                "cycle = [" + ", ".join(f"({k}, {n})" for k, n in cycle) + "]", ""]
    out += [
        "[solver-interfaces]", "riemann-solver = rusanov", "ldg-beta = 0.5", "ldg-tau = 0.1", "",
        f"[solver-interfaces-{iface}]", "flux-pts = gauss-legendre", "",
        f"[solver-elements-{elem}]", "soln-pts = gauss-legendre", "",
        "[soln-plugin-nancheck]", "nsteps = 25", "",
        "[soln-plugin-writer]", f"dt-out = {dt_out!r}", "basedir = .", "basename = soln-{t:.6f}", "",
        "[soln-plugin-pseudostats]", "flushsteps = 1", "file = pseudostats.csv", "header = true", "",
    ]
    for ob in case.obstacles:
        pn = patch_name(ob.id)
        if pn in names:
            out += [f"[soln-plugin-fluidforce-{pn}]", "nsteps = 10", f"quad-deg = {2 * order + 2}", f"file = forces-{pn}.csv",
                    "header = true", ""]
    for nm in names:
        out.append(f"[soln-bcs-{nm}]")
        if nm.startswith("obs_"):
            out.append("type = no-slp-wall")
        else:
            b = getattr(case.boundaries, nm)
            if b.type == "inlet":
                out.append("type = ac-in-fv")
                out += [f"{comps[k]} = {_inlet_expr(case, nm, k, nd)}" for k in range(nd)]
            elif b.type == "outlet":
                out += ["type = ac-out-fp", "p = 0"]
            elif b.type == "symmetry":
                out.append("type = slp-wall")
            else:
                out.append("type = no-slp-wall")
                if b.wall_velocity:
                    out.append(f"{comps[TANGENT[nm]]} = {b.wall_velocity:.10g}")
        out.append("")
    out += ["[soln-ics]"] + [f"{comps[k]} = {v0[k]:.10g}" for k in range(nd)] + ["p = 0.0", ""]
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(out))
    return {"tend": tend, "dt": dt, "zeta": zeta, "order": order}


RUN_SCRIPT = """#!/bin/bash
# Generated by CFDFlow: import the mesh, run PyFR and export every solution to VTU.
cd "$(dirname "$0")" || exit 1
DEVICE="${PYFR_DEVICE:-openmp}"
if [ "$1" != "export-only" ]; then
  rm -f soln-*.pyfrs soln-*.vtu pseudostats.csv forces-*.csv
  echo "CFDFLOW-STEP import"
  pyfr import mesh.msh mesh.pyfrm || { echo "CFDFLOW-FAILED import"; exit 1; }
  echo "CFDFLOW-STEP run"
  pyfr run -b "$DEVICE" mesh.pyfrm config.ini || { echo "CFDFLOW-FAILED run"; exit 1; }
fi
echo "CFDFLOW-STEP export"
for f in soln-*.pyfrs; do
  [ -f "$f" ] && pyfr export mesh.pyfrm "$f" "${f%.pyfrs}.vtu"
done
echo "CFDFLOW-DONE"
"""


_VTK_TYPES = {"Float32": "<f4", "Float64": "<f8", "Int32": "<i4", "Int64": "<i8", "UInt8": "u1", "UInt32": "<u4", "UInt64": "<u8"}


def read_vtu(path: Path) -> dict:
    """Minimal reader for PyFR's VTU output (XML header + raw appended binary, VTK file version 2.1,
    which meshio does not accept). Returns points, connectivity, offsets and point data."""
    raw = Path(path).read_bytes()
    a = raw.find(b"<AppendedData")
    if a < 0:  # not appended: let meshio handle it
        import meshio

        m = meshio.read(path)
        conn = np.concatenate([cb.data.ravel() for cb in m.cells])
        offs = np.cumsum(np.concatenate([np.full(len(cb.data), cb.data.shape[1]) for cb in m.cells]))
        return {"points": m.points, "connectivity": conn, "offsets": offs, "point_data": dict(m.point_data)}
    header = raw[:a].decode("latin1")
    start = raw.index(b"_", raw.index(b">", a)) + 1
    head = re.search(r'header_type="(\w+)"', header)
    hdt = np.dtype(_VTK_TYPES[head.group(1)] if head else "<u4")
    arrays = {}
    section = None
    for m in re.finditer(r"<(PointData|CellData|Points|Cells)[\s>]|<DataArray([^>]*)>", header):
        if m.group(1):
            section = m.group(1)
            continue
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        if attrs.get("format") != "appended":
            continue
        off = start + int(attrs["offset"])
        size = int(np.frombuffer(raw, hdt, 1, off)[0])
        dt = np.dtype(_VTK_TYPES[attrs["type"]])
        data = np.frombuffer(raw, dt, size // dt.itemsize, off + hdt.itemsize)
        nc = int(attrs.get("NumberOfComponents") or 1)
        arrays[(section, attrs.get("Name", ""))] = data.reshape(-1, nc) if nc > 1 else data
    return {"points": arrays[("Points", "")], "connectivity": arrays[("Cells", "connectivity")].astype(np.int64),
            "offsets": arrays[("Cells", "offsets")].astype(np.int64),
            "point_data": {name: v for (sec, name), v in arrays.items() if sec == "PointData"}}


def vtu_to_grid(case, path: Path) -> dict[str, np.ndarray]:
    """Average PyFR's (high-order) VTU point data onto the CFDFlow cells."""
    g = build_grid_nd(case)
    nd = g.ndim
    v = read_vtu(path)
    pts = np.asarray(v["points"], dtype=float)[:, :nd]
    pd = {k.lower(): val for k, val in v["point_data"].items()}
    vel = pd.get("velocity")
    pres = pd.get("pressure")
    if vel is None or pres is None:
        raise BackendFailed(f"Unexpected PyFR output fields in {path.name}: {list(v['point_data'])}")
    vel = np.asarray(vel, dtype=float).reshape(len(pts), -1)
    # nudge points towards their element centroid so shared vertices are binned into the right cell
    conn, offs = v["connectivity"], v["offsets"]
    counts = np.diff(np.concatenate([[0], offs]))
    owner = np.repeat(np.arange(len(offs)), counts)
    cen = np.stack([np.bincount(owner, weights=pts[conn, a]) / counts for a in range(nd)], axis=1)
    cent = np.zeros_like(pts)
    cnt = np.zeros(len(pts))
    np.add.at(cent, conn, cen[owner])
    np.add.at(cnt, conn, 1)
    cent /= np.maximum(cnt, 1)[:, None]
    q = pts + 1e-6 * (cent - pts)
    idx = tuple(np.clip(np.floor(q[:, a] / g.h[a]).astype(int), 0, g.n[a] - 1) for a in range(nd))
    flat = np.ravel_multi_index(idx, g.n)
    count = np.bincount(flat, minlength=g.n_cells).astype(float)
    out = {}
    for name, arr in [(c, vel[:, k]) for k, c in enumerate("uvw"[:nd])] + [("p", pres.reshape(-1) * case.material.density)]:
        s = np.bincount(flat, weights=arr, minlength=g.n_cells)
        out[name] = np.where(count > 0, s / np.maximum(count, 1), 0.0).reshape(g.n)
    out["T"] = np.full(g.n, case.initial.temperature)
    if nd == 2:
        out["w"] = np.zeros(g.n)
    return out


class PyFRBackend(SolverBackend):
    id = "pyfr"
    label = "PyFR (flux reconstruction)"
    description = ("High-order flux reconstruction with PyFR's artificial-compressibility incompressible solver "
                   "(ac-navier-stokes, dual time stepping), 2-D quads / 3-D hexes, OpenMP or GPU backends. Isothermal. "
                   "Falls back to the built-in Python artificial-compressibility solver when PyFR is unavailable.")
    capabilities = {"dims": (2, 3), "steady": True, "transient": True, "energy": False, "buoyancy": False, "turbulence": False}
    fallback_id = "python-ac"
    exportable = True

    def _launcher(self):
        s = get_settings()
        return resolve_launcher("pyfr", lambda: shutil.which("pyfr") is not None, "pyfr", s.pyfr_docker_image,
                                s.pyfr_launcher, s.pyfr_docker_image, s.pyfr_wsl_distro)

    def availability(self) -> Availability:
        return self._launcher()[1]

    def check(self, case) -> list[CheckMsg]:
        out = super().check(case)
        if case.run.mode == "steady":
            out.append(CheckMsg("info", "run", "PyFR has no steady mode for incompressible flow: the case is integrated in time "
                                               "to 20 flow-through times."))
        return out

    def export_case(self, case, dest: Path) -> list[Path]:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        info = write_gmsh(case, dest / "mesh.msh")
        write_ini(case, dest / "config.ini", info["boundaries"], get_settings().pyfr_device)
        with open(dest / "run_pyfr.sh", "w", encoding="ascii", newline="\n") as fh:
            fh.write(RUN_SCRIPT)
        return [dest / "mesh.msh", dest / "config.ini", dest / "run_pyfr.sh"]

    def run(self, case, hooks, run_dir: Path) -> BackendResult:
        launcher, av = self._launcher()
        if launcher is None:
            raise BackendFailed(f"PyFR is not available: {av.detail}")
        t0 = time.time()
        case_dir = Path(run_dir) / "pyfr"
        shutil.rmtree(case_dir, ignore_errors=True)
        self.export_case(case, case_dir)
        info = write_ini(case, case_dir / "config.ini", write_gmsh(case, case_dir / "mesh.msh")["boundaries"])
        hooks.log("info", f"PyFR case written to {case_dir} (order {info['order']}, dt {info['dt']:.4g}, "
                          f"tend {info['tend']:.4g}, ac-zeta {info['zeta']:.4g}; {av.detail}).")
        name = f"cfdflow-pyfr-{Path(run_dir).name[:12]}-{int(time.time()) % 100000}"
        device = get_settings().pyfr_device
        argv = ["env", f"PYFR_DEVICE={device}", "bash", "run_pyfr.sh"]
        g = build_grid_nd(case)
        state = {"failed": None, "tail": [], "last": 0.0, "it": 0, "t": 0.0}
        stats = case_dir / "pseudostats.csv"

        def poll_stats():
            try:
                with open(stats, newline="") as fh:
                    rows = list(csv.reader(fh))
            except OSError:
                return
            rows = [r for r in rows if r and r[0][0].isdigit()]
            if not rows:
                return
            last = rows[-1]
            n_steps = len({r[1] for r in rows})
            state["it"], state["t"] = n_steps, float(last[1])
            names = ["p", "u", "v", "w"][: len(last) - 3]
            res = {}
            for k, v in zip(names, last[3:]):
                try:
                    res[k] = float(v)
                except ValueError:
                    pass
            if res:
                hooks.on_monitor(n_steps, state["t"], res, {"dt": info["dt"]})
            hooks.on_progress(min(0.99, state["t"] / info["tend"]), n_steps, state["t"])

        def on_lines(lines):
            for ln in lines:
                s = ln.strip()
                if s.startswith("CFDFLOW-STEP"):
                    hooks.log("info", f"PyFR: {s.split(maxsplit=1)[1]} …")
                elif s.startswith("CFDFLOW-FAILED"):
                    state["failed"] = s.split(maxsplit=1)[1]
                elif s:
                    state["tail"] = (state["tail"] + [s])[-20:]
            if time.time() - state["last"] > 1.0:
                state["last"] = time.time()
                poll_stats()

        rc, stopped = run_streaming(launcher.command(case_dir, argv, name), case_dir, Path(run_dir) / "pyfr.log",
                                    on_lines=on_lines, should_stop=hooks.should_stop,
                                    kill=lambda p: launcher.kill(p, name), grace=5.0)
        poll_stats()
        if stopped:
            run_streaming(launcher.command(case_dir, argv[:-1] + ["run_pyfr.sh", "export-only"], name + "-x"), case_dir,
                          Path(run_dir) / "pyfr-export.log", on_lines=lambda ls: None, should_stop=lambda: False)
        if state["failed"] or (rc != 0 and not stopped):
            raise BackendFailed(f"PyFR step '{state['failed']}' failed: " + " | ".join(state["tail"][-6:]))
        vtus = sorted(case_dir.glob("soln-*.vtu"), key=lambda p: float(re.findall(r"soln-([0-9.eE+-]+)\.vtu", p.name)[0]))
        if not vtus:
            raise BackendFailed("PyFR produced no solution files.")
        if case.run.mode == "steady":
            vtus = vtus[-1:]
        times, frames, last = [], [], None
        for p in vtus:
            last = vtu_to_grid(case, p)
            times.append(float(re.findall(r"soln-([0-9.eE+-]+)\.vtu", p.name)[0]))
            frames.append(frame_from_cc(g, last))
        fields = full_fields(g, last)
        converged = (not stopped) and times[-1] >= info["tend"] * (1 - 1e-6)
        summary = summary_from_cc(case, g, fields, iterations=state["it"], sim_time=times[-1], wall=time.time() - t0,
                                  converged=converged, notes=[], backend=self.id)
        summary["pyfr"] = {"order": info["order"], "device": device, "launcher": av.mode, "ac_zeta": info["zeta"]}
        return BackendResult("cancelled" if stopped else "completed", converged, state["it"], times[-1],
                             meta_for(case, g, self.id, state["it"]), g.solid, times, frames, summary)
