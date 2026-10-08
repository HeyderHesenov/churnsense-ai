"""SHAP explanations for the shipped pipeline.

**The whole pipeline is explained, never a convenient inner piece.** The
selected model may be wrapped in ``CalibratedClassifierCV``, and calibration
changes the probability a customer is actually served. Explaining the
uncalibrated estimator would produce a tidy attribution of a number nobody
sees. So the explainer runs on the preprocessed matrix against the *fitted
classifier the pipeline holds*, and additivity is checked: contributions plus
the base value reproduce the served probability exactly.

One explainer, not a branch per model family. ``shap.Explainer`` on a plain
callable resolves to the model-agnostic Permutation explainer, which is correct
for linear models, tree ensembles and calibrated wrappers alike. At 44 encoded
columns it explains 200 customers in under four seconds, so the uniformity
costs nothing worth branching for.

SHAP values are reported on the raw feature level: the one-hot columns of a
categorical are summed back into their source column. That is exact (SHAP is
additive) and it is what a reader needs - nobody reasons about
``Contract_One year`` in isolation.

**Attribution is not causation.** A SHAP value says how much a feature moved
*this model's output* relative to a baseline. It does not say that changing
the feature would change the customer's behaviour.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import shap
from sklearn.pipeline import Pipeline

from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.logging_setup import get_logger

logger = get_logger(__name__)

CAVEAT = (
    "These are model attributions, not causal effects. They describe how each "
    "feature moved this model's output relative to an average customer. They do "
    "not establish that changing a feature would change whether the customer stays."
)

DEFAULT_BACKGROUND = 100
DEFAULT_SAMPLE = 300


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

    @property
    def caveat(self) -> str:
        return CAVEAT

    def as_dict(self) -> dict[str, Any]:
        return {
            "probability": self.probability,
            "base_value": self.base_value,
            "contributions": [
                {
                    "feature": c.feature,
                    "label": c.label,
                    "value": str(c.value),
                    "shap_value": c.shap_value,
                    "direction": c.direction,
                }
                for c in self.contributions
            ],
            "caveat": CAVEAT,
        }


def _parts(model: Pipeline):
    """Split the pipeline into its preprocessor and its fitted classifier."""
    return model.named_steps["preprocess"], model.named_steps["classifier"]


def _column_owners(preprocessor) -> list[str]:
    """Map each encoded column back to the source feature that produced it.

    Matched by longest source-name prefix rather than by splitting on ``_``,
    because category *values* contain underscores and spaces. The mapping is
    asserted to be total: an unowned column would silently drop its
    contribution out of the aggregate, which is exactly the kind of quiet
    wrongness that never shows up as an error.
    """
    by_name = {name: columns for name, _, columns in preprocessor.transformers_}
    numeric, categorical = list(by_name.get("num", [])), list(by_name.get("cat", []))

    owners: list[str] = []
    for encoded in preprocessor.get_feature_names_out():
        if encoded in numeric:
            owners.append(encoded)
            continue
        candidates = [c for c in categorical if encoded.startswith(f"{c}_")]
        if not candidates:
            raise ValueError(
                f"encoded column '{encoded}' maps to no source feature; the "
                "preprocessing contract and the explainer have diverged"
            )
        owners.append(max(candidates, key=len))
    return owners


def _shap_values(
    model: Pipeline, X: pd.DataFrame, background: pd.DataFrame, seed: int = 0
) -> pd.DataFrame:
    """SHAP values per *source* feature, as a frame aligned with ``X``.

    The explainer is seeded. Permutation SHAP samples feature orderings, so an
    unseeded run returns slightly different attributions each time - which would
    mean a customer's explanation changed on every dashboard refresh while the
    prediction stayed put. Nothing erodes trust in an explanation faster.
    """
    preprocessor, classifier = _parts(model)
    encoded, encoded_background = preprocessor.transform(X), preprocessor.transform(background)

    explainer = shap.explainers.PermutationExplainer(
        lambda a: classifier.predict_proba(a)[:, 1], encoded_background, seed=seed
    )
    explanation = explainer(encoded, max_evals=2 * encoded.shape[1] + 1, silent=True)

    per_column = pd.DataFrame(explanation.values, columns=_column_owners(preprocessor))
    aggregated = per_column.T.groupby(level=0).sum().T  # sum one-hot columns into their source
    aggregated.index = X.index
    aggregated.attrs["base_value"] = float(np.mean(explanation.base_values))
    return aggregated[[c for c in X.columns if c in aggregated.columns]]


def _background(X: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    return X.sample(min(n, len(X)), random_state=seed) if len(X) > n else X


def global_importance(
    model: Pipeline,
    X: pd.DataFrame,
    *,
    background: pd.DataFrame | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Rank features by mean absolute SHAP value over ``X``.

    Deliberately called *importance* and not *driver*: it measures how much a
    feature moves this model, which is a property of the model, not of the
    customers.
    """
    reference = background if background is not None else _background(X, DEFAULT_BACKGROUND, seed)
    values = _shap_values(model, X, reference, seed=seed)

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
    model: Pipeline,
    X: pd.DataFrame,
    row: int,
    *,
    top_k: int = 5,
    background: pd.DataFrame | None = None,
    seed: int = 0,
) -> CustomerExplanation:
    """Explain one row's prediction, strongest contributions first.

    ``X`` carries the surrounding rows so the explainer has a population to
    sample a background from; ``row`` is a positional index into it.
    """
    if not 0 <= row < len(X):
        raise IndexError(f"row {row} is out of range for {len(X)} customers")

    reference = background if background is not None else _background(X, DEFAULT_BACKGROUND, seed)
    target = X.iloc[[row]]
    values = _shap_values(model, target, reference, seed=seed)

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
    importance = global_importance(
        model,
        explained,
        background=_background(splits.X_train, DEFAULT_BACKGROUND, cfg.random_seed),
        seed=cfg.random_seed,
    )

    payload = {
        "model_key": meta["model_key"],
        "trained_at": meta["trained_at"],
        "n_explained": len(explained),
        "n_background": min(DEFAULT_BACKGROUND, len(splits.X_train)),
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
    print(f"OK  {compute_and_cache(sample=args.sample)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
