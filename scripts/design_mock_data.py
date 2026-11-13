"""Illustrative mock data for the design artboards in ``docs/design/``.

The artboards are static design mock-ups. They once embedded figures measured on the IBM
sample - the validation sweep at every 0.01, segment counts of a few customers - which the
public demo itself no longer publishes. Every number they show now comes from here: an
invented population, drawn from fixed parameters with a fixed seed. Nothing is read from
the dataset, the model, a report or an artifact, and nothing here is fitted to them; the
parameters were chosen by hand to give charts of a plausible shape, nothing more.

``python scripts/design_mock_data.py`` prints the values as JavaScript constants;
``tests/test_design_artboards.py`` checks that every artboard holds exactly these values
and says on its face that they are illustrative.
"""

from __future__ import annotations

import json
import math
import sys

import numpy as np

#: The seed of the invented population. Changing it changes every artboard.
SEED = 20261113

#: An invented book: 6,000 customers, of whom 4,800 have a shown outcome (train 3,600,
#: validation 1,200). Round numbers on purpose: they say "mock" at a glance.
CUSTOMERS, LABELLED, TRAIN, VALIDATION = 6000, 4800, 3600, 1200

#: The mock validation partition's churners and stayers.
POS, NEG = 320, 880

#: The banner every artboard shows, word for word.
BANNER = "Illustrative mock data \u2014 not measured model results"

#: The mock operating point and assumptions (the artboards' controls start here).
THRESHOLD = 0.36
OFFER, SUCCESS, HORIZON, MARGIN = 50.0, 0.30, 12, 0.65

CONTRACTS = ("Month-to-month", "One year", "Two year")
PAYMENTS = ("Electronic check", "Mailed check", "Bank transfer (auto)", "Credit card (auto)")
INTERNET = ("Fiber optic", "DSL", "No internet")
#: The artboards print an en dash in ranges (U+2013), written as an escape here.
DASH = "\u2013"
PRICES = ("Under $35", f"$35{DASH}55", f"$55{DASH}75", f"$75{DASH}95", "$95+")
TENURES = tuple(f"{a}{DASH}{b}" for a, b in ((0, 3), (4, 6), (7, 12), (13, 24), (25, 48), (49, 72)))


def _rng() -> np.random.Generator:
    return np.random.default_rng(SEED)


def population() -> dict[str, np.ndarray]:
    """The invented customers: attributes, a score, an outcome and a monthly charge."""
    rng = _rng()
    n = CUSTOMERS
    contract = rng.choice(3, n, p=[0.52, 0.22, 0.26])
    payment = rng.choice(4, n, p=[0.33, 0.23, 0.22, 0.22])
    internet = rng.choice(3, n, p=[0.43, 0.35, 0.22])
    tenure = np.clip(np.round(rng.gamma(1.4, 22.0, n) * np.where(contract == 0, 0.45, 1.3)), 0, 72)
    charge = np.round(
        np.where(internet == 0, 88.0, np.where(internet == 1, 58.0, 24.0)) + rng.normal(0, 11.0, n),
        2,
    ).clip(18.25, 118.75)
    logit = (
        -1.1
        + np.array([1.25, -0.55, -1.7])[contract]
        + np.array([0.5, -0.1, -0.25, -0.3])[payment]
        + np.array([0.45, -0.15, -0.9])[internet]
        - 0.028 * tenure
        + rng.normal(0, 0.55, n)
    )
    score = 1.0 / (1.0 + np.exp(-logit))
    churn = (rng.random(n) < score).astype(int)
    part = np.array(["train"] * TRAIN + ["validation"] * VALIDATION + ["test"] * (n - LABELLED))
    rng.shuffle(part)
    return {
        "contract": contract,
        "payment": payment,
        "internet": internet,
        "tenure": tenure,
        "charge": charge,
        "score": np.round(score, 3),
        "churn": churn,
        "part": part,
    }


def validation_sweep() -> dict[str, list]:
    """``F`` flagged, ``T`` flagged churners and ``S`` their monthly charges, at 0.00..1.00.

    Its own partition of exactly ``POS`` churners and ``NEG`` stayers, drawn apart from
    the population above, so the totals are the round ones the artboards state.
    """
    rng = np.random.default_rng(SEED + 1)
    churner = np.round(rng.beta(3.1, 2.3, POS), 3)
    stayer = np.round(rng.beta(1.25, 4.4, NEG), 3)
    churner_charge = np.round(rng.uniform(25.0, 115.0, POS), 2)
    cuts = [i / 100 for i in range(101)]
    f = [int((churner >= c).sum() + (stayer >= c).sum()) for c in cuts]
    t = [int((churner >= c).sum()) for c in cuts]
    s = [round(float(churner_charge[churner >= c].sum()), 2) for c in cuts]
    return {"F": f, "T": t, "S": s}


def histogram() -> list[list[float]]:
    """Per 0.05 of score: [customers, monthly charges, with an outcome, churned]."""
    p = population()
    out = []
    for b in range(20):
        low, high = b / 20, (b + 1) / 20
        rows = (p["score"] >= low) & ((p["score"] < high) if b < 19 else True)
        shown = rows & (p["part"] != "test")
        out.append(
            [
                int(rows.sum()),
                round(float(p["charge"][rows].sum()), 1),
                int(shown.sum()),
                int(p["churn"][shown].sum()),
            ]
        )
    return out


def _groups(
    codes: np.ndarray, labels: tuple[str, ...], mask: np.ndarray, churn: np.ndarray
) -> list:
    return [
        [label, int((mask & (codes == i)).sum()), int(churn[mask & (codes == i)].sum())]
        for i, label in enumerate(labels)
    ]


def segments() -> dict[str, list]:
    """The overview's segment rows on the mock training partition: [label, customers, churned]."""
    p = population()
    train = p["part"] == "train"
    price = np.digitize(p["charge"], [35, 55, 75, 95])
    tenure = np.digitize(p["tenure"], [4, 7, 13, 25, 49])
    return {
        "contract": _groups(p["contract"], CONTRACTS, train, p["churn"]),
        "payment": _groups(p["payment"], PAYMENTS, train, p["churn"]),
        "internet": _groups(p["internet"], INTERNET, train, p["churn"]),
        "price": _groups(price, PRICES, train, p["churn"]),
        "tenure": _groups(tenure, TENURES, train, p["churn"]),
    }


def bands() -> list[dict]:
    """The risk bands over the whole mock book: customers, monthly charges, churn, flagged."""
    p = population()
    edges = (0.0, 0.25, 0.50, 0.75, 1.01)
    out = []
    for name, low, high in zip(
        ("Low", "Moderate", "High", "Critical"), edges, edges[1:], strict=False
    ):
        rows = (p["score"] >= low) & (p["score"] < high)
        shown = rows & (p["part"] != "test")
        rate = p["churn"][shown].mean() if shown.any() else math.nan
        out.append(
            {
                "k": name,
                "cust": int(rows.sum()),
                "mon": round(float(p["charge"][rows].sum()), 1),
                "churn": f"{rate * 100:.1f}%",
                "flagged": int((rows & (p["score"] >= THRESHOLD)).sum()),
            }
        )
    return out


#: Invented importances and associations: shapes only, chosen by hand, never measured.
IMPORTANCE = (
    ("Tenure", 12.0),
    ("Monthly charges", 10.5),
    ("Internet service", 9.0),
    ("Contract type", 6.5),
    ("Total charges to date", 5.0),
)
#: Keys and labels as the patterns page names them; values invented, strictly decreasing.
ASSOCIATION = (
    ("Contract", "Contract", 0.40),
    ("OnlineSecurity", "Online security", 0.33),
    ("tenure", "Tenure", 0.31),
    ("TechSupport", "Tech support", 0.30),
    ("InternetService", "Internet service", 0.29),
    ("PaymentMethod", "Payment method", 0.27),
    ("OnlineBackup", "Online backup", 0.24),
    ("DeviceProtection", "Device protection", 0.22),
    ("StreamingMovies", "Streaming movies", 0.18),
    ("StreamingTV", "Streaming TV", 0.17),
    ("TotalCharges", "Total charges", 0.16),
    ("monthly", "Monthly charges", 0.15),
    ("PaperlessBilling", "Paperless billing", 0.14),
    ("Dependents", "Dependents", 0.12),
    ("Partner", "Partner", 0.11),
    ("SeniorCitizen", "Senior citizen", 0.09),
    ("MultipleLines", "Multiple lines", 0.03),
    ("PhoneService", "Phone service", 0.00),
)
#: The overview's five, under its own labels.
ASSOCIATION_LABELS = (
    "Contract type",
    "Online security",
    "Tenure",
    "Tech support",
    "Internet service",
)


def headline() -> dict[str, float]:
    """The figures the overview prints: the book and the campaign at the mock threshold."""
    p = population()
    shown = p["part"] != "test"
    flagged = p["score"] >= THRESHOLD
    sweep = validation_sweep()
    i = round(THRESHOLD * 100)
    value = OFFER  # one offer per flagged customer
    retained = sweep["S"][i] * HORIZON * MARGIN * SUCCESS
    return {
        "customers": CUSTOMERS,
        "labelled": int(shown.sum()),
        "churned": int(p["churn"][shown].sum()),
        "monthly": round(float(p["charge"].sum()), 1),
        "flagged_book": int(flagged.sum()),
        "flagged_book_monthly": round(float(p["charge"][flagged].sum()), 1),
        "val_flagged": sweep["F"][i],
        "val_tp": sweep["T"][i],
        "campaign_cost": sweep["F"][i] * value,
        "retained": round(retained, 2),
        "net": round(retained - sweep["F"][i] * value, 2),
    }


#: Invented cross-validation results, chosen by hand: (name, mean CV PR-AUC, std, standard
#: error, validation PR-AUC, validation ROC-AUC). Not measured, and not derived from anything.
CANDIDATES = (
    ("Random forest", 0.6260, 0.0134, 0.0060, 0.6050, 0.8210),
    ("Hist. gradient boosting", 0.6250, 0.0130, 0.0058, 0.6110, 0.8195),
    ("Logistic regression", 0.6215, 0.0212, 0.0095, 0.6150, 0.8205),
    ("Baseline (prior)", 0.2667, 0.0004, 0.0002, 0.2667, 0.5000),
)
#: The comparison chart's axis: from 0.600 to 0.640.
AXIS = (0.600, 0.640)
#: Invented calibration figures at 0.5: (uncalibrated, calibrated) precision, recall,
#: Brier score and calibration error.
AT_HALF = {
    "precision": (0.495, 0.650),
    "recall": (0.770, 0.520),
    "brier": (0.171, 0.141),
    "ece": (0.130, 0.030),
}


def position(value: float) -> str:
    """Where ``value`` sits on the comparison chart's axis, as a CSS percentage."""
    low, high = AXIS
    return f"{(value - low) / (high - low) * 100:g}%"


def test_point() -> dict[str, float]:
    """The mock test partition at the mock threshold: the confusion matrix and its rates."""
    p = population()
    rows = p["part"] == "test"
    y, flagged = p["churn"][rows] == 1, p["score"][rows] >= THRESHOLD
    tp, fp = int((y & flagged).sum()), int((~y & flagged).sum())
    fn, tn = int((y & ~flagged).sum()), int((~y & ~flagged).sum())
    n = tp + fp + fn + tn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "n": n,
        "positives": tp + fn,
        "precision": tp / (tp + fp),
        "recall": tp / (tp + fn),
        "accuracy": (tp + tn) / n,
        "no_churn": (fp + tn) / n,
    }


def net_benefit(success: float = SUCCESS) -> list[float]:
    """The mock sweep's simulated net benefit at every cut."""
    s = validation_sweep()
    return [v * HORIZON * MARGIN * success - f * OFFER for v, f in zip(s["S"], s["F"], strict=True)]


def recommended(success: float = SUCCESS) -> tuple[float, float]:
    """(peak, recommendation): the highest cut within one offer of the best, as the app does."""
    nb = net_benefit(success)
    best = max(nb)
    peak = nb.index(best) / 100
    return peak, max(i for i, v in enumerate(nb) if v >= best - OFFER) / 100


def net_benefit_path(width: float = 260.0, height: float = 60.0) -> str:
    """The overview's small net-benefit curve, as an SVG path over 0..1."""
    nb = net_benefit()
    top = max(nb)
    return " ".join(
        f"{'M' if i == 0 else 'L'}{i / 100 * width:.1f} {height - max(v, 0.0) / top * (height - 6):.1f}"
        for i, v in enumerate(nb)
    )


def medians() -> tuple[int, int]:
    """Median tenure of the mock training partition's churners and stayers."""
    p = population()
    train = p["part"] == "train"
    return (
        int(np.median(p["tenure"][train & (p["churn"] == 1)])),
        int(np.median(p["tenure"][train & (p["churn"] == 0)])),
    )


def train_rate() -> float:
    p = population()
    train = p["part"] == "train"
    return float(p["churn"][train].mean())


def everything() -> dict:
    return {
        "banner": BANNER,
        "pos": POS,
        "neg": NEG,
        "sweep": validation_sweep(),
        "hist": histogram(),
        "segments": segments(),
        "bands": bands(),
        "importance": [list(r) for r in IMPORTANCE],
        "association": [list(r) for r in ASSOCIATION],
        "headline": headline(),
        "test": test_point(),
        "recommended": recommended(),
        "sensitivity": {rate: recommended(rate)[1] for rate in (0.1, 0.2, 0.3, 0.4, 0.5)},
        "medians": medians(),
        "train_rate": train_rate(),
        "candidates": [list(c) for c in CANDIDATES],
    }


if __name__ == "__main__":
    json.dump(everything(), sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
