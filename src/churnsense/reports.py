"""Read the tracked reports the pipeline writes, refusing anything malformed.

The dashboard shows figures that ``make eda`` and ``make train`` wrote into
``reports/``. A missing file is normal (a fresh clone before ``make dev``) and
reads as ``None``. A file that exists but does not
have the expected shape is an error: a half-written or hand-edited report must
never pass for a measured one.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from churnsense.exceptions import DataError

ASSOCIATION_FILE = "association_ranking.csv"
COMPARISON_FILE = "model_comparison.csv"

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


#: The columns that hold a measured number. A read that let text through would pass a
#: hand-edited figure, or fail later as something other than a refusal.
_ASSOCIATION_NUMBERS = ("strength",)
_COMPARISON_NUMBERS = ("cv_pr_auc", "cv_std", "cv_se", "val_pr_auc", "val_roc_auc")


def _csv(path: Path, columns: tuple[str, ...], numbers: tuple[str, ...]) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    frame = pd.read_csv(path)
    if missing := [c for c in columns if c not in frame.columns]:
        raise DataError(f"{path.name} is missing column(s) {missing}")
    if text := [
        c
        for c in numbers
        if not pd.api.types.is_numeric_dtype(frame[c]) or pd.api.types.is_bool_dtype(frame[c])
    ]:
        raise DataError(f"{path.name} holds text where a number belongs, in column(s) {text}")
    if not np.isfinite(frame[list(numbers)].to_numpy(dtype=float)).all():
        raise DataError(f"{path.name} holds a blank or non-finite number")
    return frame


#: Decimals an association strength is served with (``churnsense.eda.STRENGTH_DECIMALS``).
STRENGTH_DECIMALS = 2


def association_ranking(reports_dir: Path) -> pd.DataFrame | None:
    """``make eda``'s ranking: Cramer's V or |point-biserial r| per feature, training partition."""
    frame = _csv(reports_dir / ASSOCIATION_FILE, _ASSOCIATION_COLUMNS, _ASSOCIATION_NUMBERS)
    if frame is None:
        return None
    if not frame["strength"].between(0, 1).all():
        raise DataError(f"{ASSOCIATION_FILE} holds a strength outside [0, 1]")
    # Ordered as written (``make eda`` orders on the exact values), then at most two decimals:
    # an exact strength beside the published counts can be inverted into a withheld one.
    ordered = frame.sort_values("strength", ascending=False, kind="mergesort")
    return ordered.assign(strength=ordered["strength"].round(STRENGTH_DECIMALS)).reset_index(
        drop=True
    )


def model_comparison(reports_dir: Path) -> pd.DataFrame | None:
    """``make train``'s candidate table: cross-validated and validation scores."""
    return _csv(reports_dir / COMPARISON_FILE, _COMPARISON_COLUMNS, _COMPARISON_NUMBERS)
