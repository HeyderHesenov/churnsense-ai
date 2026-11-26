"""Synthetic account snapshot CSVs for the V2 Data Hub tests.

**Nothing here is real data.** Every account is ``SYN-`` and a number, every name
is ``Synthetic Co`` and a number, and every amount is a round figure written by
hand: no value is sampled from, or stands for, a real customer.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping

REQUIRED = ("account_external_id", "as_of_date", "status", "mrr", "currency")


def row(**values: str) -> dict[str, str]:
    """A valid active account snapshot, with ``values`` replacing or adding fields."""
    base = {
        "account_external_id": "SYN-0001",
        "as_of_date": "2026-10-31",
        "status": "active",
        "mrr": "1250.50",
        "currency": "USD",
    }
    return {**base, **values}


def text(rows: Iterable[Mapping[str, str]], header: list[str] | None = None) -> str:
    """The rows as CSV text; the header is every field the rows use, in first-seen order."""
    rows = list(rows)
    if header is None:
        header = []
        for r in rows:
            header += [k for k in r if k not in header]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for r in rows:
        writer.writerow([r.get(name, "") for name in header])
    return out.getvalue()


def payload(rows: Iterable[Mapping[str, str]], header: list[str] | None = None) -> bytes:
    return text(rows, header).encode()
