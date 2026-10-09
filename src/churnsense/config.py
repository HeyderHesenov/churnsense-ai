"""Typed access to ``configs/config.yaml``.

Why dataclasses and not pydantic: this file is committed, trusted input. The
value of validation here is catching *our own* typos, which frozen dataclasses
already do via ``TypeError`` on construction. Pydantic is reserved for the API,
where the input is untrusted and the error messages are part of the contract.

Paths are resolved against the repository root, so every command works
regardless of the directory it is invoked from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from churnsense.data.schema import RAW_COLUMNS
from churnsense.exceptions import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.yaml"


@dataclass(frozen=True, slots=True)
class Paths:
    raw_dir: Path
    interim_dir: Path
    processed_dir: Path
    artifacts_dir: Path
    reports_dir: Path
    figures_dir: Path

    @property
    def model_file(self) -> Path:
        return self.artifacts_dir / "model.joblib"

    @property
    def model_meta_file(self) -> Path:
        return self.artifacts_dir / "model_meta.json"

    @property
    def shap_cache_file(self) -> Path:
        return self.artifacts_dir / "shap_global.json"

    def ensure(self) -> None:
        """Create every output directory. Never touches the raw data directory."""
        for d in (
            self.interim_dir,
            self.processed_dir,
            self.artifacts_dir,
            self.reports_dir,
            self.figures_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class Dataset:
    filename: str
    url: str
    expected_rows: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class Split:
    test_size: float
    validation_size: float
    stratify: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.test_size < 1 or not 0 < self.validation_size < 1:
            raise ConfigError("split sizes must each be strictly between 0 and 1")
        if self.test_size + self.validation_size >= 0.9:
            raise ConfigError(
                f"test_size + validation_size = {self.test_size + self.validation_size:.2f} "
                "leaves too little training data"
            )

    @property
    def train_size(self) -> float:
        return 1.0 - self.test_size - self.validation_size


@dataclass(frozen=True, slots=True)
class Features:
    drop_columns: tuple[str, ...]
    include_total_charges: bool = True


@dataclass(frozen=True, slots=True)
class Training:
    cv_folds: int
    scoring: str
    n_jobs: int
    grids: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)

    @property
    def calibration_enabled(self) -> bool:
        return bool(self.calibration.get("enabled", False))


@dataclass(frozen=True, slots=True)
class RiskBand:
    name: str
    min: float
    max: float


@dataclass(frozen=True, slots=True)
class Business:
    """Retention-economics assumptions. Not estimated from the dataset.

    The dataset records no campaign outcomes, so ``offer_success_rate`` in
    particular is a business input. Anything computed from these fields is a
    simulation and must be labelled as such wherever it is displayed.
    """

    currency: str
    retention_offer_cost: float
    offer_success_rate: float
    expected_horizon_months: int
    gross_margin: float

    def __post_init__(self) -> None:
        if not 0 <= self.offer_success_rate <= 1:
            raise ConfigError("offer_success_rate must be a probability in [0, 1]")
        if not 0 <= self.gross_margin <= 1:
            raise ConfigError("gross_margin must be a fraction in [0, 1]")
        if self.retention_offer_cost < 0 or self.expected_horizon_months <= 0:
            raise ConfigError("offer cost must be >= 0 and the horizon must be >= 1 month")

    def customer_value(self, monthly_charges: float) -> float:
        """Margin a retained customer is assumed to generate over the horizon."""
        return monthly_charges * self.expected_horizon_months * self.gross_margin


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """Limits on what one upload may cost. Shared by the API and the dashboard."""

    max_batch_rows: int
    max_upload_bytes: int
    # Defaulted so a config written before this limit existed still loads.
    max_upload_columns: int = 1000

    def __post_init__(self) -> None:
        limits = (self.max_batch_rows, self.max_upload_bytes, self.max_upload_columns)
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in limits):
            raise ConfigError("api upload limits must each be a whole number of at least 1")
        # Below the raw contract's width the project's own template is refused.
        if self.max_upload_columns < len(RAW_COLUMNS):
            raise ConfigError(
                f"max_upload_columns must be at least {len(RAW_COLUMNS)}, the width of the "
                "raw data contract"
            )


@dataclass(frozen=True, slots=True)
class Config:
    name: str
    random_seed: int
    paths: Paths
    dataset: Dataset
    split: Split
    features: Features
    training: Training
    risk_bands: tuple[RiskBand, ...]
    business: Business
    api: ApiConfig
    log_level: str
    log_format: str

    @property
    def raw_data_file(self) -> Path:
        return self.paths.raw_dir / self.dataset.filename


def _build(raw: dict[str, Any], root: Path) -> Config:
    try:
        paths = Paths(**{k: root / v for k, v in raw["paths"].items()})
        return Config(
            name=raw["project"]["name"],
            random_seed=int(raw["project"]["random_seed"]),
            paths=paths,
            dataset=Dataset(**raw["dataset"]),
            split=Split(**raw["split"]),
            features=Features(
                drop_columns=tuple(raw["features"]["drop_columns"]),
                include_total_charges=raw["features"]["include_total_charges"],
            ),
            training=Training(**raw["training"]),
            risk_bands=tuple(RiskBand(**b) for b in raw["risk_bands"]),
            business=Business(**raw["business"]),
            api=ApiConfig(**raw["api"]),
            log_level=raw["logging"]["level"],
            log_format=raw["logging"]["format"],
        )
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc


@lru_cache(maxsize=4)
def load_config(path: str | Path | None = None) -> Config:
    """Load and validate the configuration.

    Cached, so the many modules that need config do not re-read and re-validate
    the file. ``CHURNSENSE_CONFIG`` overrides the default location.
    """
    cfg_path = Path(path or os.getenv("CHURNSENSE_CONFIG") or DEFAULT_CONFIG_PATH)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if not cfg_path.is_file():
        raise ConfigError(f"configuration file not found: {cfg_path}")

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"configuration file is not a YAML mapping: {cfg_path}")
    return _build(raw, PROJECT_ROOT)
