"""The outsider's arithmetic itself: what it finds, and that it finds what it should.

``churnsense.disclosure_audit`` is what ``make demo-data`` runs on the file it is
about to write. These cases are small enough to solve by hand.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from churnsense import disclosure_audit as audit
from churnsense.evaluation.disclosure import MIN_CELL

V = {audit.VALIDATION}


def test_a_union_of_intervals_that_are_not_neighbours_is_solved_for():
    """The total less the middle gives the two ends together, though neither alone."""
    facts = [
        audit.between(V, {audit.CHURNED}, 0.0, 2.0, 60),  # everyone
        audit.between(V, {audit.CHURNED}, 0.1, 0.3, 55),  # the middle
    ]
    found, solved = audit.score_violations(facts)
    assert any("[0.0, 0.3]" in line and ": 5" in line for line in found), found
    assert solved >= 3  # the total, the middle, and the two ends together


def test_with_many_atoms_every_run_of_neighbours_is_still_tried():
    cuts = [round(i / 100, 2) for i in range(30)]  # more atoms than every set allows
    assert len(cuts) > audit.EVERY_SET_UP_TO
    churners = [300 - 10 * i for i in range(len(cuts))]
    facts = [audit.at_or_above(t, audit.CHURNED, n) for t, n in zip(cuts, churners, strict=True)]
    found, _ = audit.score_violations(facts)
    # Each step holds 10 churners: a run of two holds 20, which keeps the rule.
    assert any(": 10" in line for line in found)
    assert not any(f": {2 * 10}" in line for line in found)


def test_counts_of_at_least_twenty_or_none_of_both_are_fine():
    facts = [
        audit.between(V, {audit.CHURNED}, 0.0, 0.5, MIN_CELL),
        audit.between(V, {audit.STAYED}, 0.0, 0.5, MIN_CELL),
        audit.between(V, {audit.CHURNED}, 0.5, 2.0, 0),
        audit.between(V, {audit.STAYED}, 0.5, 2.0, 0),
    ]
    assert audit.score_violations(facts)[0] == []


def test_figures_that_disagree_are_refused_rather_than_solved():
    facts = [
        audit.between(V, {audit.CHURNED}, 0.0, 2.0, 50),
        audit.between(V, {audit.CHURNED}, 0.0, 0.5, 30),
        audit.between(V, {audit.CHURNED}, 0.5, 2.0, 30),  # 30 + 30 is not 50
    ]
    with pytest.raises(ValueError, match="disagree"):
        audit.score_violations(facts)


#: The score facts of the dataset-backed control (tests/test_public_demo.py) as they were
#: when Linux CI failed: 1,122 facts over 404 atoms, rank 210, consistent - the book's own
#: counts solve it exactly - yet ``np.linalg.pinv``, whose cutoff keeps rounding noise as
#: singular values, missed by 0.034 with OpenBLAS (9.6e-12 by least squares).
RANK_DEFICIENT = Path(__file__).parent / "fixtures" / "rank_deficient_score_facts.npz"


def test_a_consistent_rank_deficient_system_is_solved_to_its_published_values():
    data = np.load(RANK_DEFICIENT)
    a, b = data["a"].astype(float), data["b"].astype(float)
    assert a.shape == (1122, 404) and np.linalg.matrix_rank(a) == 210
    x = audit._solution(a, b)
    assert np.abs(a @ x - b).max() < 1e-6


def test_figures_with_no_solution_are_still_refused():
    a = np.array([[1.0, 0.0], [1.0, 0.0]])
    x = audit._solution(a, np.array([1.0, 2.0]))
    assert not np.allclose(a @ x, [1.0, 2.0])
