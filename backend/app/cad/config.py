"""CAD settings.

The database and CORS now belong to the Gearhead platform (`app.config`). What stays here is CAD-specific: where
uploaded STEP files are kept (`CAD_DATA_DIR`) and the safety limits of the geometry / CAM / simulation kernels
(`MAX_FEA_ELEMENTS`, `MAX_TOOLPATH_POINTS`, `CCX_TIMEOUT`; the CalculiX launcher reads `CCX_COMMAND`, `CCX_DOCKER_IMAGE`
and `CCX_THREADS` directly, see services/calculix.py).
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(str(_BACKEND / ".env"), str(_BACKEND.parent / ".env")), extra="ignore",
                                      populate_by_name=True)

    data_dir: str = Field(str(_BACKEND / "data" / "cad"), validation_alias="CAD_DATA_DIR")
    # Safety limits for the geometry / simulation kernels
    max_fea_elements: int = 120_000
    max_toolpath_points: int = 400_000
    ccx_timeout: float = 1800.0  # seconds per CalculiX run

    @property
    def uploads_dir(self) -> Path:
        p = Path(self.data_dir) / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
