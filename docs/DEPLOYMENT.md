# Deploying the ChurnSense AI portfolio demo

This is the one deployment the repository supports: a **public, unauthenticated
portfolio demo** of the FastAPI service and the React front end it serves, on one
host. It lists **generated demo customers** and shows the public IBM Telco sample
only as **published aggregates**: the dataset is not in the image, not mounted, and
not on the host at all (§6). It is not a SaaS product. It has no accounts, no tenants,
no persistent workspaces and no database, and it must never hold real customer data.

| | Portfolio demo (this document) | A future production SaaS (not built) |
|---|---|---|
| Users | Anonymous visitors | Authenticated users in organisations |
| Data | Generated demo customers; the sample dataset as aggregates only; uploads scored and discarded | Customer-owned data, stored and isolated per tenant |
| Topology | One host: Caddy + one app container, one worker | Load balancer, replicas, shared state, job queue |
| Persistence | None (Caddy's certificates only) | Database, object storage, audit log, backups |


## Blockers before a public deployment

This repository is **not ready for a public deployment yet**. Each item below must be
closed first:

1. **Memory is measured in local Docker only, and for the earlier layout** (§16,
   2026-11-10): the app peaked at 762,073,088 bytes under its 1,610,612,736-byte limit,
   with no OOM - while it still built its book from a mounted dataset. The service now
   holds a smaller, generated book and no dataset, so that figure is expected to
   overstate what is deployed, but that expectation is not measured. **Neither the
   current image nor the VPS's real RAM use is measured**; the VPS size in §2 is an
   estimate until §16 is repeated.
2. **The real domain is not set** (§5): `demo.example.com` is a placeholder the bundle
   check refuses.
3. **The stack has been run locally only, without a certificate.** On 2026-11-12 the
   current image (`churnsense-ai:d6687995d4b3-dirty`, a local validation build with the
   uncommitted E8 change) was built and started with Compose: the app reported healthy,
   served generated customers only and answered 404 for a dataset ID; no file in it
   hashes to the pinned dataset, and it has no `data/` folder, no `shap_global.json`,
   no volume and no dataset path that exists. Caddy redirected HTTP to HTTPS, but could
   not get a certificate for the placeholder domain (Let's Encrypt refuses it), so TLS
   with a real certificate, the proxy headers in practice and the host itself are
   still unverified. Idle, the app used about 197 MiB (peak 211 MiB after startup and
   a few reads); the load phases of §16 were not run on this image.

**Closed, 2026-11-11: the API no longer serves the dataset's rows.** It used to return
all 7,043 customers of the IBM sample (a listing, a lookup by ID, a CSV export, and
attributions that carried their values), against the project's own policy that the
dataset is not redistributed. The public demo now lists generated customers only, and
the dataset is not in its reach: see §6 and "What the demo serves instead of the
dataset" in §20. Since 2026-11-12 the validation sweep is published only at cuts that
cannot be differenced into fewer than 20 customers (E8, §20), and the two reports that held
the full sweep are no longer tracked; earlier commits still hold them (§20).

## 1. Architecture

```
internet ──HTTPS 443 / HTTP 80──▶ caddy ──(proxy network: internal, 172.30.10.0/29)──▶ app:8000
                                  172.30.10.2                                           172.30.10.3
```

- **Caddy** (`deploy/Caddyfile`) is the only service with published ports. It
  terminates TLS, redirects HTTP to HTTPS, and reverse-proxies to the app.
- **app** (`Dockerfile`) is one uvicorn process with one worker. It serves `/api/*`,
  the scoring endpoints, and the built front end on the same origin, so the front end's
  relative `/api` calls, its Content-Security-Policy and the same-origin checks work
  unchanged.
- **The `proxy` network is `internal`.** Only the two services join it, it has no route
  in or out, and Caddy has a fixed address on it. That address is the only entry in
  `api.trusted_proxies` (`configs/deploy.yaml`).

**Trusted-proxy rule** (`src/churnsense/proxies.py`, `LoopbackOnly` in
`src/churnsense/api/middleware.py`):

- The app answers loopback peers (its own health check) and the configured proxy
  address. Every other peer gets 403 `loopback_only`, as it does locally.
- Only from the trusted proxy does it believe `X-Forwarded-For` and `X-Forwarded-Proto`.
  - The client is the rightmost address the proxy did not itself add, so a visitor
    cannot choose their own address.
  - The scheme becomes `https` only if the proxy says so.
  - A value from the proxy it cannot read is a 400 `invalid_forwarded_header`, never a
    guess.
- uvicorn runs with `--no-proxy-headers`, so nothing else rewrites the peer first.
- Everything after this check sees the visitor:
  - the Host and Origin checks (`TrustedHosts`)
  - the per-client budgets (`RateLimit`)
  - HSTS (`SecurityHeaders`)

**Caddy's side** (Caddy documentation, `reverse_proxy` → Defaults):

- Caddy ignores `X-Forwarded-*` values sent by clients unless they come from its own
  `trusted_proxies`, which is not set here.
- It sets `X-Forwarded-For` to the client's address and `X-Forwarded-Proto` to the
  scheme it received.
- It passes `Host` through unchanged to an HTTP upstream.
- The Caddyfile also drops `X-Forwarded-Host` and `Forwarded`, which the app never
  reads.

## 2. Host prerequisites

- **Host:** one Linux VPS (x86-64 or arm64; the runtime lock covers both) with a public
  IPv4 address (see §4 for IPv6).
- **Size:** an estimate, **not a measurement of a VPS**: 2 vCPU and 4 GB RAM is a
  conservative start (the app container is limited to 1.5 GB, Caddy to 256 MB, the OS
  needs the rest), with ~10 GB SSD. The local measurement (§16) fits the app's limit
  with room to spare; repeat it on the host before relying on the size.
- **Software:** Docker Engine with the Compose v2 plugin. No language runtimes are
  needed on the host.
- **Access:** SSH by key only. Keep automatic security updates on.

## 3. Build versions and pinned images

| Stage | Version | Source |
|---|---|---|
| Front end build | Node 22.23.4 | `frontend/.nvmrc`, `package.json` engines `^22.12.0`, `npm ci` from `package-lock.json` |
| Runtime | Python 3.12.13 | the version CI and the model's metadata record |
| Python packages | `requirements.txt`, installed with `--require-hashes` | the runtime lock (no dev or notebook tools) |

Every external image is pinned by version **and** by the digest of its multi-platform
index, so a rebuild or a `docker compose pull` cannot bring in a different image
silently. `scripts/verify_deploy_bundle.py` refuses any `FROM` or `image:` without a
`@sha256:` digest.

| Image | Version tag | Index digest | Platforms checked | Verified |
|---|---|---|---|---|
| `node` (front end build) | `22.23.4-slim` | `sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d` | linux/amd64, linux/arm64/v8 (also arm/v7, ppc64le) | 2026-11-10 |
| `python` (runtime) | `3.12.13-slim` | `sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36` | linux/amd64, linux/arm64/v8 (and six more) | 2026-11-10 |
| `caddy` (proxy) | `2.11.9` | `sha256:0c2243efcf437ab7f7a978f1027fcf0d4f9be8a75df27b1b3fbc5dbe0ffa8cf0` | linux/amd64, linux/arm64/v8 (and five more) | 2026-11-10 |

How the digests were obtained:

- From Docker Hub's registry API (`registry-1.docker.io`), with an anonymous pull token
  from `auth.docker.io`, and read only.
- Each `Docker-Content-Digest` header equals the SHA-256 of the index manifest that was
  returned.
- Each index lists linux/amd64 and linux/arm64. `node:22-slim` currently resolves to
  the same index as `22.23.4-slim`.
- `caddy:2` and `caddy:2.11` resolve to a different index for the same v2.11.9 build
  (it also lists riscv64). The versioned tag is pinned.

To update an image: resolve the new tag's index digest the same way, check both
platforms, change the reference and this table together, and rebuild.

## 4. DNS

- Point an `A` record for your domain at the host's IPv4 address.
- Caddy needs the name to resolve to the host before it can obtain a certificate.
- **No `AAAA` record yet.** The compose `edge` network has no IPv6. With Docker's
  default userland proxy, IPv6 visitors would reach Caddy from the network gateway, so
  every IPv6 visitor would share one address and one rate budget. Add IPv6 only after
  enabling it on that network and checking that Caddy sees real client addresses.

## 5. Configuration

Two files hold the domain. Change both together; the bundle check (§7) refuses a
mismatch and refuses the example domain.

| File | What to set |
|---|---|
| `configs/deploy.yaml` | `api.allowed_hosts: [<your-domain>, 127.0.0.1]` (keep `127.0.0.1`: the container's own health check uses it). Never `"*"`. |
| `deploy/Caddyfile` | the site address `<your-domain> {` |

**`api.trusted_proxies`:**

- It is `[172.30.10.2]`, Caddy's fixed address in `deploy/compose.yaml`.
- Change it only together with the compose file.
- Entries must be single addresses or ranges no broader than /24 (IPv4) or /120 (IPv6).
  A wildcard, `0.0.0.0/0`, `::/0`, loopback and any broader range are refused when the
  config loads.

**Other settings:**

- `configs/deploy.yaml` must otherwise equal `configs/config.yaml` (apart from the
  deployment policy below). A test and the bundle check enforce this, so limits, risk
  bands and business assumptions cannot drift.
- **Environment variables:** `CHURNSENSE_CONFIG=configs/deploy.yaml` (already set in
  the image and in compose) and `CHURNSENSE_LOG_LEVEL` (default `INFO`).
- **Secrets:** there are none. The service calls no external API.
- **Compose:** needs `CHURNSENSE_IMAGE` (the image tag to run, from §8). Nothing else:
  no dataset path is given, because no dataset is deployed (§6).

**The deployment policy** is its own section of the configuration, and the only thing
that decides whose customers are listed:

```yaml
deployment:
  mode: public_demo            # local | public_demo
  customer_records: synthetic  # dataset | synthetic
```

- `configs/deploy.yaml` states exactly this. The bundle check (§7) refuses any other
  value, and a missing section.
- `public_demo` with `customer_records: dataset` does not load at all
  (`ConfigError`): the combination that would list the dataset's rows to anonymous
  visitors cannot be configured.
- It is read from nothing else. The proxies and the host names (above) are network
  settings; they do not select the data.
- `configs/config.yaml` states `local` and `dataset`: on a developer's machine, on
  loopback, the service builds its book from the dataset as before.

## 6. Model packaging, and why no dataset is deployed

The image holds exactly these runtime files (`.dockerignore` is an allowlist):

- `artifacts/model.joblib` and `model_meta.json`
- `artifacts/public_evidence.json` and `demo_explanations.json` (below)
- `governance/held_out_release.json`
- `reports/association_ranking.csv` and `reports/model_comparison.csv`

**The model** is not in git. Build from the checkout that holds the approved artifact;
never retrain for a deployment. It is served from inside the image, so a rollback
brings the matching code and model back together.

**No dataset is in the image, in the build context, mounted into the container, or on
the host.**

*Why.* The IBM Telco sample is Apache-2.0 licensed, and this project's policy is not to
redistribute it (`docs/MODEL_CARD.md`). A public, unauthenticated API that lists its
rows redistributes it, whatever the image holds. Keeping the file in the container and
trusting every route never to hand a row out would rest the policy on the absence of
bugs. So the file is not there: a route cannot return what the process does not have.

*What the service lists instead.* 2,000 **generated demo customers**
(`src/churnsense/demo/customers.py`): a pure function of a size and a seed that opens
no file and takes no frame, so no value in it was sampled, copied or perturbed from a
dataset. Their IDs are `DEMO-` and six digits. They have no outcome and no protected
attribute. The served model scores them through the same path as `POST /predict`.
Every customer listing, lookup, export, example and explanation is of these, and each
is labelled synthetic (the partition is `synthetic`, the outcome `withheld`, the status
`active`).

*What it shows of the dataset.* Aggregates only, read from
`artifacts/public_evidence.json`: observed churn overall and per risk band, the
breakdowns of the training partition, the validation sweep's totals per threshold, the
validation metrics, calibration bins and segments, and the validation sample's feature
ranking. Every group of fewer than 20 customers has no rate in it, and a table hides
the further groups that would give one away (`src/churnsense/suppression.py`). It is a
closed format (`src/churnsense/evidence.py`): every key is named, every count and rate
is checked when the service reads it, and it is bound to the model, the dataset pin,
the split and the threshold. A file that breaks a rule, or belongs to another model, is
refused.

*How the two files are made.* Once, by the maintainer, on a machine that has the
dataset:

```sh
make demo-data
```

It reads what the service reads at every local start (training and validation
outcomes; the test partition's are withheld by `churnsense.book`), fits nothing, runs no
final evaluation, and writes two new files beside the model. It refuses a dataset the
model was not trained on, and a generated customer that happens to have a dataset
customer's exact inputs. Every figure that splits validation customers by score is
written at one set of cuts only, chosen so that at least 20 churners **and** 20 stayers
lie between any two (`churnsense.score_disclosure`): the sweep, the calibration bins and
the operating point. The validation segments at the threshold and the observed churn
per risk band keep the same rule, with complementary suppression, and no validation
histogram is written. Where no such cuts exist, no sweep is written and the pages say
why. The service's reader checks all of it again at start. `demo_explanations.json` holds attributions of 300 generated
customers against a background of 100 generated customers: nothing of the dataset is in
it. `artifacts/shap_global.json` (`make explain`'s cache) holds 300 **validation rows**
and is therefore **not** in the image; the bundle check refuses it anywhere.

*What readiness does.*

- A generator that fails, a published file that is missing, malformed, or bound to
  another model, or attributions that are not of the customers being listed, leave the
  book unbuilt. `/ready` then answers 503 with `"dataset": false`, every data route
  answers 503 `demo_data_unavailable`, the container stays `unhealthy`, and Caddy does
  not start.
- **Nothing falls back to other data.** The dataset's loader is not on this path.
- **A dataset file beside a public demo keeps it down.** In `public_demo` mode the
  service refuses to build its book if the configured dataset file exists: a file that
  should not be there is a misconfiguration, not something to ignore.

**What the image is not:**

- **Not a public file server.** The front end route serves only `frontend/dist/assets`
  files, by a strict name pattern. The model is never reachable as a file over HTTP.
- **Not for a public registry.** Whoever can pull the image has the bundled model (a
  pickle). The image holds no dataset row, so that is the only reason left.

## 7. Bundle integrity check

Run this before every build, in the checkout. It refuses the example domain until you
set yours:

```sh
.venv/bin/python scripts/verify_deploy_bundle.py
```

It checks the following without loading the model:

- **Release:** the ledger passes the service's own strict reader
  (`churnsense.evaluation.release`: exact keys, formats and its digest) and is
  `published`.
- **Model:** `model.joblib` hashes to the `model_sha256` its metadata records, and it
  is the model the release names, at the same threshold.
- **Dataset pin:** the SHA-256 pinned in `configs/config.yaml` is the one the release
  names. No dataset file is checked: none is deployed.
- **Published evidence:** it has exactly the format's top-level keys; it is bound to
  that model, that dataset pin, the split and the threshold; and no customer column is
  a key anywhere in it. The check does not read the figures themselves: the service's
  strict reader does that at start (§6), and it needs the model stack this check never
  loads.
- **Demo attributions:** computed for that model; exactly the builder's keys and no
  other; they name the generated book by its hash, and every row in them is one of that
  book's customers, whole (the check generates the book itself to compare).
- **No dataset by another name:** in the image, no file hashes to the pinned dataset,
  whatever it is called and wherever it sits.
- **Lock:** it pins the scikit-learn version that trained the model.
- **Images:** every external image is `name:version@sha256:<digest>`.
- **Configuration and wiring:**
  - `configs/deploy.yaml` is valid, states `deployment: {mode: public_demo,
    customer_records: synthetic}`, and otherwise differs from the development config
    only in its two `api` keys.
  - The compose network gives Caddy the trusted address.
  - The app publishes no port and **mounts nothing**: any volume, another container's
    volumes, a config, a secret, a device or an environment file on the app is refused,
    and so is any mention of `CHURNSENSE_DATASET`.
  - Nothing unchecked is merged in: the app may not `extends` another file's service,
    `compose.yaml` may not `include` another file, and no override file
    (`compose.override.yaml` and its three other spellings) may sit beside it, since
    `docker compose up` would merge one unasked.
  - The Caddyfile site equals the allowed host.
  - `.dockerignore` re-includes exactly the expected paths.
  - The domain is not a documentation domain.

It only reads. When every check passes it prints one fixed line to stdout,
`deployment bundle ready`, and nothing else: no hash prefix and no file contents are
written to stdout. When bundle validation finds problems it prints nothing to stdout;
each problem is written to stderr as a diagnostic `PROBLEM:` line, and the exit status
is 1. Wrong command-line usage is a different case: the argument parser reports it and
the exit status is 2. Its release reader imports pandas and numpy, never scikit-learn,
joblib or the model.

The image build runs it again with `--in-image` on the copied files. That run also
refuses:

- the example domain;
- any file under `data/`, the dataset's own file name anywhere, and
  `shap_global.json` anywhere (it holds validation rows);
- `.egg-info`, `.env*`, `.git`, `audit-reports/`, tests, the held-out report, other
  reports, and keys.

For a local test build only, waive the domain check with
`--build-arg ALLOW_EXAMPLE_DOMAIN=1`, or with `--allow-example-domain` on the command
line. Never use it for an image you deploy.

## 8. Build the image

An image tag must never claim a commit the build context does not match. Get the build
command from the provenance helper. It only reads git and prints; it never runs Docker.

Run the helper, and the command it prints, **from the repository root**. The command
always ends with the build context `.` — exactly once, as its last argument — and `.`
is whatever directory the command is run in: from `deploy/` it would be the wrong
context. The helper prints the directory to run it in on stderr.

```sh
.venv/bin/python scripts/image_provenance.py                        # prints: docker build --tag … .
.venv/bin/python scripts/image_provenance.py --build-arg ALLOW_EXAMPLE_DOMAIN=1   # local test build only
```

The tag depends on the build context (every path `.dockerignore` re-includes, plus
`Dockerfile` and `.dockerignore`):

| Build context | Tag | Labels |
|---|---|---|
| No change, staged or not, and no untracked file | `churnsense-ai:<12-char commit>` | `io.churnsense.source.dirty=false` |
| Anything else | `churnsense-ai:<12-char commit>-dirty` | `io.churnsense.source.dirty=true` |

- Files outside the build context (an untracked `audit-reports/`, say) neither enter the
  image nor make it dirty.
- The labels `org.opencontainers.image.revision` (the full commit) and
  `io.churnsense.source.dirty` are set from the same values.
- Built without the helper, the image says `unknown` for both, never "clean".
- To check a built image:
  `docker inspect --format '{{json .Config.Labels}}' churnsense-ai:<tag>`.
- Deploy only clean tags. A `-dirty` image is for local validation.
- The model artifact is ignored by git, so git cannot vouch for it. The bundle check's
  hash does (§7).

**Front end stage:** `npm ci` (install scripts are disabled by `frontend/.npmrc`), then
`npm run build`.

**Runtime stage:**

- Installs the hash-locked runtime packages and runs `pip check`.
- Copies the source and the approved files (never the dataset, never
  `shap_global.json`) and runs the bundle check.
- Compiles the source, and drops to the non-root user `churnsense` (uid 10001). No
  `data/` folder is created: there is nothing to mount.

To get the image to the host, either build it there from the same checkout, or
`docker save … | ssh host docker load`. Do not push it to a public registry.

## 9. Start

```sh
cd deploy
CHURNSENSE_IMAGE=churnsense-ai:<tag> docker compose up -d
docker compose ps        # app: healthy; caddy: running
```

How the two containers start:

- **Caddy** waits for the app's health check to pass (`depends_on: service_healthy`).
- **The app:**
  - mounts nothing of the host (§6);
  - runs with a read-only root filesystem and a 384 MB memory-backed `/tmp`, which
    holds uploads over 1 MB while they arrive and the numba and matplotlib caches;
  - has no capabilities, `no-new-privileges`, and `init: true`;
  - is limited to 1 CPU and 1.5 GB RAM; measured locally in the earlier layout, its
    peak was about 47% of that limit (§16).

## 10. TLS

- Caddy obtains and renews the certificate automatically for the site address, using
  the `caddy_data` volume.
- Ports 80 and 443 (TCP) must be reachable; open UDP 443 as well for HTTP/3.
- Firewall: allow inbound 22 (from your address only, if possible), 80, 443/tcp and
  443/udp. Deny everything else.
- **HSTS:** the app sends `Strict-Transport-Security: max-age=31536000` on HTTPS
  responses (from the trusted `X-Forwarded-Proto`). Make sure HTTPS works before
  sharing the URL, since browsers will remember it.

## 11. Health and readiness

| Endpoint | Meaning | Status codes |
|---|---|---|
| `GET /health` | Liveness: the process answers. Says whether a model is loaded. | Always 200 (`ok` or `degraded`) |
| `GET /ready` (also `/api/ready`) | Readiness: the model is loaded, the customer book is built, the front end is built. | 200 `ready`, else 503 `not_ready` with which part is missing |

**How readiness works:**

- The book is built once, at startup, so the first visitor does not wait for it.
- `/ready` only reads what is in memory. It never loads, reads or scores anything, and
  it names no path or reason; the log says why.
- **Healthy container, wrong data:** a model, demo data or front end that is missing
  or fails its check leaves the container `unhealthy`, but still running, so the log
  explains it. (`/ready` still calls the book `dataset`: the field name is kept.)
- **Probes:** the container health check calls `/ready` on `127.0.0.1` from inside the
  container. From outside, probe `https://<your-domain>/ready`.
- **No automatic recovery from a failed startup build.** If the book fails to build at
  startup, readiness stays 503 and Caddy never starts, because it waits for a healthy
  app. Everything the book is built from is inside the image, so the cause is a broken
  image: fix the bundle (§7) and redeploy. A restart alone changes nothing.
- **The readiness read takes no lock**, so a health check never waits on a rebuild.

## 12. Logs

```sh
docker compose logs -f app      # startup, refusals (sampled), errors; never upload contents
docker compose logs -f caddy
```

- Logs are rotated by Docker (`json-file`, 3 × 10 MB per service).
- There is no access log. uvicorn runs with `--no-access-log`, because the request
  scope carries the visitor's address after the trusted-proxy rule, and Caddy's own
  access log is off. Both would record visitors' IP addresses, which is personal data;
  turn one on only deliberately, with a retention period.
- The app logs refusals in samples (the 1st, 10th, 100th…), never upload contents.

## 13. Restart

```sh
docker compose restart app      # the model and book load again (no state is lost)
```

## 14. Roll back

Every image is tagged with its commit. To roll back:

```sh
CHURNSENSE_IMAGE=churnsense-ai:<previous-tag> docker compose up -d app
```

- The model and ledger are inside the image, so a rollback can never pair code with a
  model it was not built with.
- The published evidence is inside the image too, bound to that model: a rollback
  brings the matching aggregates back with it.
- Keep the last two or three tags on the host (`docker image ls churnsense-ai`).

## 15. Backups

There is no application data to back up: uploads are scored in memory and discarded.

Keep these:

- The Caddy volumes (`caddy_data`, for certificates and the ACME account), or let Caddy
  issue certificates again.
- An offline copy of the approved model artifact, the two demo files and - on the
  maintainer's machine only, never on the host - the dataset (with their SHA-256
  hashes), and of the image tags you deploy.

## 16. Resource monitoring and the memory measurement

**Status: LOCAL DOCKER MEASUREMENT DONE (2026-11-10), FOR THE EARLIER LAYOUT. THE
CURRENT IMAGE AND THE VPS ARE NOT MEASURED.** The figures below were taken while the app
built its book from a mounted dataset (7,043 customers). It now holds 2,000 generated
customers and reads its aggregates from a file, so they are expected to overstate the
current image's use, but that expectation is not measured. Repeat the procedure before
relying on them.
The app container's memory was measured in local Docker, and the figures are below. The
real RAM use of a VPS — the host, Caddy and the app together, on the deployment's own
machine — has not been measured: repeat the procedure there. Startup time was not
recorded.

**Local measurement, 2026-11-10.**

- **Image:** `churnsense-ai:c8495555f955-dirty` — commit `c8495555f955` with the
  uncommitted deployment work, a local validation build (§8), not a deployable tag.
- **Container limit:** `memory_max` = 1,610,612,736 bytes (1,536 MiB), the
  `deploy/compose.yaml` limit; 1 CPU.
- **Conditions:** local Docker, one machine; Compose with the dataset mounted; the
  helper run inside the app container against `127.0.0.1:8000`, so every request came
  from one client and bypassed Caddy; synthetic customers only. The helper's own
  process runs in the same cgroup, so the payloads it builds are in the figures.

| Run | Phase | Answers | `memory_peak` (bytes) | MiB | `oom` / `oom_kill` |
|---|---|---|---|---|---|
| First full run (`--phases all`) | `idle` | ready | 560,562,176 | 535 | 0 / 0 |
| | `typical` | 5 × 200, 5 × 503 `explanations_busy` | 675,258,368 | 644 | 0 / 0 |
| | `one-client-max` | 2 × 200, 1 × 429, 9 with no status recorded (see below) | 762,073,088 | 727 | 0 / 0 |
| Isolated re-run (`--phases one-client-max`) | `one-client-max` | 2 × 200, 3 × 429 `batch_busy`, 7 × 429 `rate_limited`; `transport_errors` = `{}` | 439,963,648 (`memory_peak_before` 222,130,176) | 420 (212) | 0 / 0 |

- **Highest peak seen:** 762,073,088 bytes, 47% of the limit. No OOM and no OOM kill in
  any run.
- **Every `memory_peak` is cumulative** since the container's cgroup was created (see
  "Reading `memory_peak`" below): the 762,073,088 is the maximum of the whole first
  run, startup included, not the last phase's own peak.
- **The typical phase behaved as designed:** both batches and three explanations were
  answered 200, and the other five explanations 503 `explanations_busy`.
- **The nine results without a status are explained.** The first run used an earlier
  helper that recorded any failed send as `0`. Re-run alone with the current helper,
  all twelve uploads were answered over HTTP: the seven the `batch` budget refused got
  429 `rate_limited`. For three of those seven the upload was cut off while still
  being sent (`send_interrupted`: `ConnectionResetError`) — the service answers a
  spent budget before it reads the body, then closes — and the 429 was still read.
  They were refusals by design, never failures to reach the service. The first run
  split differently (one `batch_busy`, nine refused early) because the typical phase
  had just spent part of the same budget.

**Procedure** (local first, then on the host):

1. Build an image (§8) and start Compose (§9).
2. Wait for `docker compose ps` to show `healthy`, and record how long it took.
3. Run the load **inside** the app container, so its own cgroup is measured:

   ```sh
   cd deploy
   docker compose exec -T app python - --phases all < ../scripts/measure_capacity.py
   docker stats --no-stream churnsense-app-1 churnsense-caddy-1
   ```

   The helper sends synthetic customers only (built from the contract's example
   records). It never reads the dataset file, never trains or evaluates, and never runs
   Docker. Its phases are:

   - `idle`: memory once ready.
   - `typical`: two 10,000-row batches (every batch slot) and eight explanations
     (more than the explanation slots), at once.
   - `one-client-max`: as many ~5 MiB uploads as one client may have in flight (12).
     The per-client budgets apply, so this is what one visitor can cause.

**Evidence to record.** Each phase prints one JSON line:

- `http`: the HTTP answers, counted by status and the service's `error` code
  (`"429 rate_limited": 7`). A 429 or a 503 is an answer — the service turning a
  request away as designed — and is never counted as a failure to connect.
- `transport_errors`: the requests that got **no** HTTP answer, counted by the stage
  that failed (`connect`, `send`, `response`) and the exception class
  (`"send ConnectionResetError": 2`).
- `requests`: every request by its id (`typical-explain-03`,
  `one-client-max-batch-07`) with its own outcome — status, `error` and `retry_after`,
  or `stage`, `exception`, `errno` and `reason`. A request whose upload was cut short
  but which was still answered is an HTTP outcome carrying `send_interrupted`.
- `memory_current`, `memory_peak_before`, `memory_peak`, `memory_max` (the container
  limit) and the `oom` / `oom_kill` counters.

Expect:

- `oom` and `oom_kill` at 0;
- `transport_errors` empty in every phase. If it is not, the measurement is not
  finished: read the failing ids in `requests` and explain them first;
- `typical`: at least 5 responses with 200 (2 batch slots + 3 explanation slots), and
  the other explanations — up to 5 of the eight — 503 `explanations_busy`. That is
  the intended behaviour, not a fault: eight explanations at once are more than the
  slots, and one past them is turned away, never queued;
- `one-client-max`: at most 2 responses with 200 (the batch slots), and 429
  `batch_busy` / `rate_limited` / `too_many_in_flight` for the rest. How the 429s
  divide depends on how much of the client's `batch` budget is left: wait at least
  30 seconds after any earlier batch request for a full budget.

**Reading `memory_peak`.** It is the kernel's `memory.peak`: the highest the
container's cgroup has reached **since the cgroup was created**. It is cumulative —
startup, the model load, building the book and every earlier phase are in it — so the
value printed with a phase is not that phase's own peak. `memory_peak_before` is the
same counter read as the phase began; the phase raised the mark only if the two
differ. To attribute a peak to one phase, restart the app container, wait for
`healthy`, and run that phase alone (`--phases typical`).

**Turning it into a size:**

- **Container limit.** The app needs at least the `memory_peak` printed after the
  last phase of a full run (the highest it reached at any point, startup included),
  plus the worst case of uploads arriving from many clients at once.
  That worst case is a calculation, not something one machine can produce:
  - each upload over 1 MB is spooled to the 384 MB tmpfs, so up to 384 MB in total;
  - each JSON body is at most 64 KiB, and at most 64 requests are in flight.
- **VPS RAM.** Add Caddy's peak and about 0.5–1 GB for the OS, then round up to the
  next VPS size.
- If the app's limit is too low, raise `deploy.resources.limits.memory` in
  `deploy/compose.yaml`.

**Monitoring afterwards.** `docker stats`, and an external uptime monitor on `/ready`,
are enough for this demo.

## 17. Common failures

| Symptom | Likely cause | What to do |
|---|---|---|
| App `unhealthy`; `/ready` says `model: false` | Artifact missing, or its hash disagrees with its metadata | Rebuild from the approved checkout; run §7 |
| `/ready` says `dataset: false`; data routes answer 503 `demo_data_unavailable` | A demo file is missing, malformed or built for another model; or a dataset file is present beside a public demo | Read the app's log line `demo data unavailable`; rebuild the two files with `make demo-data` on the maintainer's machine, run §7, rebuild the image; remove any dataset file from the container |
| The bundle check refuses a volume on the app | Something was mounted into the app container | Remove it: the app mounts nothing (§6) |
| Every request 403 `loopback_only` | Caddy's address is not `api.trusted_proxies`, or the network changed | Check `deploy/compose.yaml` against `configs/deploy.yaml`; run §7 |
| 400 `invalid_host` | Domain differs between Caddyfile and `api.allowed_hosts` | Set both; rebuild |
| Explanations or batch uploads 403 `cross_origin` | The app is not seeing `X-Forwarded-Proto: https` | Caddy must be the peer, and HTTPS must work |
| 400 `invalid_forwarded_header` | A proxy in front of Caddy, or a misconfiguration, sends unreadable headers | Remove the extra proxy, or configure it explicitly |
| 413 with a 64 KB limit | A non-upload request with a large body | Expected; only the three upload paths take files |
| 429 `rate_limited` | The client spent an endpoint's budget (§20) | Expected; wait for `Retry-After` |
| 429 `too_many_in_flight` | One client has 12 requests open at once | Expected; wait for `Retry-After` |
| Batch upload 429 `batch_busy` | Two batches already being scored (`BATCH_SLOTS`) | Expected under load; the page asks the visitor to retry |
| Occasional 502 on POST | Keep-alive mismatch: Caddy kept a connection uvicorn closed | Keep Caddy's `keepalive` (60s) below uvicorn's `--timeout-keep-alive` (75) |
| No certificate | DNS not pointing at the host, or port 80/443 closed | Fix DNS or the firewall; `docker compose logs caddy` |

## 18. Shut down safely

```sh
docker compose down             # SIGTERM; uvicorn finishes requests within 20 s
```

`docker compose down -v` also deletes Caddy's certificates. Avoid it unless you mean
to.

## 19. Update the application

1. Merge the change, then build from the updated checkout.
2. Run §7.
3. Build a new tag.
4. `CHURNSENSE_IMAGE=churnsense-ai:<new-tag> docker compose up -d`.
5. Check `/ready` and the pages.

A new model or dataset is a new release under SECURITY.md's process. It is never a
deployment step.

## 20. Security limits of this demo

**What it does not have:**

- **No authentication.** Every endpoint is public, including the batch upload and
  `/docs`.
- **No tenant isolation and no persistence.** Uploads are not stored, but they do pass
  through the server.
- **Never upload real or personally identifiable customer data.** The batch page says
  so.

**What the demo serves instead of the dataset** (§6 has the mechanism):

| A visitor asks for | What answers |
|---|---|
| A customer listing, one customer, a CSV export, the examples | Generated demo customers only (`DEMO-` IDs), each `synthetic` / `withheld` / `active` |
| An explanation of a customer, or of a typed record | Attributions against a background of 100 generated customers; the response says `background: synthetic` |
| The explainability page's sample | 300 generated customers' attributions; no validation row |
| Observed rates, breakdowns, performance | The dataset's published aggregates; no group under 20 has a rate |
| The validation sweep, its curve, the calibration bins, the operating point, the model's validation record at 0.5 | The sweep at 11 cuts (0.00, 0.08, 0.18, 0.23, 0.30, 0.36, 0.39, 0.50, 0.53, 0.58, 0.64), each interval between two with at least 20 churners and 20 stayers. The cuts include every threshold the documents state validation figures at (the peak 0.30, the operating point 0.36, and 0.50), so the demo's peak and recommendation are the model card's (0.30, $26,579; 0.36, $26,547), and a document read beside the demo cuts nothing finer. Under other assumptions the peak is read among the same cuts. If no such cuts existed, no sweep and no figure at a threshold would be shown, and the pages would say why |
| Validation segments at 0.36, observed churn per risk band | Shown only where each side of the cut (each band) holds none or at least 20 churners and 20 stayers, and where the hidden ones together do too: Contract and SeniorCitizen are hidden whole |
| A dataset customer by ID | 404, as for any ID the book does not hold |
| A label check on an uploaded file | Nothing: no label is compared or returned, because the demo cannot tell a held-out customer apart, and so no answer depends on who was uploaded |

**Closed in the API, 2026-11-12: differencing the validation sweep (E8).** The sweep
used to be published at every 0.01; of its 100 steps, 16 held exactly one churner, and
the difference of two neighbouring retained values was that customer's monthly charge.
Now every count an outsider can solve for *exactly* from the public answers - any
difference of cuts, a total less the shown groups, a band less a validation interval,
any union of those - holds none or at least 20 churners and 20 stayers.
`make demo-data` checks this itself before it writes the file
(`churnsense.disclosure_audit`) and writes nothing otherwise. On the real file, read
through the service in-process, 4,130 counts can be solved for and none breaks the rule,
also with the README's figures at 0.30, 0.36 and 0.50 added to what the outsider knows;
the same arithmetic on the old file finds 1,248 that do. The tests hold the same checks,
with controls that find the old leak.

What this is not: it is minimum-cohort suppression, a rule about what the application
publishes. It is not differential privacy and it does not make anyone anonymous. Counts
still bound one another (a hidden segment's flagged customers bound its flagged churners
from above), and the IBM sample is itself public: with it, the split seed in this
repository and `/api/predict`, anyone can recompute every validation score and outcome.
The rule keeps the demo from *publishing* finer groups; it protects nothing the dataset
already gives away (see also `SECURITY.md`).

**The repository's own reports, 2026-11-12.** `reports/threshold_sweep.csv` (the full
validation sweep at every 0.01, with counts and money) and `reports/model_comparison.md`
(validation figures at 0.88, 0.50, 0.36, 0.30, 0.23 and 0.22, and a segment table with
groups of a few customers) are no longer tracked: `make train` still writes them, locally,
and `.gitignore` keeps them out. The documents that quoted their segment figures now
suppress the same groups the demo does. The held-out release report and the final
evaluation report still name `reports/model_comparison.md` as where the validation
figures came from; they are part of the published record and were not changed.

**What removing them does not do.** The commit that removed them deletes them from the
tree from that commit on. Every earlier commit still holds them - on GitHub, in every
clone and fork, and in any copy GitHub or a search engine cached - and anyone can read
them there (for example `git show afdd8257:reports/threshold_sweep.csv`). Only rewriting
the history and asking GitHub to purge its caches would remove them; that was decided
against, so their exposure is permanent for what was already published. Neither file was
ever in the image.

**What protects it:**

| Concern | Control |
|---|---|
| Who may connect | Only loopback and the configured proxy (`LoopbackOnly`), whose forwarded headers are read from the trusted end |
| Host and Origin | Allowed host only; same-origin state changes; cross-site `/api` reads refused |
| Request size | By path, in the app and in Caddy: the three upload paths take 5 MiB (Caddy: 6 MB), every other path 64 KiB, whatever the content type; 10,000 rows; 1,000 columns |
| Load | Per-visitor budgets (below); at most 12 requests in flight per visitor; at most 2 batches and 3 explanations at once in the process; uvicorn `--limit-concurrency 64` |
| Slow clients | Caddy allows 10 s for headers and 30 s for a whole body; the in-flight cap keeps one visitor to 12 of the 64 server slots |
| Browser | Strict CSP, `nosniff`, `no-store`, `frame-ancestors 'none'`, HSTS over HTTPS |
| Errors | Generic, no internals or paths |
| Container | Non-root, read-only, no capabilities, internal network with no route out |

**Budgets**, per visitor (an IPv6 visitor counts as its /64). A burst, then one
request per interval. A HEAD request counts as the GET it runs.

| Budget | Endpoints | Burst | Then one every |
|---|---|---|---|
| explain | `POST /api/explain` | 10 | 2 s |
| batch | `POST /api/batch/score`, `/api/predict/batch`, `/predict/batch` | 5 | 6 s |
| predict | `POST /predict`, `/api/predict` | 20 | 0.5 s |
| query | `GET /api/customers`, `/api/segments`, `/api/threshold/sweep` | 30 | 0.5 s |
| export | `GET /api/customers/export` | 3 | 10 s |

These are not budgeted:

- the memoised reads: overview, performance, examples, one customer, the global
  explanation;
- `/health`, `/ready`, the pages and the static assets. These are also outside the
  in-flight cap.

**Rate limits are per visitor address and held in memory.** They reset on restart, and
a visitor with many addresses can spend many budgets. The process-wide batch and
explanation limits bound the total either way.

**What the batch slot does not cover.** It covers reading, validating and scoring an
upload. Turning a 10,000-row result into JSON happens after the slot is released, so a
few more large responses than `BATCH_SLOTS` can be in memory at once.

**The busy message is generic.** A turned-away batch shows the batch page's existing
429 message ("Too many files in a short time"), even when other visitors caused it.
A dedicated "service busy" message is a front-end follow-up.

**Not for production.** A multi-user product needs:

- authentication and per-tenant isolation;
- stored data with an audit trail;
- a shared rate-limit store, background jobs for batches, and replicas;
- monitoring with alerting.

None of these exist here.
