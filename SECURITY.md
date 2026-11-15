# Security policy

## Supported versions

ChurnSense is a portfolio project with no tagged releases. Only the latest
commit on `main` is maintained: fixes land there and are not backported.

## Reporting a vulnerability

Please **do not** open a public issue, discussion or pull request for a
security problem.

Report it privately through GitHub instead: on the repository's **Security**
tab choose **Report a vulnerability**, or go straight to
<https://github.com/HeyderHesenov/churnsense-ai/security/advisories/new>.
The report is visible only to you and the maintainer.

A useful report says which part is affected (API, dashboard, training or
scoring pipeline, CI workflows), the commit you tested, the steps to
reproduce, and what an attacker gains.

## What to expect

The project has a single maintainer working on it in their own time, so there
is no guaranteed response or fix time. Reports are read and triaged on a
best-effort basis. A confirmed vulnerability is fixed on `main` and, where it
warrants one, disclosed in a GitHub security advisory that credits the
reporter unless they ask not to be named.

Please keep the details private until a fix is available or we have agreed
that none is coming.

## Dependency audits

CI audits the dependencies on every pull request and every push to `main`:
`pip-audit` over the environment the test jobs resolve, and over both hashed
locks exactly as pinned. The job is a required check, so **any known
vulnerability blocks merging** - `pip-audit` reports no severities, so there is
no "low enough to ignore" tier. Dependabot alerts and security updates cover
the same locks between pull requests, and the dependency review blocks a pull
request that would add a vulnerable version.

When a fix exists, the dependency is upgraded (`make lock
LOCK_ARGS="--upgrade-package <name>"`). When none does, the advisory is assessed
and either the dependency is removed or the advisory is accepted explicitly:
its ID goes on both `pip-audit` steps in `.github/workflows/ci.yml` as
`--ignore-vuln <ID>`, and a row is added below. An entry without a reason and a
review date is not accepted.

| Advisory | Package | Why it does not affect this project | Owner | Review by |
|---|---|---|---|---|
| *(none)* | | | | |

The front end is audited with npm in two tiers, kept apart on purpose
(`frontend` job in `.github/workflows/ci.yml`): `npm audit --omit=dev` covers
what ships in `frontend/dist` and blocks at **any** severity; `npm audit
--audit-level=high` covers the dev toolchain (linters, test runners, the type
generator) and blocks at high and critical. A moderate or low advisory in a
dev-only package is assessed and, when it does not reach this project, listed
here; one that ships, or reaches high, is fixed instead.

| Advisory | Package (dev only) | Why it does not affect this project | Owner | Review by |
|---|---|---|---|---|
| [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249) (moderate) | `minimatch` < 11.0.1, through eslint, typescript-eslint and `@redocly/openapi-core` | The amplification needs `optimizationLevel` ≥ 2, which no caller sets; the patterns come only from `eslint.config.js`, never from input; nothing of it is in `frontend/dist`. `npm audit fix --force` would downgrade the linters, and an override to 11 changes `**` matching. Assessed 2026-11-01 (`docs/frontend/PLAN.md`, Phase 3.5). | maintainer | 2027-02-01, or as soon as eslint and typescript-eslint accept `^11.0.1` |

Accepted advisories are reviewed by their date and removed as soon as a fixed
version is available.

## Secrets

GitHub secret scanning and push protection are enabled for the repository, so
a recognised credential is blocked at push time and alerted on if it lands.
The project needs no credentials: `.env.example` lists the only two
environment variables it reads, neither of them secret. If a credential is
ever committed, treat it as exposed: revoke it first, then clean the history.

## Scope

In scope: the code in this repository — the FastAPI service
(`src/churnsense/api`), the Streamlit dashboard (`app/`), the data, training
and scoring pipeline, and the GitHub Actions workflows.

The API, the front end it serves (`frontend/`) and the dashboard are **local
by design**: they have no authentication and bind to `127.0.0.1`, and the API
answers loopback peers only. That is documented in the
[README](README.md#security) and is not a vulnerability in itself. The one
deployment the repository supports, a public portfolio demo behind Caddy
([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)), adds exactly
one peer: the proxy address listed in `api.trusted_proxies`, whose forwarded
client address and scheme are the only ones believed. It is still
unauthenticated, so it must never hold real customer data. It holds no dataset
either: it is configured as `deployment: {mode: public_demo, customer_records:
synthetic}`, lists generated demo customers only, and shows the sample dataset
solely as published aggregates in which no group with fewer than 20 churners or
fewer than 20 stayers has an outcome shown. The dataset is not in its image, not mounted into its container and not read
by that code path; a missing or untrusted demo file is answered with 503, never
with other data. Every request body is
limited by path: 5 MiB on the three upload paths, 64 KiB everywhere else. A way around one of
the mitigations described there (the body-size limit, upload validation,
export neutralisation, the Host and Origin checks, the trusted-proxy rule, the
per-client limits on explanations and batch uploads and the process-wide
batch limit, the front end's Content-Security-Policy) is in scope. So is any way
to make the public demo return a row of the dataset, name one of its customers,
or give the outcome of a group with fewer than 20 churners or 20 stayers - including a
count solved for exactly by combining the demo's published figures with one another, with
the current documents in this repository, or with the immutable release reports and the
dataset's public per-level totals ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), section 20).

Known and **not** resolved, recorded in the same section:

- the immutable release reports (`reports/final_evaluation.md`,
  `reports/held_out_release.md`) state the validation sweep at 0.10 to 0.80, and two of
  their own intervals hold fewer than 20 stayers; the current suppression keeps the demo
  from cutting them finer, but does not and cannot cover what those reports state;
- every earlier commit still holds the full validation sweep, the old segment table, the
  design artboards' measured arrays and the one- and two-year contract counts, from which a
  figure the demo now withholds (Two year's validation churners) can be worked out; the
  history was not rewritten.

Bounds are not covered either: published counts still limit hidden ones from above and
below. A report showing such a bound is welcome, but it is a known limit of minimum-cohort
suppression, not a defect in it.

Out of scope: a vulnerable third-party dependency with no demonstrated effect
on this project (report it upstream; Dependabot tracks advisories here), and
loading a model artifact you replaced yourself — the artifact path comes from
local config and unpickling a file is equivalent to executing it.

## The held-out release

The test partition's customers are listed with their flags and scores, so every figure
published about them is an equation over known people. Its rules are in
`src/churnsense/evaluation/disclosure.py`: one two-by-two table and the rates it gives, only
if all four cells hold at least 20 customers. No segment, reliability bin, calibration
error, Brier score, ROC-AUC, PR-AUC, second cut or simulated money, whatever its value.

Two tables on the same customers subtract to what the customers between them did, so the
project publishes **one release** of the partition. Five different things stand behind that,
and they do not reach equally far:

| Level | What it does | What it cannot do |
|---|---|---|
| **One working tree**, enforced by code (`src/churnsense/evaluation/release.py`) | The ledger and a `flock` on its folder allow one release in that checkout | See any other clone, worktree or fork. `flock` is local to one filesystem and one ledger, not a global lock |
| **The official environment**, an operating rule | Names the one place the project's release is made | Nothing checks it in code: it holds because the one operator follows it |
| **A pull-request check**, best-effort (`held-out-release-governance`) | Catches an accidental or ordinary invalid change to the ledger on its way to `main`, a second release among them | Stop a pull request written to get past it, which can edit its own workflow or checker. It is not a required check today, and requiring it would not change that |
| **Maintainer review**, an operating rule | A person inspects every change to the governance files | Nothing enforces it: the ruleset requires no approval |
| **An independent calculation**, not preventable | | The dataset is public and the split reproducible, so anyone can compute what they like. That is not this project's release |

### In one working tree

`governance/held_out_release.json` holds the state, the fingerprints and the released figures
in one file, replaced in one step. It holds no customer ID, label, score or withheld figure.

- The state moves only forward: `open`, `reserved`, `published`.
- `make evaluate CONFIRM=yes` first checks the environment and the artifact, then takes an
  exclusive lock, checks the fingerprints, and only then scores. Repeating the same evaluation
  scores nothing. A different one is refused, naming the fields that differ. A missing or
  damaged ledger refuses too; it never means "go ahead".
- There is no reset, force flag or environment variable. No variable can redirect the ledger or
  the command's paths, which come from the default configuration by name, though logging may still
  read `CHURNSENSE_CONFIG` for its own settings.

**What counts as the same evaluation.** The fingerprints cover:

- the dataset, the split, the artifact and its threshold;
- the preprocessing and model configuration and the feature schema;
- the evaluation's source: `evaluation/{disclosure,metrics,release,report}.py`;
- the data path's source: `data/{download,loader,schema,split}.py`, `features/preprocess.py`
  (`download.py` computes the dataset's hash and the artifact's byte check);
- the model's source: `models/{calibration_clamp,predict,registry}.py`, and `models/train.py`, whose
  `load_artifact` checks the artifact's bytes against the hash the identity records;
- the runtime: the Python version, platform and machine, scikit-learn, NumPy, pandas, SciPy and
  joblib, and the digest of `requirements.txt`.

Source files are hashed by their bytes. Editing any of them, or moving the lock, makes a later
run a different evaluation: it is refused, even one that would only read a release back.
Readers are not affected.

Not hashed, on purpose: `config.py`, because the values it parses that can move a figure (features,
split, seed, row count, training settings, the dataset's hash) are fingerprinted themselves; the
validation sweep and the money formatting (`evaluation/threshold.py`, `viz.py`), which only the
report's validation table uses; and the modules off the scoring path, such as the API and the
dashboard. `tests/test_release.py` checks that the scoring path's steps and the two hash helpers sit in
fingerprinted files. It checks a list kept by hand, not every call they make, so a change to what
the evaluation calls needs this list read again. The ledger's own path comes from where the package
is, not from the configuration.

**What a failure does depends on when it happens.**

- **Before the reservation.** A Python outside the supported minor versions, a numerical package
  other than the locked one, an artifact trained under another scikit-learn, or a dataset or split
  that is not the artifact's stops the run. So does a failed rehearsal: on the validation partition
  the model scores, the metrics and the threshold's confusion matrix are worked out and dropped, and
  the description, summary, report, ledger and metadata are serialized for one withheld evaluation and
  one invented table, into a temporary directory. The ledger is not touched, and no validation figure
  is logged or printed.
- **After it, and transient.** A killed process, a full disk or running out of memory leaves the
  ledger `reserved`, which every reader shows as withheld. Run the same command again, under the
  exact same identity, and it finishes.
- **After it, and deterministic.** A failure that repeats stays `reserved` on every retry, and fixing
  it changes a fingerprint, which makes it another evaluation, and that is refused. So such a failure
  can permanently consume the release. The rehearsal looks for these first. It reduces this risk and
  does not remove it: it cannot see an unexpected failure specific to the test rows or a system-level
  failure, and neither can anything else before those rows are read.
- **After publication.** The release is final and is never scored again. If the report cannot be
  produced, the ledger is left as it is, only the class of the error is logged and the exit status
  is non-zero. The same command renders `reports/held_out_release.md` from the ledger without
  scoring the test partition again (it reruns the rehearsal and the validation sweep the report
  shows), for as long as every fingerprint is unchanged. After a change to one, it refuses rather
  than render under another identity, and the report stays unwritten until the exact bytes are back.

Whichever it is, a failure is not an answer: nothing is published, no report or figure is written,
only the class of the error is logged, and printed output comes only after publication. Only a table
under the 20-customer rule is recorded as a withheld release, and that is final.

**What the rehearsal is kept from, and what it is not.** It receives an object holding only the
validation rows and labels, so it scores nothing else and computes nothing from anything else. That
boundary is narrower than the process: `make_splits` loads the whole file and stratifies on every
label, the test partition's included, as it does in `make dev`, so the test rows are held in the
process's memory before the rehearsal starts. No test row or label reaches the model or a metric
until after the reservation.

### The official release

The project's release is made only on the maintainer's **MacBook Air M3, in the canonical
checkout at `~/Desktop/Customer Retention Intelligence Platform`**. Every clone and worktree has its
own ledger, on that machine too, so what makes this one the project's is an operating rule, and the
last line of the table above says why no more than that is claimed.

1. The release-governance code is on protected `main`, merged after the maintainer's review, and
   the checks the ruleset does require are green.
2. `held-out-release-governance` has run green and been added to the ruleset. **Not done today**:
   see the manual step below.
3. The checkout is on `main`, clean and up to date, with the committed `open` ledger. This is for
   the first run only: a retry follows "After a reservation", below, and updates nothing.
4. The operator runs `make evaluate CONFIRM=yes` once.
5. **Right after a release, commit the ledger** and `reports/held_out_release.md` on a new branch
   made from that `main`, so it holds those two files and nothing else, and merge them through a pull
   request. Only once the ledger reads `published`: before that, see "After a reservation". Other
   clones do not see it until then.

Other clones, forks and worktrees are not sources of the project's release, whatever their ledger
says: their figures are not this project's. All of this is an operating policy, followed by the one
operator. Canonical evaluation is not something the code can impose, and nothing technical can stop
another clone calculating what it likes on this public dataset.

### What a ledger may hold

`src/churnsense/evaluation/release.py`, for every reader of the release, and the pull-request check
each hold this schema, and refuse anything else whatever its digest says. A reader that refuses
shows the generic withheld state.

| State | Fields |
|---|---|
| `open` | `identity`, `evaluated_at` and `released` are all null |
| `reserved` | `identity` (ten SHA-256 fingerprints and the threshold), `evaluated_at` (a UTC date and time); `released` is null |
| `published` | as `reserved`, with `released` either null, when the rule withheld the table, or exactly: `threshold`, `n`, `positives`, `tn`, `fp`, `fn`, `tp`, `accuracy`, `precision`, `recall`, `f1` |

A published table needs all four cells as whole numbers of at least 20, `n` as their sum,
`positives` as `tp + fn`, the threshold of its identity, and rates that are what its counts give. The
format is exactly 1. A `reserved` or `published` ledger has policy exactly 2. The one intended
difference between the readers: the pull-request check lets an `open` ledger stand at most one
version ahead, only with `POLICY` moved in the script and in `evaluation/disclosure.py` in the same
change, because the script that judges that change is the old one. The runtime reader knows one
policy and withholds until that code has landed. That comparison catches a mistake, not a change
written to get past it. No field outside the list is allowed, so no test Brier, ROC-AUC, PR-AUC,
calibration error, segment, reliability bin or figure, tuning output or money. The file must be the
very bytes the writer lays out, and the writer checks its own output the same way.

A valid shape is not a true value. Neither reader can tell that a fingerprint is a real one, that a
date is the real date, or that the counts are what scoring produced, so digits in those fields could
carry anything a person chose to put there, and that is outside what this project's readers can
check. It takes write access to the ledger, which `disclosure.py` already puts out of scope, since
the command itself derives the fingerprints from files and configuration and the date from the
clock, none of it from scoring. Consumers get only the date and the table, so digits in a fingerprint
would stay in the file, while a chosen date or count would be shown; and all of it, in a file
committed to a public repository, is public. This limit is not closed: reading the ledger change
remains the maintainer's job.

### In a pull request

`held-out-release-governance` is a best-effort consistency check. It runs
`scripts/check_release_ledger.py` on every pull request and merge-queue group for `main`. It needs
no data and runs no evaluation. It compares the ledger on `main` as it is when the check runs with
what merging the change would leave:

| On `main` | What the change may leave |
|---|---|
| no ledger | `open` |
| `open` | itself, `reserved`, `published`; `open` again only under the next policy, with its code in the same change |
| `reserved` | itself, or `published` for the same identity and date |
| `published` | itself, byte for byte |

So a deleted ledger, a second identity, an invalid publication and an old `open` file brought back
by a conflict resolution all fail the check. A conflict with `main` fails outright. An earlier green
run proves nothing once `main` has moved: the ruleset's "up to date" rule, already on, makes such a
branch run again.

**Its limits.** It is not a trusted boundary. It catches accidents and ordinary mistakes, not a
change written to get past it:

- On a pull request GitHub takes the workflow file from the pull request, so a change can edit or
  remove the check.
- The script is read from `main`, but a change that only weakens it touches no ledger and passes.
  Once merged, the next change is judged by the weaker copy
  (`test_known_limit_a_merged_change_can_weaken_the_check_for_the_next_one`). With no script on
  `main`, the workflow falls back to the pull request's own copy: for the change that first adds it,
  and for every change after one that deleted it.
- It is not a required check: the ruleset does not list it, so today it reports and blocks nothing.
  Requiring it does not make it tamper-resistant, for the reasons above.
- A ruleset administrator could change the ruleset; it has no bypass list today.

This limit is accepted on purpose. `pull_request_target` stays prohibited, and so does any other
privileged route to a pull request's code (`workflow_run`, `issue_comment`, `repository_dispatch`).
If a trusted automated gate is needed later, that is a separate architecture decision: a trusted
GitHub App, or another design authorized on its own.

**Manual step.** Still pending: after the check's first successful run, add the context
`held-out-release-governance` to the required status checks of the `Protect main` ruleset. That
makes an ordinary mistake harder to merge. It does not change anything in "Its limits".

### Maintainer review

Whatever the check says, the maintainer inspects every pull request that changes any of these
before merging it:

- `.github/workflows/**`
- `scripts/check_release_ledger.py`
- `governance/**`
- `src/churnsense/evaluation/release.py`
- `src/churnsense/evaluation/disclosure.py`
- anything else that touches a reservation or the ledger's schema

Nothing enforces this. The ruleset requires no approval, so it holds because the maintainer does it.

### A reservation has no way back

- **The exact same identity** may retry: run the same command, which rehearses again first. That
  finishes only if the failure does not repeat.
- **A different identity** is refused, with no exception. Changed code is a different identity.
- **`published`** is final, including a release the rule withheld.

There is no recovery path from `reserved` to `open`: no command, flag, variable or record. Undoing a
reservation is only safe with an independent approval that can be verified, and none exists: the
ruleset requires no approval, and a file saying a review took place proves nothing. So
`governance/release_recoveries.json` has no meaning, and the check refuses it in any change.

A reservation whose evaluation can never run again, because the defect is in the scoring code or the
artifact, or the lock has moved, therefore stays `reserved`. Readers withhold, and the project has no
release to show until the maintainer takes a new and separate decision. That is the accepted price
of not pretending to have an approval.

A future recovery protocol would need, as prerequisites, a ruleset with at least one required
approval from someone other than the author, and a check that the pull request cannot edit. That
protocol is not active today, and neither prerequisite is met.

### After a reservation

From the moment the ledger reads `reserved` until it reads `published`, the canonical checkout has
to stay exactly as it was. A retry is allowed only under the exact same identity, and the fingerprints
cover the source, the lock, the Python and the artifact byte for byte. Most of these can be undone by
restoring those exact bytes. A fix to a real defect cannot, since the old bytes still hold the defect.

In that checkout, until then:

- Do not run `make train`, `make dev`, `make all` (which runs `make dev` first) or `make clean`, or
  otherwise change the model artifact or its sidecar.
- Do not change the configuration.
- Do not update a dependency lock or a package, or merge a Dependabot or other dependency change.
- Do not update Python, not even to a patch version.
- Do not change the scoring implementation or edit any fingerprinted file, comments included.
- Run no Git command that writes: no new branch, no commit, no switch, rebase, merge or pull, and no
  `git restore`, `git checkout --`, `git stash` or `git reset`. Each can move the fingerprinted files
  or silently put the committed `open` ledger back. Do not pull `main` to retry. `git status` and
  `git diff` only read and are fine.

These hold because the operator follows them. Preflight will not notice: it reads the Python's minor
version and the numerical packages, so a patch update or a changed lock passes it, and only the
identity then refuses.

**What to do after a failed run.** The log line says what the ledger holds. A process killed from
outside (a `SIGKILL`, the system running out of memory) logs nothing: read the `state` field of
`governance/held_out_release.json`, which only reads. If the line says the ledger cannot be read, or
the file cannot be read, treat it as step 3.

1. `nothing was reserved or published`: the ledger is still `open`. Fix the cause; that is an ordinary
   change, and the release has not begun.
2. `the ledger stays reserved`: leave the checkout exactly as it is. If the cause was outside the
   fingerprints (the process was killed, the disk was full, the machine ran out of memory), remove that
   cause outside the checkout and run `make evaluate CONFIRM=yes` again: the same command, under the
   same identity, which rehearses again first.
3. If that retry fails the same way, or the only fix is a change to a fingerprinted file, the
   artifact, the lock or the Python: stop. Do not edit, retrain or update anything to make a run pass.
   No command, branch, pull request or record takes the ledger back to `open`, by design (see "A
   reservation has no way back"); what happens next is a separate decision by the maintainer.

A reservation is an uncommitted local file until the release is published, and it is committed only
then (step 5 of "The official release"). Until then a rollback in that checkout would silently bring
back the committed `open` ledger, and no code can see that: this is the accepted price of making no Git
change mid-release, and the rule above is all that guards it. Anyone with write access can replace the
file at any time; the boundary is operational, not cryptographic.

### What this does not protect against

- *The dataset.* It is public and the split is reproducible: this limits what the
  project says, and keeps nothing from someone who fetches them.
- *Local write access.* Anyone who can write to a checkout can restore or delete its ledger, or
  evaluate in a clone that has not received it. The consistency check may notice in a later pull
  request; it cannot stop it happening.
- *What was already published.* `reports/final_evaluation.md` and this repository's Git
  history hold earlier figures, segments and calibration included, and a commit does not
  remove them. That exposure is open, and the earlier tables stay comparable with the release.

This is a limit on disclosure from the application. It is not differential privacy,
anonymity or cryptographic secrecy.
