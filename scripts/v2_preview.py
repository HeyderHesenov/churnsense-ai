"""Start, check and stop the local V2 Preview (docs/V2_PREVIEW_RUNTIME.md).

Three commands, each kept running until Ctrl+C:

    .venv/bin/python scripts/v2_preview.py dev    # make v2-preview: the API and the Vite dev server
    .venv/bin/python scripts/v2_preview.py api    # make v2-preview-api: the API alone
    .venv/bin/python scripts/v2_preview.py serve  # make v2-preview-serve: the built preview alone

``dev`` starts both servers; ``serve`` starts only the server of the built
preview (``make v2-preview-build``) and needs ``api`` running beside it. A
fourth, ``check``, starts nothing: it only looks at what a build needs.

**Local only.** Both servers are bound to 127.0.0.1 and no option changes that.
The API is the service as it is, with its V2 switch set in its own environment
and a configuration whose ``deployment.mode`` is ``local`` - any other is refused
before anything starts. No file, setting or route is changed.

**Ready means checked.** The API is ready when its own V2 portfolio route
answers with the documented synthetic dataset, not when the port answers; the
front end when its shell, its V2 addresses, its build-time switch and its
forwarding of ``/api`` have each been seen to work.

**It stops what it started, and nothing else.** Each server runs in a process
group of its own, made here, and only those groups are ever signalled. A port
that is in use is reported, never freed.

The standard library only, and nothing is installed: a missing dependency is
reported with the command that installs it.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from churnsense.workspace.workbench_schemas import DatasetInfo

ROOT: Final = Path(__file__).resolve().parents[1]
FRONTEND: Final = ROOT / "frontend"
VITE: Final = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
#: What ``make v2-preview-build`` writes (``frontend/vite.config.ts``, ``V2_PREVIEW_OUT``).
BUILD: Final = ROOT / "build" / "v2-preview"

#: The only address either server is bound to, and the only one this script asks.
HOST: Final = "127.0.0.1"
#: Not ``make api``'s 8000 or Vite's 5173, so the preview starts beside either.
API_PORT: Final = 8010
WEB_PORT: Final = 5180

API_FLAG: Final = "CHURNSENSE_V2_DATA_HUB_PREVIEW"
WEB_FLAG: Final = "VITE_CHURNSENSE_V2_PREVIEW"
CONFIG_VAR: Final = "CHURNSENSE_CONFIG"
SUMMARY: Final = "/api/v2/portfolio/summary"
#: The V2 addresses a reader may open or reload directly (``src/v2``).
V2_PAGES: Final = ("/v2", "/v2/data-hub", "/v2/portfolio", "/v2/accounts", "/v2/account")
#: Text only a build with the preview on holds. The client adds "/api" to these paths.
V2_MARKERS: Final = (
    "/v2/portfolio/summary",
    "/v2/imports/preview",
    "/v2/accounts",
    "V2 Preview",
    "exposed_mrr",
)

#: Loading the model and the book takes seconds; a slow or busy machine, longer.
API_TIMEOUT: Final = 180.0
WEB_TIMEOUT: Final = 60.0
#: How long a server gets to end by itself before its group is killed.
STOP_TIMEOUT: Final = 10.0
POLL: Final = 0.1
MAX_BODY: Final = 8 * 1024 * 1024


class StartupError(Exception):
    """The preview cannot start, or stopped by itself; the message says what to do."""


class Stopped(Exception):
    """Ctrl+C, SIGTERM or a closed terminal."""


# --- what is started --------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path


def _base_env(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` without what would change where or how a server listens.

    uvicorn reads every option from ``UVICORN_*`` as well as from its command line
    (``UVICORN_FD`` or ``UVICORN_UDS`` would replace the address it is given), its
    workers from ``WEB_CONCURRENCY`` and its proxies from ``FORWARDED_ALLOW_IPS``;
    Vite reads extra allowed hosts from its own variable. The two V2 switches are
    dropped too: each server is given its own, and only its own.
    """
    dropped = {
        "WEB_CONCURRENCY",
        "FORWARDED_ALLOW_IPS",
        "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS",
        API_FLAG,
        WEB_FLAG,
    }
    return {
        name: value
        for name, value in environ.items()
        if name not in dropped and not name.startswith("UVICORN_")
    }


def api_command(port: int, config: Path, environ: Mapping[str, str]) -> Command:
    """The API as ``make api`` starts it, without the reloader, with its V2 switch on."""
    return Command(
        name="API",
        argv=(
            sys.executable,
            "-m",
            "uvicorn",
            "churnsense.api.main:app",
            "--host",
            HOST,
            "--port",
            str(port),
            "--no-proxy-headers",
            "--no-access-log",
        ),
        env={**_base_env(environ), API_FLAG: "1", CONFIG_VAR: str(config)},
        cwd=ROOT,
    )


def _vite(node: str, arguments: Sequence[str], port: int) -> tuple[str, ...]:
    # Vite itself, not npm or npx: neither passes every signal on, and npx may install.
    return (node, str(VITE), *arguments, "--host", HOST, "--port", str(port), "--strictPort")


def dev_command(node: str, port: int, api_port: int, environ: Mapping[str, str]) -> Command:
    """The Vite dev server with the V2 pages on, forwarding ``/api`` to ``api_port``."""
    return Command(
        name="front end",
        argv=(*_vite(node, (), port), "--clearScreen", "false"),
        env={**_base_env(environ), WEB_FLAG: "1", "API_PORT": str(api_port)},
        cwd=FRONTEND,
    )


def serve_command(node: str, port: int, api_port: int, environ: Mapping[str, str]) -> Command:
    """``vite preview`` of the built V2 Preview, forwarding ``/api`` to ``api_port``.

    No switch: what the build holds was decided when it was built.
    """
    out = os.path.relpath(BUILD, FRONTEND)
    return Command(
        name="front end",
        argv=_vite(node, ("preview", "--outDir", out), port),
        env={**_base_env(environ), "API_PORT": str(api_port)},
        cwd=FRONTEND,
    )


# --- what must be true first ------------------------------------------------------------------


def local_config(environ: Mapping[str, str]) -> Path:
    """The configuration the API will read, refused unless ``deployment.mode`` is ``local``."""
    try:
        from churnsense.config import DEFAULT_CONFIG_PATH, load_config
        from churnsense.exceptions import ConfigError
    except ImportError as exc:
        raise StartupError(
            f"the Python environment is incomplete ({exc}). Run: make setup"
        ) from None
    path = Path(environ.get(CONFIG_VAR) or DEFAULT_CONFIG_PATH)
    path = path if path.is_absolute() else ROOT / path
    try:
        mode = load_config(path).deployment.mode
    except ConfigError as exc:
        raise StartupError(f"the configuration does not load: {exc}") from None
    if mode != "local":
        raise StartupError(
            f"{path} sets deployment.mode to {mode}; the V2 Preview runs in a local deployment "
            f"only. Unset {CONFIG_VAR} to use the default configuration."
        )
    return path


def node_path(frontend: Path = FRONTEND, environ: Mapping[str, str] = os.environ) -> str:
    """The ``node`` to run, once it and the front end's installed packages are the right ones."""
    node = shutil.which("node", path=environ.get("PATH"))
    wanted = json.loads((frontend / "package.json").read_text(encoding="utf-8"))["engines"]["node"]
    if node is None:
        raise StartupError(f"node is not on PATH. Install Node {wanted} (frontend/.nvmrc).")
    found = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    if not _satisfies(found.stdout.strip(), wanted):
        raise StartupError(
            f"Node is {found.stdout.strip() or 'not working'}, and the front end needs {wanted} "
            "(frontend/.nvmrc)."
        )
    installed = frontend / "node_modules" / ".package-lock.json"
    if not installed.is_file() or not (frontend / VITE.relative_to(FRONTEND)).is_file():
        raise StartupError("the front end is not installed. Run: make frontend-install")
    # The rule ``make frontend`` follows; here nothing is reinstalled, only reported.
    if installed.stat().st_mtime < (frontend / "package-lock.json").stat().st_mtime:
        raise StartupError(
            "frontend/package-lock.json is newer than the installed packages. "
            "Run: make frontend-install"
        )
    return node


def _satisfies(version: str, wanted: str) -> bool:
    """Whether ``version`` (``v22.14.0``) is within a caret range (``^22.12.0``)."""
    have = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    need = re.fullmatch(r"\^(\d+)\.(\d+)\.(\d+)", wanted)
    if have is None or need is None:
        return False
    got, floor = tuple(map(int, have.groups())), tuple(map(int, need.groups()))
    return got[0] == floor[0] and got >= floor


def port_in_use(port: int) -> bool:
    """Whether anything answers on ``port`` at this machine, or holds it.

    Connecting first: on macOS a program listening on every address does not stop
    another from binding 127.0.0.1. ``::1`` as well, because a browser may read
    ``localhost`` as either.
    """
    for family, address in ((socket.AF_INET, HOST), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(1.0)
                if probe.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue  # no IPv6 here
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # As the servers bind: a port in TIME_WAIT is free.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError:
            return True
    return False


def require_free(port: int, variable: str, what: str) -> None:
    if port_in_use(port):
        raise StartupError(
            f"port {port}, for the {what}, is already in use; nothing was stopped.\n"
            f"  See what holds it:     lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  Then stop it yourself, or use another port:  {variable}=<port>"
        )


def v2_markers(assets: Path) -> set[str]:
    """Which of ``V2_MARKERS`` the scripts of a build hold."""
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in sorted(assets.glob("*.js"))]
    return {marker for marker in V2_MARKERS if any(marker in text for text in texts)}


def require_build(build: Path = BUILD) -> None:
    """A V2 Preview build, with the workspace in it."""
    if not (build / "index.html").is_file():
        raise StartupError("the V2 Preview is not built. Run: make v2-preview-build")
    missing = sorted(set(V2_MARKERS) - v2_markers(build / "assets"))
    if missing:
        raise StartupError(
            f"{build} is not a V2 Preview build (it lacks {', '.join(missing)}). "
            "Run: make v2-preview-build"
        )


# --- what is asked of a running server --------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    status: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def get(port: int, path: str, accept: str = "*/*") -> Answer | None:
    """``GET path`` from this machine's ``port``, or None while nothing answers.

    ``http.client`` connects where it is told: no proxy variable is read.
    """
    connection = http.client.HTTPConnection(HOST, port, timeout=10)
    try:
        connection.request("GET", path, headers={"Accept": accept, "Connection": "close"})
        response = connection.getresponse()
        return Answer(response.status, response.read(MAX_BODY))
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def synthetic_dataset(answer: Answer, where: str) -> DatasetInfo:
    """The dataset a portfolio summary is of, refused unless it is the documented synthetic one."""
    from pydantic import ValidationError

    from churnsense.workspace import workbench
    from churnsense.workspace.workbench_schemas import PortfolioSummary

    if answer.status == 404:
        raise StartupError(
            f"{where} answers, but it does not serve the V2 preview ({SUMMARY}: 404). "
            f"It was started without {API_FLAG}=1, or not in a local deployment."
        )
    if answer.status != 200:
        raise StartupError(f"{where}: {SUMMARY} answered {answer.status}, not 200.")
    try:
        summary = PortfolioSummary.model_validate_json(answer.body)
    except ValidationError:
        raise StartupError(f"{where}: {SUMMARY} is not a portfolio summary.") from None
    if summary.dataset != workbench.dataset_info() or summary.total_accounts == 0:
        raise StartupError(f"{where}: {SUMMARY} is not of the documented synthetic dataset.")
    return summary.dataset


def api_answer(port: int, where: str) -> DatasetInfo | None:
    """The API's V2 dataset, None while it is not up, StartupError if it is the wrong server."""
    health = get(port, "/health")
    if health is None:
        return None
    try:
        body = json.loads(health.body)
        ours = body["status"] in {"ok", "degraded"} and isinstance(body["model_loaded"], bool)
    except (ValueError, KeyError, TypeError):
        ours = False
    if health.status != 200 or not ours:
        raise StartupError(f"{where} is not the ChurnSense API (/health: {health.status}).")
    summary = get(port, SUMMARY)
    if summary is None:
        return None
    return synthetic_dataset(summary, where)


_SHELL = re.compile(rb"""<div id=["']root["']""")


def _shell(port: int, path: str) -> bytes:
    """The app's page at ``path``, as a browser opening or reloading the address gets it."""
    answer = get(port, path, accept="text/html")
    if answer is None or answer.status != 200 or _SHELL.search(answer.body) is None:
        status = "no answer" if answer is None else answer.status
        raise StartupError(f"the front end does not serve {path} ({status}).")
    return answer.body


def check_pages(port: int) -> None:
    for path in V2_PAGES:
        _shell(port, path)


def check_proxy(port: int) -> None:
    """The front end's own ``/api`` reaches the V2 API."""
    answer = get(port, SUMMARY)
    if answer is None:
        raise StartupError("the front end does not forward /api to the API (no answer).")
    synthetic_dataset(answer, "the API behind the front end")


def check_dev_switch(port: int) -> None:
    """The dev server was started with the V2 pages on: its modules are told so."""
    answer = get(port, "/src/v2/flag.ts")
    if answer is None or re.search(rf'"{WEB_FLAG}":\s*"1"', answer.text) is None:
        raise StartupError(f"the dev server does not have {WEB_FLAG}=1.")


def check_served_build(port: int, build: Path = BUILD) -> None:
    """What is served is the V2 Preview build, its page and its scripts both."""
    if _shell(port, "/v2") != (build / "index.html").read_bytes():
        raise StartupError("the front end does not serve the V2 Preview build.")
    for script in sorted((build / "assets").glob("*.js")):
        answer = get(port, f"/assets/{script.name}")
        if answer is None or answer.status != 200 or answer.body != script.read_bytes():
            raise StartupError(f"the front end does not serve the build's {script.name}.")


# --- the processes ----------------------------------------------------------------------------


class Supervisor:
    """The servers this command started: watched, and stopped when it ends."""

    def __init__(self, stop_timeout: float = STOP_TIMEOUT) -> None:
        self.children: list[tuple[str, subprocess.Popen[bytes]]] = []
        self._asked = False
        self._stop_timeout = stop_timeout

    def listen(self) -> None:
        """Ctrl+C, SIGTERM and a closed terminal each ask for a stop, and only ask."""
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, self._ask)

    def _ask(self, _signum: int, _frame: FrameType | None) -> None:
        self._asked = True

    def start(self, command: Command) -> None:
        self.pause(0)
        try:
            child = subprocess.Popen(
                command.argv,
                env=dict(command.env),
                cwd=command.cwd,
                stdin=subprocess.DEVNULL,
                # A session, so a process group, of its own: Ctrl+C reaches this
                # script alone, and stopping reaches all the server started.
                start_new_session=True,
            )
        except OSError as exc:
            raise StartupError(f"the {command.name} could not be started: {exc}") from None
        self.children.append((command.name, child))

    def pause(self, seconds: float) -> None:
        """Wait, then end with Stopped if a stop was asked or StartupError if a server ended."""
        if not self._asked:
            time.sleep(seconds)
        if self._asked:
            raise Stopped
        for name, child in self.children:
            if (status := child.poll()) is not None:
                raise StartupError(f"the {name} ended by itself (status {status}); see above.")

    def until[T](self, ready: Callable[[], T | None], what: str, timeout: float) -> T:
        """``ready()``'s first answer that is not None."""
        deadline = time.monotonic() + timeout
        while True:
            self.pause(0)
            if (value := ready()) is not None:
                # It answered; the servers must still be running, so that it was theirs.
                self.pause(POLL)
                return value
            if time.monotonic() >= deadline:
                raise StartupError(f"the {what} was not ready after {timeout:.0f} seconds.")
            self.pause(POLL)

    def watch(self) -> None:
        """Until a stop is asked or a server ends."""
        while True:
            self.pause(POLL)

    def stop(self) -> None:
        """End every server started here, the last first; whatever is left of a group is killed."""
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, signal.SIG_IGN)  # already stopping, in bounded time
        for _, child in reversed(self.children):
            if child.poll() is None:
                _signal_group(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + self._stop_timeout
        for _, child in reversed(self.children):
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=max(0.0, deadline - time.monotonic()))
            # The server, if it did not end; and anything of its own that outlived it.
            _signal_group(child.pid, signal.SIGKILL)
            child.wait()
        self.children.clear()


def _signal_group(group: int, number: int) -> None:
    """Signal a process group ``start`` made; nothing if it is already gone."""
    # macOS answers EPERM, not ESRCH, for a group whose processes have all ended.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, number)


# --- the commands -----------------------------------------------------------------------------


def _long_date(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def _announce(mode: str, dataset: DatasetInfo, rows: Sequence[tuple[str, str]]) -> None:
    lines = [
        ("Mode", mode),
        *rows,
        (
            "Dataset",
            f"synthetic demo data, {dataset.accounts} generated accounts, "
            f"snapshot {_long_date(dataset.as_of_date)}",
        ),
        ("Stop", "Ctrl+C"),
    ]
    print("\nThe V2 Preview is ready.\n", flush=True)
    for label, value in lines:
        print(f"  {label:<10} {value}", flush=True)
    print(flush=True)


def _url(port: int, path: str = "") -> str:
    return f"http://{HOST}:{port}{path}"


def _start_api(supervisor: Supervisor, port: int, config: Path) -> DatasetInfo:
    print("Starting the API (the model and the book are loaded first)...", flush=True)
    supervisor.start(api_command(port, config, os.environ))
    return supervisor.until(
        lambda: api_answer(port, f"the server on port {port}"), "API", API_TIMEOUT
    )


def _start_web(supervisor: Supervisor, command: Command, port: int) -> None:
    print("Starting the front end...", flush=True)
    supervisor.start(command)
    supervisor.until(lambda: get(port, "/", accept="text/html"), command.name, WEB_TIMEOUT)
    check_pages(port)


def run_dev(supervisor: Supervisor, api_port: int, web_port: int) -> None:
    config, node = local_config(os.environ), node_path()
    require_free(api_port, "V2_API_PORT", "API")
    require_free(web_port, "V2_WEB_PORT", "front end")
    dataset = _start_api(supervisor, api_port, config)
    _start_web(supervisor, dev_command(node, web_port, api_port, os.environ), web_port)
    check_dev_switch(web_port)
    check_proxy(web_port)
    supervisor.pause(0)
    _announce(
        "local Preview, development (Vite dev server)",
        dataset,
        [
            ("Front end", _url(web_port, "/v2")),
            ("API", f"{_url(api_port)} (deployment.mode local, {API_FLAG}=1)"),
        ],
    )


def run_api(supervisor: Supervisor, api_port: int, _web_port: int) -> None:
    config = local_config(os.environ)
    require_free(api_port, "V2_API_PORT", "API")
    dataset = _start_api(supervisor, api_port, config)
    supervisor.pause(0)
    _announce(
        "local Preview, API only",
        dataset,
        [("API", f"{_url(api_port)} (deployment.mode local, {API_FLAG}=1)")],
    )


def run_serve(supervisor: Supervisor, api_port: int, web_port: int) -> None:
    node = node_path()
    require_build()
    require_free(web_port, "V2_WEB_PORT", "front end")
    # Not started here, so never stopped here: the API of `make v2-preview-api`.
    dataset = api_answer(api_port, f"the server on port {api_port}")
    if dataset is None:
        raise StartupError(
            f"no API answers on port {api_port}. Start it first, in another terminal: "
            "make v2-preview-api"
        )
    _start_web(supervisor, serve_command(node, web_port, api_port, os.environ), web_port)
    check_served_build(web_port)
    check_proxy(web_port)
    supervisor.pause(0)
    built = datetime.fromtimestamp((BUILD / "index.html").stat().st_mtime)
    _announce(
        "local Preview, built (vite preview)",
        dataset,
        [
            ("Front end", _url(web_port, "/v2")),
            ("API", f"{_url(api_port)} (started separately; it keeps running)"),
            ("Build", f"{os.path.relpath(BUILD, ROOT)}, built {built:%Y-%m-%d %H:%M}"),
        ],
    )


RUNS: Final[Mapping[str, Callable[[Supervisor, int, int], None]]] = {
    "dev": run_dev,
    "api": run_api,
    "serve": run_serve,
}


def _port(text: str) -> int:
    if not text.isdecimal() or not 1024 <= int(text) <= 65535:
        raise argparse.ArgumentTypeError(f"{text!r} is not a port from 1024 to 65535")
    return int(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=[*RUNS, "check"])
    parser.add_argument("--api-port", type=_port, default=API_PORT)
    parser.add_argument("--web-port", type=_port, default=WEB_PORT)
    args = parser.parse_args(argv)
    if args.api_port == args.web_port:
        parser.error("the API and the front end need different ports")

    supervisor = Supervisor()
    supervisor.listen()
    try:
        if args.command == "check":
            node_path()
            return 0
        RUNS[args.command](supervisor, args.api_port, args.web_port)
        supervisor.watch()
    except Stopped:
        print("\nStopping the V2 Preview...", flush=True)
        supervisor.stop()
        print("Stopped.", flush=True)
        return 0
    except StartupError as exc:
        print(f"\nV2 Preview: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        supervisor.stop()
    return 1  # watch() never returns


if __name__ == "__main__":
    raise SystemExit(main())
