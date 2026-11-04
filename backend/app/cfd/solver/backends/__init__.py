"""Solver backends behind one interface (see :mod:`base`).

User-selectable (``case.solver.backend``):

* ``native``   - built-in finite-volume solver (2-D :mod:`ns2d`, 3-D :mod:`ns3d`)
* ``openfoam`` - OpenFOAM case generation + run (fallback: ``native``)
* ``fenicsx``  - DOLFINx finite elements (fallback: ``python-fem``)
* ``pyfr``     - PyFR flux reconstruction, artificial compressibility (fallback: ``python-ac``)

Fallbacks (used automatically when the external tool is missing and ``allow_fallback`` is set):

* ``python-fem`` - :mod:`fem_fallback`, NumPy/SciPy stabilised finite elements
* ``python-ac``  - :mod:`ac_fallback`, NumPy artificial-compressibility solver
"""
from __future__ import annotations

from .ac_fallback import AcFallbackBackend
from .base import (Availability, BackendFailed, BackendResult, BackendUnavailable, CheckMsg, SolverBackend)
from .fem_fallback import FemFallbackBackend
from .fenicsx import FenicsxBackend
from .native import NativeBackend
from .openfoam import OpenFOAMBackend
from .pyfr import PyFRBackend

BACKENDS: dict[str, SolverBackend] = {
    b.id: b for b in (NativeBackend(), OpenFOAMBackend(), FenicsxBackend(), PyFRBackend(), FemFallbackBackend(), AcFallbackBackend())
}
USER_BACKENDS = ("native", "openfoam", "fenicsx", "pyfr")


def get_backend(backend_id: str) -> SolverBackend:
    return BACKENDS[backend_id]


def resolve(case) -> tuple[SolverBackend, list[str]]:
    """The backend that will actually run ``case`` and explanatory notes (fallback used, ...)."""
    b = BACKENDS[case.solver.backend]
    av = b.availability()
    if av.ok:
        return b, []
    if not b.fallback_id or not case.solver.allow_fallback:
        raise BackendUnavailable(f"{b.label} is not available here ({av.detail}) and the fallback is disabled.")
    fb = BACKENDS[b.fallback_id]
    return fb, [f"{b.label} is not available ({av.detail}); running the equivalent built-in solver: {fb.label}."]


def list_backends() -> list[dict]:
    out = []
    for bid in USER_BACKENDS + ("python-fem", "python-ac"):
        info = BACKENDS[bid].info()
        info["selectable"] = bid in USER_BACKENDS
        if info["fallback"]:
            info["fallback_label"] = BACKENDS[info["fallback"]].label
        out.append(info)
    return out


__all__ = ["BACKENDS", "USER_BACKENDS", "Availability", "BackendFailed", "BackendResult", "BackendUnavailable",
           "CheckMsg", "SolverBackend", "get_backend", "list_backends", "resolve"]
