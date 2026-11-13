"""Bring a published evidence file to the current rules, from the file itself alone.

``make demo-data`` computes the public demo's aggregates from the dataset. When the
rules for what may be published only get stricter, the file already written holds
everything needed to comply: every change is a figure withheld or two intervals
merged into one. This command makes those changes and nothing else:

* the validation sweep is kept at the cuts that keep the rule read together with the
  release reports' thresholds (``score_disclosure.legacy_holds``), and the calibration
  bins are merged onto those cuts (customers, churners and the customer-weighted mean
  score add up exactly);
* the training tables are guarded again, all of them as one system
  (``insights.guard_published``, ``churnsense.joint_disclosure``): a group with fewer
  than ``MIN_CELL`` churners or stayers, and Two year's churn, hidden with whatever in
  any table - a one-way margin included - would give them back; a hidden group's reason
  becomes one its size or the rule already states (format 4: ``too_few``,
  ``cross_partition`` or ``withheld``);
* the result is parsed and audited exactly as the builder's output is, then written
  in one step.

It reads no dataset, scores nobody, fits nothing and evaluates nothing; a figure it
cannot derive from the file, it withholds. ``python -m churnsense.demo.migrate``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from churnsense import evidence, insights, legacy_disclosures, score_disclosure
from churnsense.config import load_config
from churnsense.evaluation.threshold import SweepBasis
from churnsense.exceptions import ChurnSenseError, DemoDataError
from churnsense.logging_setup import configure_logging

if TYPE_CHECKING:
    from churnsense.models.predict import Predictor

logger = logging.getLogger(__name__)

#: The formats this command reads: the current one is migrated to itself, which changes nothing.
READS = (2, 3, evidence.FORMAT)


def _basis(record: dict[str, Any]) -> SweepBasis:
    return SweepBasis(**{key: tuple(v) if isinstance(v, list) else v for key, v in record.items()})


def merged_reliability(bins: list[dict[str, Any]], cuts: tuple[float, ...]) -> list[dict[str, Any]]:
    """The calibration bins between ``cuts``, each the sum of the old bins inside it."""
    out = []
    for i, low in enumerate(cuts):
        high = 1.0 if i + 1 == len(cuts) else cuts[i + 1]
        inside = [b for b in bins if b["low"] >= low - 1e-9 and (b["high"] <= high + 1e-9)]
        if (
            not inside
            or not math.isclose(inside[0]["low"], low, abs_tol=1e-9)
            or not math.isclose(inside[-1]["high"], high, abs_tol=1e-9)
        ):
            raise DemoDataError("the calibration bins do not line up with the new cuts")
        customers = sum(b["customers"] for b in inside)
        churned = sum(b["churned"] for b in inside)
        mean = sum(b["mean_predicted"] * b["customers"] for b in inside) / customers
        out.append(
            {
                "low": float(low),
                "high": float(high),
                "customers": customers,
                "mean_predicted": mean,
                "churned": churned,
                "rate": churned / customers,
            }
        )
    return out


def migrated(body: dict[str, Any], model: Predictor) -> dict[str, Any]:
    """``body`` under the current rules: fewer cuts, more hidden, nothing new."""
    if type(body.get("format")) is not int or body["format"] not in READS:
        raise DemoDataError("not a published evidence file this command can migrate")
    out = json.loads(evidence.dumps(body))
    out["format"] = evidence.FORMAT
    released = legacy_disclosures.describes(out["identity"])
    if out["sweep_basis"] is not None:
        basis = _basis(out["sweep_basis"])
        legacy = evidence.legacy_for(out["identity"], basis.churners)
        cuts = score_disclosure.safe_cuts(
            basis,
            required=(model.threshold,),
            preferred=(
                insights.economics_from(basis, model.config.business).peak.threshold,
                score_disclosure.AT_HALF,
            ),
            legacy=legacy,
        )
        if cuts is None:
            out["sweep_basis"], out["validation"] = None, None
        else:
            out["sweep_basis"] = _plain_basis(score_disclosure.restrict(basis, cuts))
            if out["validation"] is not None:
                out["validation"]["reliability"] = merged_reliability(
                    out["validation"]["reliability"], cuts
                )
    insights.guard_published(out["breakdowns"], out["lifecycle"])
    stated = _stated(out) if released and out["sweep_basis"] is not None else None
    return evidence.audited(out, model, None, stated, released)


def _plain_basis(basis: SweepBasis) -> dict[str, Any]:
    return {key: list(v) if isinstance(v, tuple) else v for key, v in asdict(basis).items()}


def _stated(body: dict[str, Any]) -> dict[float, tuple[int, int]]:
    """The release reports' figures at the thresholds where they fix a count exactly.

    The migration reads no dataset, so the exact churners at a threshold are known only where
    the printed precision and recall allow a single count; the audit treats what it is given
    as exact, so an ambiguous threshold is left out rather than guessed. Those thresholds are
    still judged - over their whole range, at its worst - by the cut rule
    (``score_disclosure.legacy_holds``), and the released file is audited at the true counts
    where the local sweep exists (``tests/test_cross_source_disclosure.py``).
    """
    churners = body["sweep_basis"]["true_positives"][0] + body["sweep_basis"]["false_negatives"][0]
    out = {}
    for point in legacy_disclosures.LEGACY_SWEEP:
        low, high = legacy_disclosures.churner_range(point, churners)
        if low != high:
            continue  # an ambiguous count is no exact fact; the cut rule checks its range
        out[point.threshold] = (point.flagged, low)
    return out


def migrate_file(path: Path, model: Predictor) -> dict[str, Any]:
    """Read ``path``, migrate it and write it back in one step; the migrated body."""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DemoDataError(
            f"the published evidence could not be read: {exc.__class__.__name__}"
        ) from exc
    result = migrated(body, model)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(evidence.dumps(result), encoding="utf-8")
    temporary.replace(path)
    return result


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0]).parse_args(argv)
    configure_logging()
    from churnsense.models.predict import load_predictor

    try:
        cfg = load_config()
        # The model's metadata and configuration only: nothing is scored.
        model = replace(load_predictor(cfg.paths.artifacts_dir), config=cfg)
        result = migrate_file(model.source / evidence.EVIDENCE_FILE, model)
    except ChurnSenseError as exc:
        logger.error("nothing was written: %s", exc)
        return 1
    basis = result["sweep_basis"]
    logger.info(
        "published evidence migrated to format %d: %s",
        result["format"],
        "no sweep" if basis is None else f"sweep at {len(basis['thresholds'])} cuts",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
