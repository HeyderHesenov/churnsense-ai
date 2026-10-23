"""The dashboard's endpoints, mounted under ``/api``.

Thin by design: every figure comes from ``churnsense.insights``,
``churnsense.book``, ``churnsense.reports`` or the explainability cache, and a
route only validates the request, picks the data and serialises the result.
Every probability comes from ``churnsense.models.predict``, the one inference
path, so a number on a page equals the number ``POST /predict`` returns.

Three rules hold on every route here:

* no test-partition outcome is ever serialised - outcomes come through
  ``churnsense.book``, which withholds them;
* no rate is returned for fewer than 20 customers;
* every error has the ``ErrorResponse`` shape and names no file or path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import StringConstraints, ValidationError

from churnsense import insights, reports
from churnsense.api import dashboard_schemas as out
from churnsense.api.schemas import (
    BusinessAssumptions,
    ContractValue,
    CustomerFeatures,
    ErrorResponse,
    InternetServiceValue,
    PaymentMethodValue,
)
from churnsense.api.state import (
    Dashboard,
    Unavailable,
    explanation_slot,
    get_dashboard,
    optional_dashboard,
    require_predictor,
)
from churnsense.api.uploads import read_upload
from churnsense.config import Business, load_config
from churnsense.data import schema
from churnsense.evaluation.threshold import DISCLAIMER, ThresholdScenario, scenario_at
from churnsense.exceptions import DataError
from churnsense.explainability.shap_explain import (
    CAVEAT,
    PERMUTATIONS,
    load_cached_explanations,
    read_cache,
)
from churnsense.exports import csv_bytes
from churnsense.models.predict import ignored_columns

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

#: The largest gap between base value + contributions and the served
#: probability an explanation may show. SHAP's efficiency makes it exact up to
#: floating point; anything bigger means the explanation is of something else.
ADDITIVITY_TOLERANCE = 1e-9

_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (400, 403, 422, 500, 503)
}
_ID = r"^[A-Za-z0-9-]{1,32}$"
CustomerId = Annotated[str, StringConstraints(pattern=_ID)]
BandName = Annotated[str, StringConstraints(max_length=16)]


class OutcomesWithheldError(Unavailable):
    """The partitions could not be verified, so no outcome-based figure exists."""

    def __init__(self, reason: str | None) -> None:
        super().__init__(
            "outcomes_withheld",
            f"Historical outcomes are withheld: {reason or 'the partitions are unverified.'}",
        )


def _invalid(location: str, message: str) -> RequestValidationError:
    """A query-parameter error in the same shape FastAPI gives its own."""
    return RequestValidationError([{"loc": ("query", location), "msg": message, "type": "value"}])


def _py(value: Any) -> Any:
    """A numpy scalar as the Python value Pydantic and JSON expect."""
    return value.item() if isinstance(value, np.generic) else value


def _assumptions(business: Business) -> BusinessAssumptions:
    return BusinessAssumptions.model_validate(business, from_attributes=True)


def _cell(cell: insights.Cell) -> out.ReportedCell | out.SuppressedCell:
    if cell.suppressed:
        return out.SuppressedCell(row=cell.row, column=cell.column, n=cell.customers)
    return out.ReportedCell(
        row=cell.row,
        column=cell.column,
        n=cell.customers,
        churned=cell.churned,
        rate=cell.rate,
        lift=cell.lift,
    )


def _scenario(s: ThresholdScenario) -> out.Scenario:
    return out.Scenario.model_validate(s, from_attributes=True)


def _points(sweep: pd.DataFrame) -> list[out.SweepPoint]:
    columns = list(out.SweepPoint.model_fields)
    return [out.SweepPoint(**row) for row in sweep[columns].to_dict(orient="records")]


def _report(reader: Callable[[Path], pd.DataFrame | None], directory: Path) -> pd.DataFrame | None:
    """A tracked report, or ``None`` - also when it is malformed, which is logged, not raised."""
    try:
        return reader(directory)
    except (DataError, ValueError, OSError) as exc:
        logger.warning("a tracked report is unreadable: %s", exc)
        return None


def _association(d: Dashboard) -> list[out.Association] | None:
    ranking = _report(reports.association_ranking, d.reports_dir)
    if ranking is None:
        return None
    return [
        out.Association(feature=r.feature, measure=r.measure, strength=r.strength)
        for r in ranking.itertuples()
    ]


def _importance(d: Dashboard) -> list[out.Importance] | None:
    cached = read_cache(d.shap_cache, d.model.meta.get("model_sha256", ""))
    try:
        return None if cached is None else [out.Importance(**r) for r in cached["importance"]]
    except (KeyError, TypeError, ValidationError):
        logger.warning("the SHAP cache's importance ranking is malformed; ignoring it")
        return None


def recorded_evaluation(reports_dir: Path, model_sha256: str) -> out.HoldoutEvaluation | None:
    """The test evaluation ``make evaluate`` recorded for this artifact, or ``None``.

    The record is a published report - aggregates only, the same figures
    ``reports/final_evaluation.md`` prints - and is shown as recorded, never
    recomputed. A record that is missing, describes another artifact or is
    malformed reads as "not recorded": a broken report must not take a page
    down. Segments of fewer than 20 customers lose their rates here.
    """
    try:
        record = reports.final_evaluation(reports_dir, model_sha256)
        if record is None:
            return None
        segments = [
            {k: row[k] for k in out.HoldoutSegment.model_fields}
            | (
                {}
                if row["customers"] >= insights.MIN_CELL
                else dict.fromkeys(
                    ("churners", "churn_rate", "flagged_rate", "precision", "recall")
                )
            )
            for row in record["segments"]
        ]
        return out.HoldoutEvaluation(
            evaluated_at=record["evaluated_at"],
            metrics=record["metrics"],
            ece=record["ece"],
            no_skill_accuracy=record["no_skill_accuracy"],
            test_optimal_threshold=record["test_optimal_threshold"],
            segments=segments,
        )
    except (DataError, KeyError, TypeError, ValueError, OSError) as exc:
        logger.warning("the recorded test evaluation is unreadable: %s", exc.__class__.__name__)
        return None


def _detail(d: Dashboard, row: pd.Series) -> out.CustomerDetail:
    features = {c: _py(row[c]) for c in CustomerFeatures.model_fields}
    return out.CustomerDetail(
        id=row[schema.ID_COLUMN],
        features=CustomerFeatures(**features),
        churn_probability=float(row["churn_probability"]),
        risk_band=str(row["risk_band"]),
        flagged=bool(row["flagged"]),
        threshold=d.model.threshold,
        partition=row["partition"],
        outcome=row["outcome"],
    )


# --- overview --------------------------------------------------------------------------


def _segment_tab(d: Dashboard, key: str) -> out.SegmentTab | None:
    split = insights.breakdown(d.book, key)
    if split is None:
        return None
    reported = [c for c in split.row_totals if c.rate is not None]
    top = max(reported, key=lambda c: c.rate, default=None)
    low = min(reported, key=lambda c: c.rate, default=None)
    return out.SegmentTab(
        key=key,
        partition_customers=split.partition_customers,
        base_rate=split.base_rate,
        levels=[_cell(c) for c in split.row_totals],
        spread=top.rate / low.rate if top and low and low.rate else None,
        peak=top.row if top else None,
    )


@router.get("/overview", response_model=out.OverviewResponse, responses=_ERRORS)
def overview(d: Annotated[Dashboard, Depends(get_dashboard)]) -> out.OverviewResponse:
    """Everything the executive overview shows, for the whole book.

    What the book determines is computed once per served book; what the
    tracked reports say is read on each request, so a re-run of ``make
    explain`` shows without a restart.
    """
    cfg = d.config
    lifecycle = d.memo("lifecycle", lambda: insights.lifecycle(d.book))
    tabs = d.memo(
        "segment_tabs",
        lambda: [tab for key in insights.OVERVIEW_SEGMENTS if (tab := _segment_tab(d, key))],
    )
    economics = d.economics(cfg.business)
    campaign = None
    if economics is not None:
        shipped = scenario_at(economics.sweep, d.model.threshold, economics.business)
        campaign = out.Campaign(
            threshold=d.model.threshold,
            customers=economics.customers,
            flagged=shipped.flagged,
            precision=shipped.precision,
            recall=shipped.recall,
            intervention_cost=shipped.intervention_cost,
            retained_value=shipped.retained_value,
            net_benefit=shipped.net_benefit,
            peak_threshold=economics.peak.threshold,
            peak_net_benefit=economics.peak.net_benefit,
            recommended_threshold=economics.recommended.threshold,
            assumptions=_assumptions(cfg.business),
            curve=[
                out.CurvePoint(threshold=t, net_benefit=n)
                for t, n in zip(
                    economics.sweep["threshold"], economics.sweep["net_benefit"], strict=True
                )
            ],
        )
    association = _association(d)
    return out.OverviewResponse(
        customers=len(d.frame),
        monthly_charges=float(d.frame["MonthlyCharges"].sum()),
        threshold=d.model.threshold,
        observed=d.memo("observed", lambda: insights.observed_overall(d.book)),
        high_or_critical=d.memo("high", lambda: insights.high_or_critical(d.book, cfg)),
        flagged=d.memo("flagged", lambda: insights.flagged_share(d.book)),
        bands=d.memo("bands", lambda: insights.band_summaries(d.book, cfg)),
        histogram=d.memo("histogram", lambda: insights.probability_histogram(d.book, cfg)),
        segments=tabs,
        lifecycle=None
        if lifecycle is None
        else out.Lifecycle(
            partition_customers=lifecycle.partition_customers,
            base_rate=lifecycle.base_rate,
            buckets=[_cell(c) for c in lifecycle.buckets],
            median_tenure_churned=lifecycle.median_tenure_churned,
            median_tenure_stayed=lifecycle.median_tenure_stayed,
        ),
        importance=_importance(d),
        association=association,
        campaign=campaign,
        withheld_reason=d.book.withheld,
    )


# --- customers ----------------------------------------------------------------------------


def customer_filter(
    d: Annotated[Dashboard, Depends(get_dashboard)],
    q: Annotated[str, Query(max_length=32, pattern=r"^[A-Za-z0-9-]*$")] = "",
    bands: Annotated[list[BandName] | None, Query(max_length=8)] = None,
    contract: ContractValue | None = None,
    internet: InternetServiceValue | None = None,
    payment: PaymentMethodValue | None = None,
    tenure_min: Annotated[int | None, Query(ge=0, le=120)] = None,
    tenure_max: Annotated[int | None, Query(ge=0, le=120)] = None,
    flagged: bool | None = None,
    partition: out.Partition | None = None,
) -> insights.CustomerFilter:
    """The filters a customer listing and its export share, validated."""
    names = {band.name for band in d.config.risk_bands}
    if bands is not None and (unknown := sorted(set(bands) - names)):
        raise _invalid("bands", f"unknown risk band(s) {unknown[:5]}; expected {sorted(names)}")
    if tenure_min is not None and tenure_max is not None and tenure_min > tenure_max:
        raise _invalid("tenure_min", "tenure_min must not exceed tenure_max")
    return insights.CustomerFilter(
        query=q,
        bands=tuple(bands) if bands is not None else None,
        contract=contract,
        internet=internet,
        payment=payment,
        tenure_min=tenure_min,
        tenure_max=tenure_max,
        flagged=flagged,
        partition=partition,
    )


SortKey = Literal["id", "probability", "tenure", "monthly", "at_risk"]
Direction = Literal["asc", "desc"]


@router.get("/customers", response_model=out.CustomerPage, responses=_ERRORS)
def customers(
    d: Annotated[Dashboard, Depends(get_dashboard)],
    selection: Annotated[insights.CustomerFilter, Depends(customer_filter)],
    sort: SortKey = "probability",
    direction: Annotated[Direction, Query(alias="dir")] = "desc",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> out.CustomerPage:
    """One page of the scored book, filtered and sorted, with a summary of every match.

    A page past the end is clamped to the last one, so a stale link still
    shows rows. ``outcome`` is ``"withheld"`` for every test customer.
    """
    matches = insights.select(d.frame, selection)
    pages = max(1, -(-len(matches) // page_size))
    page = min(page, pages)
    rows = insights.ordered(matches, sort, direction == "desc").iloc[
        (page - 1) * page_size : page * page_size
    ]
    return out.CustomerPage(
        total=len(matches),
        page=page,
        page_size=page_size,
        pages=pages,
        rows=[
            out.CustomerRow(
                id=r[schema.ID_COLUMN],
                contract=r["Contract"],
                internet_service=r["InternetService"],
                payment_method=r["PaymentMethod"],
                tenure=int(r["tenure"]),
                monthly_charges=float(r["MonthlyCharges"]),
                churn_probability=float(r["churn_probability"]),
                risk_band=str(r["risk_band"]),
                flagged=bool(r["flagged"]),
                partition=r["partition"],
                outcome=r["outcome"],
                at_risk=float(r["at_risk"]),
            )
            for _, r in rows.iterrows()
        ],
        summary=insights.summarise(matches, len(d.frame), d.book.outcomes),
        withheld_reason=d.book.withheld,
    )


@router.get(
    "/customers/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **_ERRORS},
)
def export_customers(
    d: Annotated[Dashboard, Depends(get_dashboard)],
    selection: Annotated[insights.CustomerFilter, Depends(customer_filter)],
    ids: Annotated[list[CustomerId] | None, Query(max_length=200)] = None,
    sort: SortKey = "probability",
    direction: Annotated[Direction, Query(alias="dir")] = "desc",
) -> Response:
    """The filtered book, or exactly the listed customers, as CSV; every cell spreadsheet-safe.

    ``ids`` and the filters are alternatives: both at once is a 422 rather
    than a guess at which wins. The outcome column reads ``withheld`` for
    every test customer.
    """
    if ids is not None:
        if selection != insights.CustomerFilter():
            raise _invalid("ids", "list customers by ids or filter the book, not both")
        selection = insights.CustomerFilter(ids=tuple(ids))
    matches = insights.ordered(insights.select(d.frame, selection), sort, direction == "desc")
    body = csv_bytes(insights.export_frame(matches, d.model.feature_columns))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="churnsense_customers.csv"'},
    )


@router.get("/customers/examples", response_model=out.ExamplesResponse, responses=_ERRORS)
def example_customers(d: Annotated[Dashboard, Depends(get_dashboard)]) -> out.ExamplesResponse:
    """The README example and one validation customer per case the single-customer page compares."""
    chosen = [d.customer(cid) for cid in insights.example_customers(d.book)]
    return out.ExamplesResponse(
        readme_example=CustomerFeatures(**schema.EXAMPLE_HIGH_RISK),
        customers=[_detail(d, row) for row in chosen if row is not None],
    )


@router.get(
    "/customers/{customer_id}",
    response_model=out.CustomerDetail,
    responses={404: {"model": ErrorResponse}, **_ERRORS},
)
def customer(
    d: Annotated[Dashboard, Depends(get_dashboard)],
    customer_id: Annotated[str, PathParam(pattern=_ID)],
) -> out.CustomerDetail:
    """One customer's inputs and served prediction; the outcome only if it may be shown."""
    row = d.customer(customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No customer with that ID.")
    return _detail(d, row)


# --- explanations ----------------------------------------------------------------------------


def _explanation(
    d: Dashboard, record: pd.DataFrame, served: float, customer_id: str | None
) -> out.Explanation:
    """``record`` explained, checked against ``served``: the probability ``Predictor`` gave it."""
    result = d.explain(record)
    total = result.base_value + sum(c.shap_value for c in result.contributions)
    gap = abs(total - served)
    if not gap <= ADDITIVITY_TOLERANCE:
        # Never serve an explanation that does not add up to the served score.
        logger.error("explanation additivity gap %.3g exceeds tolerance", gap)
        raise RuntimeError("explanation does not reproduce the served probability")
    return out.Explanation(
        customer_id=customer_id,
        churn_probability=served,
        base_value=result.base_value,
        contributions=[
            out.Contribution(
                feature=c.feature,
                label=c.label,
                value=_py(c.value) if c.feature in schema.NUMERIC_COLUMNS else str(c.value),
                shap_value=c.shap_value,
            )
            for c in result.contributions
        ],
    )


@router.post(
    "/explain",
    response_model=out.ExplainResponse,
    responses={404: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, **_ERRORS},
)
def explain(
    request: out.ExplainRequest, d: Annotated[Dashboard, Depends(get_dashboard)]
) -> out.ExplainResponse:
    """Every feature's signed contribution to the served probability.

    Measured against the same 100 training customers, with the same seed and
    number of orderings, on every page. These attribute the model's output;
    they say nothing about why a customer stays or leaves.
    """
    with explanation_slot():
        if request.customer_ids is not None:
            rows = {cid: d.customer(cid) for cid in request.customer_ids}
            if missing := [cid for cid, row in rows.items() if row is None]:
                raise HTTPException(
                    status_code=404, detail=f"No customer with ID(s) {missing[:5]}."
                )
            explanations = [
                _explanation(
                    d,
                    d.model.model_inputs(row.to_frame().T),
                    float(row["churn_probability"]),
                    cid,
                )
                for cid, row in rows.items()
            ]
        else:
            record = pd.DataFrame([request.record.model_dump()])
            served = float(d.model.predict_frame(record)["churn_probability"].iloc[0])
            explanations = [_explanation(d, d.model.model_inputs(record), served, None)]
    return out.ExplainResponse(
        permutations=PERMUTATIONS,
        seed=d.config.random_seed,
        background_customers=len(d.background) if d.background is not None else 0,
        explanations=explanations,
        caveat=CAVEAT,
    )


@router.get(
    "/explain/global",
    response_model=out.GlobalExplanation,
    responses=_ERRORS,
)
def global_explanation(d: Annotated[Dashboard, Depends(get_dashboard)]) -> out.GlobalExplanation:
    """``make explain``'s attributions for a validation sample, as the explainability page draws them."""
    sha = d.model.meta.get("model_sha256", "")
    cached = load_cached_explanations(d.shap_cache, sha)
    if cached is None:
        raise Unavailable(
            "explanations_not_cached",
            "No attributions are cached for the served model. Run `make explain`.",
        )
    ranking = _report(reports.association_ranking, d.reports_dir)
    rank = None
    if ranking is not None:
        model_features = ranking[ranking["feature"].isin(cached["features"])]
        rank = {f: i + 1 for i, f in enumerate(model_features["feature"])}
    return out.GlobalExplanation(
        model_sha256=sha,
        partition=cached["partition"],
        n_explained=cached["n_explained"],
        n_background=cached["n_background"],
        permutations=cached["permutations"],
        seed=cached.get("seed"),
        base_value=cached["base_value"],
        max_additivity_gap=cached["max_additivity_gap"],
        features=cached["features"],
        labels={f: schema.FEATURE_LABELS.get(f, f) for f in cached["features"]},
        importance=[out.Importance(**row) for row in cached["importance"]],
        values=[[round(v, 6) for v in row] for row in cached["values"]],
        x=cached["x"],
        association_rank=rank,
        total_charges_r=insights.collinearity(d.book),
        caveat=CAVEAT,
    )


# --- patterns --------------------------------------------------------------------------------

DimensionKey = Literal[tuple(insights.DIMENSIONS)]  # type: ignore[valid-type]


@router.get("/segments", response_model=out.SegmentsResponse, responses=_ERRORS)
def segments(
    d: Annotated[Dashboard, Depends(get_dashboard)],
    rows: DimensionKey = "Contract",
    cols: DimensionKey | None = None,
) -> out.SegmentsResponse:
    """A one- or two-way breakdown of the training partition, small cells suppressed."""
    if cols == rows:
        raise _invalid("cols", "rows and cols must be different dimensions")
    split = insights.breakdown(d.book, rows, cols)
    if split is None:
        raise OutcomesWithheldError(d.book.withheld)
    return out.SegmentsResponse(
        rows=split.rows,
        columns=split.columns,
        partition_customers=split.partition_customers,
        partition_churned=split.partition_churned,
        base_rate=split.base_rate,
        row_totals=[_cell(c) for c in split.row_totals],
        column_totals=[_cell(c) for c in split.column_totals],
        cells=[_cell(c) for c in split.cells],
        ranked=[_cell(c) for c in split.ranked],
        association=_association(d),
    )


# --- threshold economics ----------------------------------------------------------------------


@router.get("/threshold/sweep", response_model=out.SweepResponse, responses=_ERRORS)
def threshold_sweep(
    d: Annotated[Dashboard, Depends(get_dashboard)],
    cost: Annotated[float | None, Query(ge=0, le=10_000)] = None,
    success: Annotated[float | None, Query(ge=0, le=1)] = None,
    horizon: Annotated[int | None, Query(ge=1, le=120)] = None,
    margin: Annotated[float | None, Query(ge=0, le=1)] = None,
) -> out.SweepResponse:
    """The validation partition swept across thresholds under the given assumptions.

    Unset assumptions take the configured defaults. The recommendation is the
    repository's own rule: the highest threshold within one offer's cost of
    the best simulated net benefit. Every money figure is SIMULATED.
    """
    base = d.config.business
    business = Business(
        currency=base.currency,
        retention_offer_cost=base.retention_offer_cost if cost is None else cost,
        offer_success_rate=base.offer_success_rate if success is None else success,
        expected_horizon_months=base.expected_horizon_months if horizon is None else horizon,
        gross_margin=base.gross_margin if margin is None else margin,
    )
    economics = d.economics(business)
    if economics is None:
        raise OutcomesWithheldError(d.book.withheld)
    return out.SweepResponse(
        assumptions=_assumptions(business),
        customers=economics.customers,
        churners=economics.churners,
        shipped_threshold=d.model.threshold,
        points=_points(economics.sweep),
        recommended=_scenario(economics.recommended),
        peak=_scenario(economics.peak),
        band=out.IndifferenceBand(
            low=economics.band_low, high=economics.band_high, floor=economics.band_floor
        ),
        sensitivity=[
            out.Sensitivity(**row) for row in economics.sensitivity.to_dict(orient="records")
        ],
        disclaimer=DISCLAIMER,
    )


# --- performance ----------------------------------------------------------------------------------


def _at_half(metrics: dict | None) -> out.AtHalf | None:
    if not metrics:
        return None
    return out.AtHalf(
        precision=metrics["precision"], recall=metrics["recall"], brier=metrics["brier"]
    )


@router.get("/performance", response_model=out.PerformanceResponse, responses=_ERRORS)
def performance(d: Annotated[Dashboard, Depends(get_dashboard)]) -> out.PerformanceResponse:
    """Selection, calibration and the validation sweep, plus the recorded test evaluation.

    No figure here is computed from the test partition: the test block is the
    record ``make evaluate CONFIRM=yes`` wrote for this very artifact, or
    nothing.
    """
    meta = d.model.meta
    selection = meta.get("selection")
    comparison = _report(reports.model_comparison, d.reports_dir)
    economics = d.economics(d.config.business)
    record = recorded_evaluation(d.reports_dir, meta.get("model_sha256", ""))
    return out.PerformanceResponse(
        display_name=meta["display_name"],
        threshold=d.model.threshold,
        selection=None
        if not selection
        else out.Selection(
            rule=selection["rule"],
            metric=meta["cv_metric"],
            folds=meta["cv_folds"],
            leader=selection["leader"],
            standard_error=selection["standard_error"],
            cutoff=selection["cutoff"],
            eligible=selection["eligible"],
        ),
        candidates=None
        if comparison is None
        else [
            out.Candidate(**row)
            for row in comparison[list(out.Candidate.model_fields)].to_dict(orient="records")
        ],
        calibration=out.Calibration(
            method=meta.get("calibration_method"),
            epsilon=meta.get("probability_epsilon"),
            uncalibrated=_at_half(meta.get("candidate_validation_metrics")),
            calibrated=_at_half(meta.get("validation_metrics")),
            uncalibrated_ece=meta.get("uncalibrated_ece"),
            calibrated_ece=meta.get("calibrated_ece"),
        ),
        bands=d.memo("bands", lambda: insights.band_summaries(d.book, d.config)),
        validation=None
        if economics is None
        else out.ValidationCurve(
            customers=economics.customers,
            churners=economics.churners,
            points=_points(economics.sweep),
        ),
        test=record,
        test_reason=None
        if record is not None
        else (
            "No test evaluation is recorded for this model. "
            "It is written by `make evaluate CONFIRM=yes`."
        ),
        withheld_reason=d.book.withheld,
    )


# --- batch scoring ------------------------------------------------------------------------------


def _notes(frame: pd.DataFrame, ignored: list[str]) -> list[str]:
    """What the page says about the upload's extra columns: known ones, then unknown ones."""
    notes = []
    if "gender" in frame.columns:
        notes.append("gender is ignored: excluded from the model as a protected attribute")
    if schema.TARGET in frame.columns:
        notes.append(
            "Churn is never used to score; it is kept only to compare against the predictions"
        )
    if ignored:
        shown = ", ".join(c[:40] for c in ignored[:6])
        notes.append(f"ignored unknown column(s): {shown}{'…' if len(ignored) > 6 else ''}")
    return notes


def _held_out(frame: pd.DataFrame, inputs: pd.DataFrame, d: Dashboard) -> pd.Series:
    """Which uploaded rows are held-out test customers (or unverified ones), by ID or by inputs.

    Matched on the customer ID - ignoring case and surrounding space - and,
    whatever the ID says, on the exact inputs the model scores: dropping or
    renaming the ID column does not turn a test customer into a labelled one.
    A stranger who happens to share every input with a held-out customer is
    left out too; leaving a row out only narrows the comparison.
    """
    held = d.frame[d.frame["partition"].isin(("test", "unknown"))]
    ids = set(held[schema.ID_COLUMN].str.strip().str.upper())
    columns = list(inputs.columns)
    fingerprints = set(d.model.model_inputs(held).itertuples(index=False, name=None))
    by_input = pd.Series(
        [row in fingerprints for row in inputs[columns].itertuples(index=False, name=None)],
        index=inputs.index,
    )
    if schema.ID_COLUMN not in frame.columns:
        return by_input
    by_id = frame[schema.ID_COLUMN].astype(str).str.strip().str.upper().isin(ids)
    return by_id | by_input


def _label_check(frame: pd.DataFrame, flagged: pd.Series, held_out: pd.Series) -> out.LabelCheck:
    """Precision and recall on the file's own labels, held-out test customers left out."""
    kept = ~held_out
    y, f = frame.loc[kept, schema.TARGET].astype(int), flagged[kept]
    tp, fp, fn = int((f & y.eq(1)).sum()), int((f & y.eq(0)).sum()), int((~f & y.eq(1)).sum())
    return out.LabelCheck(
        labelled=int(kept.sum()),
        excluded_test=int(held_out.sum()),
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
    )


@router.post(
    "/batch/score",
    response_model=out.BatchScoreResponse,
    responses={413: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, **_ERRORS},
)
def batch_score(
    file: Annotated[UploadFile, File(description="CSV with the feature columns")],
    d: Annotated[Dashboard | None, Depends(optional_dashboard)],
) -> out.BatchScoreResponse:
    """Score a CSV upload for the batch page: every row, with the inputs it was scored on.

    The same body limit, the same ``validate_upload`` contract and the same
    ``Predictor`` as ``POST /predict/batch``; only the response carries more,
    so the page can show each row and export it. Nothing is written to disk.
    """
    cfg = load_config()
    predictor = require_predictor()
    payload, frame = read_upload(file, cfg)
    scored = predictor.predict_frame(frame)
    inputs = predictor.model_inputs(frame)
    has_id, has_label = schema.ID_COLUMN in frame.columns, schema.TARGET in frame.columns
    labels, labels_reason = None, None
    # Without the book no row can be told apart from a test customer, so no
    # label is compared or echoed back at all (see the label condition below).
    held_out = pd.Series(False, index=frame.index)
    if has_label and d is None:
        labels_reason = (
            "The dataset is not available, so customers from the held-out test partition "
            "could not be told apart. Labels are not compared or shown."
        )
    elif has_label and d is not None:
        held_out = _held_out(frame, inputs, d)
        labels = _label_check(frame, scored["flagged"], held_out)
    n = len(scored)
    counts = scored["risk_band"].value_counts()
    rows = [
        out.BatchRow(
            row=int(index),
            customer_id=(str(frame.at[index, schema.ID_COLUMN]) or None) if has_id else None,
            label=int(frame.at[index, schema.TARGET])
            if has_label and d is not None and not held_out.at[index]
            else None,
            features={c: _py(v) for c, v in inputs.loc[index].items()},
            churn_probability=float(s.churn_probability),
            risk_band=str(s.risk_band),
            flagged=bool(s.flagged),
        )
        for index, s in zip(scored.index, scored.itertuples(index=False), strict=True)
    ]
    return out.BatchScoreResponse(
        columns=len(frame.columns) + len(ignored_columns(payload)),
        threshold=predictor.threshold,
        rows=rows,
        summary=out.BatchSummary(
            rows=n,
            flagged=int(scored["flagged"].sum()),
            flagged_share=float(scored["flagged"].mean()) if n else None,
            mean_probability=float(scored["churn_probability"].mean()) if n else None,
            expected_churners=float(scored["churn_probability"].sum()),
            at_risk=float((scored["churn_probability"] * frame["MonthlyCharges"]).sum()),
            bands=[
                out.BandCount(name=band.name, customers=int(counts.get(band.name, 0)))
                for band in cfg.risk_bands
            ],
            labels=labels,
            labels_reason=labels_reason,
        ),
        notes=_notes(frame, ignored_columns(payload)),
    )
