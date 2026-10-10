"""The dependency lock: hashed, split into runtime and dev, and in step with pyproject.

`pyproject.toml` declares the constraints, `requirements*.in` repeat them for
`make lock` (uv) and Dependabot, and `requirements*.txt` are the resolved,
hashed locks that `make setup` and `make setup-runtime` install with
`--require-hashes`. These tests catch the drift that pip would only report at
install time, or never: an `.in` file that no longer matches pyproject, a pin
without a hash, a package locked differently in the two files, or code that
imports a package pyproject never declared.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = ROOT / "requirements.txt"
DEV_LOCK = ROOT / "requirements-dev.txt"

# Top-level tools that must never reach the runtime lock.
DEV_ONLY = ("pytest", "ruff", "pip-audit", "jupyter", "ipykernel", "uv", "cyclonedx-bom")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _project() -> dict:
    return _pyproject()["project"]


def _key(requirement: Requirement) -> tuple[str, tuple[str, ...], str, str]:
    return (
        canonicalize_name(requirement.name),
        tuple(sorted(requirement.extras)),
        str(requirement.specifier),
        str(requirement.marker or ""),
    )


def _declared(specs: list[str]) -> set[tuple]:
    return {_key(Requirement(spec)) for spec in specs}


def _in_file(path: Path) -> tuple[set[tuple], list[str]]:
    """Requirements of an .in file, and its option lines (-c, -r)."""
    requirements, options = set(), []
    for raw in path.read_text().splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            options.append(line)
        else:
            requirements.add(_key(Requirement(line)))
    return requirements, options


def _lock(path: Path) -> dict[str, tuple[str, str, frozenset[str]]]:
    """name -> (pinned specifier, marker, hashes) for every entry of a lock."""
    text = re.sub(r"\\\n", " ", path.read_text())
    entries = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        spec, *hash_parts = line.split("--hash=")
        requirement = Requirement(spec.strip())
        name = canonicalize_name(requirement.name)
        assert name not in entries, f"{path.name}: {name} is locked twice"
        entries[name] = (
            str(requirement.specifier),
            str(requirement.marker or ""),
            frozenset(part.strip() for part in hash_parts),
        )
    return entries


def test_requirements_in_mirrors_the_project_dependencies():
    requirements, options = _in_file(ROOT / "requirements.in")
    assert options == []
    assert requirements == _declared(_project()["dependencies"])


def test_requirements_dev_in_mirrors_the_extras_and_the_build_backend():
    """The runtime requirements plus tools, constrained by the runtime lock.
    The build backend is locked too: `make setup` builds the editable install
    without build isolation, so setuptools is not fetched unhashed."""
    requirements, options = _in_file(ROOT / "requirements-dev.in")
    extras = _project()["optional-dependencies"]
    build = _pyproject()["build-system"]["requires"]
    assert options == ["-r requirements.in", "-c requirements.txt"]
    assert requirements == _declared(extras["dev"] + extras["notebook"] + build)


@pytest.mark.parametrize("lock", [RUNTIME_LOCK, DEV_LOCK], ids=lambda p: p.name)
def test_every_locked_package_is_pinned_and_hashed(lock: Path):
    entries = _lock(lock)
    assert entries, f"{lock.name} is empty"
    for name, (specifier, _, hashes) in entries.items():
        assert re.fullmatch(r"==[^,]+", specifier), f"{lock.name}: {name} is not pinned"
        assert hashes, f"{lock.name}: {name} has no hash"
        assert all(h.startswith("sha256:") for h in hashes), f"{lock.name}: {name}"


def test_shared_packages_are_locked_identically():
    """The dev lock is the runtime lock plus tools, never a different runtime."""
    runtime, dev = _lock(RUNTIME_LOCK), _lock(DEV_LOCK)
    missing = sorted(set(runtime) - set(dev))
    assert not missing, f"in requirements.txt but not requirements-dev.txt: {missing}"
    differ = sorted(name for name in runtime if runtime[name] != dev[name])
    assert not differ, f"locked differently in the two files: {differ}"


def test_the_runtime_lock_carries_no_dev_tools():
    runtime = _lock(RUNTIME_LOCK)
    assert not [name for name in DEV_ONLY if canonicalize_name(name) in runtime]


def _imported_distributions() -> set[str]:
    """Distributions that the code in src/ and app/ imports directly."""
    modules = set()
    for path in [*ROOT.glob("src/**/*.py"), *ROOT.glob("app/**/*.py")]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    third_party = modules - set(sys.stdlib_module_names) - {"churnsense", "app"}
    providers = packages_distributions()
    return {canonicalize_name(dist) for m in third_party for dist in providers.get(m, [m])}


def test_every_directly_imported_package_is_declared():
    """Regression: scipy and starlette were imported but only arrived through
    scikit-learn and fastapi, so a change in either could remove or re-range a
    package this code calls directly."""
    declared = {canonicalize_name(Requirement(spec).name) for spec in _project()["dependencies"]}
    assert sorted(_imported_distributions() - declared) == []
