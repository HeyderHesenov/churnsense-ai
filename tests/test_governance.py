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
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import check_provenance, check_split, make_splits
from churnsense.exceptions import DataError
from tests.conftest import pinned_split_digest, record_provenance

ROOT = Path(__file__).resolve().parents[1]

#: Files allowed to name the test partition, and why.
ALLOWED = {
    "src/churnsense/data/split.py": "defines the partitions",
    "src/churnsense/evaluation/report.py": "the final evaluation",
    "src/churnsense/book.py": "labels each row with its partition (index only, no scoring)",
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
        for folder in ("src", "app", "scripts")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if (lines := _test_partition_references(path))
        and str(path.relative_to(ROOT)) not in ALLOWED
    }
    assert not offenders, f"test partition referenced outside the final evaluation: {offenders}"


def test_the_release_is_written_in_one_module_and_never_elsewhere():
    """No other module names the ledger, and the report module does not touch a file."""
    release_file = ROOT / "src/churnsense/evaluation/release.py"
    assert "_write_atomic" in release_file.read_text()
    for folder in ("src", "app", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if path != release_file:
                assert "_write_ledger" not in source, path
                assert "_write_atomic" not in source, path
                # The consistency check names the ledger to compare it; it writes nothing.
                allowed = ("report.py", "check_release_ledger.py")
                assert "held_out_release.json" not in source or path.name in allowed, path
    final = (ROOT / "src/churnsense/evaluation/report.py").read_text(encoding="utf-8")
    body = final.split("# Final evaluation: the only code that reads the test partition")[1]
    for writer in ("write_text", "write_bytes", "json.dump", "unlink", "open("):
        assert writer not in body, writer


def test_the_ledger_is_read_through_the_one_validating_reader():
    """No second reader, metadata consumer or report path can skip ``release._decode``.

    The synthetic end-to-end server only points the module at a temporary ledger, and the
    consistency check is the separate program ``tests/ledger_payloads.py`` holds to the same rules.
    """
    release_file = ROOT / "src/churnsense/evaluation/release.py"
    setup_only = {ROOT / "scripts/e2e_server.py"}
    for folder in ("src", "app", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            if path == release_file:
                continue
            source = path.read_text(encoding="utf-8")
            assert "_decode" not in source, path
            assert "read_ledger" not in source, path
            assert "LEDGER_FILE" not in source or path in setup_only, path


def test_every_consumer_of_a_release_gets_it_from_the_validating_module():
    consumers = {
        "src/churnsense/api/dashboard.py": "release.published_release(",
        "app/streamlit_app.py": "published_release(",
        "app/sections/performance.py": "published_release(",
        "src/churnsense/evaluation/report.py": "release.freeze(",
    }
    for name, call in consumers.items():
        assert call in (ROOT / name).read_text(encoding="utf-8"), name
    held_out_keys = re.compile(r"\.(?:released|identity)\b")
    readers = {
        str(path.relative_to(ROOT))
        for folder in ("src", "app")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if held_out_keys.search(path.read_text(encoding="utf-8"))
    }
    assert readers <= {
        "src/churnsense/evaluation/release.py",
        "src/churnsense/evaluation/report.py",
    }


def test_no_code_writes_or_reads_the_historical_report():
    for folder in ("src", "app", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert 'final_evaluation.md"' not in source, path
            assert "final_evaluation.json" not in source, path


def test_the_final_evaluation_refuses_without_confirmation(monkeypatch, capsys):
    """It must stop before loading anything - not after scoring."""
    from churnsense.evaluation import report

    def boom(*_, **__):
        raise AssertionError("the refusal must come before any work")

    monkeypatch.setattr(report, "load_config", boom)
    monkeypatch.setattr(report, "configure_logging", boom)

    assert report.main([]) == 2
    assert "Refusing to run" in capsys.readouterr().err


@pytest.mark.parametrize("recorded", ["0" * 64, None])
def test_the_final_evaluation_refuses_another_dataset_before_scoring(
    pipeline_cfg, monkeypatch, recorded
):
    """The artifact's threshold was chosen on the data it records; a different
    file, or none recorded, is refused before a single test row is scored."""
    from churnsense.evaluation import report

    record_provenance(
        pipeline_cfg, data_sha256=recorded, split_digest=pinned_split_digest(pipeline_cfg)
    )
    scored = []
    monkeypatch.setattr(report, "load_config", lambda _path: pipeline_cfg)
    monkeypatch.setattr(report, "configure_logging", lambda: None)
    monkeypatch.setattr(report, "score_held_out", lambda *args, **_: scored.append(args))

    assert report.main(["--confirm-test-read"]) == 1
    assert not scored
    assert not (pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME).exists()


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


def test_partitions_are_trusted_only_for_the_dataset_the_model_records(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    meta = {"data_sha256": "a" * 64, "split_digest": splits.digest()}

    check_provenance(splits, "a" * 64, meta)  # the recorded file, the recorded split

    with pytest.raises(DataError, match="dataset differs"):
        check_provenance(splits, "b" * 64, meta)
    with pytest.raises(DataError, match="dataset differs"):
        check_provenance(splits, None, meta)  # a file read without its hash proves nothing
    with pytest.raises(DataError, match="records no dataset hash"):
        check_provenance(splits, None, {"split_digest": splits.digest()})  # None == None
    with pytest.raises(DataError, match="no split digest"):
        check_provenance(splits, "a" * 64, {"data_sha256": "a" * 64})


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


#: The IBM Telco customer IDs are four digits, a dash and five capitals. The repository
#: does not redistribute the dataset, so no file in it names a real customer;
#: synthetic rows use DEMO- IDs, and examples use this format hint.
_DATASET_ID = re.compile(r"\b[0-9]{4}-[A-Z]{5}\b")
_ID_FORMAT_HINT = "1234-ABCDE"


def test_no_file_in_the_repository_names_a_real_customer():
    scanned = [
        path
        for folder in (
            "src",
            "app",
            "tests",
            "docs",
            "configs",
            "scripts",
            "frontend/src",
            "frontend/e2e",
        )
        for path in sorted((ROOT / folder).rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx", ".css", ".md", ".json", ".yaml", ".html"}
    ] + [ROOT / "README.md", ROOT / "frontend" / "playwright.config.ts"]
    named = {
        str(path.relative_to(ROOT)): sorted(set(found) - {_ID_FORMAT_HINT})
        for path in scanned
        if (found := _DATASET_ID.findall(path.read_text(encoding="utf-8", errors="ignore")))
        and set(found) - {_ID_FORMAT_HINT}
    }
    assert not named, f"real customer IDs in tracked files: {named}"
