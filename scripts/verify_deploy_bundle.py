"""Check a ChurnSense deployment bundle: the right files, the approved hashes, nothing else.

Two modes (docs/DEPLOYMENT.md):

``python scripts/verify_deploy_bundle.py``
    In the checkout, before ``docker build``. Checks the approved assets, the
    deploy configuration, the proxy wiring of ``deploy/compose.yaml`` and
    ``deploy/Caddyfile`` against it, that the app mounts nothing, the
    ``.dockerignore`` allowlist, that every base image is pinned by digest,
    and that the site address is a real domain.

``python scripts/verify_deploy_bundle.py --in-image --root /app``
    Inside the image build, on what was copied. Checks the assets, the
    configuration, the built front end, the source modules, and that the image
    holds nothing it must not (a ``.env``, a ``.git``, ``audit-reports/``,
    tests, the held-out report...).

Both refuse a documentation domain (``demo.example.com``) in the deploy config;
``--allow-example-domain`` waives that one check, for a local test build only
(the Dockerfile passes it only when built with ``--build-arg ALLOW_EXAMPLE_DOMAIN=1``).

**No customer record of the dataset may be in reach of a public demo.** The
deploy configuration must say ``deployment: {mode: public_demo,
customer_records: synthetic}``; the app container must mount no volume at all;
neither the dataset (by name or by content) nor ``make explain``'s cache (which
holds validation rows) may be in the build context or the image; the published
evidence must be the current format, with its top-level keys and no customer
column anywhere in it (the first format held the validation sweep at every 0.01);
and the demo attributions must hold the builder's keys only, and rows of
generated customers only. The figures inside the evidence are not read here: the
service's strict reader does that at start, with the model stack this check
never loads.

What "approved" means, for each asset:

* The release ledger passes the service's own strict reader
  (``churnsense.evaluation.release``: schema, formats and digest) and is
  ``published``.
* ``model.joblib`` hashes to the ``model_sha256`` its metadata records, and
  that is the model the published release names, at the same threshold.
* The dataset SHA-256 pinned in ``configs/config.yaml`` is the one the release
  names.
* The published evidence is bound to that model, that dataset and its split;
  the demo attributions were computed for that model, name the generated book
  by its hash, and every row in them is one of that book's customers.
* The runtime lock pins the scikit-learn version the model was trained with.

Nothing is unpickled, loaded or scored: files are hashed, JSON/YAML metadata
read, and the demo book generated to compare rows with. Importing the release
reader and the generator brings pandas and numpy, never scikit-learn, joblib or
the model. On success it prints one fixed line and nothing about what
it read - no hash, host name or address; a failure names each problem on stderr,
never file contents. Exit status 0 when the bundle is complete and consistent,
1 when it is not, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

#: All that is printed about a passing check: fixed text. What a check found
#: (hash prefixes, host names, proxy addresses) stays in ``verify()``'s notes,
#: for a caller to read; it is never written to the output.
PASSED = "deployment bundle ready"

MODEL = "artifacts/model.joblib"
META = "artifacts/model_meta.json"
#: The dataset's aggregates, published for a service that holds no dataset.
EVIDENCE = "artifacts/public_evidence.json"
#: Attributions of generated demo customers.
DEMO_EXPLANATIONS = "artifacts/demo_explanations.json"
LEDGER = "governance/held_out_release.json"
CONFIG = "configs/config.yaml"
DEPLOY_CONFIG = "configs/deploy.yaml"
LOCK = "requirements.txt"
REPORTS = ("reports/association_ranking.csv", "reports/model_comparison.csv")

#: Every file the image holds for the service, besides its source and front end.
#: No dataset and no row of one: a public demo lists generated customers.
ASSETS = (MODEL, META, EVIDENCE, DEMO_EXPLANATIONS, LEDGER, CONFIG, DEPLOY_CONFIG, LOCK, *REPORTS)

#: Source modules the service cannot start without.
MODULES = (
    "src/churnsense/__init__.py",
    "src/churnsense/config.py",
    "src/churnsense/proxies.py",
    "src/churnsense/hosts.py",
    "src/churnsense/book.py",
    "src/churnsense/models/predict.py",
    "src/churnsense/models/train.py",
    "src/churnsense/evaluation/release.py",
    "src/churnsense/api/main.py",
    "src/churnsense/api/middleware.py",
    "src/churnsense/api/state.py",
    "src/churnsense/api/spa.py",
    "src/churnsense/api/dashboard.py",
)

#: What .dockerignore lets back in after excluding everything: exactly this.
DOCKER_CONTEXT = frozenset(
    {
        "requirements.txt",
        "src/",
        "scripts/verify_deploy_bundle.py",
        CONFIG,
        DEPLOY_CONFIG,
        LEDGER,
        *REPORTS,
        MODEL,
        META,
        EVIDENCE,
        DEMO_EXPLANATIONS,
        "frontend/",
        "tests/fixtures/demo_customers.csv",
    }
)

#: Top-level entries an image may hold under its root, and only these.
IMAGE_TOP_LEVEL = frozenset(
    {"src", "configs", "governance", "reports", "artifacts", "frontend", "scripts", LOCK}
)
#: File names that must never appear anywhere in an image: ``make explain``'s
#: cache holds rows of the dataset's validation partition.
FORBIDDEN_NAMES = frozenset({"shap_global.json"})
#: Path parts that must never appear anywhere in an image.
FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "audit-reports",
        "notebooks",
        "tests",
        "docs",
        ".pytest_cache",
        ".ruff_cache",
        ".vitest-cache",
        "__pycache__",
    }
)
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
#: Package build metadata of a local editable install: never needed at run time.
FORBIDDEN_PART_SUFFIXES = (".egg-info",)
#: The only files these folders may hold in an image.
EXACT_FOLDERS = {
    "artifacts": {MODEL, META, EVIDENCE, DEMO_EXPLANATIONS},
    "reports": set(REPORTS),
    "governance": {LEDGER},
    "configs": {CONFIG, DEPLOY_CONFIG},
    "scripts": {"scripts/verify_deploy_bundle.py"},
}

#: The deploy config may differ from the development one in these ``api`` keys,
#: and in its ``deployment`` section, only.
DEPLOY_ONLY_KEYS = frozenset({"allowed_hosts", "trusted_proxies"})
#: The one deployment a public bundle may state.
PUBLIC_DEPLOYMENT = {"mode": "public_demo", "customer_records": "synthetic"}
#: The published evidence's format and top-level keys: a closed format
#: (``churnsense.evidence``; tests/test_deploy.py holds both equal). The first format held
#: the validation sweep at every 0.01, which differences into single customers; the second
#: cut it finer than the release reports' thresholds allow, and showed small training groups.
EVIDENCE_FORMAT = 3
EVIDENCE_KEYS = frozenset(
    {
        "format",
        "identity",
        "dataset_customers",
        "observed",
        "band_observed",
        "breakdowns",
        "lifecycle",
        "sweep_basis",
        "validation",
        "collinearity",
        "importance",
    }
)
#: What the evidence is bound to, as the model's metadata records it.
EVIDENCE_IDENTITY = ("model_sha256", "data_sha256", "split_digest", "threshold")
#: Every key of the demo attributions (``churnsense.demo.build`` writes exactly these;
#: a test holds the two equal). Anything under another key would ride into the image.
DEMO_EXPLANATION_KEYS = frozenset(
    {
        "model_key",
        "trained_at",
        "model_sha256",
        "n_explained",
        "n_background",
        "permutations",
        "seed",
        "partition",
        "caveat",
        "importance",
        "base_value",
        "features",
        "values",
        "x",
        "max_additivity_gap",
        "records_sha256",
    }
)
#: Compose merges these into ``compose.yaml`` on its own, when they sit beside it.
COMPOSE_OVERRIDES = (
    "compose.override.yaml",
    "compose.override.yml",
    "docker-compose.override.yaml",
    "docker-compose.override.yml",
)
#: The columns of a customer record (``churnsense.data.schema.RAW_COLUMNS``; held
#: equal by tests/test_deploy.py). None may be a key anywhere in the published
#: evidence: an aggregate is never keyed by a customer's column.
RECORD_COLUMNS = frozenset(
    {
        "customerID",
        "gender",
        "SeniorCitizen",
        "Partner",
        "Dependents",
        "tenure",
        "PhoneService",
        "MultipleLines",
        "InternetService",
        "OnlineSecurity",
        "OnlineBackup",
        "DeviceProtection",
        "TechSupport",
        "StreamingTV",
        "StreamingMovies",
        "Contract",
        "PaperlessBilling",
        "PaymentMethod",
        "MonthlyCharges",
        "TotalCharges",
        "Churn",
    }
)
_HEX64 = re.compile(r"[0-9a-f]{64}")
#: Host names reserved for documentation (RFC 2606): never a real deployment.
EXAMPLE_HOST = re.compile(r"(^|\.)(example(\.(com|net|org))?|test|invalid)$")
LOOPBACK_NAMES = frozenset({"localhost"})
#: An external image: name, a version tag, and the digest of what that tag meant.
PINNED_IMAGE = re.compile(
    r"[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,127}@sha256:[0-9a-f]{64}"
)


class Problems(list[str]):
    def add(self, text: str) -> None:
        self.append(text)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _short(digest: object) -> str:
    return f"{str(digest)[:12]}..."


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _yaml(path: Path) -> dict[str, Any] | None:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return value if isinstance(value, dict) else None


def _is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# --- the approved assets -----------------------------------------------------------------


def read_release(root: Path, problems: Problems) -> Any | None:
    """The ledger as the service's own strict reader decodes it, or ``None`` with a problem.

    ``_decode`` is private, and used anyway: ``evaluation/release.py`` is one of
    the sources the published release fingerprints, so it cannot gain a public
    parser without changing the release itself. It is exactly what
    ``read_ledger()`` applies to the file's text, and it only reads.
    """
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from churnsense.evaluation import release
        from churnsense.exceptions import ReleaseRefused
    except ImportError as exc:
        problems.add(f"the release reader could not be imported: {exc}")
        return None
    finally:
        sys.path.pop(0)
    try:
        text = (root / LEDGER).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        problems.add(f"{LEDGER} cannot be read")
        return None
    try:
        ledger = release._decode(text)
    except ReleaseRefused as exc:
        problems.add(f"{LEDGER} is refused by the release's strict reader: {exc}")
        return None
    if ledger.state != release.PUBLISHED or not isinstance(ledger.identity, dict):
        problems.add(f"{LEDGER} is not a published release: there is no approved model")
        return None
    return ledger


def holds_a_record(value: Any) -> bool:
    """Whether a customer's column is a key anywhere in ``value``: an aggregate has none."""
    if isinstance(value, dict):
        return bool(RECORD_COLUMNS.intersection(value)) or any(map(holds_a_record, value.values()))
    return isinstance(value, list) and any(map(holds_a_record, value))


def check_demo_data(
    root: Path, meta: dict[str, Any], pinned: object, problems: Problems, notes: list[str]
) -> None:
    """The published evidence and the demo attributions: this model's, and no dataset row."""
    evidence = _json(root / EVIDENCE)
    if (
        evidence is None
        or set(evidence) != EVIDENCE_KEYS
        or type(evidence["format"]) is not int
        or evidence["format"] != EVIDENCE_FORMAT
    ):
        problems.add(f"{EVIDENCE} is not the published evidence's closed format")
    else:
        bound = evidence["identity"] if isinstance(evidence["identity"], dict) else {}
        if any(key not in bound or bound[key] != meta.get(key) for key in EVIDENCE_IDENTITY):
            problems.add(f"{EVIDENCE} was not computed for this model, its dataset and its split")
        elif bound["data_sha256"] != pinned:
            problems.add(f"{EVIDENCE} is not of the dataset pinned in {CONFIG}")
        else:
            notes.append("published evidence is bound to the model and the pinned dataset")
        if holds_a_record(evidence):
            problems.add(f"{EVIDENCE} holds a customer record: it may hold aggregates only")
    demo = _json(root / DEMO_EXPLANATIONS)
    if demo is None or demo.get("model_sha256") != meta.get("model_sha256"):
        problems.add(f"{DEMO_EXPLANATIONS} was not computed for this model")
    elif demo.get("partition") != "synthetic" or not _HEX64.fullmatch(
        str(demo.get("records_sha256"))
    ):
        problems.add(f"{DEMO_EXPLANATIONS} is not of generated demo customers")
    elif set(demo) != DEMO_EXPLANATION_KEYS:
        problems.add(f"{DEMO_EXPLANATIONS} is not the demo attributions' closed format")
    else:
        check_demo_rows(demo, problems, notes)


def _cell(value: object) -> str | float:
    """A cell as the file and the generator both spell it: text, or a number as a float."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("a cell is text or a number")
    return float(value)


def check_demo_rows(demo: dict[str, Any], problems: Problems, notes: list[str]) -> None:
    """Every row the attributions would show is a generated customer's, whole.

    What the file says of itself is not trusted: validation rows under a correct
    header would otherwise be built into the image. The book is generated here by
    the service's own generator (a pure function: it opens no file), the file must
    name that book by hash, and each of its rows must be one of that book's
    customers over every feature - no fewer, and nothing beside them. The service
    makes the same check at start; this one is made before an image exists.
    """
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from churnsense.demo.customers import COLUMNS, demo_customers, records_sha256
    except ImportError as exc:
        problems.add(f"the demo customer generator could not be imported: {exc}")
        return
    finally:
        sys.path.pop(0)
    book = demo_customers()
    if demo["records_sha256"] != records_sha256(book):
        problems.add(
            f"{DEMO_EXPLANATIONS} does not explain the demo customers the service generates"
        )
        return
    inputs = COLUMNS[1:]  # every generated column but the ID
    features, rows = demo.get("features"), demo.get("x")
    try:
        if not isinstance(features, list) or sorted(features) != sorted(inputs):
            raise TypeError("the features are not the generated inputs")
        if not isinstance(rows, list) or not rows:
            raise TypeError("no rows")
        listed = {
            tuple(map(_cell, row))
            for row in book.loc[:, features].itertuples(index=False, name=None)
        }
        for row in rows:
            if not isinstance(row, dict) or len(row) != len(features):
                raise TypeError("a row is its features and nothing else")
            if tuple(_cell(row[feature]) for feature in features) not in listed:
                raise LookupError("no generated customer has this row")
    except (TypeError, KeyError, LookupError):
        # One answer, and never the row: a failure names the file, not its contents.
        problems.add(f"{DEMO_EXPLANATIONS} draws a row that is no generated customer's")
        return
    notes.append("demo attributions draw generated customers only")


def check_assets(root: Path, problems: Problems, notes: list[str]) -> None:
    """Every asset present; the ledger strict and published; model, ledger, dataset
    pin, published evidence, demo attributions and lock all agree."""
    missing = [name for name in ASSETS if not (root / name).is_file()]
    for name in missing:
        problems.add(f"missing: {name}")
    if missing:
        return

    meta = _json(root / META)
    config = _yaml(root / CONFIG)
    if meta is None:
        problems.add(f"{META} is not a JSON object")
    if config is None:
        problems.add(f"{CONFIG} is not a YAML mapping")
    ledger = read_release(root, problems)
    if meta is None or config is None or ledger is None:
        return
    identity = ledger.identity

    model_sha = sha256_of(root / MODEL)
    recorded = meta.get("model_sha256")
    if model_sha != recorded:
        problems.add(f"{MODEL} does not hash to the model_sha256 its metadata records")
    else:
        notes.append(f"model {_short(model_sha)} matches its metadata")
    if identity.get("model_sha256") != recorded:
        problems.add("the model is not the one the published release names")
    elif recorded == model_sha:
        notes.append("model is the published release's")
    if identity.get("threshold") != meta.get("threshold"):
        problems.add("the model's threshold is not the published release's")

    pinned = (config.get("dataset") or {}).get("sha256")
    if identity.get("dataset_sha256") != pinned:
        problems.add(f"the dataset pinned in {CONFIG} is not the one the published release names")
    else:
        notes.append(f"dataset pin {_short(pinned)} is the release's")

    check_demo_data(root, meta, pinned, problems, notes)

    trained_with = meta.get("sklearn_version")
    lock = (root / LOCK).read_text(encoding="utf-8")
    if not re.search(rf"^scikit-learn=={re.escape(str(trained_with))}\b", lock, re.MULTILINE):
        problems.add(f"{LOCK} does not pin scikit-learn {trained_with}, which trained the model")


# --- the configuration ---------------------------------------------------------------------


def _without_deploy_keys(config: dict[str, Any]) -> dict[str, Any]:
    api = {k: v for k, v in (config.get("api") or {}).items() if k not in DEPLOY_ONLY_KEYS}
    rest = {k: v for k, v in config.items() if k != "deployment"}
    return {**rest, "api": api}


def public_hosts(deploy: dict[str, Any]) -> list[str]:
    hosts = (deploy.get("api") or {}).get("allowed_hosts") or []
    return [str(h).strip().lower() for h in hosts if not _is_loopback(str(h).strip().lower())]


def check_config(root: Path, problems: Problems, notes: list[str]) -> dict[str, Any] | None:
    """The deploy config: valid, a public demo of synthetic records, and otherwise only
    its two ``api`` keys apart from the development one; no wildcard."""
    config, deploy = _yaml(root / CONFIG), _yaml(root / DEPLOY_CONFIG)
    if config is None or deploy is None:
        problems.add(f"{CONFIG} and {DEPLOY_CONFIG} must both be YAML mappings")
        return None
    if _without_deploy_keys(config) != _without_deploy_keys(deploy):
        problems.add(
            f"{DEPLOY_CONFIG} differs from {CONFIG} outside deployment, api.allowed_hosts "
            "and api.trusted_proxies"
        )
    # Stated, never inferred: the one deployment that cannot list a dataset's rows.
    if deploy.get("deployment") != PUBLIC_DEPLOYMENT:
        problems.add(
            f"{DEPLOY_CONFIG} must set deployment to mode public_demo with customer_records "
            "synthetic: a public demo lists generated customers only"
        )
    # The service's own validation, from this script's checkout (in the image: /app/src).
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from churnsense.config import ApiConfig, ConfigError
    except ImportError as exc:
        problems.add(f"the service's configuration code could not be imported: {exc}")
        return deploy
    finally:
        sys.path.pop(0)
    try:
        api = ApiConfig(**deploy["api"])
    except (ConfigError, TypeError, KeyError) as exc:
        problems.add(f"{DEPLOY_CONFIG} api section is invalid: {exc}")
        return deploy
    if "*" in api.allowed_hosts:
        problems.add(f"{DEPLOY_CONFIG} allows every host ('*')")
    if not public_hosts(deploy):
        problems.add(f"{DEPLOY_CONFIG} names no public host in api.allowed_hosts")
    if not api.trusted_proxies:
        problems.add(f"{DEPLOY_CONFIG} trusts no proxy: every request through Caddy would be 403")
    if not problems:
        notes.append(
            f"deploy config valid: hosts {list(api.allowed_hosts)}, proxies {list(api.trusted_proxies)}"
        )
    return deploy


def check_domain(deploy: dict[str, Any], problems: Problems) -> None:
    for host in public_hosts(deploy):
        if EXAMPLE_HOST.search(host):
            problems.add(
                f"{host} is a documentation domain: set the real one in {DEPLOY_CONFIG} "
                "and deploy/Caddyfile"
            )


# --- the deployment files in the checkout ---------------------------------------------------


def caddy_sites(text: str) -> list[str]:
    """The site addresses of a Caddyfile: each top-level block's label, but the global one."""
    sites, depth = [], 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if depth == 0 and line.endswith("{") and line != "{":
            sites.extend(a.strip().rstrip(",") for a in line[:-1].split() if a.strip(","))
        depth += line.count("{") - line.count("}")
    return sites


def check_wiring(root: Path, deploy: dict[str, Any], problems: Problems, notes: list[str]) -> None:
    """compose.yaml and the Caddyfile agree with the deploy config, and expose only Caddy."""
    compose = _yaml(root / "deploy" / "compose.yaml")
    if compose is None:
        problems.add("deploy/compose.yaml is missing or not a YAML mapping")
        return
    if compose.get("include"):
        problems.add(
            "deploy/compose.yaml must not include another file: nothing in it would be checked"
        )
    services = compose.get("services") or {}
    app, caddy = services.get("app") or {}, services.get("caddy") or {}
    if "ports" in app:
        problems.add("deploy/compose.yaml publishes the app's port: only Caddy may")
    if set(app.get("networks") or {}) != {"proxy"}:
        problems.add("the app must join the internal proxy network and nothing else")
    check_nothing_mounted(root, app, problems)
    members = {name for name, s in services.items() if "proxy" in ((s or {}).get("networks") or {})}
    if members != {"app", "caddy"}:
        problems.add(f"only the app and Caddy may join the proxy network, not {sorted(members)}")
    proxy_net = (compose.get("networks") or {}).get("proxy") or {}
    if proxy_net.get("internal") is not True:
        problems.add("the proxy network must be internal")
    try:
        subnet = ipaddress.ip_network(proxy_net["ipam"]["config"][0]["subnet"])
        caddy_ip = ipaddress.ip_address(caddy["networks"]["proxy"]["ipv4_address"])
        app_ip = ipaddress.ip_address(app["networks"]["proxy"]["ipv4_address"])
    except (KeyError, IndexError, TypeError, ValueError):
        problems.add(
            "deploy/compose.yaml must give the proxy network a subnet and both services fixed addresses"
        )
        return
    trusted = [
        ipaddress.ip_network(str(t), strict=False)
        for t in (deploy.get("api") or {}).get("trusted_proxies") or []
    ]
    if caddy_ip not in subnet or not any(caddy_ip in t for t in trusted):
        problems.add("Caddy's address on the proxy network is not the trusted proxy in deploy.yaml")
    if any(app_ip in t for t in trusted):
        problems.add("the app's own address must not be a trusted proxy")
    if any(t.version != subnet.version or not t.subnet_of(subnet) for t in trusted):
        problems.add("a trusted proxy range reaches outside the internal proxy network")
    # The network's gateway is the host: trusting it would let any host process forge headers.
    if any(next(subnet.hosts()) in t for t in trusted if t.version == subnet.version):
        problems.add("the proxy network's gateway (the host) must not be a trusted proxy")

    caddyfile = root / "deploy" / "Caddyfile"
    caddy_text = caddyfile.read_text(encoding="utf-8") if caddyfile.is_file() else ""
    sites = caddy_sites(caddy_text)
    if re.search(r"^\s*trusted_proxies\b", caddy_text, re.MULTILINE):
        problems.add("deploy/Caddyfile must not trust clients' forwarded headers (trusted_proxies)")
    if sorted(sites) != sorted(public_hosts(deploy)):
        problems.add(
            f"deploy/Caddyfile serves {sites} but {DEPLOY_CONFIG} allows {public_hosts(deploy)}"
        )
    if not problems:
        notes.append(
            f"proxy wiring: Caddy {caddy_ip} trusted, app {app_ip} unexposed, site {sites}"
        )


def check_nothing_mounted(root: Path, app: dict[str, Any], problems: Problems) -> None:
    """The app reaches no host path: a public demo holds no dataset, so it mounts nothing.

    Its ``tmpfs`` is memory, not a host path, and stays. Anything that could
    bring a file in from the host - a volume of any kind, another container's
    volumes, a config, a secret, an environment file, or a service definition
    taken from another file - is refused, and so is any mention of the variable
    an earlier layout named the dataset file with. An override file beside
    ``compose.yaml`` is refused too: Compose merges it unasked, and it is not read here.
    """
    for key in ("volumes", "volumes_from", "configs", "secrets", "devices", "env_file", "extends"):
        if app.get(key):
            problems.add(
                f"the app must not declare {key}: a public demo holds no dataset and "
                "reaches no host path"
            )
    for name in COMPOSE_OVERRIDES:
        if (root / "deploy" / name).exists():
            problems.add(f"deploy/{name} would be merged into compose.yaml unchecked: remove it")
    compose = root / "deploy" / "compose.yaml"
    if compose.is_file() and "CHURNSENSE_DATASET" in compose.read_text(encoding="utf-8"):
        problems.add("deploy/compose.yaml names CHURNSENSE_DATASET: no dataset is mounted")


def _from_images(dockerfile: str) -> list[str]:
    """The external images a Dockerfile builds from (stage names are not images)."""
    stages: set[str] = set()
    images: list[str] = []
    for line in dockerfile.replace("\\\n", " ").splitlines():
        words = line.split()
        if not words or words[0].upper() != "FROM":
            continue
        args = [w for w in words[1:] if not w.startswith("--")]
        if not args:
            continue
        if args[0].lower() not in stages:
            images.append(args[0])
        if len(args) >= 3 and args[1].upper() == "AS":
            stages.add(args[2].lower())
    return images


def check_images(root: Path, problems: Problems, notes: list[str]) -> None:
    """Every external image - each FROM, and Caddy in compose - is pinned by version and digest."""
    dockerfile = root / "Dockerfile"
    images = (
        [("Dockerfile", ref) for ref in _from_images(dockerfile.read_text(encoding="utf-8"))]
        if dockerfile.is_file()
        else []
    )
    if not images:
        problems.add("Dockerfile names no base image")
    compose = _yaml(root / "deploy" / "compose.yaml") or {}
    for name, service in (compose.get("services") or {}).items():
        ref = str((service or {}).get("image", ""))
        if ref.startswith("${"):
            continue  # the app's own image, chosen by the operator by tag
        images.append((f"compose service {name}", ref))
    for where, ref in images:
        if not PINNED_IMAGE.fullmatch(ref):
            problems.add(f"{where}: {ref!r} is not pinned as name:version@sha256:<64 hex>")
    if not problems:
        notes.append(f"{len(images)} external images pinned by digest")


def check_dockerignore(root: Path, problems: Problems) -> None:
    """.dockerignore excludes everything, then re-includes exactly ``DOCKER_CONTEXT``."""
    path = root / ".dockerignore"
    if not path.is_file():
        problems.add(".dockerignore is missing")
        return
    rules = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not rules or rules[0] != "*":
        problems.add(".dockerignore must start by excluding everything ('*')")
    included = {rule[1:] for rule in rules if rule.startswith("!")}
    if included != DOCKER_CONTEXT:
        extra, absent = sorted(included - DOCKER_CONTEXT), sorted(DOCKER_CONTEXT - included)
        problems.add(
            f".dockerignore re-includes {extra or 'nothing extra'}, lacks {absent or 'nothing'}"
        )


# --- the image ---------------------------------------------------------------------------


def _files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() or path.is_symlink():
            yield path.relative_to(root)


def _hashes_to(path: Path, digest: str) -> bool:
    """Whether ``path`` is a readable file with this SHA-256 (a dangling link is not)."""
    try:
        return path.is_file() and sha256_of(path) == digest
    except OSError:
        return False


def check_image(root: Path, problems: Problems, notes: list[str]) -> None:
    """The source and front end are there, and nothing else is."""
    for module in MODULES:
        if not (root / module).is_file():
            problems.add(f"missing: {module}")
    dist = root / "frontend" / "dist"
    if not (dist / "index.html").is_file() or not any((dist / "assets").glob("*.js")):
        problems.add("missing: the built front end (frontend/dist/index.html and assets)")
    # The dataset's own file name, wherever a copy of it might be put - and its
    # own bytes, whatever a copy of it might be called.
    dataset = (_yaml(root / CONFIG) or {}).get("dataset") or {}
    dataset_name, pinned = dataset.get("filename"), dataset.get("sha256")
    count = 0
    for rel in _files(root):
        count += 1
        parts, name = rel.parts, rel.as_posix()
        if parts[0] not in IMAGE_TOP_LEVEL:
            problems.add(f"unexpected in the image: {name}")
        elif _HEX64.fullmatch(str(pinned)) and _hashes_to(root / rel, pinned):
            problems.add(f"forbidden in the image: {name} is the pinned dataset")
        elif (
            FORBIDDEN_PARTS.intersection(parts)
            or rel.name in FORBIDDEN_NAMES
            or rel.name == dataset_name
            or any(part.endswith(FORBIDDEN_PART_SUFFIXES) for part in parts)
            or rel.name.startswith(".env")
            or rel.name.endswith(FORBIDDEN_SUFFIXES)
        ):
            problems.add(f"forbidden in the image: {name}")
        elif parts[0] in EXACT_FOLDERS and name not in EXACT_FOLDERS[parts[0]]:
            problems.add(f"not needed at runtime, so not allowed in the image: {name}")
        elif parts[0] == "frontend" and parts[:2] != ("frontend", "dist"):
            problems.add(f"only the built front end belongs in the image: {name}")
    notes.append(f"image layout: {count} files checked")


# --- entry point ---------------------------------------------------------------------------


def verify(
    root: Path,
    *,
    in_image: bool,
    allow_example_domain: bool = False,
) -> tuple[Problems, list[str]]:
    problems, notes = Problems(), []
    check_assets(root, problems, notes)
    deploy = check_config(root, problems, notes)
    if deploy is not None and not allow_example_domain:
        check_domain(deploy, problems)
    if in_image:
        check_image(root, problems, notes)
    else:
        check_images(root, problems, notes)
        check_dockerignore(root, problems)
        if deploy is not None:
            check_wiring(root, deploy, problems, notes)
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="bundle root (default: this checkout)"
    )
    parser.add_argument("--in-image", action="store_true", help="check an image's copied files")
    parser.add_argument(
        "--allow-example-domain",
        action="store_true",
        help="accept demo.example.com (a local test build only; never for a real deployment)",
    )
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error(f"no such directory: {args.root}")
    problems, _notes = verify(
        args.root.resolve(),
        in_image=args.in_image,
        allow_example_domain=args.allow_example_domain,
    )
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if problems:
        print(f"deployment bundle NOT ready: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(PASSED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
