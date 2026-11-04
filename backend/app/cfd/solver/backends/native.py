"""The built-in finite-volume solver (2-D: :mod:`ns2d`, 3-D: :mod:`ns3d`)."""
from __future__ import annotations

from pathlib import Path

from ..runner import Hooks, run_simulation
from .base import BackendResult, SolverBackend


class NativeBackend(SolverBackend):
    id = "native"
    label = "Gearhead CFD native (finite volume)"
    description = ("Built-in NumPy/SciPy MAC-grid projection solver. 2-D and 3-D, laminar, energy + Boussinesq "
                   "buoyancy. Also the Python stand-in for OpenFOAM when OpenFOAM is not installed.")
    capabilities = {"dims": (2, 3), "steady": True, "transient": True, "energy": True, "buoyancy": True, "turbulence": False}

    def run(self, case, hooks: Hooks, run_dir: Path) -> BackendResult:
        result = run_simulation(case, hooks)
        s = result.solver
        meta = s.result_meta(case.run.mode)
        meta["backend"] = self.id
        result.summary["backend"] = self.id
        result.summary["dimension"] = case.ndim
        return BackendResult(result.status, result.converged, s.iteration, s.time, meta, s.S,
                             result.times or [s.time], result.frames or [s.state_arrays()], result.summary)
