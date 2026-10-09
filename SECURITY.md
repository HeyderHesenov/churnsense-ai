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
