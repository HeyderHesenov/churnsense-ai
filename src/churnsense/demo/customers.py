"""The customers a public demo lists: generated here, never read from anywhere.

A public, unauthenticated demo must not hand out the dataset's rows, so it
lists these instead. ``demo_customers`` is a pure function of a size and a
seed. It opens no file, takes no frame, and imports the column contract only
(``churnsense.data.schema``: the names and vocabularies a record must use) -
so no value here can have been sampled, copied or perturbed from a dataset.
``tests/test_demo_customers.py`` holds it to that.

Every share and price below is a round number chosen by hand to make a varied
book. None was computed from data, and none is a claim about real customers:
the customers have no outcome, and no protected attribute is generated at all.

Their IDs are ``DEMO-`` and six digits, a format no dataset customer has.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from churnsense.data import schema

#: Customers in the demo book.
BOOK_SIZE = 2000
#: The one seed the served book is generated from.
SEED = 2026
ID_PREFIX = "DEMO-"

#: The columns generated, in order: the ID, then every input the contract has
#: except the protected attribute.
COLUMNS: tuple[str, ...] = (
    schema.ID_COLUMN,
    *schema.model_input_columns(drop_columns=("gender",)),
)

# --- design constants: chosen, not estimated ------------------------------------------

_CONTRACT_SHARES = (0.5, 0.25, 0.25)  # month-to-month, one year, two year
#: Months a customer may have been with the company, by contract: [low, high).
_TENURE_RANGE = {"Month-to-month": (0, 37), "One year": (6, 61), "Two year": (12, 73)}
_PHONE_SHARE = 0.85
_INTERNET_SHARES = (0.35, 0.40, 0.25)  # DSL, fibre, none
_ADDON_SHARE = 0.4
_SENIOR_SHARE = 0.2
_DEPENDENTS_SHARE = 0.25
#: A made-up price list, per month.
_PRICE_PHONE, _PRICE_SECOND_LINE = 15.0, 8.0
_PRICE_INTERNET = {"DSL": 30.0, "Fiber optic": 55.0, "No": 0.0}
_PRICE_PROTECTION, _PRICE_STREAMING = 6.0, 9.0
_PRICE_NOISE = 3.0
#: How far what was billed over the months may stray from tenure x today's price.
_BILLING_DRIFT = 0.05

_STREAMING = ("StreamingTV", "StreamingMovies")


def demo_customers(n: int = BOOK_SIZE, seed: int = SEED) -> pd.DataFrame:
    """``n`` generated customers: an ID and every model input, typed, with no outcome."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("a demo book holds at least one customer")
    rng = np.random.default_rng(seed)

    def pick(column: str, shares: tuple[float, ...] | None = None) -> np.ndarray:
        return rng.choice(np.array(schema.ALLOWED_CATEGORIES[column], dtype=object), n, p=shares)

    def yes(share: float) -> np.ndarray:
        return rng.random(n) < share

    contract = pick("Contract", _CONTRACT_SHARES)
    tenure = np.zeros(n, dtype=int)
    for name, (low, high) in _TENURE_RANGE.items():
        drawn = rng.integers(low, high, n)
        tenure = np.where(contract == name, drawn, tenure)

    phone = yes(_PHONE_SHARE)
    internet = pick("InternetService", _INTERNET_SHARES)
    # Nobody is a customer of nothing: without a phone line, there is internet.
    internet = np.where(~phone & (internet == "No"), "DSL", internet).astype(object)
    online = internet != "No"
    second_line = phone & yes(0.5)
    addons = {column: online & yes(_ADDON_SHARE) for column in schema.ADDON_COLUMNS}

    monthly = (
        phone * _PRICE_PHONE
        + second_line * _PRICE_SECOND_LINE
        + np.array([_PRICE_INTERNET[kind] for kind in internet])
        + sum(
            taken * (_PRICE_STREAMING if column in _STREAMING else _PRICE_PROTECTION)
            for column, taken in addons.items()
        )
        + rng.uniform(-_PRICE_NOISE, _PRICE_NOISE, n)
    ).round(2)
    billed = tenure * monthly * rng.uniform(1 - _BILLING_DRIFT, 1 + _BILLING_DRIFT, n)

    def text(flags: np.ndarray, off: str = "No") -> np.ndarray:
        return np.where(flags, "Yes", off).astype(object)

    frame = pd.DataFrame(
        {
            schema.ID_COLUMN: [f"{ID_PREFIX}{i:06d}" for i in range(n)],
            "SeniorCitizen": np.where(yes(_SENIOR_SHARE), "1", "0").astype(object),
            "Partner": text(yes(0.5)),
            "Dependents": text(yes(_DEPENDENTS_SHARE)),
            "tenure": tenure,
            "PhoneService": text(phone),
            "MultipleLines": np.where(phone, text(second_line), "No phone service").astype(object),
            "InternetService": internet,
            **{
                column: np.where(online, text(taken), "No internet service").astype(object)
                for column, taken in addons.items()
            },
            "Contract": contract,
            "PaperlessBilling": text(yes(0.5)),
            "PaymentMethod": pick("PaymentMethod"),
            "MonthlyCharges": monthly,
            # Billed nothing exactly at tenure 0: the contract's own rule.
            "TotalCharges": np.where(tenure == 0, 0.0, np.maximum(billed.round(2), 0.01)),
        }
    )
    return frame.loc[:, list(COLUMNS)]


def records_sha256(frame: pd.DataFrame) -> str:
    """The SHA-256 of a generated book: what was explained is what is being listed."""
    text = frame.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
