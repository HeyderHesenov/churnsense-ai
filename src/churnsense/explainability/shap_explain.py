"""SHAP explanations for the shipped model.

**The served function is explained, never a convenient inner piece.** The
explainer calls the shipped model's own ``predict_proba`` - preprocessing,
calibration and probability clamp included - so an attribution always
describes the probability a customer is actually shown. SHAP's efficiency
property makes the contributions plus the base value reproduce that
probability exactly, and the test suite checks it.

**Raw features, not one-hot columns.** Categories are handed to the explainer
as vocabulary codes and decoded back to text inside the model call, so a
masked feature swaps a whole value. Explaining the encoded matrix instead (an
earlier version) permuted one-hot columns independently: it scored impossible
"two contracts at once" rows, and each feature's effect had to be re-summed
from its dummies afterwards.

**Enough permutations to be stable.** The model-agnostic Permutation explainer
covers every model family, but it samples feature orderings. One ordering per
customer - the earlier setting - changed the top three drivers for over a
third of customers when only the seed changed. At ``PERMUTATIONS`` orderings
the measured seed-to-seed spread on the real data is about 0.3 percentage
points per contribution, against 1 point with a single ordering; doubling the
budget again bought little and doubled the cost. The explainer is also
seeded, so a refresh never changes an explanation.

**Attribution is not causation.** A SHAP value says how much a feature moved
*this model's output* relative to a baseline. It does not say that changing
the feature would change the customer's behaviour.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import shap

from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.exceptions import SchemaValidationError
from churnsense.logging_setup import configure_logging
from churnsense.models.calibration_clamp import ProbabilityClamp

logger = logging.getLogger(__name__)

CAVEAT = (
    "These are model attributions, not causal effects. They describe how each "
    "feature moved this model's output relative to an average customer. They do "
    "not establish that changing a feature would change whether the customer stays."
)

DEFAULT_BACKGROUND = 100
DEFAULT_SAMPLE = 300
#: Antithetic feature orderings sampled per explained customer.
PERMUTATIONS = 10


@dataclass(frozen=True, slots=True)
class FeatureContribution:
    """One feature's signed contribution to one prediction."""

    feature: str
    label: str
    value: Any
    shap_value: float

    @property
    def direction(self) -> str:
        return "raises" if self.shap_value > 0 else "lowers"

    @property
    def percentage_points(self) -> float:
        """The contribution expressed in probability points, as shown in the UI."""
        return self.shap_value * 100.0


@dataclass(frozen=True, slots=True)
class CustomerExplanation:
    """A single customer's prediction, decomposed."""

    probability: float
    base_value: float
    contributions: list[FeatureContribution]


def _as_codes(X: pd.DataFrame) -> np.ndarray:
    """Raw features as one float matrix, each category replaced by its vocabulary index."""
    codes = np.column_stack(
        [
            X[column].map({value: i for i, value in enumerate(schema.ALLOWED_CATEGORIES[column])})
            if column in schema.ALLOWED_CATEGORIES
            else X[column]
            for column in X.columns
        ]
    ).astype(float)
    if np.isnan(codes).any():
        raise SchemaValidationError("explanations need complete values from the column contract")
    return codes


def _from_codes(codes: np.ndarray, columns: list[str]) -> pd.DataFrame:
    """Inverse of ``_as_codes``: the frame the shipped model actually scores."""
    frame = pd.DataFrame(codes, columns=columns)
    for column in columns:
        if column in schema.ALLOWED_CATEGORIES:
            vocabulary = np.asarray(schema.ALLOWED_CATEGORIES[column], dtype=object)
            frame[column] = vocabulary[frame[column].to_numpy(dtype=int)]
    return frame


def _shap_values(
    model: ProbabilityClamp,
    X: pd.DataFrame,
    background: pd.DataFrame,
    seed: int = 0,
    permutations: int = PERMUTATIONS,
) -> pd.DataFrame:
    """SHAP values per raw feature, as a frame aligned with ``X``.

    Contributions sum to the served probability minus the base value for any
    ``permutations``; the budget only buys stability of the split between
    features.
    """
    columns = list(X.columns)
    explainer = shap.explainers.PermutationExplainer(
        lambda codes: model.predict_proba(_from_codes(codes, columns))[:, 1],
        _as_codes(background[columns]),
        seed=seed,
    )
    explanation = explainer(
        _as_codes(X), max_evals=permutations * (2 * len(columns) + 1), silent=True
    )
    values = pd.DataFrame(explanation.values, index=X.index, columns=columns)
    values.attrs["base_value"] = float(np.mean(explanation.base_values))
    return values


def background_sample(X: pd.DataFrame, seed: int, n: int = DEFAULT_BACKGROUND) -> pd.DataFrame:
    """The reference population that "an average customer" means.

    One definition, used by the cached global ranking and by both dashboard
    pages, so a customer's base value does not depend on where they are viewed.
    """
    return X.sample(n, random_state=seed) if len(X) > n else X


def global_importance(
    model: ProbabilityClamp,
    X: pd.DataFrame,
    *,
    background: pd.DataFrame | None = None,
    seed: int = 0,
    permutations: int = PERMUTATIONS,
) -> pd.DataFrame:
    """Rank features by mean absolute SHAP value over ``X``.

    Deliberately called *importance* and not *driver*: it measures how much a
    feature moves this model, which is a property of the model, not of the
    customers.
    """
    reference = background if background is not None else background_sample(X, seed)
    values = _shap_values(model, X, reference, seed=seed, permutations=permutations)

    mean_abs = values.abs().mean().sort_values(ascending=False)
    return pd.DataFrame(
        {
            "feature": mean_abs.index,
            "label": [schema.FEATURE_LABELS.get(f, f) for f in mean_abs.index],
            "mean_abs_shap": mean_abs.to_numpy(),
            "share": (mean_abs / mean_abs.sum()).to_numpy() if mean_abs.sum() else 0.0,
        }
    ).reset_index(drop=True)


def explain_customer(
    model: ProbabilityClamp,
    X: pd.DataFrame,
    row: int,
    *,
    top_k: int = 5,
    background: pd.DataFrame | None = None,
    seed: int = 0,
    permutations: int = PERMUTATIONS,
) -> CustomerExplanation:
    """Explain one row's prediction, strongest contributions first.

    ``X`` carries the surrounding rows so the explainer has a population to
    sample a background from; ``row`` is a positional index into it.
    """
    if not 0 <= row < len(X):
        raise IndexError(f"row {row} is out of range for {len(X)} customers")

    reference = background if background is not None else background_sample(X, seed)
    target = X.iloc[[row]]
    values = _shap_values(model, target, reference, seed=seed, permutations=permutations)

    series = values.iloc[0]
    ordered = series.reindex(series.abs().sort_values(ascending=False).index)
    contributions = [
        FeatureContribution(
            feature=feature,
            label=schema.FEATURE_LABELS.get(feature, feature),
            value=target.iloc[0][feature],
            shap_value=float(shap_value),
        )
        for feature, shap_value in ordered.head(top_k).items()
    ]
    return CustomerExplanation(
        probability=float(model.predict_proba(target)[:, 1][0]),
        base_value=values.attrs["base_value"],
        contributions=contributions,
    )


def _format_value(contribution: FeatureContribution) -> str:
    value = contribution.value
    if contribution.feature == "tenure":
        return f"{value:.0f} months"
    if contribution.feature in {"MonthlyCharges", "TotalCharges"}:
        return f"${value:,.2f}"
    if contribution.feature == "SeniorCitizen":
        return "yes" if str(value) == "1" else "no"
    return str(value)


def narrate(explanation: CustomerExplanation) -> list[str]:
    """Turn contributions into plain sentences a retention agent can read.

    The wording is a correctness requirement, not a style choice: every
    sentence describes an association with the model's output, and the test
    suite asserts that causal phrasing never appears.
    """
    return [
        f"{c.label} ({_format_value(c)}) {c.direction} the estimated risk by "
        f"{abs(c.percentage_points):.1f} percentage points, relative to an average customer."
        for c in explanation.contributions
    ]


def compute_and_cache(cfg: Config | None = None, sample: int = DEFAULT_SAMPLE) -> Path:
    """Compute global importance once and cache it for the dashboard.

    The dashboard must not recompute SHAP on every rerun; this writes the
    ranking to ``artifacts/shap_global.json`` so the app reads a file.
    """
    cfg = cfg or load_config()
    from churnsense.data.loader import features_and_target, load_clean
    from churnsense.data.split import make_splits
    from churnsense.models.train import load_artifact

    model, meta = load_artifact(cfg.paths.artifacts_dir)
    df, _ = load_clean(cfg)
    X, y = features_and_target(df, cfg)
    splits = make_splits(X, y, cfg)

    explained = splits.X_val.sample(min(sample, len(splits.X_val)), random_state=cfg.random_seed)
    background = background_sample(splits.X_train, cfg.random_seed)
    importance = global_importance(model, explained, background=background, seed=cfg.random_seed)

    payload = {
        "model_key": meta["model_key"],
        "trained_at": meta["trained_at"],
        "n_explained": len(explained),
        "n_background": len(background),
        "permutations": PERMUTATIONS,
        "partition": "validation",
        "caveat": CAVEAT,
        "importance": importance.to_dict(orient="records"),
    }
    path = cfg.paths.shap_cache_file
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("cached global SHAP importance for %d features -> %s", len(importance), path.name)
    return path


def load_cached_importance(cfg: Config | None = None) -> pd.DataFrame | None:
    """Read the cached ranking, or ``None`` if it has not been computed."""
    cfg = cfg or load_config()
    path = cfg.paths.shap_cache_file
    if not path.is_file():
        return None
    return pd.DataFrame(json.loads(path.read_text(encoding="utf-8"))["importance"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute and cache global SHAP importance.")
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE,
        help="validation customers to explain (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    configure_logging()
    print(f"OK  {compute_and_cache(sample=args.sample)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
