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
from churnsense.models.predict import validate_upload


def _template(cfg: Config) -> pd.DataFrame:
    """A two-row example with the exact columns an upload needs."""
    columns = schema.model_input_columns(
        cfg.features.drop_columns, cfg.features.include_total_charges
    )
    rows = [
        {
            "SeniorCitizen": "0",
            "Partner": "No",
            "Dependents": "No",
            "tenure": 3,
            "PhoneService": "Yes",
            "MultipleLines": "No",
            "InternetService": "Fiber optic",
            "OnlineSecurity": "No",
            "OnlineBackup": "No",
            "DeviceProtection": "No",
            "TechSupport": "No",
            "StreamingTV": "Yes",
            "StreamingMovies": "Yes",
            "Contract": "Month-to-month",
            "PaperlessBilling": "Yes",
            "PaymentMethod": "Electronic check",
            "MonthlyCharges": 95.0,
            "TotalCharges": 285.0,
        },
        {
            "SeniorCitizen": "1",
            "Partner": "Yes",
            "Dependents": "Yes",
            "tenure": 64,
            "PhoneService": "Yes",
            "MultipleLines": "Yes",
            "InternetService": "DSL",
            "OnlineSecurity": "Yes",
            "OnlineBackup": "Yes",
            "DeviceProtection": "Yes",
            "TechSupport": "Yes",
            "StreamingTV": "No",
            "StreamingMovies": "No",
            "Contract": "Two year",
            "PaperlessBilling": "No",
            "PaymentMethod": "Credit card (automatic)",
            "MonthlyCharges": 68.3,
            "TotalCharges": 4371.2,
        },
    ]
    return pd.DataFrame(rows).reindex(columns=columns)


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


def render(frame: pd.DataFrame, cfg: Config) -> None:
    st.header("Batch scoring and export")
    model = data.predictor()

    st.markdown(
        "Upload a CSV of customers to score them with the shipped model. The file "
        "is validated before anything is predicted: column names, value ranges, "
        "category values, row count and file size."
    )

    with st.expander("What the file must contain"):
        required = schema.model_input_columns(
            cfg.features.drop_columns, cfg.features.include_total_charges
        )
        st.markdown(
            f"- **{len(required)} required columns**, exactly as named below\n"
            f"- at most **{cfg.api.max_batch_rows:,} rows** and "
            f"**{cfg.api.max_upload_bytes / 1_048_576:.0f} MB**\n"
            "- `customerID` and `Churn` are optional; if present they are carried "
            "through to the output and ignored by the model\n"
            "- `gender` is **not** used: it is a protected attribute with no "
            "measurable signal in this dataset, so the model never sees it"
        )
        st.code(", ".join(required), language=None)
        ui.download_button(
            _template(cfg), "churnsense_template.csv", "Download a template CSV", "ba_template"
        )

    upload = st.file_uploader("Customer CSV", type=["csv"], key="ba_upload")
    if upload is None:
        st.info("Upload a CSV to score it, or download the template above to start from.")
        return

    try:
        cleaned = validate_upload(upload.getvalue(), cfg)
    except SchemaValidationError as error:
        st.error("The file could not be used.")
        st.markdown(f"**{error.args[0].split(':')[0]}**")
        for problem in error.problems or [str(error)]:
            st.markdown(f"- {problem}")
        st.caption(
            "Nothing was scored. Fix the file and upload it again - the dashboard "
            "will not guess at missing or malformed values."
        )
        return
    except ChurnSenseError:
        st.error("The file could not be processed. Check that it is a valid CSV.")
        return

    predictions = model.predict_frame(cleaned)
    scored = pd.concat([cleaned, predictions], axis=1)

    if unseen := model.unseen_categories(cleaned):
        st.warning(
            "These values were not present in training, so predictions for the "
            "affected rows rest on unfamiliar input: "
            + "; ".join(f"**{column}**: {', '.join(values)}" for column, values in unseen.items())
        )

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
