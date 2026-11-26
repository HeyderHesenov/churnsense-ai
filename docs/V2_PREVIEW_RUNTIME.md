# V2 Preview runtime (M6)

How to start, build, check and stop the V2 Preview on your own machine. What the
preview is and how its pages work is in
[V2_PREVIEW_WORKSPACE.md](V2_PREVIEW_WORKSPACE.md).

**This is a local preview.** It is ready to be run and looked at on one machine. It
is not a production release and not a commercial service; see
[Limitations](#limitations).

| Command | What it starts | Stop with |
|---|---|---|
| `make v2-preview` | The API and the Vite dev server | Ctrl+C |
| `make v2-preview-build` | Nothing. It builds the V2 Preview into `build/v2-preview` | - |
| `make v2-preview-api` | The API alone, for the built preview | Ctrl+C |
| `make v2-preview-serve` | The server of the built preview alone | Ctrl+C |

## Normal local preview

### Prerequisites

Once, from the repository's root:

```bash
make setup             # .venv (Python 3.12 or newer)
make frontend-install  # frontend/node_modules (Node 22.12 or newer 22.x)
```

The preview needs no dataset and no trained model. Without a model the dashboard's
own pages (V1) say that none is loaded; the V2 pages work the same.

### Start

```bash
make v2-preview
```

One terminal, and no setting to export. The command:

1. checks `.venv`, Node, the installed front end, the configuration and both ports;
2. starts the API with its V2 routes on, and waits until they answer correctly;
3. starts the Vite dev server with the V2 pages on, and checks it as well;
4. prints the addresses and keeps both running.

It installs nothing and changes no file of the project (Vite keeps its own cache in
`frontend/node_modules`).

### How to recognize a successful startup

The start has succeeded when, and only when, this is printed:

```text
The V2 Preview is ready.

  Mode       local Preview, development (Vite dev server)
  Front end  http://127.0.0.1:5180/v2
  API        http://127.0.0.1:8010 (deployment.mode local, CHURNSENSE_V2_DATA_HUB_PREVIEW=1)
  Dataset    synthetic demo data, 500 generated accounts, snapshot 31 October 2026
  Stop       Ctrl+C
```

- **Browser:** <http://127.0.0.1:5180/v2>
- **API:** <http://127.0.0.1:8010>

The lines before it are the API's and Vite's own. If the start fails, the last line
says why and what to do, everything already started is stopped, and the command ends
with a non-zero status.

### Stop

Press **Ctrl+C** in that terminal. Both servers end and `Stopped.` is printed. Then
`make` may add a line of its own saying that it was interrupted, which is normal.
Closing the terminal window, or `kill <pid>` of the command, stops both servers too.

Only `kill -9` of the command itself leaves the two servers running, because nothing
can react to it. To find them, use the command in the next section; then stop each
with `kill <pid>`.

### If a port is in use

```text
V2 Preview: port 8010, for the API, is already in use; nothing was stopped.
```

The command never stops another program. Either see what holds the port and stop
it yourself:

```bash
lsof -nP -iTCP:8010 -sTCP:LISTEN
```

or use another port:

```bash
make v2-preview V2_API_PORT=8011 V2_WEB_PORT=5181
```

The default ports (8010 and 5180) differ from those of `make api` (8000) and
`npm run dev` (5173), so the preview can run beside them.

### Other messages

| Message | What to do |
|---|---|
| `The V2 Preview runs on .venv: run make setup first.` | `make setup` |
| `node is not on PATH` / `Node is v…, and the front end needs ^22.12.0` | Install Node 22 (`frontend/.nvmrc`) |
| `the front end is not installed` / `package-lock.json is newer than the installed packages` | `make frontend-install` |
| `… sets deployment.mode to public_demo` | `unset CHURNSENSE_CONFIG` |
| `the API ended by itself` / `the front end ended by itself` | Read the server's own lines above the message |
| `the V2 Preview is not built` / `is not a V2 Preview build` | `make v2-preview-build` |
| `no API answers on port 8010` | Start `make v2-preview-api` in another terminal |

## Built local preview

The same pages from a built bundle instead of the dev server.

### Build

```bash
make v2-preview-build
```

It writes `build/v2-preview`. Git ignores that folder, and it is outside the Docker
build context. The default build (`make frontend`) writes `frontend/dist`, and the two
never mix: `frontend/vite.config.ts` refuses, before writing anything, a build with the
V2 switch on into any folder but `build/v2-preview`, and a build with it off into that
one. So a switch left exported in the shell, or in an `.env` file, makes the default
build fail instead of putting V2 in it.

Build again after changing the front end: the serve command prints when the build it
serves was made.

### Serve

The built preview needs the API running as a separate process.

```bash
# terminal 1: the API with its V2 routes on
make v2-preview-api

# terminal 2, once terminal 1 says "The V2 Preview is ready."
make v2-preview-serve
```

Then open <http://127.0.0.1:5180/v2>. Every V2 address can be opened and reloaded
directly: `/v2`, `/v2/data-hub`, `/v2/portfolio`, `/v2/accounts`, `/v2/account?id=…`.

The server is `vite preview`. It answers those addresses with the app's page and
forwards `/api` to the API (`preview.proxy` in `frontend/vite.config.ts`). The API sees
the browser's own Host and Origin, so its host and origin checks apply as they do
everywhere else.

With other ports, give both commands the same API port:

```bash
make v2-preview-api V2_API_PORT=8011
make v2-preview-serve V2_API_PORT=8011 V2_WEB_PORT=5181
```

### Stop

Ctrl+C in terminal 2, then in terminal 1. The serve command never stops the API: it did
not start it.

## Configuration

Two independent switches, and one condition:

| | Where it is read | What it does |
|---|---|---|
| `CHURNSENSE_V2_DATA_HUB_PREVIEW=1` | the API, when it starts | Adds the V2 routes |
| `deployment.mode: local` | the API's configuration | Required for the V2 routes; `public_demo` never has them |
| `VITE_CHURNSENSE_V2_PREVIEW=1` | Vite, when it builds or serves the source | Adds the V2 pages |

The commands above set each switch in the environment of the one process it belongs to,
whatever the shell holds, and set it nowhere else. The front end's switch alone does not
open the API: its pages then say that the local server does not serve the preview.

The configuration is `configs/config.yaml`, or the file `CHURNSENSE_CONFIG` names. The
command refuses to start when its `deployment.mode` is not `local`, and it changes no
configuration.

### What is checked before "ready"

- The API process is running, and `/health` is this API's own.
- `GET /api/v2/portfolio/summary` is a valid portfolio summary of exactly the documented
  synthetic dataset. An API without the V2 routes, or another program on the port, is an
  error and not a success.
- The front end answers every V2 address with the app's page.
- Development: the dev server's modules see the V2 switch on. Built: what is served is,
  byte for byte, the page and the scripts of `build/v2-preview`.
- The same portfolio summary is reached through the front end.

### Processes

Both servers are bound to `127.0.0.1`, and no option or environment variable changes
that (`UVICORN_*` and similar variables are not passed on). Each server runs in a process
group made by the command. Stopping signals those groups and no other process: first
SIGTERM, then, after ten seconds, SIGKILL. The API runs without its reloader, so restart
the command after changing Python code; the dev server picks up front-end changes by
itself.

## Data boundaries

- **The CSV preview is stateless.** A file is checked in memory and discarded with the
  response. Nothing is written to disk, imported or committed
  ([V2_DATA_HUB_PREVIEW.md](V2_DATA_HUB_PREVIEW.md)).
- **The Workbench is synthetic.** It shows 500 generated accounts, always the same, with
  a fixed snapshot date of **31 October 2026**. Its pages are labelled "Synthetic demo
  data". A checked file never appears in it
  ([V2_RETENTION_WORKBENCH.md](V2_RETENTION_WORKBENCH.md)).
- **There is no authentication, no persistence and no tenant isolation.** Anyone who can
  reach the port can use the preview, nothing is kept between requests, and there is one
  shared view. That is why it answers this machine only.
- **Do not use it for real customer data.** There are no accounts, no access control, no
  audit trail, no retention or deletion rules and no separation between customers. Check
  synthetic or test files only.

The API is the whole local service, V1 included. If the local dataset and model are
there, the V1 pages list that dataset's customers, exactly as `make api` does.

## Limitations

- V2 is not enabled in production. No deployed file sets either switch, and the default
  build has no V2 code.
- `make api` does not serve the V2 pages: the API's page list is fixed to V1
  (`src/churnsense/api/spa.py`), and M6 does not change it.
- The Action Center (M3) and Impact & Experiments (M4) are contracts only. Their tasks
  and experiments cannot be run here.
- `vite preview` and the dev server do not send the API's Content-Security-Policy with
  the page itself; responses under `/api` carry all of the API's headers.
- It runs on macOS and Linux (process groups and POSIX signals).
- This is a local preview, not a commercial SaaS product and not a production release.
  Being ready to run locally says nothing about readiness for production.

## Checks

| What | Where |
|---|---|
| Environment, loopback binding, ports, readiness, clean stop, both builds | `tests/test_v2_preview_runtime.py` |
| The built preview in a browser: V2 addresses, reload, `/api`, the whole journey, axe | Playwright project `v2-built` |
| The dev server in a browser | Playwright project `v2-preview` |
| The default build has no V2 | `tests/test_workspace_workbench_api.py`, `e2e/v2-off.spec.ts` |

```bash
.venv/bin/python -m pytest tests/test_v2_preview_runtime.py
make e2e   # builds both, then every browser test
```

CI's browser job builds both, checks what each holds, runs the runtime's tests with
none skipped for a missing build, and runs the browser tests. The default build there is
made without the switch, as everywhere.
