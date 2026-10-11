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

The API and the dashboard are **local by design**: they have no
authentication and no rate limiting, and bind to `127.0.0.1`. That is
documented in the [README](README.md#security) and is not a vulnerability in
itself. A way around one of the mitigations described there (the body-size
limit, upload validation, export neutralisation, the Host allowlist) is in
scope.

Out of scope: a vulnerable third-party dependency with no demonstrated effect
on this project (report it upstream; Dependabot tracks advisories here), and
loading a model artifact you replaced yourself — the artifact path comes from
local config and unpickling a file is equivalent to executing it.
