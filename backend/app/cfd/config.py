"""CFD settings (environment variables prefixed with ``CFD_``).

The database, CORS and SPA hosting now belong to the Gearhead platform (`app.config`): CFD tables live in the same
database as accounts, FEA models and CAD documents. What stays here is CFD-specific: where run results are written,
run concurrency, size limits and the external solver launchers.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(str(_BACKEND / ".env"), str(_BACKEND.parent / ".env")),
                                      env_prefix="CFD_", extra="ignore")

    data_dir: Path = _BACKEND / "data" / "cfd"
    max_concurrent_runs: int = 2
    max_cells: int = 250_000
    scheduler_interval: float = 0.5
    # cap on the triangles of an imported solid obstacle (FEA model / CAD part sent to CFD); larger surfaces are decimated
    max_obstacle_triangles: int = 6000

    # ---- external solver backends (launcher: auto | native | docker | wsl | off)
    openfoam_launcher: str = "auto"
    openfoam_docker_image: str = "opencfd/openfoam-default:2406"
    openfoam_wsl_distro: str = ""
    openfoam_buoyant_solver: str = "boussinesq"  # boussinesq | compressible (buoyantSimpleFoam family)
    fenicsx_launcher: str = "auto"
    fenicsx_docker_image: str = "dolfinx/dolfinx:v0.9.0"
    fenicsx_python: str = "python3"
    fenicsx_wsl_distro: str = ""
    pyfr_launcher: str = "auto"
    pyfr_docker_image: str = "cfdflow/pyfr:2.0.3"
    pyfr_wsl_distro: str = ""
    pyfr_device: str = "openmp"  # PyFR compute backend: openmp | cuda | hip | metal | opencl
    max_fem_cells: int = 40_000  # cell limit of the pure-Python finite-element fallback

    @property
    def runs_dir(self) -> Path:
        p = Path(self.data_dir) / "runs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def run_dir(self, run_id: str) -> Path:
        p = self.runs_dir / run_id
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
