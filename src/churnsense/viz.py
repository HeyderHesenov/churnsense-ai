"""Shared visual language for every chart in the project.

One palette, two renderers. The EDA report draws with matplotlib and the
dashboard draws with Plotly, so the tokens live here rather than in either
one - a chart must not mean one thing in the report and another in the app.

The categorical palette was **validated, not chosen by eye**, against the
dark card surface (#161b22) using the data-viz validator:

    lightness band  all 6 inside OKLCH L 0.48-0.67      PASS
    chroma floor    all 6 >= 0.1                        PASS
    CVD separation  worst adjacent dE 8.9 (tritan)      PASS  (>= 8 target)
    normal vision   worst adjacent dE 19.7              PASS  (>= 15 floor)
    contrast        all 6 >= 3:1 vs surface             PASS

Risk bands deliberately use the reserved **status** palette instead of
categorical slots, because "Low -> Critical" is a state, not an identity. The
warning/serious pair measures dE 13.6, below the 15 normal-vision floor, so
every risk mark carries a visible text label - colour never carries the
meaning alone. That pairing is the documented mitigation, not an oversight.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

_logger = logging.getLogger(__name__)

# --- Surfaces and ink -------------------------------------------------------
PAGE_BG: Final = "#0d1117"
SURFACE: Final = "#161b22"  # card / chart surface
SURFACE_RAISED: Final = "#1c2128"
BORDER: Final = "#2a3038"

INK: Final = "#e6edf3"  # 14.6:1 on SURFACE
INK_SECONDARY: Final = "#adb7c2"  # 8.5:1
INK_MUTED: Final = "#8b949e"  # 5.6:1
GRID: Final = "#21262d"
AXIS: Final = "#30363d"

# --- Categorical series, in fixed slot order (never cycled) -----------------
SERIES: Final[tuple[str, ...]] = (
    "#1897ae",  # 1 cyan    - primary accent
    "#c98500",  # 2 amber   - secondary accent
    "#5b8def",  # 3 blue
    "#d55181",  # 4 magenta
    "#9085e9",  # 5 violet
    "#3fa655",  # 6 green
)
ACCENT: Final = SERIES[0]
ACCENT_ALT: Final = SERIES[1]

# --- Reserved status colours; also the risk-band scale ----------------------
STATUS: Final[dict[str, str]] = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}
RISK_BAND_COLORS: Final[dict[str, str]] = {
    "Low": STATUS["good"],
    "Moderate": STATUS["warning"],
    "High": STATUS["serious"],
    "Critical": STATUS["critical"],
}

# --- Sequential (magnitude) and diverging (polarity) ------------------------
SEQUENTIAL_BLUE: Final[tuple[str, ...]] = (
    "#cde2fb",
    "#9ec5f4",
    "#6da7ec",
    "#3987e5",
    "#256abf",
    "#184f95",
    "#0d366b",
)


def figure(*args, **kwargs):
    """Create a themed matplotlib figure.

    Every figure in the project goes through here so the theme cannot be
    forgotten. It was, once: the evaluation figures silently rendered on
    matplotlib's default light background because `apply_matplotlib_theme`
    was only called in the EDA module.
    """
    import matplotlib.pyplot as plt

    apply_matplotlib_theme()
    return plt.subplots(*args, **kwargs)


def save_figure(fig, path: Path) -> Path:
    """Write a figure and close it, returning the path."""
    import matplotlib.pyplot as plt

    fig.savefig(path)
    plt.close(fig)
    _logger.info("wrote figure %s", path.name)
    return path


def money(amount: float, currency: str = "USD") -> str:
    """Format a monetary amount for display.

    Here rather than in the report or the figure module because both render
    the same numbers into the same artifact set - `make evaluate` writes
    final_evaluation.md and threshold_economics.png in one run - and two
    copies of the currency table meant they could disagree inside it. The
    dashboard reads it too, so `business.currency` finally means something
    everywhere instead of only in the reports.
    """
    symbol = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}.get(currency, f"{currency} ")
    return f"{symbol}{amount:,.0f}"


def rgba(hex_color: str, alpha: float) -> str:
    """A palette colour at reduced opacity, as a CSS/Plotly rgba() string.

    Exists so tints are derived from the palette rather than hand-written. A
    literal `rgba(201, 133, 0, 0.11)` elsewhere in the codebase is the same
    colour as ACCENT_ALT until someone changes one of them.
    """
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def apply_matplotlib_theme() -> None:
    """Set matplotlib rcParams for the report figures.

    Called once per figure-generating process. Recessive chrome, hairline
    grid on the y-axis only, no top/right spines - the data should be the
    highest-contrast thing in the frame.
    """
    import matplotlib as mpl

    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "savefig.bbox": "tight",
            "savefig.dpi": 140,
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 10,
            "text.color": INK,
            "axes.labelcolor": INK_SECONDARY,
            "axes.edgecolor": AXIS,
            "axes.titlecolor": INK,
            "axes.titlesize": 12,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.titlepad": 12,
            "axes.labelsize": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.frameon": False,
            "legend.labelcolor": INK_SECONDARY,
            "legend.fontsize": 9,
            "lines.linewidth": 2.0,
            "lines.markersize": 8,
            "axes.prop_cycle": mpl.cycler(color=list(SERIES)),
        }
    )


def plotly_template() -> dict:
    """Plotly layout template matching the matplotlib theme.

    Returned as a plain dict so this module stays importable without Plotly
    installed - the EDA report and the tests need the tokens, not the library.
    """
    return {
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "font": {
                "family": 'system-ui, -apple-system, "Segoe UI", sans-serif',
                "size": 13,
                "color": INK_SECONDARY,
            },
            "title": {"font": {"size": 15, "color": INK}, "x": 0, "xanchor": "left"},
            "colorway": list(SERIES),
            "xaxis": {
                "gridcolor": GRID,
                "zerolinecolor": AXIS,
                "linecolor": AXIS,
                "tickfont": {"color": INK_MUTED, "size": 11},
                "automargin": True,
            },
            "yaxis": {
                "gridcolor": GRID,
                "zerolinecolor": AXIS,
                "linecolor": AXIS,
                "tickfont": {"color": INK_MUTED, "size": 11},
                "automargin": True,
            },
            "legend": {
                "bgcolor": "rgba(0,0,0,0)",
                "font": {"color": INK_SECONDARY, "size": 11},
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "x": 0,
            },
            "hoverlabel": {
                "bgcolor": SURFACE_RAISED,
                "bordercolor": BORDER,
                "font": {"color": INK, "size": 12},
            },
            "margin": {"l": 8, "r": 8, "t": 48, "b": 8},
            "colorscale": {"sequential": [[i / 6, c] for i, c in enumerate(SEQUENTIAL_BLUE)]},
        }
    }
