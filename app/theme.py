"""Dashboard theme: CSS tokens and the shared Plotly template.

Colour values are not defined here. They come from ``churnsense.viz``, which
is also what the report figures use, so a risk band cannot be amber in the
dashboard and orange in the PDF someone pasted it into.

The design target is a **data-dense executive dashboard**: tight padding, many
small panels, numbers as the loudest element on the page. Streamlit's defaults
are generous with whitespace in a way that suits a form and wastes a
dashboard, so the CSS below mostly reclaims space and quietens chrome.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from churnsense import viz

TEMPLATE_NAME = "churnsense"


def register_plotly_template() -> str:
    """Register the shared Plotly template once per process."""
    if TEMPLATE_NAME not in pio.templates:
        pio.templates[TEMPLATE_NAME] = go.layout.Template(viz.plotly_template())
    pio.templates.default = TEMPLATE_NAME
    return TEMPLATE_NAME


def _css() -> str:
    return f"""
    <style>
      :root {{
        --bg:        {viz.PAGE_BG};
        --surface:   {viz.SURFACE};
        --raised:    {viz.SURFACE_RAISED};
        --border:    {viz.BORDER};
        --ink:       {viz.INK};
        --ink-2:     {viz.INK_SECONDARY};
        --ink-muted: {viz.INK_MUTED};
        --accent:    {viz.ACCENT};
        --accent-2:  {viz.ACCENT_ALT};
        --good:      {viz.STATUS["good"]};
        --warning:   {viz.STATUS["warning"]};
        --serious:   {viz.STATUS["serious"]};
        --critical:  {viz.STATUS["critical"]};
      }}

      .stApp {{ background: var(--bg); }}
      section[data-testid="stSidebar"] {{
        background: var(--surface);
        border-right: 1px solid var(--border);
      }}
      .block-container {{ padding: 1.6rem 2.2rem 3rem; max-width: 1500px; }}

      h1, h2, h3, h4 {{ color: var(--ink); letter-spacing: -0.012em; }}
      h1 {{ font-size: 1.55rem; font-weight: 650; }}
      h2 {{ font-size: 1.12rem; font-weight: 620; margin: 0.4rem 0 0.2rem; }}
      h3 {{ font-size: 0.97rem; font-weight: 600; }}
      p, li, label, .stMarkdown {{ color: var(--ink-2); line-height: 1.55; }}

      /* --- KPI tile ------------------------------------------------------ */
      .kpi {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.85rem 1rem 0.9rem;
        height: 100%;
      }}
      .kpi-label {{
        color: var(--ink-muted);
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.055em;
        text-transform: uppercase;
      }}
      .kpi-value {{
        color: var(--ink);
        font-size: 1.75rem;
        font-weight: 640;
        line-height: 1.15;
        margin-top: 0.3rem;
      }}
      .kpi-note {{ color: var(--ink-muted); font-size: 0.76rem; margin-top: 0.22rem; }}
      .kpi-accent {{ border-left: 3px solid var(--accent); }}
      .kpi-warn   {{ border-left: 3px solid var(--warning); }}
      .kpi-crit   {{ border-left: 3px solid var(--critical); }}

      /* --- Panels and callouts ------------------------------------------- */
      .panel {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 1rem 1.15rem;
      }}
      .question {{
        color: var(--ink-muted);
        font-size: 0.8rem;
        margin: 0 0 0.35rem;
        font-style: italic;
      }}
      .sim-note {{
        background: rgba(201, 133, 0, 0.11);
        border-left: 3px solid var(--warning);
        border-radius: 0 7px 7px 0;
        padding: 0.6rem 0.85rem;
        color: var(--ink-2);
        font-size: 0.82rem;
        margin: 0.5rem 0 0.9rem;
      }}
      .caveat {{
        color: var(--ink-muted);
        font-size: 0.78rem;
        border-top: 1px solid var(--border);
        padding-top: 0.55rem;
        margin-top: 0.7rem;
      }}

      /* --- Risk band chips; the label carries the meaning, not the colour -- */
      .band {{
        display: inline-block;
        padding: 0.12rem 0.6rem;
        border-radius: 999px;
        font-size: 0.76rem;
        font-weight: 600;
        border: 1px solid currentColor;
      }}
      .band-Low      {{ color: var(--good); }}
      .band-Moderate {{ color: var(--warning); }}
      .band-High     {{ color: var(--serious); }}
      .band-Critical {{ color: var(--critical); }}

      /* --- Streamlit widget chrome --------------------------------------- */
      div[data-testid="stMetricValue"] {{ color: var(--ink); }}
      .stTabs [data-baseweb="tab-list"] {{ gap: 0.2rem; border-bottom: 1px solid var(--border); }}
      .stTabs [data-baseweb="tab"] {{ color: var(--ink-muted); padding: 0.45rem 0.85rem; }}
      .stTabs [aria-selected="true"] {{ color: var(--ink); }}
      .stDataFrame {{ border: 1px solid var(--border); border-radius: 8px; }}
      [data-testid="stDataFrame"] td {{ font-variant-numeric: tabular-nums; }}
      button[kind], .stDownloadButton button {{ transition: background-color .18s, border-color .18s; }}
      button:focus-visible, a:focus-visible, [role="tab"]:focus-visible {{
        outline: 2px solid var(--accent);
        outline-offset: 2px;
      }}
      .stSlider label, .stSelectbox label, .stNumberInput label {{
        color: var(--ink-2);
        font-size: 0.84rem;
      }}

      @media (prefers-reduced-motion: reduce) {{
        * {{ transition: none !important; animation: none !important; }}
      }}
    </style>
    """


def apply() -> None:
    """Install the CSS and the Plotly template. Safe to call on every rerun."""
    register_plotly_template()
    st.markdown(_css(), unsafe_allow_html=True)
