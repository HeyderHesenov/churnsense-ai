"""``make eda`` publishes what the public demo publishes about the training partition.

The EDA report and the association ranking are tracked files: they are read beside the
public API, by the same outsider. So their tables withhold the same groups
(``insights.guard_levels``) and the ranking's strengths are rounded where the exact value
could be inverted into a withheld count. Rendered here on the synthetic customers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churnsense import analysis as an
from churnsense import eda, insights
from churnsense.data import schema
from churnsense.data.loader import CleaningReport


@pytest.fixture(scope="module")
def train(public_parts, public_frame) -> pd.DataFrame:
    return public_frame.loc[public_parts.X_train.index]


@pytest.fixture(scope="module")
def report(train, cfg, tmp_path_factory) -> str:
    ranking = an.association_ranking(train.drop(columns=[schema.ID_COLUMN]))
    figures = {
        key: tmp_path_factory.mktemp("figures") / f"{key}.png"
        for key in (
            "contract",
            "tenure",
            "payment",
            "price",
            "addons",
            "heatmap",
            "association",
            "collinearity",
        )
    }
    cleaning = CleaningReport(len(train), len(train), 0, 0, 0, 0)
    return eda._markdown(train, cleaning, ranking, cfg, figures, n_file=len(train))


def test_two_year_contracts_churn_is_withheld_with_its_complement(report, train):
    section = report.split("## 3. Contract type", 1)[1].split("## 4.", 1)[0]
    lines = {
        row[0]: row
        for row in (
            [c.strip() for c in line.strip().strip("|").split("|")]
            for line in section.splitlines()
            if line.startswith("| ") and not line.startswith("| Contract")
        )
    }
    assert lines["Two year"][2] == lines["Two year"][3] == "*suppressed*"
    assert lines["One year"][2] == "*suppressed*"
    two = train[train["Contract"] == "Two year"]
    # Neither the count nor a ratio built on it is printed.
    assert f"{int(two[schema.TARGET].sum()):,} |" not in section
    assert "x difference" not in section


@pytest.mark.parametrize(
    "key", ["Contract", "PaymentMethod", "tenure", "monthly", "InternetService"]
)
def test_a_report_table_withholds_what_the_public_demo_withholds(train, public_book, key):
    """The report's tables, from the training frame, hide exactly what the demo's tables,
    from the book of the same customers, hide - every table guarded together in both."""
    column = next(c for c, k in eda._DIMENSION_OF.items() if k == key)
    frame = train
    if column == "tenure_bucket":
        frame = train.assign(tenure_bucket=an.tenure_bucket(train["tenure"]))
    if column == "charge_band":
        frame = train.assign(charge_band=an.charge_band(train["MonthlyCharges"]))
    table = eda.guarded(an.churn_rate_by(frame, column), key, insights.training_reasons(train))
    by_level = table.set_index(table[column].astype(str))
    demo = {c.row: c for c in insights.breakdown(public_book, key).row_totals}
    for level in insights.DIMENSIONS[key].levels:
        if level not in by_level.index:
            continue
        shown = not np.isnan(by_level.loc[level, "churned"])
        assert shown == (demo[level].rate is not None), (key, level, demo[level].reason)
        if shown:
            churned = by_level.loc[level, "churned"]
            assert churned == demo[level].churned
            assert churned >= insights.MIN_CELL
            assert by_level.loc[level, "customers"] - churned >= insights.MIN_CELL


def test_the_published_ranking_is_ordered_on_exact_strengths_then_rounded():
    ranking = pd.DataFrame(
        {
            "feature": ["a", "b", "c"],
            "measure": ["Cramer's V"] * 3,
            "strength": [0.4149, 0.41889026665374857, 0.2],
            "direction": ["n/a"] * 3,
        }
    )
    out = eda.published_ranking(ranking)
    assert out["feature"].tolist() == ["b", "a", "c"]
    assert out["strength"].tolist() == [0.42, 0.41, 0.2]


def test_no_strength_in_the_report_has_more_than_two_decimals(report):
    section = report.split("## 7. Ranked association", 1)[1].split("##", 1)[0]
    for line in section.splitlines():
        if line.startswith("| ") and not line.startswith("| Feature"):
            assert len(line.strip().strip("|").split("|")[-1].strip().split(".")[1]) == 2
    assert "measures 0." in report and "measures 0.0000" not in report


def test_the_newest_customers_are_described_without_a_count(report):
    point = report.split("`tenure == 0` customers cannot have churned", 1)[1].split("\n\n", 1)[0]
    assert not any(ch.isdigit() for ch in point.replace("tenure == 0", ""))


def test_a_report_of_a_book_too_small_to_show_any_price_band_still_renders(train, cfg, tmp_path):
    """Every price band withheld: the report says so, rather than failing on a peak of
    nothing (``idxmax`` of all-missing rates raises)."""
    small = train.head(60)
    ranking = an.association_ranking(small.drop(columns=[schema.ID_COLUMN]))
    figures = {
        key: tmp_path / f"{key}.png"
        for key in (
            "contract",
            "tenure",
            "payment",
            "price",
            "addons",
            "heatmap",
            "association",
            "collinearity",
        )
    }
    cleaning = CleaningReport(len(small), len(small), 0, 0, 0, 0)
    text = eda._markdown(small, cleaning, ranking, cfg, figures, n_file=len(small))
    assert "**not monotonic**" not in text
