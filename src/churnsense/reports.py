"""Read the tracked reports the pipeline writes, refusing anything malformed.

The dashboard shows figures that ``make eda``, ``make train`` and ``make
evaluate`` wrote into ``reports/``. A missing file is normal (a fresh clone
before ``make dev``) and reads as ``None``. A file that exists but does not
have the expected shape is an error: a half-written or hand-edited report must
never pass for a measured one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from churnsense.exceptions import DataError

ASSOCIATION_FILE = "association_ranking.csv"
COMPARISON_FILE = "model_comparison.csv"
FINAL_EVALUATION_FILE = "final_evaluation.json"

_ASSOCIATION_COLUMNS = ("feature", "measure", "strength")
_COMPARISON_COLUMNS = (
    "model",
    "cv_pr_auc",
    "cv_std",
    "cv_se",
    "within_one_se",
    "val_pr_auc",
    "val_roc_auc",
    "selected",
)


def _csv(path: Path, columns: tuple[str, ...]) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    frame = pd.read_csv(path)
    if missing := [c for c in columns if c not in frame.columns]:
        raise DataError(f"{path.name} is missing column(s) {missing}")
    numbers = frame[list(columns)].select_dtypes("number")
    if not np.isfinite(numbers.to_numpy(dtype=float)).all():
        raise DataError(f"{path.name} holds a blank or non-finite number")
    return frame


def association_ranking(reports_dir: Path) -> pd.DataFrame | None:
    """``make eda``'s ranking: Cramer's V or |point-biserial r| per feature, training partition."""
    frame = _csv(reports_dir / ASSOCIATION_FILE, _ASSOCIATION_COLUMNS)
    if frame is None:
        return None
    if not frame["strength"].between(0, 1).all():
        raise DataError(f"{ASSOCIATION_FILE} holds a strength outside [0, 1]")
    return frame.sort_values("strength", ascending=False, kind="mergesort").reset_index(drop=True)


def model_comparison(reports_dir: Path) -> pd.DataFrame | None:
    """``make train``'s candidate table: cross-validated and validation scores."""
    return _csv(reports_dir / COMPARISON_FILE, _COMPARISON_COLUMNS)


def final_evaluation(reports_dir: Path, model_sha256: str) -> dict[str, Any] | None:
    """The recorded test evaluation of the artifact with ``model_sha256``, or ``None``.

    ``None`` both when ``make evaluate CONFIRM=yes`` has not been run and when
    the record describes another artifact: a retrained model has not been
    evaluated, whatever an older file says.
    """
    path = reports_dir / FINAL_EVALUATION_FILE
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"), parse_constant=_refuse_constant)
    if not isinstance(record, dict) or record.get("model_sha256") is None:
        raise DataError(f"{FINAL_EVALUATION_FILE} does not name the artifact it evaluated")
    if record["model_sha256"] != model_sha256:
        return None
    return record


def _refuse_constant(name: str) -> float:
    """``json`` would read NaN and Infinity; a measured figure is never either."""
    raise DataError(f"{FINAL_EVALUATION_FILE} holds a non-finite number ({name})")
