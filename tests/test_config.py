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


def _streamlit_server() -> dict:
    import tomllib

    from churnsense.config import PROJECT_ROOT

    with (PROJECT_ROOT / ".streamlit" / "config.toml").open("rb") as fh:
        return tomllib.load(fh)["server"]


def test_the_dashboard_listens_on_loopback_however_it_is_started():
    """`make app` passes --server.address, but a bare `streamlit run` reads only
    this file - and Streamlit's own default is every interface."""
    assert _streamlit_server()["address"] == "127.0.0.1"


def test_the_dashboard_and_the_api_answer_the_same_host_names(cfg: Config):
    """Streamlit checks the WebSocket handshake, the app checks every page and
    the API every request; one list, or a name works on one surface only."""
    from churnsense.hosts import allowed_host_entry

    hosts = {allowed_host_entry(h) for h in _streamlit_server()["allowedHosts"]}
    assert hosts == set(cfg.api.allowed_hosts)


def test_the_dashboard_upload_limit_matches_the_api(cfg: Config):
    assert _streamlit_server()["maxUploadSize"] * 1024 * 1024 == cfg.api.max_upload_bytes


@pytest.mark.parametrize(
    "limits",
    [
        {"crawl": {"burst": 1, "per_seconds": 1}},  # an unknown budget
        {"explain": {"burst": 0, "per_seconds": 1}},
        {"explain": {"burst": True, "per_seconds": 1}},
        {"batch": {"burst": 1, "per_seconds": 0}},
        {"batch": {"burst": 1}},
        "explain",
    ],
)
def test_rate_limits_are_validated(limits):
    with pytest.raises(ConfigError):
        ApiConfig(max_batch_rows=10, max_upload_bytes=1024, rate_limits=limits)


def test_rate_limits_default_per_budget(cfg: Config):
    from churnsense.config import DEFAULT_RATE_LIMITS, RateLimit

    partial = ApiConfig(
        max_batch_rows=10,
        max_upload_bytes=1024,
        rate_limits={"explain": {"burst": 3, "per_seconds": 1}},
    )
    assert dict(partial.rate_limits)["explain"] == RateLimit(burst=3, per_seconds=1)
    assert dict(partial.rate_limits)["batch"] == dict(DEFAULT_RATE_LIMITS)["batch"]
    assert [name for name, _ in cfg.api.rate_limits] == [name for name, _ in DEFAULT_RATE_LIMITS]
    hash(cfg.api)  # a frozen config stays hashable


# --- deployment: where the service runs and whose customers it lists ---------------------


def _config_with(tmp_path: Path, section: str | None) -> Path:
    """The committed configuration with its deployment section replaced (or removed)."""
    import yaml

    from churnsense.config import DEFAULT_CONFIG_PATH

    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    del raw["deployment"]
    text = yaml.safe_dump(raw) + (section or "")
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_configuration_is_local_and_lists_the_dataset(cfg: Config):
    assert (cfg.deployment.mode, cfg.deployment.customer_records) == ("local", "dataset")
    assert not cfg.deployment.public and not cfg.deployment.synthetic


def test_a_configuration_without_the_section_is_local(tmp_path: Path):
    """A config written before the setting existed still loads, and is not a public demo."""
    from churnsense.config import Deployment

    assert load_config(_config_with(tmp_path, None)).deployment == Deployment()
    assert load_config(_config_with(tmp_path, "deployment:\n")).deployment == Deployment()


@pytest.mark.parametrize(
    ("mode", "records"),
    [("local", "dataset"), ("local", "synthetic"), ("public_demo", "synthetic")],
)
def test_the_stated_deployments_load(tmp_path: Path, mode: str, records: str):
    section = f"deployment:\n  mode: {mode}\n  customer_records: {records}\n"
    deployment = load_config(_config_with(tmp_path, section)).deployment
    assert (deployment.mode, deployment.customer_records) == (mode, records)
    assert deployment.synthetic is (records == "synthetic")
    assert deployment.public is (mode == "public_demo")


def test_a_public_demo_of_the_datasets_rows_does_not_load(tmp_path: Path):
    section = "deployment:\n  mode: public_demo\n  customer_records: dataset\n"
    with pytest.raises(ConfigError, match="synthetic customer records only"):
        load_config(_config_with(tmp_path, section))


def test_a_public_demo_must_state_its_records(tmp_path: Path):
    """Left out, the records default to the dataset's - which a public demo may not list."""
    with pytest.raises(ConfigError, match="synthetic customer records only"):
        load_config(_config_with(tmp_path, "deployment:\n  mode: public_demo\n"))


@pytest.mark.parametrize(
    "section",
    [
        "deployment:\n  mode: public\n",
        "deployment:\n  mode: PUBLIC_DEMO\n  customer_records: synthetic\n",
        "deployment:\n  customer_records: real\n",
        "deployment:\n  customer_records: yes\n",
        "deployment:\n  mode: [local]\n",
        "deployment:\n  mode: local\n  dataset: true\n",
        "deployment: public_demo\n",
        # Not text either: nothing to iterate over, and still a configuration error.
        "deployment: 5\n",
        "deployment: true\n",
        "deployment: [public_demo, synthetic]\n",
    ],
)
def test_an_unknown_deployment_does_not_load(tmp_path: Path, section: str):
    with pytest.raises(ConfigError, match="deployment"):
        load_config(_config_with(tmp_path, section))


def test_the_policy_reads_nothing_of_the_network_settings(tmp_path: Path):
    """Proxies and hosts do not decide whose rows are listed: only the stated policy does."""
    import yaml

    from churnsense.config import DEFAULT_CONFIG_PATH

    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    raw["api"]["trusted_proxies"] = ["172.30.10.2"]
    raw["api"]["allowed_hosts"] = ["churn.example.org", "127.0.0.1"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loaded = load_config(path)
    assert loaded.api.trusted_proxies and loaded.deployment.customer_records == "dataset"
    raw["api"]["trusted_proxies"] = []
    raw["deployment"] = {"mode": "public_demo", "customer_records": "synthetic"}
    other = tmp_path / "public.yaml"  # load_config is cached by path
    other.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(other).deployment.synthetic
