"""Batch scoring from an uploaded CSV, plus export."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from app import data
from churnsense import viz
from churnsense.config import Config
from churnsense.data import schema
from churnsense.exceptions import ChurnSenseError, SchemaValidationError
from churnsense.models.predict import ignored_columns, validate_upload


def _template(inputs: list[str]) -> pd.DataFrame:
    """A two-row example with the exact columns an upload needs."""
    rows = [schema.EXAMPLE_HIGH_RISK, schema.EXAMPLE_LOW_RISK]
    return pd.DataFrame(rows).reindex(columns=inputs)


def with_predictions(
    cleaned: pd.DataFrame, predictions: pd.DataFrame, inputs: list[str]
) -> pd.DataFrame:
    """The scored file: identifiers, the model's ``inputs``, then its outputs.

    Only known columns travel to the export. Anything else in the upload is
    dropped rather than echoed back, so an unexpected column - personal data,
    or a header crafted to run as a spreadsheet formula - cannot ride along;
    the page lists what was dropped. Prediction columns already in the upload
    (a scored export uploaded again) are replaced, not duplicated: two
    ``flagged`` columns crashed the page.
    """
    keep = [c for c in (schema.ID_COLUMN, schema.TARGET) if c in cleaned.columns] + inputs
    return pd.concat([cleaned[keep], predictions], axis=1)


def _band_summary(scored: pd.DataFrame) -> go.Figure:
    order = [b for b in viz.RISK_BAND_COLORS if (scored["risk_band"] == b).any()]
    counts = scored["risk_band"].value_counts().reindex(order).fillna(0)
    figure = go.Figure(
        go.Bar(
            x=counts.index.astype(str),
            y=counts.to_numpy(),
            marker_color=ui.band_colors(order),
            text=[f"{int(c):,}" for c in counts],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>%{y:,} customers<extra></extra>",
        )
    )
    figure.update_layout(
        yaxis_title="Customers",
        xaxis_title=None,
        showlegend=False,
        margin={"l": 8, "r": 8, "t": 10, "b": 8},
    )
    return ui.add_label_headroom(figure, "y", 1.16)


def render(frame: pd.DataFrame, cfg: Config) -> None:  # noqa: ARG001 - dispatch signature
    st.header("Batch scoring and export")
    model = data.predictor()
    # The served model's own columns, not the config's: the two can drift
    # until the next `make train`, and the model is what checks the file.
    inputs = model.feature_columns

    st.markdown(
        "Upload a CSV of customers to score them with the shipped model. The file "
        "is validated before anything is predicted: file size, column count, "
        "column names, value ranges, category values and row count."
    )

    with st.expander("What the file must contain"):
        st.markdown(
            f"- **{len(inputs)} required columns**, exactly as named below\n"
            f"- at most **{cfg.api.max_batch_rows:,} rows**, "
            f"**{cfg.api.max_upload_columns:,} columns** and "
            f"**{cfg.api.max_upload_bytes / 1_048_576:.0f} MB**\n"
            "- `customerID` and `Churn` are optional; if present they are carried "
            "through to the output and ignored by the model (`Churn` must then be "
            "`Yes` or `No`); any other column is dropped from the output\n"
            "- every row is scored, duplicates included, in file order\n"
            "- numbers must be present and numeric, and `tenure` a whole number of "
            "months; the one blank accepted is `TotalCharges` where `tenure` is 0 "
            "(not yet billed, read as 0.00)\n"
            "- `gender` is **not** used: it is a protected attribute with no "
            "measurable signal in this dataset, so the model never sees it"
        )
        st.code(", ".join(inputs), language=None)
        ui.download_button(
            _template(inputs), "churnsense_template.csv", "Download a template CSV", "ba_template"
        )

    upload = st.file_uploader("Customer CSV", type=["csv"], key="ba_upload")
    if upload is None:
        st.info("Upload a CSV to score it, or download the template above to start from.")
        return

    payload = upload.getvalue()
    try:
        cleaned = validate_upload(payload, cfg)
        # Scored inside the try: the model checks its own feature columns too,
        # and if the config has drifted from the artifact that check fails here.
        scored = with_predictions(cleaned, model.predict_frame(cleaned), inputs)
    except SchemaValidationError as error:
        st.error("The file could not be used.")
        st.markdown(f"**{error.args[0].split(':')[0]}**")
        # Problems quote values from the uploaded file, so they are shown as
        # literal text: through st.markdown a crafted value would render as a
        # link or a remote image in the analyst's browser.
        st.code(
            "\n".join(f"- {p}" for p in error.problems or [str(error)]),
            language=None,
            wrap_lines=True,
        )
        st.caption(
            "Nothing was scored. Fix the file and upload it again - the dashboard "
            "will not guess at missing or malformed values."
        )
        return
    except ChurnSenseError:
        st.error("The file could not be processed. Check that it is a valid CSV.")
        return

    if dropped := ignored_columns(payload):
        st.info(
            f"{len(dropped):,} column(s) are outside the data contract: they were not "
            "used and are not in the scored file. Join results back on `customerID`, "
            "or by position: the scored file keeps the upload's row order."
        )
        # Names come from the file, so they are shown as literal text.
        st.code(", ".join(name[:40] for name in dropped[:20]), language=None, wrap_lines=True)

    ui.kpi_row(
        [
            ("Rows scored", f"{len(scored):,}", "all rows passed validation", "accent"),
            (
                "Flagged",
                f"{int(scored['flagged'].sum()):,}",
                f"at threshold {model.threshold:.2f}",
                "warn",
            ),
            (
                "Mean probability",
                ui.format_probability(scored["churn_probability"].mean()),
                "across the file",
                "",
            ),
            (
                "High or critical",
                f"{int(scored['risk_band'].isin(['High', 'Critical']).sum()):,}",
                "need review first",
                "crit",
            ),
        ]
    )

    st.markdown("")
    left, right = st.columns([1, 2])
    with left:
        ui.question("How does risk break down across the uploaded file?")
        ui.chart(_band_summary(scored), height=280, key="ba_bands")
    with right:
        display = [
            c
            for c in (
                "customerID",
                "churn_probability",
                "risk_band",
                "flagged",
                "Contract",
                "tenure",
                "MonthlyCharges",
            )
            if c in scored.columns
        ]
        st.dataframe(
            scored[display].sort_values("churn_probability", ascending=False),
            width="stretch",
            height=280,
            hide_index=True,
            column_config={
                "churn_probability": st.column_config.ProgressColumn(
                    "Churn probability", format="%.4f", min_value=0.0, max_value=1.0
                ),
                "customerID": st.column_config.TextColumn("Customer"),
                "risk_band": st.column_config.TextColumn("Risk band"),
                "flagged": st.column_config.CheckboxColumn("Flagged"),
                "tenure": st.column_config.NumberColumn("Tenure", format="%d mo"),
                "MonthlyCharges": st.column_config.NumberColumn("Monthly", format="$%.2f"),
            },
        )

    ui.download_button(
        scored, "churnsense_batch_predictions.csv", "Download scored file (CSV)", "ba_download"
    )
    ui.caveat(
        "Scored through the same code path as the API and the single-customer "
        "form. A row scored here and the same row POSTed to `/predict/batch` "
        "return identical probabilities."
    )
