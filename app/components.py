"""Small reusable pieces of the dashboard.

Kept separate from the sections so that a KPI tile, a chart frame and the
simulation disclaimer look the same everywhere. Each helper renders; none of
them computes anything, which keeps business logic out of the UI layer.
"""

from __future__ import annotations

import html
import numbers
from collections.abc import Iterable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from churnsense import viz

#: Repeated verbatim anywhere money appears. One string, one meaning.
#: Written as HTML, not markdown: it is injected into a styled <div>, and
#: Streamlit does not process markdown inside a raw HTML block - asterisks
#: would render literally.
SIMULATION_NOTE = (
    "<strong>Simulated under stated assumptions.</strong> These figures come "
    "from the assumptions on this page, not from measured campaign results. "
    "The dataset records no retention offers, so the success rate is an input, "
    "never an estimate."
)


def kpi(label: str, value: str, note: str = "", accent: str = "") -> None:
    """A single KPI tile. ``accent`` is one of '', 'accent', 'warn', 'crit'."""
    modifier = f" kpi-{accent}" if accent else ""
    st.markdown(
        f'<div class="kpi{modifier}">'
        f'<div class="kpi-label">{html.escape(label)}</div>'
        f'<div class="kpi-value">{html.escape(value)}</div>'
        f'<div class="kpi-note">{html.escape(note)}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def kpi_row(tiles: Iterable[tuple[str, str, str, str]]) -> None:
    """Render KPI tiles across equal columns."""
    tiles = list(tiles)
    for column, (label, value, note, accent) in zip(st.columns(len(tiles)), tiles, strict=True):
        with column:
            kpi(label, value, note, accent)


def question(text: str) -> None:
    """The business question a chart answers, shown above it.

    Every chart in this dashboard carries one. If a chart cannot be given a
    question, it is decoration and should be deleted instead.
    """
    st.markdown(f'<p class="question">{html.escape(text)}</p>', unsafe_allow_html=True)


def simulation_note() -> None:
    st.markdown(f'<div class="sim-note">{SIMULATION_NOTE}</div>', unsafe_allow_html=True)


def caveat(text: str) -> None:
    st.markdown(f'<p class="caveat">{html.escape(text)}</p>', unsafe_allow_html=True)


def band_chip(name: str) -> str:
    """Risk band as an HTML chip. The text carries the meaning; colour supports it."""
    safe = html.escape(str(name))
    return f'<span class="band band-{safe}">{safe}</span>'


def money(amount: float, currency: str = "USD") -> str:
    """Re-exported from the library so the dashboard honours the configured
    currency instead of hard-coding a dollar sign in a dozen places."""
    return viz.money(amount, currency)


def format_probability(value: float, *, saturation: float = 0.001) -> str:
    """Render a probability without overstating what the model knows.

    Isotonic calibration pushes a handful of customers to the very ends of the
    scale. Rounded to one decimal those read as "100.0%" and "0.0%", which
    claims a certainty no finite sample supports, so the extremes are shown as
    bounds instead of as exact values.
    """
    if value >= 1.0 - saturation:
        return f">{(1.0 - saturation):.1%}"
    if value <= saturation:
        return f"<{saturation:.1%}"
    return f"{value:.1%}"


def add_label_headroom(figure: go.Figure, axis: str = "x", factor: float = 1.18) -> go.Figure:
    """Stop `textposition="outside"` labels being clipped at the plot edge.

    Plotly clips an outside label that falls beyond the axis range, which
    silently truncates it - "$227,264" renders as "$227" and reads as a
    hundredfold error. Disabling clipping alone is not enough, because the text
    then overlaps the panel border, so the value axis also gets headroom.
    """
    figure.update_traces(cliponaxis=False, selector={"type": "bar"})

    values: list[float] = []
    for trace in figure.data:
        series = getattr(trace, axis, None)
        # `if series` would call bool() on a numpy array and raise; the axis
        # also holds category strings on the other orientation, hence the
        # numeric filter rather than a blanket conversion.
        if series is None:
            continue
        values.extend(abs(float(v)) for v in series if isinstance(v, numbers.Real))

    if values:
        figure.update_layout({f"{axis}axis": {"range": [0, max(values) * factor]}})
    return figure


def chart(figure: go.Figure, *, height: int = 320, key: str | None = None) -> None:
    """Render a Plotly figure with consistent sizing and no modebar clutter."""
    figure.update_layout(height=height)
    st.plotly_chart(
        figure,
        width="stretch",
        key=key,
        config={
            "displaylogo": False,
            "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
        },
    )


def model_missing(message: str) -> None:
    """The state shown when no artifact is available.

    Deliberately a dead end rather than a degraded dashboard: showing empty
    charts next to live-looking filters invites someone to read zeros as data.
    """
    st.error("No trained model is available, so nothing on this page can be computed.")
    st.code("make data && make train && make evaluate && make explain", language="bash")
    st.caption(message)
    st.stop()


def download_button(frame: pd.DataFrame, filename: str, label: str, key: str) -> None:
    """CSV export. Index is dropped so the file opens cleanly in a spreadsheet."""
    st.download_button(
        label=label,
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        key=key,
    )


def band_colors(names: Iterable[str]) -> list[str]:
    """Status colours for risk bands, in the order given."""
    return [viz.RISK_BAND_COLORS.get(str(n), viz.INK_MUTED) for n in names]


def empty_filter_state() -> None:
    st.info("No customers match the current filters. Widen them to see results.")
    st.stop()
