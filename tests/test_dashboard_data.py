"""The dashboard never shows the test partition's outcomes.

Synthetic data only: nothing here reads the real dataset or its partitions.

The checks read what the data layer returns - the frame every section
receives, the outcomes kept apart from it, the explorer's table and the bytes
of its CSV - and then what the running app renders from them. Each aggregate
is compared with the same figure computed on training and validation rows
alone, and checked to differ from the figure over every row, so a test label
slipping in would change the answer.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace

import pandas as pd
import pytest
from app import components, data
from app.sections.explorer import TABLE_COLUMNS, selection_table

from churnsense import analysis as an
from churnsense.data import schema
from tests.conftest import SYNTHETIC_SHA256


def _shown_rows(clean_frame: pd.DataFrame, parts) -> pd.DataFrame:
    return clean_frame.loc[parts.X_train.index.union(parts.X_val.index)]


def _test_ids(clean_frame: pd.DataFrame, parts) -> set[str]:
    return set(clean_frame.loc[parts.X_test.index, schema.ID_COLUMN])


# --- the data layer ----------------------------------------------------------


def test_the_frame_sections_receive_has_no_outcome(book):
    assert schema.TARGET not in book.frame.columns
    assert "outcome" not in book.frame.columns
    assert book.withheld is None


def test_test_labels_never_enter_the_shown_outcomes(book, clean_frame, dashboard_parts):
    shown = book.outcomes
    assert set(shown.index) == set(_shown_rows(clean_frame, dashboard_parts)[schema.ID_COLUMN])
    assert not set(shown.index) & _test_ids(clean_frame, dashboard_parts)
    expected = clean_frame.set_index(schema.ID_COLUMN).loc[shown.index, schema.TARGET]
    assert shown.equals(expected)


def test_test_rows_are_still_scored(book, dashboard_predictor, clean_frame, dashboard_parts):
    """Withholding the outcome must not withhold the prediction."""
    test = book.frame.loc[dashboard_parts.X_test.index]
    assert test["partition"].eq("test").all()
    expected = dashboard_predictor.predict_frame(clean_frame.loc[test.index])
    pd.testing.assert_series_equal(test["churn_probability"], expected["churn_probability"])


def test_outcome_aggregates_count_training_and_validation_only(book, clean_frame, dashboard_parts):
    """What Overview and Churn patterns compute, on the frame they get."""
    seen = data.labelled(book.frame, shown=book.outcomes)
    shown = _shown_rows(clean_frame, dashboard_parts)
    assert len(seen) == len(shown)
    assert seen[schema.TARGET].sum() == shown[schema.TARGET].sum()
    assert seen[schema.TARGET].sum() != clean_frame[schema.TARGET].sum(), "test is not sensitive"
    pd.testing.assert_frame_equal(
        an.churn_rate_by(seen, "Contract"), an.churn_rate_by(shown, "Contract")
    )


def test_a_section_that_skips_labelled_fails_loudly(book):
    """No outcome column to count by mistake - an error, never a wrong rate."""
    with pytest.raises(KeyError):
        an.churn_rate_by(book.frame, "Contract")


def test_labelled_narrows_to_one_partition_and_refuses_the_test_one(book, dashboard_parts):
    validation = data.labelled(book.frame, "validation", shown=book.outcomes)
    assert set(validation.index) == set(dashboard_parts.X_val.index)
    with pytest.raises(ValueError, match="only for"):
        data.labelled(book.frame, "test", shown=book.outcomes)


def test_labelled_refuses_a_frame_from_another_book(book):
    """Outcomes join on customerID; rows it cannot place are an error, not a guess."""
    stranger = book.frame.assign(**{schema.ID_COLUMN: "DEMO-UNKNOWN"})
    with pytest.raises(KeyError, match="not from this book"):
        data.labelled(stranger, shown=book.outcomes)
    with pytest.raises(KeyError):
        data.labelled(book.frame.drop(columns="partition"), shown=book.outcomes)


def _swapped(clean_frame: pd.DataFrame, parts) -> pd.DataFrame:
    """Two churners' rows exchanged, one from test and one from training.

    The label sequence is unchanged, so the rebuilt split and its digest are too.
    """
    test_churner = parts.y_test[parts.y_test.eq(1)].index[0]
    train_row = parts.y_train[parts.y_train.eq(1)].index[0]
    order = clean_frame.index.to_list()
    i, j = order.index(test_churner), order.index(train_row)
    order[i], order[j] = order[j], order[i]
    return clean_frame.loc[order].set_axis(clean_frame.index)


#: Each way the served model's provenance can fail, and the reason a page must give.
BROKEN_PROVENANCE = [
    ("dataset hash missing", "no dataset hash"),
    ("dataset hash null", "no dataset hash"),
    ("dataset hash malformed", "invalid dataset hash"),
    ("dataset hash not text", "invalid dataset hash"),
    ("current file unhashed", "dataset unhashed"),
    ("re-pinned file", "dataset differs"),
    ("split digest missing", "no split digest"),
    ("split digest malformed", "invalid split digest"),
    ("another split", "split differs"),
    ("repeated id", "split failed"),
]


def _break_provenance(case: str, clean_frame: pd.DataFrame, parts, meta: dict):
    """The customers, the current file's hash and the model's metadata for ``case``."""
    customers, sha = clean_frame, SYNTHETIC_SHA256
    match case:
        case "dataset hash missing":
            meta.pop("data_sha256")
        case "dataset hash null":
            meta["data_sha256"] = None
        case "dataset hash malformed":
            meta["data_sha256"] = "not-a-sha256"
        case "dataset hash not text":
            meta["data_sha256"] = int(SYNTHETIC_SHA256, 16)
        case "current file unhashed":
            sha = None
        case "re-pinned file":
            customers = _swapped(clean_frame, parts)
            sha = hashlib.sha256(b"re-pinned").hexdigest()
        case "split digest missing":
            meta.pop("split_digest")
        case "split digest malformed":
            meta["split_digest"] = meta["split_digest"][:-1]
        case "another split":
            meta["split_digest"] = "0" * 64
        case "repeated id":
            customers = clean_frame.assign(
                **{schema.ID_COLUMN: clean_frame[schema.ID_COLUMN].iloc[0]}
            )
        case _:
            raise AssertionError(f"unknown case {case!r}")
    return customers, sha, meta


@pytest.mark.parametrize(
    ("case", "reason"), BROKEN_PROVENANCE, ids=[case for case, _ in BROKEN_PROVENANCE]
)
def test_untrusted_partitions_withhold_every_outcome(
    case: str, reason: str, clean_frame, dashboard_predictor, dashboard_parts
):
    """Partitions are trusted only for the file and the split the model was trained on.

    Each failure gives its own reason: a model that recorded no hash is not
    told its data "differs", and an exception's text never reaches the page.
    A re-pinned file with two rows exchanged keeps the row count, the label
    sequence and the split digest; only its hash tells it apart, and a test
    customer would otherwise be shown as a training one.
    """
    customers, sha, meta = _break_provenance(
        case, clean_frame, dashboard_parts, dict(dashboard_predictor.meta)
    )
    model = replace(dashboard_predictor, meta=meta)
    book = data.score_book(customers, sha, model)

    assert book.withheld == data.WITHHELD_REASONS[reason]
    assert book.frame["partition"].eq("unknown").all()
    assert book.outcomes.empty
    assert book.train_index.empty
    assert data.labelled(book.frame, shown=book.outcomes).empty
    assert selection_table(book.frame, book.outcomes)["outcome"].eq(data.WITHHELD).all()
    # Withholding the outcomes never withholds the scores.
    pd.testing.assert_series_equal(
        book.frame["churn_probability"], model.predict_frame(customers)["churn_probability"]
    )


def test_every_withheld_reason_is_distinct_path_free_and_actionable():
    reasons = list(data.WITHHELD_REASONS.values())
    assert len(set(reasons)) == len(reasons)
    for reason in reasons:
        assert "/" not in reason and "\\" not in reason, reason
        assert "`make " in reason, reason


# --- what the explorer shows and exports --------------------------------------


def test_the_explorer_table_has_an_explicit_schema_and_no_test_outcome(
    book, clean_frame, dashboard_parts
):
    table = selection_table(book.frame, book.outcomes)
    assert list(table.columns) == TABLE_COLUMNS
    assert table["churn_probability"].is_monotonic_decreasing
    is_test = table[schema.ID_COLUMN].isin(_test_ids(clean_frame, dashboard_parts))
    assert table.loc[is_test, "outcome"].eq(data.WITHHELD).all()
    truth = clean_frame.set_index(schema.ID_COLUMN)[schema.TARGET].map(data.OUTCOMES)
    shown = table.loc[~is_test]
    assert shown["outcome"].tolist() == truth.loc[shown[schema.ID_COLUMN]].tolist()


def test_the_explorer_csv_holds_no_test_outcome(book, clean_frame, dashboard_parts):
    payload = components.csv_bytes(selection_table(book.frame, book.outcomes))
    exported = pd.read_csv(io.BytesIO(payload), dtype=str)

    assert list(exported.columns) == TABLE_COLUMNS
    is_test = exported[schema.ID_COLUMN].isin(_test_ids(clean_frame, dashboard_parts))
    assert is_test.sum() == len(dashboard_parts.X_test)
    assert exported.loc[is_test, "outcome"].eq(data.WITHHELD).all()
    assert exported["churn_probability"].notna().all()


# --- the running app ------------------------------------------------------------


def _kpi(at, label: str) -> tuple[str, str]:
    """The rendered value and note of the KPI tile called ``label``."""
    for block in at.markdown:
        if f'<div class="kpi-label">{label}</div>' in block.value:
            value = block.value.split('<div class="kpi-value">')[1].split("</div>")[0]
            note = block.value.split('<div class="kpi-note">')[1].split("</div>")[0]
            return value, note
    raise AssertionError(f"no KPI called {label!r}")


def test_the_overview_rate_covers_training_and_validation_only(
    dashboard, clean_frame, dashboard_parts
):
    shown = _shown_rows(clean_frame, dashboard_parts)
    value, note = _kpi(dashboard("Executive overview"), "Observed churn rate")
    assert value == f"{shown[schema.TARGET].mean():.1%}"
    assert note.startswith(f"{shown[schema.TARGET].sum():,} of {len(shown):,} ")


def test_the_explorer_shows_and_filters_without_test_outcomes(
    dashboard, book, clean_frame, dashboard_parts
):
    at = dashboard("Customer explorer")
    table = at.dataframe[0].value
    assert list(table.columns) == TABLE_COLUMNS
    is_test = table[schema.ID_COLUMN].isin(_test_ids(clean_frame, dashboard_parts))
    assert table.loc[is_test, "outcome"].eq(data.WITHHELD).all()
    assert table["churn_probability"].is_monotonic_decreasing

    at.multiselect(key="filter_Contract").set_value(["Two year"]).run()
    narrowed = at.dataframe[0].value
    assert len(narrowed) == book.frame["Contract"].eq("Two year").sum()
    assert narrowed["Contract"].eq("Two year").all()
    is_test = narrowed[schema.ID_COLUMN].isin(_test_ids(clean_frame, dashboard_parts))
    assert narrowed.loc[is_test, "outcome"].eq(data.WITHHELD).all()
