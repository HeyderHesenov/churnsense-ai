"""The test partition stays out of everything except the final evaluation.

The project's published test figures are only as good as the discipline around
the partition they come from. These tests turn that discipline into checks:

* only ``churnsense.evaluation.report`` reads ``X_test`` / ``y_test`` (the
  dashboard used to re-score it on every page view);
* the final evaluation refuses to run unless explicitly confirmed;
* the routine ``make dev`` loop cannot reach it;
* a split rebuilt from changed configuration is refused, not silently used.

Synthetic data only; nothing here reads the real dataset.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import check_split, make_splits
from churnsense.exceptions import DataError

ROOT = Path(__file__).resolve().parents[1]

#: Files allowed to name the test partition, and why.
ALLOWED = {
    "src/churnsense/data/split.py": "defines the partitions",
    "src/churnsense/evaluation/report.py": "the final evaluation",
    "app/data.py": "labels each row with its partition (index only, no scoring)",
}


def _test_partition_references(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"X_test", "y_test"}
    ]


def test_only_the_final_evaluation_reads_the_test_partition():
    offenders = {
        str(path.relative_to(ROOT)): lines
        for folder in ("src", "app")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if (lines := _test_partition_references(path))
        and str(path.relative_to(ROOT)) not in ALLOWED
    }
    assert not offenders, f"test partition referenced outside the final evaluation: {offenders}"


def test_the_final_evaluation_refuses_without_confirmation(monkeypatch, capsys):
    """It must stop before loading anything - not after scoring."""
    from churnsense.evaluation import report

    def boom(*_, **__):
        raise AssertionError("the refusal must come before any work")

    monkeypatch.setattr(report, "load_config", boom)
    monkeypatch.setattr(report, "configure_logging", boom)

    assert report.main([]) == 2
    assert "Refusing to run" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    ("args", "confirmed"),
    [([], False), (["CONFIRM=no"], False), (["CONFIRM=yes"], True)],
)
def test_make_evaluate_passes_the_confirmation_only_when_asked(args, confirmed):
    dry_run = subprocess.run(
        ["make", "-n", "evaluate", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert "churnsense.evaluation.report" in dry_run
    assert ("--confirm-test-read" in dry_run) is confirmed


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_dev_never_reaches_the_final_evaluation():
    dry_run = subprocess.run(
        ["make", "-n", "dev", "CONFIRM=yes"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert "churnsense.models.train" in dry_run
    assert "churnsense.evaluation.report" not in dry_run


def test_a_changed_split_is_refused(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    trained_on = make_splits(X, y, cfg)
    meta = {"split_digest": trained_on.digest()}

    check_split(make_splits(X, y, cfg), meta)  # same config: accepted

    reseeded = replace(cfg, random_seed=cfg.random_seed + 1)
    with pytest.raises(DataError, match="differs"):
        check_split(make_splits(X, y, reseeded), meta)


def test_an_artifact_without_a_split_digest_is_refused(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    with pytest.raises(DataError, match="no split digest"):
        check_split(make_splits(X, y, cfg), {})


def test_the_split_log_gives_no_test_base_rate(clean_frame, cfg: Config, caplog):
    """`make dev` logs every partition's size; the test labels' rate stays out."""
    X, y = features_and_target(clean_frame, cfg)
    with caplog.at_level("INFO", logger="churnsense.data.split"):
        make_splits(X, y, cfg)
    lines = {
        message.split()[1]: message for message in caplog.messages if message.startswith("split ")
    }
    assert set(lines) == {"train", "validation", "test"}
    assert "positive_rate" in lines["train"]
    assert "positive_rate" in lines["validation"]
    assert "positive_rate" not in lines["test"]
