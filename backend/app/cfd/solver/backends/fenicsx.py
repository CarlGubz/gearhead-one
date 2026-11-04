"""FEniCSx (DOLFINx) backend: runs :mod:`fenicsx_script` (a standalone DOLFINx program) in a DOLFINx
environment - a native Python that can ``import dolfinx``, the ``dolfinx/dolfinx`` Docker image,
or WSL - and reads the result back. Falls back to the pure-Python finite-element solver
(:mod:`fem_fallback`) when DOLFINx is not available."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from ...config import get_settings
from ..grid3d import build_grid_nd
from .base import (Availability, BackendFailed, BackendResult, CheckMsg, SolverBackend, frame_from_cc, full_fields,
                   meta_for, summary_from_cc)
from .launcher import resolve_launcher, run_streaming

SCRIPT = Path(__file__).with_name("fenicsx_script.py")


def _native_dolfinx(python: str) -> bool:
    if not shutil.which(python):
        return False
    try:
        return subprocess.run([python, "-c", "import dolfinx"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def load_fenicsx_result(case, path: Path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        d = {k: z[k] for k in z.files}
    nd = case.ndim
    g = build_grid_nd(case)
    frames = []
    for k in range(d["u"].shape[0]):
        fr = {c: (d[c][k] if nd == 3 else d[c][k][:, :, 0]) for c in ("u", "v", "w", "p", "T")}
        frames.append(fr)
    solid = d["solid"] if nd == 3 else d["solid"][:, :, 0]
    return {"grid": g, "times": [float(t) for t in d["times"]], "frames": frames, "solid": solid.astype(bool),
            "iterations": int(d["iterations"]), "converged": bool(d["converged"]), "status": str(d["status"]),
            "wall": float(d["wall"])}



def _case_json(case) -> dict:
    """case.json for the DOLFINx program. Imported solids ("mesh" obstacles) are handed over as the list of cells
    CFDFlow marks solid (flat C-order indices of the nx x ny (x nz) grid) - the DOLFINx mesh is the same structured grid,
    so the program needs no geometry code of its own and the solid cells are identical."""
    data = case.model_dump(mode="json")
    if any(o.shape == "mesh" for o in case.obstacles):
        g = build_grid_nd(case)
        for k, (ob, raw) in enumerate(zip(case.obstacles, data["obstacles"])):
            if ob.shape == "mesh":
                raw["cells"] = np.flatnonzero((g.solid_id == k).ravel()).tolist()
                raw.pop("vertices", None)
                raw.pop("triangles", None)
    return data

class FenicsxBackend(SolverBackend):
    id = "fenicsx"
    label = "FEniCSx (DOLFINx)"
    description = ("Finite elements with DOLFINx: Taylor-Hood Q2/Q1 (+ Q1 temperature), monolithic Newton solve with MUMPS; "
                   "steady (viscosity continuation) or backward-Euler transient, 2-D and 3-D. Falls back to the built-in "
                   "Python finite-element solver when DOLFINx is unavailable.")
    capabilities = {"dims": (2, 3), "steady": True, "transient": True, "energy": True, "buoyancy": True, "turbulence": False}
    fallback_id = "python-fem"
    exportable = True

    def _launcher(self):
        s = get_settings()
        return resolve_launcher("fenicsx", lambda: _native_dolfinx(s.fenicsx_python), s.fenicsx_python,
                                s.fenicsx_docker_image, s.fenicsx_launcher, s.fenicsx_docker_image, s.fenicsx_wsl_distro)

    def availability(self) -> Availability:
        return self._launcher()[1]

    def check(self, case) -> list[CheckMsg]:
        out = super().check(case)
        dofs = case.n_cells * (case.solver.order ** case.ndim) * (case.ndim + 1)
        if dofs > 2_000_000:
            out.append(CheckMsg("warning", "mesh", f"About {dofs:,.0f} unknowns for a direct (MUMPS) solve - expect a long, memory-hungry run."))
        return out

    def export_case(self, case, dest: Path) -> list[Path]:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "case.json").write_text(json.dumps(_case_json(case), indent=2), encoding="utf-8")
        shutil.copyfile(SCRIPT, dest / "run_fenicsx.py")
        readme = dest / "README.txt"
        readme.write_text(
            "CFDFlow -> FEniCSx case\n=======================\n\n"
            "Run inside a DOLFINx (v0.9) environment, e.g.\n\n"
            "    docker run --rm -v \"$PWD\":/case -w /case dolfinx/dolfinx:v0.9.0 python3 run_fenicsx.py\n\n"
            "The script reads case.json and writes result_fenicsx.npz (u, v, w, p, T on the CFDFlow grid).\n"
            "Create a file named STOP in this directory to stop a transient run early.\n", encoding="utf-8")
        return [dest / "case.json", dest / "run_fenicsx.py", readme]

    def run(self, case, hooks, run_dir: Path) -> BackendResult:
        launcher, av = self._launcher()
        if launcher is None:
            raise BackendFailed(f"FEniCSx is not available: {av.detail}")
        case_dir = Path(run_dir) / "fenicsx"
        shutil.rmtree(case_dir, ignore_errors=True)
        self.export_case(case, case_dir)
        python = "python3" if launcher.mode != "native" else get_settings().fenicsx_python
        name = f"cfdflow-fx-{Path(run_dir).name[:12]}-{int(time.time()) % 100000}"
        cmd = launcher.command(case_dir, [python, "run_fenicsx.py"], name)
        hooks.log("info", f"FEniCSx case written to {case_dir} ({av.detail}).")
        g = build_grid_nd(case)
        state = {"it": 0, "t": 0.0, "tail": [], "last_snap": 0.0, "snap_mtime": 0.0}
        result_file = case_dir / "result_fenicsx.npz"

        def on_lines(lines):
            for ln in lines:
                if ln.startswith("CFDFLOW "):
                    try:
                        msg = json.loads(ln[8:])
                    except ValueError:
                        continue
                    kind = msg.get("type")
                    if kind == "log":
                        hooks.log(msg.get("level", "info"), "FEniCSx: " + msg.get("msg", ""))
                    elif kind == "monitor":
                        state["it"], state["t"] = int(msg["it"]), float(msg["t"])
                        hooks.on_monitor(state["it"], state["t"], msg.get("res", {}), msg.get("values", {}))
                    elif kind == "progress":
                        hooks.on_progress(float(msg["frac"]), state["it"], state["t"])
                elif ln.strip():
                    state["tail"] = (state["tail"] + [ln])[-30:]
            now = time.time()
            if now - state["last_snap"] > 3.0 and result_file.exists():
                state["last_snap"] = now
                mt = result_file.stat().st_mtime
                if mt != state["snap_mtime"]:
                    state["snap_mtime"] = mt
                    try:
                        r = load_fenicsx_result(case, result_file)
                        hooks.save_latest(meta_for(case, g, self.id, r["iterations"]), r["solid"], r["times"][-1],
                                          frame_from_cc(g, r["frames"][-1]))
                    except (OSError, ValueError, KeyError):
                        pass

        rc, stopped = run_streaming(cmd, case_dir, Path(run_dir) / "fenicsx.log", on_lines=on_lines,
                                    should_stop=hooks.should_stop, on_stop=lambda: (case_dir / "STOP").touch(),
                                    kill=lambda p: launcher.kill(p, name), grace=60.0)
        if not result_file.exists() or rc not in (0, 3):
            raise BackendFailed("FEniCSx run failed: " + " | ".join(state["tail"][-8:]) + f" (exit code {rc})")
        r = load_fenicsx_result(case, result_file)
        g.solid[...] = r["solid"]
        fields = full_fields(g, r["frames"][-1])
        notes = [] if r["converged"] or stopped else ["Newton did not converge for the requested viscosity."]
        summary = summary_from_cc(case, g, fields, iterations=r["iterations"], sim_time=r["times"][-1], wall=r["wall"],
                                  converged=r["converged"], notes=notes, backend=self.id)
        summary["fenicsx"] = {"launcher": av.mode, "elements": f"Q{case.solver.order}/Q{case.solver.order - 1}"}
        status = "cancelled" if stopped or r["status"] == "cancelled" else "completed"
        return BackendResult(status, r["converged"], r["iterations"], r["times"][-1], meta_for(case, g, self.id, r["iterations"]),
                             r["solid"], r["times"], [frame_from_cc(g, f) for f in r["frames"]], summary)
