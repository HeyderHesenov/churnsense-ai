"""The deployment files and the bundle check that guards them.

Every bundle here is synthetic: a model file of random bytes, a made-up
dataset, a ledger written for them. Nothing real is hashed, loaded or read,
except the committed deployment files themselves (configs, compose, Caddyfile,
Dockerfile, .dockerignore), which hold no data.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts import verify_deploy_bundle as bundle

from churnsense.config import load_config
from churnsense.demo import customers as demo
from tests.ledger_payloads import (
    IDENTITY,
    THRESHOLD,
    open_body,
    published_body,
    reserved_body,
    seal,
)

REPO = Path(__file__).resolve().parents[1]
SKLEARN = "1.9.1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(root: Path, name: str, data: bytes | str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def _with_dataset_pin(text: str, digest: str) -> str:
    pinned = yaml.safe_load(text)["dataset"]["sha256"]
    assert text.count(pinned) == 1
    return text.replace(pinned, digest)


#: A synthetic dataset's bytes: only its hash is ever in a bundle, as the pin.
PINNED_DATASET = b"customerID,tenure\nDEMO-0001,1\n"
SPLIT_DIGEST = "5" * 64


def _demo_data(root: Path, model_sha: str, data_sha: str, **evidence) -> None:
    """The published evidence and the demo attributions, bound to a model and a dataset."""
    identity = {
        "model_sha256": model_sha,
        "data_sha256": data_sha,
        "split_digest": SPLIT_DIGEST,
        "threshold": THRESHOLD,
    }
    body = dict.fromkeys(bundle.EVIDENCE_KEYS) | {"format": 1, "identity": identity} | evidence
    _write(root, bundle.EVIDENCE, json.dumps(body))
    _write(root, bundle.DEMO_EXPLANATIONS, json.dumps(_attributions(model_sha)))


@functools.cache
def _generated() -> tuple[str, tuple[str, ...], tuple[dict, ...]]:
    """The served demo book: its hash, its feature names and three of its customers."""
    book = demo.demo_customers()
    features = tuple(column for column in book.columns if column != "customerID")
    rows = json.loads(book.loc[:, list(features)].head(3).to_json(orient="records"))
    return demo.records_sha256(book), features, tuple(rows)


def _attributions(model_sha: str, **changes) -> dict:
    """The part of the demo attributions the check reads: of the generated book, by default."""
    records, features, rows = _generated()
    stated = {
        "model_sha256": model_sha,
        "partition": "synthetic",
        "records_sha256": records,
        "features": list(features),
        "x": [dict(row) for row in rows],
    }
    return dict.fromkeys(bundle.DEMO_EXPLANATION_KEYS) | stated | changes


def _ledger(**identity) -> str:
    """A published ledger the strict reader accepts, naming ``identity`` over the defaults."""
    return seal(published_body(identity={**IDENTITY, **identity}))


@pytest.fixture
def image(tmp_path) -> Path:
    """A complete, consistent synthetic image root. No dataset: a public demo holds none."""
    root = tmp_path / "app"
    model = os.urandom(512)
    model_sha, data_sha = _sha(model), _sha(PINNED_DATASET)
    _write(root, bundle.MODEL, model)
    _write(
        root,
        bundle.META,
        json.dumps(
            {
                "model_sha256": model_sha,
                "data_sha256": data_sha,
                "split_digest": SPLIT_DIGEST,
                "threshold": THRESHOLD,
                "sklearn_version": SKLEARN,
            }
        ),
    )
    _demo_data(root, model_sha, data_sha)
    _write(root, bundle.LEDGER, _ledger(model_sha256=model_sha, dataset_sha256=data_sha))
    _write(root, bundle.CONFIG, _with_dataset_pin((REPO / bundle.CONFIG).read_text(), data_sha))
    _write(
        root,
        bundle.DEPLOY_CONFIG,
        _with_dataset_pin((REPO / bundle.DEPLOY_CONFIG).read_text(), data_sha),
    )
    _write(
        root,
        bundle.LOCK,
        f"scikit-learn=={SKLEARN} ; sys_platform == 'linux' \\\n    --hash=sha256:00\n",
    )
    for report in bundle.REPORTS:
        _write(root, report, "a,b\n1,2\n")
    for module in bundle.MODULES:
        _write(root, module, "")
    _write(root, "frontend/dist/index.html", "<!doctype html>")
    _write(root, "frontend/dist/assets/index-abc123.js", "")
    _write(root, "scripts/verify_deploy_bundle.py", "")
    return root


def _identity(root: Path) -> dict:
    return json.loads((root / bundle.LEDGER).read_text())["identity"]


def _problems(root: Path, **kwargs) -> list[str]:
    kwargs.setdefault("allow_example_domain", True)
    return list(bundle.verify(root, in_image=True, **kwargs)[0])


def test_the_image_check_refuses_the_example_domain_unless_waived(image):
    assert any("documentation domain" in p for p in _problems(image, allow_example_domain=False))
    assert _problems(image, allow_example_domain=True) == []


def test_a_complete_bundle_passes(image):
    assert _problems(image) == []


@pytest.mark.parametrize(
    "name",
    [bundle.MODEL, bundle.LEDGER, bundle.DEPLOY_CONFIG, bundle.EVIDENCE, bundle.DEMO_EXPLANATIONS],
)
def test_a_missing_asset_is_named(image, name):
    (image / name).unlink()
    assert f"missing: {name}" in _problems(image)


def test_a_replaced_model_is_refused(image):
    _write(image, bundle.MODEL, os.urandom(512))
    assert any("does not hash to the model_sha256" in p for p in _problems(image))


def test_a_model_the_release_does_not_name_is_refused(image):
    model = os.urandom(256)
    _write(image, bundle.MODEL, model)
    meta = json.loads((image / bundle.META).read_text())
    _write(image, bundle.META, json.dumps({**meta, "model_sha256": _sha(model)}))
    _demo_data(image, _sha(model), meta["data_sha256"])
    assert _problems(image) == ["the model is not the one the published release names"]


def test_a_threshold_the_release_does_not_name_is_refused(image):
    meta = json.loads((image / bundle.META).read_text())
    _write(image, bundle.META, json.dumps({**meta, "threshold": 0.5}))
    assert "the model's threshold is not the published release's" in _problems(image)


@pytest.mark.parametrize("body", [open_body, reserved_body])
def test_an_unpublished_ledger_approves_nothing(image, body):
    _write(image, bundle.LEDGER, seal(body()))
    assert any("not a published release" in p for p in _problems(image))


def _sealed(mutate) -> str:
    """A published ledger changed by ``mutate`` and sealed again, so only the schema can refuse it."""
    body = published_body(identity={**IDENTITY})
    mutate(body)
    return seal(body)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        "[]",
        _sealed(lambda b: b.pop("policy")),
        _sealed(lambda b: b.update(extra="field")),
        _sealed(lambda b: b.update(state="released")),
        _sealed(lambda b: b.update(format="1")),
        _sealed(lambda b: b["identity"].update(model_sha256="not-a-digest")),
        _sealed(lambda b: b["identity"].pop("dataset_sha256")),
        _sealed(lambda b: b["identity"].update(threshold="0.36")),
        _sealed(lambda b: b.update(evaluated_at="yesterday")),
    ],
)
def test_a_ledger_the_strict_reader_refuses_is_refused(image, text):
    _write(image, bundle.LEDGER, text)
    problems = _problems(image)
    assert any("strict reader" in p or "not a published release" in p for p in problems), problems


def test_a_ledger_whose_digest_does_not_match_is_refused(image):
    text = (image / bundle.LEDGER).read_text()
    body = json.loads(text)
    body["evaluated_at"] = "2026-11-08T00:00:00+00:00"  # changed after sealing
    _write(image, bundle.LEDGER, json.dumps(body, indent=2, sort_keys=True) + "\n")
    assert any("strict reader" in p for p in _problems(image))


def test_the_dataset_pin_must_be_the_releases(image):
    _write(image, bundle.LEDGER, _ledger(model_sha256=_identity(image)["model_sha256"]))
    assert any("not the one the published release names" in p for p in _problems(image))


def test_the_check_takes_no_dataset_file(image, capsys):
    """No dataset is deployed, so there is none to hand the check: the option is gone."""
    with pytest.raises(SystemExit) as refused:
        bundle.main(["--in-image", "--root", str(image), "--dataset", "/srv/x.csv"])
    assert refused.value.code == 2
    capsys.readouterr()


def _meta(image: Path) -> dict:
    return json.loads((image / bundle.META).read_text())


def test_demo_attributions_for_another_model_are_refused(image):
    _write(
        image,
        bundle.DEMO_EXPLANATIONS,
        json.dumps(
            {"model_sha256": "0" * 64, "partition": "synthetic", "records_sha256": "a" * 64}
        ),
    )
    assert any("was not computed for this model" in p for p in _problems(image))


@pytest.mark.parametrize(
    "body",
    [
        {"partition": "validation", "records_sha256": "a" * 64},
        {"partition": "synthetic"},
        {"partition": "synthetic", "records_sha256": "not a hash"},
    ],
)
def test_attributions_that_are_not_of_generated_customers_are_refused(image, body):
    """``make explain``'s cache under the demo file's name is still validation rows."""
    _write(
        image,
        bundle.DEMO_EXPLANATIONS,
        json.dumps({"model_sha256": _meta(image)["model_sha256"]} | body),
    )
    assert _problems(image) == [f"{bundle.DEMO_EXPLANATIONS} is not of generated demo customers"]


def _demo_problems(image: Path, **changes) -> list[str]:
    body = _attributions(_meta(image)["model_sha256"], **changes)
    _write(image, bundle.DEMO_EXPLANATIONS, json.dumps(body))
    return _problems(image)


def _in_month(tenure: int) -> dict:
    """A generated customer with this tenure, as the attributions would hold them."""
    book = demo.demo_customers()
    features = list(_generated()[1])
    held = book.loc[book["tenure"] == tenure, features].head(1)
    assert len(held) == 1
    return json.loads(held.to_json(orient="records"))[0]


def test_attributions_of_another_generated_book_are_refused(image):
    """A well-formed hash is not enough: it must be the hash of the book the service lists."""
    other = demo.records_sha256(demo.demo_customers(seed=demo.SEED + 1))
    assert _demo_problems(image, records_sha256=other) == [
        f"{bundle.DEMO_EXPLANATIONS} does not explain the demo customers the service generates"
    ]


def _one_changed(feature: str, value: object) -> list[dict]:
    rows = [dict(row) for row in _generated()[2]]
    assert rows[1][feature] != value
    rows[1][feature] = value
    return rows


@pytest.mark.parametrize(
    ("feature", "value"),
    [("tenure", 73.0), ("MonthlyCharges", 0.01), ("Contract", "Three year")],
)
def test_a_row_that_is_no_generated_customers_is_refused(image, feature, value):
    """Whatever the file says of itself: validation rows under a correct header are refused."""
    assert _demo_problems(image, x=_one_changed(feature, value)) == [
        f"{bundle.DEMO_EXPLANATIONS} draws a row that is no generated customer's"
    ]


def test_a_row_may_hold_nothing_beside_its_features(image):
    """An extra field is where a record would ride along."""
    rows = [dict(row) | {"note": "kept"} for row in _generated()[2]]
    assert _demo_problems(image, x=rows) == [
        f"{bundle.DEMO_EXPLANATIONS} draws a row that is no generated customer's"
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"x": []},
        {"x": "rows"},
        {"x": [["0", "Yes"]]},
        {"x": [{}]},
        {"features": []},
        {"features": "tenure"},
        {"features": [["tenure"]]},
        {"features": ["tenure", "tenure"], "x": [{"tenure": 1.0}]},
        {"features": ["not a column"], "x": [{"not a column": 1.0}]},
        # One real value of a generated customer is not that customer's record.
        {"features": ["tenure"], "x": [{"tenure": _generated()[2][0]["tenure"]}]},
        # A customer in their first month: JSON's true must not pass for the number 1.
        {"x": [_in_month(1) | {"tenure": True}]},
        {"x": [dict(_generated()[2][0]) | {"tenure": None}]},
        {"x": [dict(_generated()[2][0]) | {"Partner": ["Yes"]}]},
    ],
)
def test_attributions_whose_rows_cannot_be_read_are_refused(image, changes):
    assert _demo_problems(image, **changes) == [
        f"{bundle.DEMO_EXPLANATIONS} draws a row that is no generated customer's"
    ]


def test_rows_are_matched_whole_never_feature_by_feature(image):
    """Two customers' values mixed into one row are nobody's record."""
    first, second = (dict(row) for row in _generated()[2][:2])
    assert first["MonthlyCharges"] != second["MonthlyCharges"]
    mixed = first | {"MonthlyCharges": second["MonthlyCharges"]}
    assert _demo_problems(image, x=[mixed]) == [
        f"{bundle.DEMO_EXPLANATIONS} draws a row that is no generated customer's"
    ]


@pytest.mark.parametrize("extra", ["meta", "customers", "x_validation"])
def test_attributions_may_hold_nothing_beside_their_named_keys(image, extra):
    """Whatever sits under another key is in the image, checked by nobody."""
    assert _demo_problems(image, **{extra: [{"tenure": 1}]}) == [
        f"{bundle.DEMO_EXPLANATIONS} is not the demo attributions' closed format"
    ]


def test_attributions_must_hold_every_named_key(image):
    body = _attributions(_meta(image)["model_sha256"])
    del body["values"]
    _write(image, bundle.DEMO_EXPLANATIONS, json.dumps(body))
    assert _problems(image) == [
        f"{bundle.DEMO_EXPLANATIONS} is not the demo attributions' closed format"
    ]


def test_a_whole_number_is_the_number_however_json_spells_it(image):
    """36 and 36.0 are one tenure: the builder writes floats, the generator draws integers."""
    row = _in_month(1)
    assert _demo_problems(image, x=[row | {"tenure": 1}]) == []
    assert _demo_problems(image, x=[row | {"tenure": 1.0}]) == []


def test_any_generated_customer_may_be_explained(image):
    """Not just the first rows: the sample is drawn from the whole book."""
    book = demo.demo_customers()
    features = list(_generated()[1])
    rows = json.loads(book.loc[:, features].sample(40, random_state=0).to_json(orient="records"))
    assert _demo_problems(image, x=rows) == []


@pytest.mark.parametrize("bound", ["model_sha256", "data_sha256", "split_digest", "threshold"])
def test_evidence_of_another_model_dataset_split_or_threshold_is_refused(image, bound):
    meta = _meta(image)
    evidence = json.loads((image / bundle.EVIDENCE).read_text())
    evidence["identity"][bound] = 0.123 if bound == "threshold" else "9" * 64
    _write(image, bundle.EVIDENCE, json.dumps(evidence))
    assert _problems(image) == [
        f"{bundle.EVIDENCE} was not computed for this model, its dataset and its split"
    ]
    assert meta[bound] != evidence["identity"][bound]


def test_evidence_of_a_dataset_other_than_the_pinned_one_is_refused(image):
    """Bound to the model's metadata and to the pin: both must name one dataset."""
    meta = _meta(image) | {"data_sha256": "7" * 64}
    _write(image, bundle.META, json.dumps(meta))
    _demo_data(image, meta["model_sha256"], meta["data_sha256"])
    assert f"{bundle.EVIDENCE} is not of the dataset pinned in {bundle.CONFIG}" in _problems(image)


_NOT_CLOSED = f"{bundle.EVIDENCE} is not the published evidence's closed format"
_NOT_BOUND = f"{bundle.EVIDENCE} was not computed for this model, its dataset and its split"


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda body: body.update(customers=[{"tenure": 1}]), _NOT_CLOSED),
        (lambda body: body.update(note="kept"), _NOT_CLOSED),
        (lambda body: body.pop("sweep_basis"), _NOT_CLOSED),
        (lambda body: body.update(identity=None), _NOT_BOUND),
        (lambda body: body.update(identity=[]), _NOT_BOUND),
    ],
)
def test_evidence_outside_the_closed_format_is_refused(image, change, problem):
    body = json.loads((image / bundle.EVIDENCE).read_text())
    change(body)
    _write(image, bundle.EVIDENCE, json.dumps(body))
    assert _problems(image) == [problem]


@pytest.mark.parametrize(
    "smuggled",
    [
        {"observed": {"customerID": "1234-ABCDE"}},
        {"breakdowns": [{"cells": [{"row": "x", "tenure": 3, "MonthlyCharges": 29.85}]}]},
        {"importance": {"ranking": [[{"Churn": 1}]]}},
    ],
)
def test_evidence_that_holds_a_customer_column_is_refused(image, smuggled):
    """A record tucked inside a permitted key is still a record: no column may be a key."""
    meta = _meta(image)
    _demo_data(image, meta["model_sha256"], meta["data_sha256"], **smuggled)
    assert _problems(image) == [
        f"{bundle.EVIDENCE} holds a customer record: it may hold aggregates only"
    ]


def test_the_checks_own_lists_are_the_services():
    """The check re-declares two lists so it need not import the data stack: held equal here."""
    from churnsense import evidence
    from churnsense.data import schema

    assert set(evidence._TOP_LEVEL) == bundle.EVIDENCE_KEYS
    assert set(schema.RAW_COLUMNS) == bundle.RECORD_COLUMNS
    assert Path(bundle.EVIDENCE).name == evidence.EVIDENCE_FILE
    assert Path(bundle.DEMO_EXPLANATIONS).name == evidence.DEMO_EXPLANATIONS_FILE


def test_a_lock_that_does_not_pin_the_training_sklearn_is_refused(image):
    _write(image, bundle.LOCK, "scikit-learn==1.8.0 \\\n")
    assert any("does not pin scikit-learn" in p for p in _problems(image))


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "configs/.env.production",
        ".git/HEAD",
        "audit-reports/SECURITY_AUDIT.md",
        "tests/test_x.py",
        "reports/held_out_release.md",
        "reports/final_evaluation.md",
        "data/interim/x.csv",
        "data/raw/Telco-Customer-Churn.csv",
        "data/raw/.gitkeep",
        "reports/Telco-Customer-Churn.csv",
        "frontend/dist/assets/Telco-Customer-Churn.csv",
        "artifacts/shap_global.json",
        "src/churnsense/shap_global.json",
        "frontend/dist/shap_global.json",
        "src/churnsense.egg-info/PKG-INFO",
        "artifacts/model.pkl",
        "src/churnsense/__pycache__/x.pyc",
        "frontend/node_modules/x/index.js",
        "frontend/src/main.tsx",
        "notebooks/x.ipynb",
        "server.pem",
        "configs/tls.key",
    ],
)
def test_a_file_the_image_must_not_hold_is_named(image, name):
    _write(image, name, "x")
    problems = _problems(image)
    assert len(problems) == 1 and name in problems[0]


@pytest.mark.parametrize(
    "name",
    ["src/churnsense/notes.txt", "frontend/dist/assets/data.js", "reports/extra/book.csv"],
)
def test_the_pinned_dataset_under_any_name_is_refused(image, name):
    """By content, not by name: the file the configuration pins is in no image."""
    _write(image, name, PINNED_DATASET)
    assert f"forbidden in the image: {name} is the pinned dataset" in _problems(image)


def test_an_image_without_the_built_front_end_is_refused(image):
    shutil.rmtree(image / "frontend")
    assert any("built front end" in p for p in _problems(image))


def test_a_deploy_config_that_drifts_from_the_development_one_is_refused(image):
    text = (
        (image / bundle.DEPLOY_CONFIG)
        .read_text()
        .replace("gross_margin: 0.65", "gross_margin: 0.9")
    )
    _write(image, bundle.DEPLOY_CONFIG, text)
    assert any("differs from" in p for p in _problems(image))


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "allowed_hosts: [demo.example.com, 127.0.0.1]",
            'allowed_hosts: ["*"]',
            "allows every host",
        ),
        (
            "allowed_hosts: [demo.example.com, 127.0.0.1]",
            "allowed_hosts: [127.0.0.1]",
            "no public host",
        ),
        ("trusted_proxies: [172.30.10.2]", "trusted_proxies: []", "trusts no proxy"),
        (
            "trusted_proxies: [172.30.10.2]",
            "trusted_proxies: [0.0.0.0/0]",
            "api section is invalid",
        ),
    ],
)
def test_an_unsafe_deploy_api_section_is_refused(image, old, new, expected):
    text = (image / bundle.DEPLOY_CONFIG).read_text()
    assert old in text
    _write(image, bundle.DEPLOY_CONFIG, text.replace(old, new))
    assert any(expected in p for p in _problems(image))


def test_the_command_fails_loudly_and_prints_no_contents(image, capsys):
    _write(image, bundle.MODEL, b"secret-looking bytes " + os.urandom(64))
    assert bundle.main(["--in-image", "--allow-example-domain", "--root", str(image)]) == 1
    out = capsys.readouterr()
    assert "PROBLEM" in out.err and "NOT ready" in out.err
    assert "secret-looking" not in out.out + out.err
    meta_sha = json.loads((image / bundle.META).read_text())["model_sha256"]
    assert meta_sha not in out.out + out.err, "no recorded hash is printed"
    assert out.out == "", "a failing run prints nothing to stdout"


def test_the_command_passes_a_complete_bundle(image, capsys):
    assert bundle.main(["--in-image", "--allow-example-domain", "--root", str(image)]) == 0
    out = capsys.readouterr()
    assert out.out == "deployment bundle ready\n" == bundle.PASSED + "\n"
    assert out.err == ""


def _repo_bundle(tmp_path: Path, image: Path) -> Path:
    """The synthetic bundle laid out as a checkout: the committed deployment files beside it."""
    root = tmp_path / "checkout"
    shutil.copytree(image, root)
    for name in ("Dockerfile", ".dockerignore", "deploy/compose.yaml", "deploy/Caddyfile"):
        _write(root, name, (REPO / name).read_text())
    return root


@pytest.mark.parametrize("mode", ["image", "checkout"])
def test_a_passing_run_prints_one_fixed_line_and_nothing_it_found(image, tmp_path, capsys, mode):
    """What the checks found - hash prefixes, hosts, proxy addresses - never reaches the output."""
    if mode == "image":
        root, argv = image, ["--in-image"]
    else:
        root, argv = _repo_bundle(tmp_path, image), []
    argv += ["--allow-example-domain", "--root", str(root)]
    problems, notes = bundle.verify(root, in_image=mode == "image", allow_example_domain=True)
    assert list(problems) == []
    assert len(notes) >= 5, "the checks still record what they found, for a caller"
    assert bundle.main(argv) == 0
    out = capsys.readouterr()
    assert out.out == bundle.PASSED + "\n" and out.err == ""
    deploy = yaml.safe_load((root / bundle.DEPLOY_CONFIG).read_text())
    meta = json.loads((root / bundle.META).read_text())
    found = [
        *notes,
        meta["model_sha256"][:12],
        _sha(PINNED_DATASET)[:12],
        *deploy["api"]["allowed_hosts"],
        *deploy["api"]["trusted_proxies"],
        "172.30.10.3",
    ]
    for item in found:
        assert str(item) not in out.out, item


def test_the_output_is_fixed_text_never_a_note():
    """No print in the command takes its text from the notes."""
    tree = ast.parse((REPO / "scripts" / "verify_deploy_bundle.py").read_text())
    main = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    prints = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print"
    ]
    assert prints and all(call in list(ast.walk(main)) for call in prints)
    for call in prints:
        names = {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}
        assert not names & {"note", "notes"}, ast.unparse(call)
    assert bundle.PASSED == "deployment bundle ready"


def test_the_check_never_loads_a_model_or_the_data_stack(image):
    """Run it as the build does: the release reader brings pandas and numpy, but the model
    stack - joblib (unpickling) and scikit-learn - must stay unimported."""
    probe = (
        "import runpy, sys\n"
        f"sys.argv = ['verify', '--in-image', '--allow-example-domain', '--root', {str(image)!r}]\n"
        "try:\n"
        f"    runpy.run_path({str(REPO / 'scripts' / 'verify_deploy_bundle.py')!r}, run_name='__main__')\n"
        "except SystemExit as exit_:\n"
        "    assert exit_.code == 0, exit_.code\n"
        "loaded = {'joblib', 'sklearn'} & set(sys.modules)\n"
        "assert not loaded, loaded\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


# --- the committed deployment files ---------------------------------------------------------


def test_the_deploy_config_differs_from_the_development_one_in_its_stated_keys_only():
    config = yaml.safe_load((REPO / bundle.CONFIG).read_text())
    deploy = yaml.safe_load((REPO / bundle.DEPLOY_CONFIG).read_text())
    assert bundle._without_deploy_keys(config) == bundle._without_deploy_keys(deploy)
    assert config["api"].get("trusted_proxies") is None, "the local default trusts no proxy"
    assert config["deployment"] == {"mode": "local", "customer_records": "dataset"}
    assert deploy["deployment"] == bundle.PUBLIC_DEPLOYMENT


def test_the_deploy_config_loads_and_answers_only_its_host_and_proxy():
    cfg = load_config(REPO / bundle.DEPLOY_CONFIG)
    assert cfg.api.allowed_hosts == ("demo.example.com", "127.0.0.1")
    assert cfg.api.trusted_proxies == ("172.30.10.2/32",)
    assert "localhost" not in cfg.api.allowed_hosts


def test_the_deploy_config_is_a_public_demo_of_synthetic_records():
    cfg = load_config(REPO / bundle.DEPLOY_CONFIG)
    assert cfg.deployment.public and cfg.deployment.synthetic


@pytest.mark.parametrize(
    "section",
    [
        None,
        "deployment:\n  mode: local\n  customer_records: synthetic\n",
        "deployment:\n  mode: local\n  customer_records: dataset\n",
        "deployment:\n  mode: public_demo\n  customer_records: dataset\n",
        "deployment:\n  mode: public_demo\n",
        "deployment:\n  mode: public_demo\n  customer_records: synthetic\n  extra: 1\n",
    ],
)
def test_a_deploy_config_that_could_list_dataset_customers_is_refused(image, section):
    """Whatever is not exactly a public demo of synthetic records does not pass."""
    text = (image / bundle.DEPLOY_CONFIG).read_text()
    stated = "deployment:\n  mode: public_demo\n  customer_records: synthetic\n"
    assert text.count(stated) == 1
    _write(image, bundle.DEPLOY_CONFIG, text.replace(stated, section or ""))
    assert any("must set deployment to mode public_demo" in p for p in _problems(image))


def test_the_policy_is_not_read_from_the_proxies_or_the_hosts(image):
    """A bundle that trusts a proxy and names a public host is still refused without the policy."""
    text = (image / bundle.DEPLOY_CONFIG).read_text()
    stated = "deployment:\n  mode: public_demo\n  customer_records: synthetic\n"
    _write(image, bundle.DEPLOY_CONFIG, text.replace(stated, ""))
    deploy = yaml.safe_load((image / bundle.DEPLOY_CONFIG).read_text())
    assert deploy["api"]["trusted_proxies"] and deploy["api"]["allowed_hosts"]
    assert any("public_demo" in p for p in _problems(image))


def test_the_committed_wiring_and_dockerignore_agree(capsys):
    problems: bundle.Problems = bundle.Problems()
    notes: list[str] = []
    deploy = yaml.safe_load((REPO / bundle.DEPLOY_CONFIG).read_text())
    bundle.check_wiring(REPO, deploy, problems, notes)
    bundle.check_dockerignore(REPO, problems)
    assert list(problems) == []


def test_the_example_domain_is_refused_until_replaced():
    deploy = yaml.safe_load((REPO / bundle.DEPLOY_CONFIG).read_text())
    problems = bundle.Problems()
    bundle.check_domain(deploy, problems)
    assert list(problems) == [
        "demo.example.com is a documentation domain: set the real one in "
        "configs/deploy.yaml and deploy/Caddyfile"
    ]
    real = {**deploy, "api": {**deploy["api"], "allowed_hosts": ["churn.heyder.dev", "127.0.0.1"]}}
    problems = bundle.Problems()
    bundle.check_domain(real, problems)
    assert list(problems) == []


@pytest.mark.parametrize(
    "host", ["example.com", "x.example.org", "demo.test", "a.invalid", "b.example"]
)
def test_documentation_domains_are_recognised(host):
    assert bundle.EXAMPLE_HOST.search(host)


@pytest.mark.parametrize(
    "host", ["churn.heyder.dev", "examples.com", "my-example.com", "testing.io"]
)
def test_real_domains_are_not_mistaken_for_examples(host):
    assert not bundle.EXAMPLE_HOST.search(host)


def test_a_published_app_port_or_a_mismatched_proxy_is_refused(tmp_path):
    for name in ("deploy/compose.yaml", "deploy/Caddyfile"):
        _write(tmp_path, name, (REPO / name).read_text())
    deploy = yaml.safe_load((REPO / bundle.DEPLOY_CONFIG).read_text())
    compose = yaml.safe_load((tmp_path / "deploy/compose.yaml").read_text())
    compose["services"]["app"]["ports"] = ["8000:8000"]
    compose["services"]["caddy"]["networks"]["proxy"]["ipv4_address"] = "172.30.10.4"
    _write(tmp_path, "deploy/compose.yaml", yaml.safe_dump(compose))
    problems = bundle.Problems()
    bundle.check_wiring(tmp_path, deploy, problems, [])
    assert any("publishes the app's port" in p for p in problems)
    assert any("not the trusted proxy" in p for p in problems)


def test_a_caddy_site_other_than_the_allowed_host_is_refused(tmp_path):
    for name in ("deploy/compose.yaml", "deploy/Caddyfile"):
        _write(tmp_path, name, (REPO / name).read_text())
    caddyfile = (
        (tmp_path / "deploy/Caddyfile")
        .read_text()
        .replace("demo.example.com {", "other.example.com {")
    )
    _write(tmp_path, "deploy/Caddyfile", caddyfile)
    problems = bundle.Problems()
    bundle.check_wiring(
        tmp_path, yaml.safe_load((REPO / bundle.DEPLOY_CONFIG).read_text()), problems, []
    )
    assert any("deploy/Caddyfile serves" in p for p in problems)


def test_the_caddyfile_site_addresses_are_read_past_the_global_block():
    text = "{\n\tservers {\n\t\ttimeouts {\n\t\t\tidle 2m\n\t\t}\n\t}\n}\n\na.dev, b.dev {\n\tfoo {\n\t}\n}\n"
    assert bundle.caddy_sites(text) == ["a.dev", "b.dev"]


def test_a_dockerignore_that_lets_more_in_is_refused(tmp_path):
    text = (REPO / ".dockerignore").read_text() + "\n!audit-reports/\n"
    _write(tmp_path, ".dockerignore", text)
    problems = bundle.Problems()
    bundle.check_dockerignore(tmp_path, problems)
    assert len(problems) == 1 and "audit-reports/" in problems[0]


def test_the_image_runs_one_worker_without_uvicorns_proxy_handling():
    dockerfile = (REPO / "Dockerfile").read_text()
    cmd = dockerfile[dockerfile.index("\nCMD ") :]
    assert '"--no-proxy-headers"' in cmd
    assert '"--workers", "1"' in cmd
    assert '"--no-access-log"' in cmd, "the access log would record every visitor's address"
    assert "ARG ALLOW_EXAMPLE_DOMAIN=\n" in dockerfile, "the waiver is off unless a build asks"
    assert "--reload" not in dockerfile
    assert "--forwarded-allow-ips" not in dockerfile
    assert "\nUSER churnsense\n" in dockerfile
    assert "/ready" in dockerfile[dockerfile.index("HEALTHCHECK") :]
    assert "requirements-dev" not in dockerfile
    assert "pip install --require-hashes -r requirements.txt" in dockerfile
    assert "verify_deploy_bundle.py --in-image" in dockerfile


def test_the_image_copies_exactly_what_dockerignore_lets_in():
    """Every COPY source from the build context is re-included, and nothing re-included is unused."""
    dockerfile = (REPO / "Dockerfile").read_text().replace("\\\n", " ")
    sources: set[str] = set()
    for line in dockerfile.splitlines():
        words = line.split()
        if words[:1] == ["COPY"] and not any(w.startswith("--from") for w in words):
            sources.update(words[1:-1])
    covered = {
        s
        for s in sources
        if any(s == i or s.startswith(i) for i in bundle.DOCKER_CONTEXT if i.endswith("/"))
        or s in bundle.DOCKER_CONTEXT
    }
    assert covered == sources
    used = {i for i in bundle.DOCKER_CONTEXT if any(s == i or s.startswith(i) for s in sources)}
    assert used == set(bundle.DOCKER_CONTEXT)


def test_compose_keeps_the_app_private_and_bounded():
    compose = yaml.safe_load((REPO / "deploy/compose.yaml").read_text())
    app, caddy = compose["services"]["app"], compose["services"]["caddy"]
    assert "ports" not in app
    assert set(app["networks"]) == {"proxy"}
    assert compose["networks"]["proxy"]["internal"] is True
    assert app["environment"]["CHURNSENSE_CONFIG"] == "configs/deploy.yaml"
    assert app["read_only"] is True and app["cap_drop"] == ["ALL"]
    assert "/ready" in " ".join(app["healthcheck"]["test"])
    assert caddy["depends_on"]["app"]["condition"] == "service_healthy"
    assert sorted(caddy["ports"]) == ["443:443", "443:443/udp", "80:80"]
    for service in (app, caddy):
        assert service["logging"]["options"]["max-size"]
        assert service["deploy"]["resources"]["limits"]["memory"]
    assert set(compose["volumes"]) == {"caddy_data", "caddy_config"}, "no application data volume"


def _wiring(tmp_path, *, deploy_edit=None, compose_edit=None, caddy_extra="") -> list[str]:
    for name in ("deploy/compose.yaml", "deploy/Caddyfile"):
        _write(tmp_path, name, (REPO / name).read_text())
    deploy = yaml.safe_load((REPO / bundle.DEPLOY_CONFIG).read_text())
    if deploy_edit:
        deploy_edit(deploy)
    if compose_edit:
        compose = yaml.safe_load((tmp_path / "deploy/compose.yaml").read_text())
        compose_edit(compose)
        _write(tmp_path, "deploy/compose.yaml", yaml.safe_dump(compose))
    if caddy_extra:
        text = (tmp_path / "deploy/Caddyfile").read_text()
        _write(
            tmp_path,
            "deploy/Caddyfile",
            text.replace("\tservers {", f"\tservers {{\n\t\t{caddy_extra}", 1),
        )
    problems = bundle.Problems()
    bundle.check_wiring(tmp_path, deploy, problems, [])
    return list(problems)


def test_the_network_gateway_cannot_be_a_trusted_proxy(tmp_path):
    def add_gateway(deploy):
        deploy["api"]["trusted_proxies"] = ["172.30.10.2", "172.30.10.1"]

    assert any("gateway" in p for p in _wiring(tmp_path, deploy_edit=add_gateway))


def test_an_ipv6_trusted_proxy_is_refused_on_the_ipv4_proxy_network(tmp_path):
    def add_v6(deploy):
        deploy["api"]["trusted_proxies"] = ["172.30.10.2", "fd00:30::2"]

    assert any(
        "outside the internal proxy network" in p for p in _wiring(tmp_path, deploy_edit=add_v6)
    )


def test_caddy_must_not_trust_forwarded_headers_from_clients(tmp_path):
    problems = _wiring(tmp_path, caddy_extra="trusted_proxies static private_ranges")
    assert any("trusted_proxies" in p for p in problems)


def test_only_the_app_and_caddy_may_join_the_proxy_network(tmp_path):
    def add_service(compose):
        compose["services"]["other"] = {"image": "x", "networks": {"proxy": {}}}

    assert any("only the app and Caddy" in p for p in _wiring(tmp_path, compose_edit=add_service))


def test_the_committed_wiring_passes_the_stricter_checks(tmp_path):
    assert _wiring(tmp_path) == []


# --- P1-1: every external image pinned by version and digest -------------------------------

PINNED = "@sha256:" + "a" * 64


def _image_problems(tmp_path, dockerfile: str, caddy_image: str) -> list[str]:
    _write(tmp_path, "Dockerfile", dockerfile)
    compose = yaml.safe_load((REPO / "deploy/compose.yaml").read_text())
    compose["services"]["caddy"]["image"] = caddy_image
    _write(tmp_path, "deploy/compose.yaml", yaml.safe_dump(compose))
    problems = bundle.Problems()
    bundle.check_images(tmp_path, problems, [])
    return list(problems)


def test_the_committed_images_are_all_pinned_by_digest():
    problems, notes = bundle.Problems(), []
    bundle.check_images(REPO, problems, notes)
    assert list(problems) == []
    dockerfile = (REPO / "Dockerfile").read_text()
    assert bundle._from_images(dockerfile) == [
        "node:22.23.4-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d",
        "python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36",
    ]
    compose = yaml.safe_load((REPO / "deploy/compose.yaml").read_text())
    assert compose["services"]["caddy"]["image"] == (
        "caddy:2.11.9@sha256:0c2243efcf437ab7f7a978f1027fcf0d4f9be8a75df27b1b3fbc5dbe0ffa8cf0"
    )


@pytest.mark.parametrize(
    "base",
    [
        "python:3.12.13-slim",
        "python@sha256:" + "a" * 64,
        "python:3.12.13-slim@sha256:" + "a" * 63,
        "python:3.12.13-slim@sha256:" + "A" * 64,
        "python:3.12.13-slim@sha512:" + "a" * 64,
        "python:latest",
    ],
)
def test_a_base_image_without_a_valid_digest_is_refused(tmp_path, base):
    dockerfile = (
        f"FROM node:22{PINNED} AS frontend\nFROM {base} AS runtime\nCOPY --from=frontend /a /b\n"
    )
    problems = _image_problems(tmp_path, dockerfile, f"caddy:2.11.9{PINNED}")
    assert len(problems) == 1 and base in problems[0]


@pytest.mark.parametrize("caddy", ["caddy:2", "caddy", "caddy:latest", "caddy@sha256:" + "a" * 64])
def test_a_floating_caddy_is_refused(tmp_path, caddy):
    dockerfile = f"FROM python:3.12.13-slim{PINNED}\n"
    problems = _image_problems(tmp_path, dockerfile, caddy)
    assert len(problems) == 1 and "compose service caddy" in problems[0]


def test_a_stage_reference_is_not_an_external_image():
    text = f"FROM --platform=linux/amd64 node:22{PINNED} AS build\nFROM build AS again\n"
    assert bundle._from_images(text) == [f"node:22{PINNED}"]


# --- no dataset in the image, its context, or in reach of the container --------------------


def test_no_dataset_and_no_dataset_row_enters_the_build_context_or_the_image():
    rules = [line.strip() for line in (REPO / ".dockerignore").read_text().splitlines()]
    included = [rule for rule in rules if rule.startswith("!")]
    assert not any("data" in rule.split("/")[0] or "Telco" in rule for rule in included)
    assert not any("shap_global" in rule for rule in included)
    dockerfile = (REPO / "Dockerfile").read_text()
    copies = [line for line in dockerfile.splitlines() if line.startswith(("COPY", "ADD"))]
    assert not any("data/" in line or "Telco" in line for line in copies)
    assert not any("shap_global" in line for line in copies)
    for held in (*bundle.DOCKER_CONTEXT, *bundle.ASSETS):
        assert "shap_global" not in held and not held.startswith("data")
    assert "data" not in bundle.IMAGE_TOP_LEVEL


def test_the_image_makes_no_place_for_a_dataset():
    """No ``data/raw`` mount point is created: there is nothing to mount there."""
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "mkdir" not in dockerfile and "VOLUME" not in dockerfile


def test_the_app_mounts_nothing_and_names_no_dataset():
    text = (REPO / "deploy/compose.yaml").read_text()
    app = yaml.safe_load(text)["services"]["app"]
    for key in ("volumes", "volumes_from", "configs", "secrets", "devices", "env_file"):
        assert key not in app, key
    assert set(app["environment"]) == {"CHURNSENSE_CONFIG", "CHURNSENSE_LOG_LEVEL"}
    assert "CHURNSENSE_DATASET" not in text and "Telco" not in text
    assert app["read_only"] is True and all(str(t).startswith("/tmp") for t in app["tmpfs"])


DATASET_MOUNT = {
    "type": "bind",
    "source": "/srv/churnsense/Telco-Customer-Churn.csv",
    "target": "/app/data/raw/Telco-Customer-Churn.csv",
    "read_only": True,
    "bind": {"create_host_path": False},
}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        # the earlier layout's own mount: one file, read-only
        ("volumes", [DATASET_MOUNT]),
        ("volumes", ["/srv:/srv:ro"]),
        ("volumes", ["caddy_data:/data"]),
        ("volumes", [{"type": "volume", "source": "caddy_data", "target": "/app/data"}]),
        ("volumes_from", ["caddy"]),
        ("configs", ["dataset"]),
        ("secrets", ["dataset"]),
        ("devices", ["/dev/sda:/dev/sda"]),
        # a host file read into the environment
        ("env_file", ["/srv/churnsense/.env"]),
        # another file's service, with whatever it mounts
        ("extends", {"file": "other.yaml", "service": "app"}),
    ],
)
def test_anything_the_app_would_mount_is_refused(tmp_path, key, value):
    def edit(compose):
        compose["services"]["app"][key] = value

    problems = _wiring(tmp_path, compose_edit=edit)
    assert any(f"must not declare {key}" in p for p in problems), problems


def test_a_compose_file_that_includes_another_is_refused(tmp_path):
    """What an included file declares is read by Compose and by no check."""

    def edit(compose):
        compose["include"] = ["extra.yaml"]

    assert _wiring(tmp_path, compose_edit=edit) == [
        "deploy/compose.yaml must not include another file: nothing in it would be checked"
    ]


@pytest.mark.parametrize(
    "name",
    [
        "compose.override.yaml",
        "compose.override.yml",
        "docker-compose.override.yaml",
        "docker-compose.override.yml",
    ],
)
def test_an_override_file_beside_the_compose_file_is_refused(tmp_path, name):
    """``docker compose up`` merges it without being asked: a mount there is a mount."""
    _write(tmp_path, f"deploy/{name}", "services:\n  app:\n    volumes: ['/srv:/srv:ro']\n")
    assert _wiring(tmp_path) == [
        f"deploy/{name} would be merged into compose.yaml unchecked: remove it"
    ]


def test_the_committed_deploy_folder_holds_no_override():
    assert not [p.name for p in (REPO / "deploy").iterdir() if "override" in p.name]


def test_a_compose_file_that_names_the_dataset_variable_is_refused(tmp_path):
    def edit(compose):
        compose["services"]["app"]["environment"]["DATASET"] = "${CHURNSENSE_DATASET}"

    assert any("CHURNSENSE_DATASET" in p for p in _wiring(tmp_path, compose_edit=edit))


def test_without_its_dataset_the_service_is_not_ready(monkeypatch, tmp_path, dashboard_predictor):
    """The real loader against a missing file and a file that is not the pinned one: never ready."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from churnsense.api import main as api_main
    from churnsense.api import spa, state
    from churnsense.book import load_book

    dist = tmp_path / "dist"
    _write(dist, "index.html", "<!doctype html>")
    monkeypatch.setattr(spa, "FRONTEND_DIST", dist)
    raw = tmp_path / "raw"
    raw.mkdir()

    def loader_for(file_bytes: bytes | None):
        def load(model):
            cfg = replace(
                model.config,
                paths=replace(model.config.paths, raw_dir=raw),
            )
            if file_bytes is not None:
                _write(raw, cfg.dataset.filename, file_bytes)
            return load_book(replace(model, config=cfg))

        return load

    for file_bytes in (None, b"customerID,tenure\nDEMO-0001,1\n"):
        monkeypatch.setattr(state, "BOOK_LOADER", loader_for(file_bytes))
        monkeypatch.setattr(state, "ARTIFACTS_DIR", dashboard_predictor.source)
        state.reset()
        with TestClient(api_main.app, base_url="http://localhost", client=("127.0.0.1", 1)) as c:
            ready = c.get("/ready")
        state.reset()
        assert ready.status_code == 503
        assert ready.json()["dataset"] is False and ready.json()["model"] is True


# --- P2-3: no egg-info, and a tag that never claims a clean commit it is not ---------------


def test_the_build_context_and_the_image_leave_egg_info_out():
    assert "**/*.egg-info" in (REPO / ".dockerignore").read_text().splitlines()


def test_the_image_says_unknown_provenance_unless_told():
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "ARG SOURCE_REVISION=unknown\n" in dockerfile
    assert "ARG SOURCE_DIRTY=unknown\n" in dockerfile
    assert 'org.opencontainers.image.revision="${SOURCE_REVISION}"' in dockerfile
    assert 'io.churnsense.source.dirty="${SOURCE_DIRTY}"' in dockerfile


HEAD = "c8495555f9553c72dbaf9a8eae693e1e3ba3d148"


def test_a_clean_tree_is_tagged_with_its_commit():
    from scripts import image_provenance as prov

    found = prov.provenance(HEAD + "\n", "")
    assert (found.tag, found.dirty) == ("churnsense-ai:c8495555f955", False)
    assert "SOURCE_DIRTY=false" in found.build_command()
    assert f"SOURCE_REVISION={HEAD}" in found.build_command()


@pytest.mark.parametrize(
    "porcelain",
    [
        " M src/churnsense/api/main.py\n",
        "M  Dockerfile\n",
        "?? src/churnsense/new.py\n",
        " D configs/deploy.yaml\n",
    ],
)
def test_any_change_in_the_context_makes_the_build_dirty(porcelain):
    from scripts import image_provenance as prov

    found = prov.provenance(HEAD, porcelain)
    assert found.dirty and found.tag == "churnsense-ai:c8495555f955-dirty"
    assert "SOURCE_DIRTY=true" in found.build_command()


def test_a_head_that_is_not_a_commit_id_is_refused():
    from scripts import image_provenance as prov

    with pytest.raises(ValueError):
        prov.provenance("main", "")


def test_only_the_build_context_counts_toward_dirty():
    from scripts import image_provenance as prov

    paths = prov.context_paths((REPO / ".dockerignore").read_text())
    assert "Dockerfile" in paths and ".dockerignore" in paths and "src" in paths
    assert not any(p.startswith(("audit-reports", "data", "docs", "tests/test")) for p in paths)
    assert {p.rstrip("/") for p in bundle.DOCKER_CONTEXT} | {"Dockerfile", ".dockerignore"} == set(
        paths
    )


def test_the_provenance_script_only_reads_git_and_never_runs_docker():
    source = (REPO / "scripts" / "image_provenance.py").read_text()
    assert '"rev-parse"' in source and '"status"' in source
    for verb in (
        'subprocess.run(["docker"',
        "os.system",
        '"commit"',
        '"add"',
        '"stash"',
        '"checkout"',
    ):
        assert verb not in source


def test_the_docs_no_longer_tag_an_image_with_a_bare_commit():
    docs = (REPO / "docs" / "DEPLOYMENT.md").read_text()
    assert "docker build -t churnsense-ai:$(git rev-parse" not in docs
    assert "scripts/image_provenance.py" in docs


# --- keep-alive and the edge limits --------------------------------------------------------


def _seconds(text: str) -> int:
    return int(text.rstrip("s"))


def test_uvicorn_keeps_idle_connections_longer_than_caddy():
    dockerfile = (REPO / "Dockerfile").read_text()
    uvicorn = int(dockerfile.split('"--timeout-keep-alive", "')[1].split('"')[0])
    caddyfile = (REPO / "deploy/Caddyfile").read_text()
    caddy = _seconds(caddyfile.split("keepalive ")[1].split()[0])
    assert caddy < uvicorn, "Caddy must close an idle upstream connection before uvicorn does"


def test_caddy_limits_bodies_by_path_as_the_app_does():
    from churnsense.api.middleware import BATCH_PATHS

    caddyfile = (REPO / "deploy/Caddyfile").read_text()
    upload = caddyfile.split("@upload path ")[1].split("\n")[0].split()
    other = caddyfile.split("@other not path ")[1].split("\n")[0].split()
    assert set(upload) == set(other) == set(BATCH_PATHS)
    assert "request_body @upload {\n\t\tmax_size 6MB" in caddyfile
    assert "request_body @other {\n\t\tmax_size 64KiB" in caddyfile
    assert "read_body 30s" in caddyfile


# --- P2-2: the measurement helper's synthetic load ------------------------------------------


def test_the_measurement_load_is_synthetic_and_passes_the_upload_contract(cfg):
    from scripts import measure_capacity as measure

    from churnsense.data import schema
    from churnsense.models.predict import validate_upload

    rows = measure.synthetic_rows(200)
    assert all(str(r["customerID"]).startswith("LOAD-") for r in rows)
    columns = [
        c for c in schema.RAW_COLUMNS if c not in cfg.features.drop_columns and c != schema.TARGET
    ]
    frame = validate_upload(measure.csv_payload(rows), cfg, columns=columns)
    assert len(frame) == 200


def test_the_largest_measurement_upload_stays_under_the_limit(cfg):
    from scripts import measure_capacity as measure

    rows = measure.synthetic_rows(cfg.api.max_batch_rows)
    payload = measure.csv_payload(rows, target_bytes=cfg.api.max_upload_bytes - 4096)
    assert cfg.api.max_upload_bytes - 64 * 1024 < len(payload) <= cfg.api.max_upload_bytes


def test_the_measurement_helper_never_reads_the_dataset_or_trains():
    source = (REPO / "scripts" / "measure_capacity.py").read_text()
    for forbidden in (
        "Telco",
        "load_clean",
        "read_raw",
        "load_book",
        "train",
        "evaluate",
        "joblib",
    ):
        assert forbidden not in source.replace("trains, evaluates", "")
