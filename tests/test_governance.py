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
                # The consistency check names the ledger to compare it, and the deploy
                # bundle check to confirm the bundled model is the released one; neither
                # writes anything (test_the_deploy_bundle_check_writes_nothing).
                allowed = ("report.py", "check_release_ledger.py", "verify_deploy_bundle.py")
                assert "held_out_release.json" not in source or path.name in allowed, path
    final = (ROOT / "src/churnsense/evaluation/report.py").read_text(encoding="utf-8")
    body = final.split("# Final evaluation: the only code that reads the test partition")[1]
    for writer in ("write_text", "write_bytes", "json.dump", "unlink", "open("):
        assert writer not in body, writer


#: The one program outside ``release.py`` that may call the canonical strict
#: validator: the read-only deploy bundle check. ``release.py`` is fingerprinted
#: by the published release, so it cannot grow a public parser for it; the check
#: is held to the exact shape ``_bundle_check_validator_faults`` describes.
BUNDLE_CHECK = "scripts/verify_deploy_bundle.py"
_READER_NAMES = ("_decode", "read_ledger")


def _bundle_check_validator_faults(source: str) -> list[str]:
    """Why ``source`` is not the one permitted use of the validator; empty when it is.

    Permitted: inside ``read_release`` only, the module imported as
    ``from churnsense.evaluation import release`` and called exactly once as
    ``release._decode(<text>)``. No other reference to the reader's names in
    code - no alias, import, ``getattr`` string or second call - no rebinding
    of ``release``, and no parser of its own beside the validator. Docstrings
    may name the reader; nothing else may.
    """
    tree = ast.parse(source)
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, scopes)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    faults: list[str] = []
    calls = {
        id(node.func): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    permitted: list[ast.Attribute] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and any(name in node.value for name in _READER_NAMES):
                faults.append(f"line {node.lineno}: the reader is named in a string")
        elif isinstance(node, ast.Name) and node.id in _READER_NAMES:
            faults.append(f"line {node.lineno}: {node.id} used as a bare name")
        elif isinstance(node, ast.alias) and {node.name, node.asname} & set(_READER_NAMES):
            faults.append(f"line {node.lineno}: {node.name} imported")
        elif isinstance(node, ast.Attribute) and node.attr in _READER_NAMES:
            call = calls.get(id(node))
            direct = (
                node.attr == "_decode"
                and isinstance(node.value, ast.Name)
                and node.value.id == "release"
                and call is not None
                and len(call.args) == 1
                and not call.keywords
            )
            if direct:
                permitted.append(node)
            else:
                faults.append(f"line {node.lineno}: .{node.attr} is not a direct release._decode()")
    if len(permitted) != 1:
        faults.append(f"release._decode(text) is called {len(permitted)} times, not once")
    readers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "read_release"
    ]
    if len(readers) != 1:
        return [*faults, "there is not exactly one read_release()"]
    inside = list(ast.walk(readers[0]))
    if any(node not in inside for node in permitted):
        faults.append("the validator is called outside read_release()")
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        and any((alias.asname or alias.name).split(".")[0] == "release" for alias in node.names)
    ]
    canonical = [
        node
        for node in imports
        if isinstance(node, ast.ImportFrom)
        and node.module == "churnsense.evaluation"
        and node.level == 0
        and any(alias.name == "release" and alias.asname is None for alias in node.names)
        and node in inside
    ]
    if len(imports) != 1 or len(canonical) != 1:
        faults.append("`release` is not solely `from churnsense.evaluation import release`")
    if any(
        isinstance(node, ast.Name) and node.id == "release" and not isinstance(node.ctx, ast.Load)
        for node in ast.walk(tree)
    ) or any(isinstance(node, ast.arg) and node.arg == "release" for node in ast.walk(tree)):
        faults.append("`release` is rebound")
    if any(isinstance(node, ast.Name) and node.id in ("json", "yaml") for node in inside):
        faults.append("read_release() parses the ledger itself")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func
        name = name.id if isinstance(name, ast.Name) else name
        if name in ("_json", "_yaml", "loads", "safe_load") and any(
            isinstance(part, ast.Name) and part.id == "LEDGER" for part in ast.walk(node)
        ):
            faults.append(f"line {node.lineno}: the ledger is parsed without the validator")
    return faults


def test_the_ledger_is_read_through_the_one_validating_reader():
    """No second reader, metadata consumer or report path can skip ``release._decode``.

    The synthetic end-to-end server only points the module at a temporary ledger, and the
    consistency check is the separate program ``tests/ledger_payloads.py`` holds to the same rules.
    The deploy bundle check is the one named consumer of the validator itself: it must
    call it, in the one permitted shape, and is otherwise held to every rule here.
    """
    release_file = ROOT / "src/churnsense/evaluation/release.py"
    bundle_check = ROOT / BUNDLE_CHECK
    setup_only = {ROOT / "scripts/e2e_server.py"}
    seen_bundle_check = False
    for folder in ("src", "app", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            if path == release_file:
                continue
            source = path.read_text(encoding="utf-8")
            if path == bundle_check:
                seen_bundle_check = True
                assert _bundle_check_validator_faults(source) == [], path
            else:
                assert "_decode" not in source, path
                assert "read_ledger" not in source, path
            assert "LEDGER_FILE" not in source or path in setup_only, path
    assert seen_bundle_check, "the deploy bundle check was not examined"


def _bundle_check_source() -> str:
    return (ROOT / BUNDLE_CHECK).read_text(encoding="utf-8")


def _edited(old: str, new: str) -> str:
    source = _bundle_check_source()
    assert source.count(old) == 1, old
    return source.replace(old, new)


_CALL = "        ledger = release._decode(text)\n"
_IMPORT = "        from churnsense.evaluation import release\n"


@pytest.mark.parametrize(
    "bypass",
    [
        # its own parser instead of the validator
        lambda: _edited(_CALL, "        ledger = json.loads(text)\n"),
        # its own parser beside the validator
        lambda: _edited(_CALL, _CALL + "        ledger = json.loads(text) or ledger\n"),
        # the generic JSON helper pointed at the ledger, elsewhere in the program
        lambda: _edited(
            "    meta = _json(root / META)\n",
            "    meta = _json(root / META)\n    unchecked = _json(root / LEDGER)\n",
        ),
        # the public reader of the service's own ledger path
        lambda: _edited(_CALL, "        ledger = release.read_ledger()\n"),
        # the validator kept under another name
        lambda: _edited(_CALL, "        decode = release._decode\n        ledger = decode(text)\n"),
        lambda: _edited(_CALL, '        ledger = getattr(release, "_decode")(text)\n'),
        lambda: _edited(
            _IMPORT, _IMPORT + "        from churnsense.evaluation.release import _decode\n"
        ),
        # another module passed off as the validating one
        lambda: _edited(_IMPORT, "        import json as release\n"),
        lambda: _edited(_IMPORT, "        from tests import ledger_payloads as release\n"),
        lambda: _edited(_CALL, "        release = problems\n" + _CALL),
        # a second use, outside the one reader
        lambda: (
            _bundle_check_source()
            + "\n\ndef again(release, text):\n    return release._decode(text)\n"
        ),
        lambda: _edited(_CALL, _CALL + "        release._decode(text)\n"),
        lambda: _edited(_CALL, "        ledger = release._decode(text, strict=False)\n"),
    ],
)
def test_the_bundle_check_exception_admits_no_other_use_of_the_reader(bypass):
    """Each way round the validator, tried on a copy of the real source, is caught."""
    assert _bundle_check_validator_faults(_bundle_check_source()) == []
    assert _bundle_check_validator_faults(bypass()) != []


def test_no_other_program_may_borrow_the_bundle_check_exception():
    """The permission is one exact path, and the only private call into the release module."""
    assert (ROOT / BUNDLE_CHECK).is_file()
    private_call = re.compile(r"\brelease\._[a-z]")
    callers = {
        str(path.relative_to(ROOT))
        for folder in ("src", "app", "scripts")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if path != ROOT / "src/churnsense/evaluation/release.py"
        and private_call.search(path.read_text(encoding="utf-8"))
    }
    assert callers == {BUNDLE_CHECK}
    assert len(private_call.findall(_bundle_check_source())) == 1


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


def test_the_deploy_bundle_check_writes_nothing():
    """It may name the ledger only because it reads it: no write of any kind is in it."""
    source = (ROOT / "scripts" / "verify_deploy_bundle.py").read_text(encoding="utf-8")
    for writer in (
        "write_text",
        "write_bytes",
        "json.dump",
        "unlink",
        "rename(",
        "rmtree",
        "mkdir",
    ):
        assert writer not in source, writer
    modes = re.findall(r"open\(([^)]*)\)", source)
    assert modes == ['"rb"'], f"files are opened for reading only, not {modes}"


def test_the_deploy_bundle_check_loads_no_model_and_no_model_library():
    """It hashes the model file; it never imports what could unpickle or run it."""
    tree = ast.parse((ROOT / BUNDLE_CHECK).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported & {"churnsense.evaluation", "churnsense.exceptions"}
    for module in imported:
        root = module.split(".")[0]
        assert root not in ("joblib", "sklearn", "pickle", "numpy", "pandas", "shap"), module
        assert not module.startswith("churnsense.models"), module
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not called & {"load", "Predictor", "import_module", "__import__"}, called
    assert "pickle" not in called and "joblib" not in called
