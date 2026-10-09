"""Tests for configuration loading and validation."""

from __future__ import annotations

import textwrap
from itertools import pairwise
from pathlib import Path

import pytest

from churnsense.config import ApiConfig, Business, Config, Split, load_config
from churnsense.exceptions import ConfigError


def test_default_config_loads(cfg: Config):
    assert cfg.random_seed >= 0
    assert cfg.training.cv_folds >= 2
    assert cfg.paths.raw_dir.is_absolute(), "paths must resolve regardless of cwd"


def test_split_fractions_sum_to_one(cfg: Config):
    assert cfg.split.train_size + cfg.split.validation_size + cfg.split.test_size == pytest.approx(
        1.0
    )


@pytest.mark.parametrize(
    ("test_size", "validation_size"),
    [(0.0, 0.2), (1.0, 0.2), (0.2, 0.0), (0.5, 0.45)],
)
def test_split_rejects_impossible_fractions(test_size: float, validation_size: float):
    with pytest.raises(ConfigError):
        Split(test_size=test_size, validation_size=validation_size)


@pytest.mark.parametrize(
    "overrides",
    [
        {"offer_success_rate": 1.5},
        {"offer_success_rate": -0.1},
        {"gross_margin": 2.0},
        {"retention_offer_cost": -10.0},
        {"expected_horizon_months": 0},
    ],
)
def test_business_assumptions_are_validated(overrides: dict):
    base = {
        "currency": "USD",
        "retention_offer_cost": 50.0,
        "offer_success_rate": 0.3,
        "expected_horizon_months": 12,
        "gross_margin": 0.65,
    }
    with pytest.raises(ConfigError):
        Business(**{**base, **overrides})


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_batch_rows": 0},
        {"max_upload_bytes": 0},
        {"max_upload_columns": 0},
        {"max_batch_rows": 10_000.5},
        {"max_upload_columns": True},
        {"max_upload_columns": 10},  # narrower than the raw contract: every upload refused
    ],
)
def test_upload_limits_are_validated(overrides: dict):
    base = {"max_batch_rows": 10, "max_upload_bytes": 1024, "max_upload_columns": 30}
    with pytest.raises(ConfigError):
        ApiConfig(**{**base, **overrides})


@pytest.mark.parametrize(
    "hosts", [[], [""], "localhost", [None], ["http://my.host"], ["*.streamlit.app"]]
)
def test_allowed_hosts_are_validated(hosts):
    with pytest.raises(ConfigError):
        ApiConfig(max_batch_rows=10, max_upload_bytes=1024, allowed_hosts=hosts)


def test_allowed_hosts_are_normalised_on_load():
    api = ApiConfig(1, 1024, allowed_hosts=["LocalHost:8501", " 127.0.0.1 ", "localhost", "*"])
    assert api.allowed_hosts == ("localhost", "127.0.0.1", "*")


def test_a_yaml_syntax_error_is_a_config_error(tmp_path: Path):
    """An unquoted * in a list is a YAML alias, and used to escape as a raw traceback."""
    bad = tmp_path / "config.yaml"
    bad.write_text("api:\n  allowed_hosts: [*]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="could not be parsed"):
        load_config(bad)


def test_allowed_hosts_load_as_a_tuple_of_loopback_names(cfg: Config):
    assert cfg.api.allowed_hosts == ("localhost", "127.0.0.1")


def test_the_config_layer_knows_the_contract_width():
    """CONTRACT_WIDTH is written out to keep pandas out of config; it must not drift."""
    from churnsense.config import CONTRACT_WIDTH
    from churnsense.data import schema

    assert len(schema.RAW_COLUMNS) == CONTRACT_WIDTH


def test_customer_value_applies_horizon_and_margin():
    business = Business("USD", 50.0, 0.3, 12, 0.5)
    assert business.customer_value(100.0) == pytest.approx(600.0)


def test_risk_bands_tile_the_probability_range(cfg: Config):
    bands = sorted(cfg.risk_bands, key=lambda b: b.min)
    assert bands[0].min == 0.0
    assert bands[-1].max > 1.0, "the top band must include probability 1.0"
    for lower, upper in pairwise(bands):
        assert lower.max == upper.min, "bands must be contiguous, with no gap or overlap"


# Band assignment itself is tested in tests/test_threshold.py against
# `assign_risk_bands`, the implementation the serving path actually uses.
# `Config.band_for` used to be a second, scalar implementation beside it; it
# had no production callers and labelled NaN and out-of-range probabilities
# as "Critical" - the highest-priority retention queue - so it was removed
# rather than fixed. See tests/test_threshold.py for the guard.


def test_missing_config_file_is_reported_clearly(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml")


def test_malformed_config_is_reported_clearly(tmp_path: Path):
    path = tmp_path / "broken.yaml"
    path.write_text(
        textwrap.dedent("""
        project:
          name: Broken
        """)
    )
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(path)


def test_non_mapping_config_is_rejected(tmp_path: Path):
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="not a YAML mapping"):
        load_config(path)


def test_load_config_is_cached(cfg: Config):
    assert load_config() is cfg
