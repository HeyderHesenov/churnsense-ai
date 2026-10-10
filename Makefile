# ChurnSense AI - reproducible entry points.
# Every target runs through .venv so results do not depend on an activated shell.

# Interpreter used to CREATE the venv. Override when the default python3 is
# too old: `make setup PYTHON=python3.12`. The project needs >= 3.12 because
# the locked environment pins shap 0.52, scipy 1.18 and contourpy 1.4, which
# all dropped 3.11 — see docs/PROJECT_WALKTHROUGH.md.
PYTHON ?= python3
MIN_PYTHON := 3.12

PY := .venv/bin/python
PIP := $(PY) -m pip
UV := .venv/bin/uv
RUNTIME := .venv-runtime

# Ports for `make app` and `make api`. Override when another server already
# holds one: `make api API_PORT=8001`. The Host allowlist ignores the port.
APP_PORT ?= 8501
API_PORT ?= 8000

.DEFAULT_GOAL := help
.PHONY: help setup setup-runtime check-python lock sbom data eda train evaluate explain all test lint format audit app api clean

help:  ## Show the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# Every file installed is checked against a sha256 in the lock, the build
# backend included: the editable install is built without isolation, from the
# setuptools and wheel the dev lock pins, rather than from an unhashed download.
setup: check-python  ## Create .venv from the hashed dev lock (requirements-dev.txt: runtime + dev and notebook tools)
	$(PYTHON) -m venv .venv
	$(PIP) install --require-hashes -r requirements-dev.txt
	$(PIP) install -e . --no-deps --no-build-isolation
	@echo "Done. Next: make data"

# What the pipeline, the API and the dashboard need and nothing else; the
# environment `make sbom` describes. Recreated each time so nothing stale stays.
setup-runtime: check-python  ## Create .venv-runtime from the hashed runtime lock alone (requirements.txt)
	$(PYTHON) -m venv --clear $(RUNTIME)
	$(RUNTIME)/bin/python -m pip install --require-hashes -r requirements.txt
	$(RUNTIME)/bin/python -m pip check

check-python:  ## Verify $(PYTHON) is new enough and the platform is one the lock covers
	@$(PYTHON) -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)' \
	  2>/dev/null || { \
	    echo ""; \
	    echo "  '$(PYTHON)' is $$($(PYTHON) -V 2>&1 | cut -d" " -f2), but this project needs >= $(MIN_PYTHON)."; \
	    echo ""; \
	    echo "  Pick a newer interpreter:"; \
	    echo "      make setup PYTHON=python3.12"; \
	    echo ""; \
	    printf "  Found on PATH:"; \
	    for v in 3.12 3.13; do command -v python$$v >/dev/null && printf " python%s" $$v; done; \
	    echo ""; echo ""; \
	    exit 1; \
	  }
	@$(PYTHON) -c 'import platform, sys; raise SystemExit(0 if sys.platform == "linux" or (sys.platform == "darwin" and platform.machine() == "arm64") else 1)' \
	  || { \
	    echo ""; \
	    echo "  The lock covers Linux and macOS on Apple silicon only: see [tool.uv] in pyproject.toml."; \
	    echo ""; \
	    exit 1; \
	  }

# Resolves requirements*.in for every platform in pyproject's [tool.uv]
# environments, pinned and hashed. Existing pins are kept; move one with
# `make lock LOCK_ARGS="--upgrade-package pandas"`. The runtime lock goes first
# because requirements-dev.in is constrained by it.
LOCK := $(UV) pip compile --quiet --universal --generate-hashes --python-version $(MIN_PYTHON)

lock:  ## Resolve requirements*.in into the hashed locks requirements.txt and requirements-dev.txt
	$(LOCK) $(LOCK_ARGS) requirements.in -o requirements.txt
	$(LOCK) $(LOCK_ARGS) requirements-dev.in -o requirements-dev.txt
	@echo "locked: requirements.txt ($$(grep -c '^[a-z0-9]' requirements.txt)), requirements-dev.txt ($$(grep -c '^[a-z0-9]' requirements-dev.txt))"

# CycloneDX JSON of the runtime environment, reproducible (no timestamp or
# random serial) and schema-validated as it is written. CI keeps one per run.
sbom: setup-runtime  ## Write sbom.cdx.json, a CycloneDX SBOM of the runtime lock
	$(PY) -m cyclonedx_py environment --pyproject pyproject.toml --mc-type application \
	  --output-reproducible --output-format JSON --output-file sbom.cdx.json $(RUNTIME)/bin/python

data:  ## Download and verify the raw dataset
	$(PY) -m churnsense.data.download

eda:  ## Regenerate reports/eda_report.md and reports/figures/*
	$(PY) -m churnsense.eda

train:  ## Train and compare all models; write artifacts/ and the comparison report
	$(PY) -m churnsense.models.train

evaluate:  ## Final (single) test-set evaluation + threshold economics
	$(PY) -m churnsense.evaluation.report

explain:  ## Compute and cache global SHAP values
	$(PY) -m churnsense.explainability.shap_explain

# `explain` runs before `evaluate`: the evaluation report embeds the cached
# SHAP importance figure, so the other order silently skips it on a first run.
all: data eda train explain evaluate  ## Full pipeline, raw data to reports

test:  ## Run the test suite
	$(PY) -m pytest

lint:  ## Lint with ruff
	$(PY) -m ruff check src app tests
	$(PY) -m ruff format --check src app tests

format:  ## Auto-format and auto-fix with ruff
	$(PY) -m ruff format src app tests
	$(PY) -m ruff check --fix src app tests

audit:  ## Check installed dependencies for known vulnerabilities
	@# --skip-editable: churnsense itself is installed editable and is not on
	@# PyPI, which --strict would report as an un-auditable dependency.
	$(PY) -m pip_audit --skip-editable

# Both servers bind to loopback: neither has authentication, and Streamlit
# otherwise listens on every interface, i.e. to anyone on the same network.
app:  ## Launch the Streamlit dashboard
	.venv/bin/streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port $(APP_PORT)

api:  ## Launch the FastAPI service
	.venv/bin/uvicorn churnsense.api.main:app --host 127.0.0.1 --port $(API_PORT) --reload

clean:  ## Remove generated artifacts, reports and caches (raw data is kept)
	rm -rf artifacts/* reports/figures/* .pytest_cache .ruff_cache
	rm -f reports/*.md reports/*.json reports/*.csv sbom.cdx.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@mkdir -p artifacts reports/figures && touch artifacts/.gitkeep reports/figures/.gitkeep
	@echo "Cleaned. data/raw was left untouched."
