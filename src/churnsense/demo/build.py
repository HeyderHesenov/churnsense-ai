"""Build what a public demo serves from: ``make demo-data``.

A public demo holds no dataset. It lists generated customers and shows, as
evidence about the model, what the dataset's training and validation
partitions showed - as aggregates. This command writes both, once, beside the
model they belong to:

* ``public_evidence.json`` - the aggregates, computed here from the dataset by
  the very functions the dataset-backed service calls, with every small group
  already hidden (``churnsense.evidence``). No customer is in it.
* ``demo_explanations.json`` - attributions of generated customers against a
  background of generated customers. Nothing of the dataset is in it.

It reads what the service reads at every start: the dataset, through
``churnsense.book.load_book``, which rebuilds the partitions and withholds the
test partition's outcomes. It fits nothing, evaluates nothing on the test
partition, and changes no existing file. It prints counts, never a row.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from churnsense import book as books
from churnsense import evidence
from churnsense.config import load_config
from churnsense.demo.customers import demo_customers, records_sha256
from churnsense.exceptions import ChurnSenseError, DemoDataError
from churnsense.explainability.shap_explain import (
    DEFAULT_SAMPLE,
    PERMUTATIONS,
    background_sample,
    explanation_cache,
)
from churnsense.logging_setup import configure_logging

if TYPE_CHECKING:
    from churnsense.config import Config
    from churnsense.models.predict import Predictor

logger = logging.getLogger(__name__)


def evidence_of(model: Predictor, book: books.Book, shap_cache: Path) -> dict[str, Any]:
    """The published aggregates of ``book``, the dataset's own, for ``model``."""
    return evidence.published_body(evidence.LiveEvidence(book, model, shap_cache), model)


def explanations_of(
    model: Predictor,
    customers: pd.DataFrame | None = None,
    *,
    sample: int = DEFAULT_SAMPLE,
    permutations: int = PERMUTATIONS,
) -> dict[str, Any]:
    """Attributions of a sample of the generated customers, against generated customers.

    The same seed, sample rule and background rule as ``make explain``; only the
    customers differ. The result names the book it explains by hash, and the
    service refuses it for any other.
    """
    generated = demo_customers() if customers is None else customers
    features = books.synthetic_book(generated, model).frame[model.feature_columns]
    seed = model.config.random_seed
    explained = features.sample(min(sample, len(features)), random_state=seed)
    payload = explanation_cache(
        model.model,
        model.meta,
        explained,
        background_sample(features, seed),
        seed=seed,
        permutations=permutations,
        partition=books.SYNTHETIC,
    )
    return payload | {"records_sha256": records_sha256(generated)}


def shared_rows(model: Predictor, book: books.Book, customers: pd.DataFrame) -> int:
    """How many generated customers have exactly a dataset customer's model inputs."""
    real = set(model.model_inputs(book.frame).itertuples(index=False, name=None))
    generated = model.model_inputs(books.synthetic_book(customers, model).frame)
    return sum(row in real for row in generated.itertuples(index=False, name=None))


def _write(path: Path, text: str) -> None:
    """All of the file or none of it: a reader never sees half."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write(directory: Path, published: dict[str, Any], explanations: dict[str, Any]) -> None:
    """Write the two files into ``directory``, beside the model they are bound to."""
    _write(directory / evidence.EVIDENCE_FILE, evidence.dumps(published))
    _write(directory / evidence.DEMO_EXPLANATIONS_FILE, evidence.dumps(explanations))


def publish(
    cfg: Config, *, sample: int = DEFAULT_SAMPLE, permutations: int = PERMUTATIONS
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute both files for ``cfg``'s model and dataset and write them beside the model.

    Raises ``ChurnSenseError`` - and writes nothing - when the dataset is not
    the one the model was trained on, when ``cfg`` is itself a public demo's
    (it holds no dataset to read), or when a generated customer has exactly a
    dataset customer's inputs.
    """
    from churnsense.models.predict import load_predictor

    if cfg.deployment.synthetic:
        raise DemoDataError(
            "the evidence is built from the dataset: run this with the development "
            "configuration (deployment.customer_records: dataset)"
        )
    model = replace(load_predictor(cfg.paths.artifacts_dir), config=cfg)
    book = books.load_book(model)
    generated = demo_customers()
    if shared := shared_rows(model, book, generated):
        raise DemoDataError(
            f"{shared} generated customer(s) have a dataset customer's exact inputs; "
            "choose another seed in churnsense.demo.customers"
        )
    published = evidence_of(model, book, cfg.paths.shap_cache_file)
    explanations = explanations_of(model, generated, sample=sample, permutations=permutations)
    write(model.source, published, explanations)
    return published, explanations


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0]).parse_args(argv)
    configure_logging()
    try:
        published, explanations = publish(load_config())
    except ChurnSenseError as exc:
        logger.error("nothing was written: %s", exc)
        return 1
    logger.info(
        "published evidence of %d dataset customers (%d breakdowns) and attributions of "
        "%d generated customers",
        published["dataset_customers"],
        len(published["breakdowns"]),
        explanations["n_explained"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
