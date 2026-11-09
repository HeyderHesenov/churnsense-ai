"""The e2e server's guards: synthetic customers only, and no path outside its temporary folder.

The server itself (training, caching, serving) runs in the browser tests; these
are the parts that decide what it may touch, checked without running it.
"""

from __future__ import annotations

import copy
import importlib.util
import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from churnsense.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config

_SPEC = importlib.util.spec_from_file_location(
    "e2e_server", PROJECT_ROOT / "scripts" / "e2e_server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
e2e_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(e2e_server)

#: The IBM Telco ID format (tests/test_governance.py).
_DATASET_ID = re.compile(r"\b[0-9]{4}-[A-Z]{5}\b")


@pytest.fixture(scope="module")
def base() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def e2e_cfg(base, tmp_path):
    """The e2e configuration, written under ``tmp_path`` and loaded the way the server loads it."""
    conf = e2e_server.e2e_config(base, tmp_path, sha256="0" * 64, rows=2000)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(conf), encoding="utf-8")
    return load_config(path)


def test_the_synthetic_customers_are_all_e2e_ids_with_one_of_the_longest_kind():
    ids = e2e_server.synthetic_customers(rows=300, seed=1)["customerID"]
    assert ids.is_unique and len(ids) == 300
    assert ids.str.startswith(e2e_server.ID_PREFIX).all()
    assert not ids.str.startswith("DEMO-").any()  # the batch demo file's rows never match one
    assert (ids.str.len() == 32).sum() == 1
    assert ids.str.fullmatch(r"[A-Za-z0-9-]{1,32}").all()  # the API's ID pattern
    assert not ids.str.contains(_DATASET_ID).any()


def test_the_e2e_configuration_keeps_every_section_and_moves_every_path(base, tmp_path):
    conf = e2e_server.e2e_config(base, tmp_path, sha256="ab" * 32, rows=2000)
    assert conf.keys() == base.keys()
    assert conf["paths"].keys() == base["paths"].keys()
    for path in conf["paths"].values():
        assert Path(path).is_absolute() and Path(path).is_relative_to(tmp_path.resolve())
    assert conf["dataset"]["sha256"] == "ab" * 32 and conf["dataset"]["expected_rows"] == 2000
    assert conf["training"]["n_jobs"] == 1
    assert conf["api"]["rate_limits"] == e2e_server.RATE_LIMITS
    # The repository's configuration is read, never changed.
    assert base == yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_the_e2e_configuration_is_one_the_api_accepts_and_stays_inside_its_folder(
    e2e_cfg, tmp_path
):
    e2e_server.confine(e2e_cfg, tmp_path)
    assert e2e_cfg.raw_data_file.is_relative_to(tmp_path)
    assert dict(e2e_cfg.api.rate_limits)["explain"].burst == 1000


@pytest.mark.parametrize("field", ["raw_dir", "artifacts_dir", "reports_dir", "figures_dir"])
def test_a_path_back_in_the_repository_is_refused(e2e_cfg, tmp_path, field):
    moved = replace(e2e_cfg, paths=replace(e2e_cfg.paths, **{field: PROJECT_ROOT / "artifacts"}))
    with pytest.raises(ValueError, match=field):
        e2e_server.confine(moved, tmp_path)


def test_a_relative_path_is_refused_even_where_it_would_land_inside(e2e_cfg, tmp_path, monkeypatch):
    # Run from the folder itself, a relative path resolves inside it; it is still
    # refused, since the API would resolve it against the repository instead.
    monkeypatch.chdir(tmp_path)
    moved = replace(e2e_cfg, paths=replace(e2e_cfg.paths, reports_dir=Path("reports")))
    with pytest.raises(ValueError, match="reports_dir"):
        e2e_server.confine(moved, tmp_path)


def test_a_dataset_file_outside_the_folder_is_refused(e2e_cfg, tmp_path):
    moved = replace(e2e_cfg, dataset=replace(e2e_cfg.dataset, filename="../../../elsewhere.csv"))
    with pytest.raises(ValueError, match="dataset"):
        e2e_server.confine(moved, tmp_path)


def test_a_temporary_folder_inside_the_repository_is_refused(e2e_cfg):
    with pytest.raises(ValueError, match="inside the repository"):
        e2e_server.confine(e2e_cfg, PROJECT_ROOT / "frontend")
    with pytest.raises(ValueError, match="inside the repository"):
        e2e_server.outside_repository(PROJECT_ROOT)


@pytest.mark.parametrize("escape", ["../elsewhere", "/elsewhere", "data/../../elsewhere"])
def test_a_base_path_that_leaves_the_folder_is_refused(base, tmp_path, escape):
    moved = copy.deepcopy(base)
    moved["paths"]["raw_dir"] = escape
    with pytest.raises(ValueError, match="raw_dir"):
        e2e_server.e2e_config(moved, tmp_path, sha256="0" * 64, rows=2000)


def test_nothing_is_written_before_the_paths_are_checked(base, tmp_path, monkeypatch):
    # The dataset and the configuration are the first writes; an escaping base
    # path is refused before either.
    moved = copy.deepcopy(base)
    moved["paths"]["raw_dir"] = "../escaped"
    config = tmp_path / "base.yaml"
    config.write_text(yaml.safe_dump(moved), encoding="utf-8")
    monkeypatch.setattr(e2e_server, "DEFAULT_CONFIG_PATH", config)
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="raw_dir"):
        e2e_server.prepare(root, rows=300, seed=1)
    assert not (tmp_path / "escaped").exists()
    assert list(root.iterdir()) == []
