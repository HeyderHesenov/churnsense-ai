"""The catalogue of model candidates to compare.

Four families, chosen to span the useful range rather than to be numerous:

* ``dummy`` - predicts the training prior. Not a formality: it fixes the floor
  that every other number is read against, and it is why this project never
  reports accuracy as a headline.
* ``logistic_regression`` - the interpretable linear baseline. On tabular data
  with strong categorical signal this is often competitive, and if it wins, it
  wins: a simpler model that ships and can be explained beats a marginally
  better one that cannot.
* ``random_forest`` - bagged trees, captures interactions, hard to overfit badly.
* ``hist_gradient_boosting`` - usually the strongest on tabular data of this
  size, and fast enough that the whole sweep runs in seconds.

Each entry is a full ``Pipeline`` so the preprocessing travels with the
estimator into cross-validation, the saved artifact and serving.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from churnsense.config import Config, load_config
from churnsense.features.preprocess import build_preprocessor_for

MODEL_KEYS: Final[tuple[str, ...]] = (
    "dummy",
    "logistic_regression",
    "random_forest",
    "hist_gradient_boosting",
)

# Ordered simplest -> most complex. Used by the one-standard-error selection
# rule to break statistical ties, so it encodes a modelling judgement and not
# just a display order: fewer fitted parameters, a directly readable decision
# function, and cheaper inference all count as "simpler".
COMPLEXITY_ORDER: Final[tuple[str, ...]] = (
    "dummy",
    "logistic_regression",
    "random_forest",
    "hist_gradient_boosting",
)

DISPLAY_NAMES: Final[dict[str, str]] = {
    "dummy": "Baseline (predicts the prior)",
    "logistic_regression": "Logistic Regression",
    "random_forest": "Random Forest",
    "hist_gradient_boosting": "Hist Gradient Boosting",
}

# Only the linear model needs scaled numerics. Trees are invariant to monotone
# rescaling, so fitting a scaler for them would add parameters that change no
# prediction - noise in the artifact and one more thing to explain.
_SCALES_NUMERICS: Final[frozenset[str]] = frozenset({"logistic_regression"})


def _classifier(key: str, seed: int) -> Any:
    """Construct the bare estimator for ``key``.

    Imbalance is handled by class weighting rather than resampling. At roughly
    1:2.8 the imbalance is moderate; weighting is leak-free inside CV (no
    synthetic rows crossing a fold boundary) and leaves the probability scale
    interpretable, which matters because the business layer consumes it.
    """
    match key:
        case "dummy":
            # "prior" predicts the training base rate for everyone: a flat
            # probability, zero discrimination, exactly the floor we want.
            return DummyClassifier(strategy="prior", random_state=seed)
        case "logistic_regression":
            return LogisticRegression(
                max_iter=2000, class_weight="balanced", random_state=seed, n_jobs=None
            )
        case "random_forest":
            return RandomForestClassifier(
                n_estimators=300,
                class_weight="balanced_subsample",
                random_state=seed,
                n_jobs=-1,
            )
        case "hist_gradient_boosting":
            # HistGradientBoosting has no class_weight; `balanced` computes the
            # same weights internally from the training labels.
            return HistGradientBoostingClassifier(
                random_state=seed, early_stopping=False, class_weight="balanced"
            )
        case _:
            raise KeyError(f"unknown model '{key}'; valid options are {list(MODEL_KEYS)}")


def build_candidate(key: str, columns: list[str], *, seed: int) -> Pipeline:
    """Return an unfitted ``preprocess -> classifier`` pipeline for ``key``."""
    classifier = _classifier(key, seed)  # raises before any other work on a bad key
    return Pipeline(
        [
            ("preprocess", build_preprocessor_for(columns, scale_numeric=key in _SCALES_NUMERICS)),
            ("classifier", classifier),
        ]
    )


def candidate_grid(key: str, cfg: Config | None = None) -> dict[str, list[Any]]:
    """Hyper-parameter grid for ``key``, taken from the config.

    Grids are declared in ``configs/config.yaml`` already prefixed with
    ``classifier__``; an unprefixed key would be silently ignored by
    ``GridSearchCV``, so the prefix is asserted in the test suite rather than
    patched in here.
    """
    cfg = cfg or load_config()
    return dict(cfg.training.grids.get(key, {}))


def iter_candidates(
    columns: list[str], cfg: Config | None = None
) -> Iterator[tuple[str, Pipeline, dict[str, list[Any]]]]:
    """Yield ``(key, pipeline, grid)`` for every candidate, in registry order."""
    cfg = cfg or load_config()
    for key in MODEL_KEYS:
        yield key, build_candidate(key, columns, seed=cfg.random_seed), candidate_grid(key, cfg)
