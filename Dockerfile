# syntax=docker/dockerfile:1
#
# ChurnSense AI - the deployed demo: one process serving the API and the built
# front end on one origin. Built from a checkout that holds the approved model
# artifact (docs/DEPLOYMENT.md); .dockerignore is an allowlist, so nothing else
# reaches the build. Never pushed to a public registry: whoever can pull the
# image has the bundled model.
#
# Base images are pinned by version and by the digest of their multi-platform
# index (linux/amd64 and linux/arm64 both present), resolved from Docker Hub
# (docs/DEPLOYMENT.md, section 3: node 2026-11-10, python 2026-11-15).
# scripts/verify_deploy_bundle.py refuses an image reference without a digest.
#
# No dataset is in the image, and none is mounted beside it (docs/DEPLOYMENT.md,
# section 6): the public demo lists generated customers, and shows the dataset's
# training and validation partitions only as published aggregates.

# --- 1. the front end: the committed lockfile, the existing build ---------------------------
FROM node:22.23.4-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
# .npmrc sets ignore-scripts and engine-strict: no install script runs.
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The batch page bundles the demo file from the test fixtures (BatchPage.tsx).
COPY tests/fixtures/demo_customers.csv /build/tests/fixtures/demo_customers.csv
RUN npm run build

# --- 2. the runtime: locked runtime dependencies, the source, the approved assets ----------
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    CHURNSENSE_CONFIG=configs/deploy.yaml \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    NUMBA_CACHE_DIR=/tmp/numba \
    HOME=/tmp

WORKDIR /app

# Runtime lock only, every file checked against its hash; no dev or notebook tools.
# The same command CI's lock job runs on Linux (`make setup-runtime`).
# pip is needed for this install and its check only, so it leaves in the same
# layer: the package, its commands and ensurepip's bundled wheel. The final
# filesystem holds no pip (the bundle check below refuses an image that does);
# the base image's own layer still holds pip's bytes (docs/DEPLOYMENT.md, section 8).
# Before it, zlib1g - the one Debian package taken above the base image - goes to
# exactly this version (docs/DEPLOYMENT.md, section 3): no other package is upgraded,
# nothing may be removed, the build stops on any other version, and apt's lists and
# cache leave in this same layer.
COPY requirements.txt ./
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        --only-upgrade --no-remove zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
    && dpkg-query -W zlib1g \
    && test "$(dpkg-query -W -f='${Version}' zlib1g)" = "1:1.3.dfsg+really1.3.1-1+deb13u1" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --require-hashes -r requirements.txt \
    && pip check \
    && python -m pip uninstall -y pip \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    && rm -f /usr/local/lib/python3.12/ensurepip/_bundled/pip-*.whl

# The source runs from the repository layout: config.PROJECT_ROOT is two levels
# above src/churnsense, so the package is not installed as a wheel.
COPY src/ src/
COPY configs/config.yaml configs/deploy.yaml configs/
COPY governance/held_out_release.json governance/
COPY reports/association_ranking.csv reports/model_comparison.csv reports/
# The model, the dataset's published aggregates and the attributions of generated
# customers (`make demo-data`). Not shap_global.json: it holds validation rows.
COPY artifacts/model.joblib artifacts/model_meta.json artifacts/
COPY artifacts/public_evidence.json artifacts/demo_explanations.json artifacts/
COPY --from=frontend /build/frontend/dist frontend/dist
COPY scripts/verify_deploy_bundle.py scripts/

# Hashes and layout of what was copied: no model is loaded, nothing is scored.
# It refuses the example domain; only a local test build may waive that, with
# --build-arg ALLOW_EXAMPLE_DOMAIN=1. Then compile the source, so a read-only
# container never needs to write it.
# The published evidence must be the file scripts/image_provenance.py hashed
# (EVIDENCE_SHA256, labelled below), and one the publication pipeline leaves as it is:
# the strict reader, the joint disclosure guard and the audit, without the dataset
# (`python -m churnsense.demo.migrate --check`, which writes nothing). Else no image.
ARG ALLOW_EXAMPLE_DOMAIN=
ARG EVIDENCE_SHA256=
RUN python scripts/verify_deploy_bundle.py --in-image --root /app \
        ${ALLOW_EXAMPLE_DOMAIN:+--allow-example-domain} \
        --evidence-sha256 "${EVIDENCE_SHA256}" \
    && python -m churnsense.demo.migrate --check \
    && python -m compileall -q src \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin churnsense

# Provenance: what this image was built from. scripts/image_provenance.py passes
# both; without them the image says "unknown", never that it is a clean commit.
ARG SOURCE_REVISION=unknown
ARG SOURCE_DIRTY=unknown
LABEL org.opencontainers.image.title="ChurnSense AI portfolio demo" \
      org.opencontainers.image.source="https://github.com/HeyderHesenov/churnsense-ai" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      io.churnsense.source.dirty="${SOURCE_DIRTY}" \
      io.churnsense.evidence.sha256="${EVIDENCE_SHA256}"

USER churnsense
EXPOSE 8000

# Readiness, from inside the container (Host 127.0.0.1 is allowed in deploy.yaml).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status != 200)"]

STOPSIGNAL SIGTERM
# Exec form: uvicorn is PID 1's child under `init: true` and receives SIGTERM.
# One worker: the model, the book and every limit are per process.
# --no-proxy-headers: the app's LoopbackOnly reads forwarded headers itself,
# and only from api.trusted_proxies (src/churnsense/proxies.py).
# --no-access-log: LoopbackOnly puts the visitor's address in the request
# scope, so an access log would record every visitor's IP (personal data).
# --timeout-keep-alive 75: longer than Caddy's upstream keepalive (60s in
# deploy/Caddyfile), so Caddy always closes an idle connection first and never
# reuses one uvicorn has just closed (a 502). It bounds idle connections only,
# not a request body in progress - Caddy's read_body timeout does that.
CMD ["uvicorn", "churnsense.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--no-proxy-headers", \
     "--no-access-log", \
     "--no-server-header", \
     "--limit-concurrency", "64", \
     "--timeout-keep-alive", "75", \
     "--timeout-graceful-shutdown", "20"]
