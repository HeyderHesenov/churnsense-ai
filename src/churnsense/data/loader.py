"""Load and clean the raw dataset.

Cleaning is reported, never silent. ``CleaningReport`` records what was changed
and why, and that record is written into the EDA report and the model metadata.
A reviewer can therefore see every transformation applied between the immutable
CSV and the training matrix.

The two judgement calls made here, both measured on the real file:

* ``TotalCharges`` is typed ``object`` because 11 rows hold a single space.
  All 11 have ``tenure == 0``, so the correct value is **0.0** - the customer
  has not been billed yet. Median imputation would invent roughly $1,400 of
  spend for a brand-new customer and would distort exactly the tenure region
  the model cares most about.
* ``SeniorCitizen`` arrives as int 0/1. It is a flag, not a quantity, so it is
  cast to a string category. Leaving it numeric would let the scaler and the
  linear model treat it as an ordered magnitude.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.exceptions import DataError
from churnsense.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CleaningReport:
    """Everything that happened between the raw CSV and the clean frame."""

    rows_in: int
    rows_out: int
    duplicate_rows_dropped: int
    total_charges_blank: int
    total_charges_filled_zero: int
    total_charges_filled_derived: int
    stripped_columns: tuple[str, ...] = field(default_factory=tuple)

    def as_lines(self) -> list[str]:
        lines = [
            f"rows: {self.rows_in} in -> {self.rows_out} out",
            f"exact duplicate rows dropped: {self.duplicate_rows_dropped}",
            f"blank TotalCharges found: {self.total_charges_blank} "
            f"(filled with 0.0 where tenure == 0: {self.total_charges_filled_zero}; "
            f"filled from tenure x MonthlyCharges: {self.total_charges_filled_derived})",
        ]
        if self.stripped_columns:
            lines.append(f"whitespace stripped in: {', '.join(self.stripped_columns)}")
        return lines


def read_raw(path: Path | None = None, cfg: Config | None = None) -> pd.DataFrame:
    """Read the raw CSV with no coercion, so cleaning stays explicit.

    Every column is read as a string. Letting pandas guess dtypes would hide
    the ``TotalCharges`` problem behind a silent ``object`` column and make the
    blank values harder to count.
    """
    cfg = cfg or load_config()
    path = Path(path) if path else cfg.raw_data_file
    if not path.is_file():
        raise DataError(
            f"raw dataset not found at {path}. Run `make data` (or "
            "`python -m churnsense.data.download`) first."
        )
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    logger.info("read raw rows=%d cols=%d path=%s", len(df), df.shape[1], path.name)
    return df


def clean_frame(
    df: pd.DataFrame, required_columns: list[str] | None = None
) -> tuple[pd.DataFrame, CleaningReport]:
    """Return a typed, validated copy of ``df`` plus a record of the changes.

    ``required_columns`` narrows validation to the columns the caller needs.
    An uploaded scoring file legitimately carries only the model's feature
    columns, and demanding ``customerID`` and ``gender`` from it - neither of
    which the model uses - would reject perfectly valid input.
    """
    rows_in = len(df)
    df = df.copy()

    object_cols = [c for c in df.columns if df[c].dtype == object]
    stripped = tuple(c for c in object_cols if df[c].str.strip().ne(df[c]).any())
    for col in object_cols:
        df[col] = df[col].str.strip()

    before = len(df)
    df = df.drop_duplicates()
    duplicates_dropped = before - len(df)
    if duplicates_dropped:
        logger.warning("dropped %d exact duplicate row(s)", duplicates_dropped)

    # Columns are handled if present rather than assumed. A scoring file need
    # not carry every column of the raw contract - with
    # `features.include_total_charges: false`, a perfectly valid upload has no
    # TotalCharges at all - and an unconditional `df["TotalCharges"]` turned
    # that into a bare KeyError, which the API served as a 500 instead of a
    # 422 explaining what was wrong.
    numeric = {
        name: pd.to_numeric(df[name], errors="coerce")
        for name in ("tenure", "MonthlyCharges", "TotalCharges")
        if name in df.columns
    }
    n_blank = zero_filled = derived_filled = 0

    # --- TotalCharges: the one genuinely ambiguous field ---------------------
    if "TotalCharges" in numeric:
        total, tenure = numeric["TotalCharges"], numeric.get("tenure")
        blank = total.isna()
        n_blank = int(blank.sum())

        if tenure is None:
            # Without tenure there is no way to tell "never billed" from
            # "missing", so 0.0 would be a guess dressed as a fact.
            zero_filled = 0
        else:
            at_zero_tenure = blank & tenure.eq(0)
            total = total.mask(at_zero_tenure, 0.0)
            zero_filled = int(at_zero_tenure.sum())

            recoverable = blank & ~tenure.eq(0)
            if recoverable.any() and "MonthlyCharges" in numeric:
                # Not expected on the IBM file (all 11 blanks have tenure 0),
                # but a blank with real tenure is recoverable: TotalCharges
                # tracks tenure x MonthlyCharges at r = 0.9996 here.
                logger.warning(
                    "%d blank TotalCharges value(s) with tenure > 0; "
                    "filling from tenure x MonthlyCharges",
                    int(recoverable.sum()),
                )
                total = total.mask(recoverable, tenure * numeric["MonthlyCharges"])
                derived_filled = int(recoverable.sum())
        numeric["TotalCharges"] = total

    for name, values in numeric.items():
        df[name] = values
    if "SeniorCitizen" in df.columns:
        df["SeniorCitizen"] = df["SeniorCitizen"].astype(str)

    if schema.TARGET in df.columns:
        df[schema.TARGET] = (df[schema.TARGET] == schema.POSITIVE_LABEL).astype("int8")

    schema.validate_frame(df, columns=required_columns, require_target=schema.TARGET in df.columns)

    report = CleaningReport(
        rows_in=rows_in,
        rows_out=len(df),
        duplicate_rows_dropped=duplicates_dropped,
        total_charges_blank=n_blank,
        total_charges_filled_zero=zero_filled,
        total_charges_filled_derived=derived_filled,
        stripped_columns=stripped,
    )
    for line in report.as_lines():
        logger.info("clean: %s", line)
    return df, report


def load_clean(
    cfg: Config | None = None, path: Path | None = None
) -> tuple[pd.DataFrame, CleaningReport]:
    """Read and clean in one step - the normal entry point for the pipeline."""
    cfg = cfg or load_config()
    return clean_frame(read_raw(path, cfg))


def features_and_target(
    df: pd.DataFrame, cfg: Config | None = None
) -> tuple[pd.DataFrame, pd.Series]:
    """Split a clean frame into the model-input matrix and the target.

    Column selection comes from the config (``features.drop_columns``,
    ``features.include_total_charges``) so the ablations documented in the
    README are a config change, not a code change.
    """
    cfg = cfg or load_config()
    if schema.TARGET not in df.columns:
        raise DataError(f"target column '{schema.TARGET}' is absent; cannot build a training set")

    columns = schema.model_input_columns(
        cfg.features.drop_columns, cfg.features.include_total_charges
    )
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise DataError(f"clean frame is missing model-input columns: {missing}")

    return df.loc[:, columns].copy(), df[schema.TARGET].copy()
