"""Spreadsheet-safe CSV exports, shared by the dashboard and the API.

Every CSV this project hands out - the dashboard's downloads and the API's
exports - goes through ``csv_bytes``, so a cell a spreadsheet would execute is
defused in one place, by one rule, with one set of tests.
"""

from __future__ import annotations

import re

import pandas as pd

#: Leading characters that make a spreadsheet read a cell as a formula (OWASP).
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

#: A trigger right after ';', a tab, a line break or a quote. Excel in locales
#: whose list separator is ';' (the comma-decimal ones, az-AZ among them) splits
#: a comma-CSV cell at ';', and outside a ';'-field's own quotes it ends the row
#: at a line break and reads quotes literally - so the piece after any of these
#: can become a formula cell of its own.
_SPLIT_FORMULA = re.compile(r"(?<=[;\t\r\n\"])(?=[=+\-@])")


def _defused(text: str) -> str:
    """``text`` with every place a spreadsheet would start a formula made inert."""
    fixed = _SPLIT_FORMULA.sub("'", text)
    return f"'{fixed}" if text.startswith(FORMULA_TRIGGERS) else fixed


def _neutralised(values: pd.Series) -> pd.Series:
    """``values`` with formula-shaped strings defused; ``values`` itself if there are none.

    Checked per distinct value: export columns are mostly categories that
    repeat thousands of times, which makes this a few milliseconds a page.
    """
    risky = {
        v: fixed
        for v in pd.unique(values.to_numpy())
        if isinstance(v, str) and (fixed := _defused(v)) != v
    }
    if not risky:
        return values
    return values.where(~values.isin(list(risky)), values.map(risky))


def neutralise_formulas(frame: pd.DataFrame) -> pd.DataFrame:
    """Make text a spreadsheet would execute display as text instead.

    Batch scoring carries uploaded values such as ``customerID`` through to
    the export, so a hostile file can plant ``=HYPERLINK(...)`` and have it
    run when the analyst opens the scored CSV in Excel or Sheets. A leading
    apostrophe is the standard defence, applied to cells and column names
    alike. Only text is touched: a negative number is data, not a formula.

    Columns are visited by position, so repeated names are handled, and
    categorical columns are checked like text: risk bands are categorical.

    Returns ``frame`` itself when nothing needs changing - the common case,
    and this runs on every rerun for every export button on a page.
    """
    changed: dict[int, pd.Series] = {}
    for position in range(frame.shape[1]):
        values = frame.iloc[:, position]
        if isinstance(values.dtype, pd.CategoricalDtype):
            values = values.astype(object)
        elif not (values.dtype == object or isinstance(values.dtype, pd.StringDtype)):
            continue
        if (fixed := _neutralised(values)) is not values:
            changed[position] = fixed

    names = pd.Series(frame.columns, dtype=object)
    safe_names = _neutralised(names)
    if not changed and safe_names is names:
        return frame

    safe = frame.copy()
    for position, values in changed.items():
        safe.isetitem(position, values)
    safe.columns = safe_names.tolist()
    return safe


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """The bytes a CSV export contains. Index is dropped so the file opens cleanly
    in a spreadsheet."""
    return neutralise_formulas(frame).to_csv(index=False).encode("utf-8")
