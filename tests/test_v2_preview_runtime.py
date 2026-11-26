"""The local V2 Preview's runtime (``scripts/v2_preview.py``) and its two front-end builds.

What is started and with which environment, what counts as ready, and that
stopping reaches exactly what was started. The real API is started here on a
configuration whose folders are empty, so no dataset and no model is read. The
tests that run Vite need the installed front end, and those that read a build need
it built; each skips without, except in CI's browser job, which requires them
(``CHURNSENSE_REQUIRE_FRONTEND_BUILD=1``).
"""

from __future__ import annotations

import http.client
import http.server
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import yaml

from churnsense.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "scripts" / "v2_preview.py"
_SPEC = importlib.util.spec_from_file_location("v2_preview", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
runtime = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runtime  # dataclasses look their module up
_SPEC.loader.exec_module(runtime)

DIST = PROJECT_ROOT / "frontend" / "dist"
REQUIRED = os.environ.get("CHURNSENSE_REQUIRE_FRONTEND_BUILD") == "1"
needs_vite = pytest.mark.skipif(
    not REQUIRED and not (runtime.VITE.is_file() and shutil.which("node")),
    reason="the front end is not installed (make frontend-install)",
)
needs_dist = pytest.mark.skipif(
    not REQUIRED and not (DIST / "index.html").is_file(),
    reason="the front end is not built (make frontend)",
)
needs_v2_build = pytest.mark.skipif(
    not REQUIRED and not (runtime.BUILD / "index.html").is_file(),
    reason="the V2 Preview is not built (make v2-preview-build)",
)
READY = "The V2 Preview is ready."


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((runtime.HOST, 0))
        return probe.getsockname()[1]


def _eventually(condition: Callable[[], object], what: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _alive(pid: int) -> bool:
    """Whether ``pid`` runs; a process that ended but was not yet collected does not."""
    found = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(found) and not found.startswith("Z")


@pytest.fixture
def local_config(tmp_path) -> Path:
    """The repository's configuration with every folder moved into an empty one."""
    conf = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    conf["paths"] = {name: str(tmp_path / relative) for name, relative in conf["paths"].items()}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(conf), encoding="utf-8")
    return path


@pytest.fixture
def handlers() -> Iterator[None]:
    """This process's own signal handlers, put back after a Supervisor used here."""
    numbers = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    before = {number: signal.getsignal(number) for number in numbers}
    yield
    for number, handler in before.items():
        signal.signal(number, handler)


class Run:
    """``scripts/v2_preview.py`` as a user runs it, its output kept in a file."""

    def __init__(self, tmp_path: Path, config: Path | None, *arguments: str) -> None:
        self.log = tmp_path / f"{arguments[0]}.log"
        environ = {**os.environ, "PYTHONUNBUFFERED": "1"}
        # Set wrongly on purpose: each server must get its own switch, not these.
        environ |= {runtime.API_FLAG: "0", runtime.WEB_FLAG: "0", "UVICORN_HOST": "0.0.0.0"}
        if config is not None:
            environ["CHURNSENSE_CONFIG"] = str(config)
        with self.log.open("wb") as out:
            self.process = subprocess.Popen(
                [sys.executable, str(SCRIPT), *arguments],
                stdout=out,
                stderr=subprocess.STDOUT,
                env=environ,
                start_new_session=True,
            )

    @property
    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def ready(self) -> str:
        _eventually(lambda: READY in self.output or self.process.poll() is not None, "no start")
        assert READY in self.output, self.output
        _eventually(lambda: "Ctrl+C\n" in self.output.split(READY)[1], "no announcement")
        return self.output.split(READY)[1]

    def children(self) -> list[int]:
        found = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, check=True
        )
        rows = [tuple(map(int, line.split())) for line in found.stdout.splitlines()]
        return [pid for pid, parent in rows if parent == self.process.pid]

    def stop(self, number: int = signal.SIGINT) -> int:
        self.process.send_signal(number)
        return self.process.wait(timeout=30)


@pytest.fixture
def run(tmp_path) -> Iterator[Callable[..., Run]]:
    started: list[Run] = []

    def start(config: Path | None, *arguments: str) -> Run:
        started.append(Run(tmp_path, config, *arguments))
        return started[-1]

    yield start
    for one in reversed(started):
        if one.process.poll() is None:
            one.process.terminate()
            one.process.wait(timeout=30)


class Site:
    """A little HTTP server: ``pages`` maps a path to its status, type and body."""

    def __init__(self, pages: dict[str, tuple[int, bytes]]) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status, body = pages.get(self.path, (404, b"{}"))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer((runtime.HOST, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def site() -> Iterator[Callable[[dict[str, tuple[int, bytes]]], int]]:
    sites: list[Site] = []

    def start(pages: dict[str, tuple[int, bytes]]) -> int:
        sites.append(Site(pages))
        return sites[-1].port

    yield start
    for one in sites:
        one.server.shutdown()
        one.server.server_close()


# --- what each server is given --------------------------------------------------------------

#: A shell with everything set that could move a server, or switch the wrong one on.
HOSTILE = {
    "PATH": "/usr/bin",
    runtime.API_FLAG: "0",
    runtime.WEB_FLAG: "0",
    "UVICORN_HOST": "0.0.0.0",
    "UVICORN_FD": "3",
    "WEB_CONCURRENCY": "4",
    "FORWARDED_ALLOW_IPS": "*",
    "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": "evil.example",
    "API_PORT": "1",
    "CHURNSENSE_CONFIG": "configs/deploy.yaml",
}


def _commands() -> list:
    return [
        runtime.api_command(8010, Path("/c.yaml"), HOSTILE),
        runtime.dev_command("node", 5180, 8010, HOSTILE),
        runtime.serve_command("node", 5180, 8010, HOSTILE),
    ]


def test_each_server_gets_its_own_switch_and_only_its_own():
    api, dev, serve = _commands()
    assert api.env[runtime.API_FLAG] == "1" and runtime.WEB_FLAG not in api.env
    assert api.env["CHURNSENSE_CONFIG"] == "/c.yaml"
    assert dev.env[runtime.WEB_FLAG] == "1" and runtime.API_FLAG not in dev.env
    # What a built preview holds was decided when it was built.
    assert runtime.WEB_FLAG not in serve.env and runtime.API_FLAG not in serve.env
    assert dev.env["API_PORT"] == serve.env["API_PORT"] == "8010"


def test_no_environment_variable_can_move_a_server():
    for command in _commands():
        assert command.env["PATH"] == "/usr/bin"
        assert not [n for n in command.env if n.startswith(("UVICORN_", "__VITE_"))]
        assert not {"WEB_CONCURRENCY", "FORWARDED_ALLOW_IPS"} & command.env.keys()


def test_every_server_is_bound_to_loopback_and_no_option_changes_that():
    for command in _commands():
        at = [i for i, word in enumerate(command.argv) if word == "--host"]
        assert [command.argv[i + 1] for i in at] == ["127.0.0.1"]
    api, dev, serve = _commands()
    assert "--no-proxy-headers" in api.argv and "--reload" not in api.argv
    assert "--strictPort" in dev.argv and "--strictPort" in serve.argv
    assert "0.0.0.0" not in SCRIPT.read_text()
    with pytest.raises(SystemExit):
        runtime.main(["dev", "--host", "0.0.0.0"])


@pytest.mark.parametrize("port", ["80", "0", "65536", "-1", "x"])
def test_a_port_outside_the_user_range_is_refused(port):
    with pytest.raises(SystemExit):
        runtime.main(["dev", "--api-port", port])


def test_one_port_for_both_servers_is_refused():
    with pytest.raises(SystemExit):
        runtime.main(["dev", "--api-port", "8010", "--web-port", "8010"])


def test_only_a_local_deployment_is_started(local_config):
    assert runtime.local_config({}) == DEFAULT_CONFIG_PATH
    assert runtime.local_config({"CHURNSENSE_CONFIG": str(local_config)}) == local_config
    before = (PROJECT_ROOT / "configs" / "deploy.yaml").read_bytes()
    with pytest.raises(runtime.StartupError, match="public_demo"):
        runtime.local_config({"CHURNSENSE_CONFIG": "configs/deploy.yaml"})
    with pytest.raises(runtime.StartupError, match="does not load"):
        runtime.local_config({"CHURNSENSE_CONFIG": "configs/absent.yaml"})
    assert (PROJECT_ROOT / "configs" / "deploy.yaml").read_bytes() == before


# --- the front end's installation, reported and never changed ---------------------------------


@pytest.fixture
def frontend(tmp_path) -> Path:
    folder = tmp_path / "frontend"
    vite = folder / runtime.VITE.relative_to(runtime.FRONTEND)
    vite.parent.mkdir(parents=True)
    vite.touch()
    (folder / "package.json").write_text('{"engines": {"node": "^22.12.0"}}')
    for name in ("package-lock.json", "node_modules/.package-lock.json"):
        (folder / name).write_text("{}")
        os.utime(folder / name, (1000, 1000))
    return folder


def _node(tmp_path: Path, version: str) -> dict[str, str]:
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    (folder / "node").write_text(f"#!/bin/sh\necho {version}\n")
    (folder / "node").chmod(0o755)
    return {"PATH": str(folder)}


def test_an_installed_front_end_is_accepted(frontend, tmp_path):
    environ = _node(tmp_path, "v22.14.0")
    assert runtime.node_path(frontend, environ) == str(tmp_path / "bin" / "node")


@pytest.mark.parametrize("version", ["v22.11.9", "v23.0.0", "v20.19.0", "nonsense"])
def test_another_node_is_refused(frontend, tmp_path, version):
    with pytest.raises(runtime.StartupError, match=r"\^22\.12\.0"):
        runtime.node_path(frontend, _node(tmp_path, version))


def test_no_node_at_all_is_refused(frontend, tmp_path):
    with pytest.raises(runtime.StartupError, match="not on PATH"):
        runtime.node_path(frontend, {"PATH": str(tmp_path / "empty")})


def test_a_missing_or_stale_installation_is_reported_not_repaired(frontend, tmp_path):
    environ = _node(tmp_path, "v22.14.0")
    os.utime(frontend / "package-lock.json", (2000, 2000))
    with pytest.raises(runtime.StartupError, match="make frontend-install"):
        runtime.node_path(frontend, environ)
    (frontend / "node_modules" / ".package-lock.json").unlink()
    with pytest.raises(runtime.StartupError, match="make frontend-install"):
        runtime.node_path(frontend, environ)
    assert not re.search(r"npm|npx", " ".join(" ".join(c.argv) for c in _commands()))


def test_a_build_without_the_workspace_is_not_served(tmp_path):
    with pytest.raises(runtime.StartupError, match="not built"):
        runtime.require_build(tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id='root'></div>")
    (tmp_path / "assets" / "index.js").write_text("/v2/accounts")
    with pytest.raises(runtime.StartupError, match="not a V2 Preview build"):
        runtime.require_build(tmp_path)
    (tmp_path / "assets" / "more.js").write_text(" ".join(runtime.V2_MARKERS))
    runtime.require_build(tmp_path)


# --- ports: reported, never freed -------------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET, "0.0.0.0"),
        (socket.AF_INET6, "::1"),
    ],
)
def test_a_port_held_by_another_program_is_in_use(family, address):
    try:
        server = socket.create_server((address, 0), family=family)
    except OSError:
        pytest.skip(f"{address} cannot be bound here")
    with server:
        assert runtime.port_in_use(server.getsockname()[1])


def test_a_free_port_is_not_in_use():
    assert not runtime.port_in_use(_free_port())


@pytest.mark.parametrize(
    ("command", "option"),
    [
        ("api", "--api-port"),
        pytest.param("dev", "--api-port", marks=needs_vite),
        pytest.param("dev", "--web-port", marks=needs_vite),
    ],
)
def test_an_occupied_port_stops_the_start_and_not_its_holder(
    site, run, local_config, command, option
):
    port = site({"/": (200, b"somebody else's")})
    ports = {"--api-port": _free_port(), "--web-port": _free_port(), option: port}
    one = run(local_config, command, *(str(word) for pair in ports.items() for word in pair))
    assert one.process.wait(timeout=60) == 1
    variable = "V2_API_PORT" if option == "--api-port" else "V2_WEB_PORT"
    assert f"port {port}" in one.output and variable in one.output
    assert "Starting" not in one.output  # nothing was started
    assert runtime.get(port, "/").body == b"somebody else's"


# --- readiness: the V2 API itself, not whatever answers ---------------------------------------


@pytest.fixture(scope="module")
def summary() -> dict:
    from churnsense.workspace import workbench

    return json.loads(workbench.portfolio_summary(workbench.load_book()).model_dump_json())


HEALTH = (200, b'{"status": "degraded", "model_loaded": false, "detail": null}')


def _api(summary_answer: tuple[int, bytes], health=HEALTH) -> dict[str, tuple[int, bytes]]:
    return {"/health": health, runtime.SUMMARY: summary_answer}


def test_the_v2_api_is_ready_with_its_documented_dataset(site, summary):
    dataset = runtime.api_answer(site(_api((200, json.dumps(summary).encode()))), "it")
    assert dataset.source == "synthetic"
    assert runtime._long_date(dataset.as_of_date) == "31 October 2026"


def test_nothing_answering_is_waited_for():
    assert runtime.api_answer(_free_port(), "it") is None


def test_an_api_with_its_v2_routes_off_is_not_ready(site):
    with pytest.raises(runtime.StartupError, match=runtime.API_FLAG):
        runtime.api_answer(site(_api((404, b"{}"))), "it")


def test_another_server_on_the_port_is_not_ready(site, summary):
    answer = (200, json.dumps(summary).encode())
    for health in ((200, b"<html>"), (200, b'{"status": "ok"}'), (503, HEALTH[1]), (404, b"{}")):
        with pytest.raises(runtime.StartupError, match="not the ChurnSense API"):
            runtime.api_answer(site(_api(answer, health)), "it")


def test_another_portfolio_is_not_ready(site, summary):
    def refused(body: object, status: int = 200) -> None:
        answer = (status, json.dumps(body).encode())
        with pytest.raises(runtime.StartupError):
            runtime.api_answer(site(_api(answer)), "it")

    refused({})
    refused([1])
    refused(summary, status=503)
    for field, value in (("as_of_date", "2026-11-30"), ("seed", 1), ("source", "live")):
        refused({**summary, "dataset": {**summary["dataset"], field: value}})
    refused({**summary, "total_accounts": 0})


SHELL = (200, b'<html><div id="root"></div></html>')


def test_the_front_end_must_serve_every_v2_address(site):
    pages = dict.fromkeys(runtime.V2_PAGES, SHELL)
    runtime.check_pages(site(pages))
    for path in runtime.V2_PAGES:
        with pytest.raises(runtime.StartupError, match=path):
            runtime.check_pages(site({p: a for p, a in pages.items() if p != path}))
    with pytest.raises(runtime.StartupError):
        runtime.check_pages(site(dict.fromkeys(runtime.V2_PAGES, (200, b"another page"))))


def test_the_dev_server_must_have_its_switch_on(site):
    def module(text: str) -> int:
        return site({"/src/v2/flag.ts": (200, text.encode())})

    runtime.check_dev_switch(module(f'import.meta.env = {{"{runtime.WEB_FLAG}": "1"}}'))
    for text in ("import.meta.env = {}", f'{{"{runtime.WEB_FLAG}": "0"}}', ""):
        with pytest.raises(runtime.StartupError, match=runtime.WEB_FLAG):
            runtime.check_dev_switch(module(text))


def test_the_front_end_must_reach_the_v2_api(site, summary):
    runtime.check_proxy(site({runtime.SUMMARY: (200, json.dumps(summary).encode())}))
    for answer in ((404, b"{}"), (500, b""), (200, b"<html>")):
        with pytest.raises(runtime.StartupError):
            runtime.check_proxy(site({runtime.SUMMARY: answer}))


def test_the_served_build_must_be_the_one_on_disk(site, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_bytes(SHELL[1])
    (tmp_path / "assets" / "a.js").write_bytes(b"one")
    runtime.check_served_build(site({"/v2": SHELL, "/assets/a.js": (200, b"one")}), tmp_path)
    for pages in (
        {"/v2": SHELL, "/assets/a.js": (200, b"two")},
        {"/v2": SHELL},
        {"/v2": (200, SHELL[1] + b" "), "/assets/a.js": (200, b"one")},
    ):
        with pytest.raises(runtime.StartupError):
            runtime.check_served_build(site(pages), tmp_path)


# --- the processes: all of what was started, and nothing else ---------------------------------


def _python(code: str, *arguments: str, name: str = "server"):
    return runtime.Command(
        name, (sys.executable, "-c", code, *arguments), dict(os.environ), PROJECT_ROOT
    )


#: Starts a process of its own, writes both IDs down, then sleeps.
PARENT = """
import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
out = Path(sys.argv[1])
out.with_suffix(".part").write_text(f"{os.getpid()} {child.pid}")
out.with_suffix(".part").rename(out)
time.sleep(600)
"""
STUBBORN = """
import signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).touch()
time.sleep(600)
"""


def test_stopping_ends_a_server_and_what_it_started_and_no_other_process(tmp_path, handlers):
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    supervisor = runtime.Supervisor()
    try:
        ids = tmp_path / "ids"
        supervisor.start(_python(PARENT, str(ids)))
        _eventually(ids.exists, "the server did not start")
        pids = [int(text) for text in ids.read_text().split()]
        assert all(map(_alive, pids))
        supervisor.stop()
        _eventually(lambda: not any(map(_alive, pids)), "a process outlived the stop", 10)
        assert bystander.poll() is None
    finally:
        supervisor.stop()
        bystander.kill()
        bystander.wait()


def test_a_server_that_ended_leaves_nothing_of_its_own_behind(tmp_path, handlers):
    ids = tmp_path / "ids"
    supervisor = runtime.Supervisor()
    supervisor.start(_python(PARENT.replace("time.sleep(600)\n", "sys.exit(3)\n"), str(ids)))
    try:
        with pytest.raises(runtime.StartupError, match="status 3"):
            supervisor.until(lambda: None, "server", timeout=60)
        _, child = (int(text) for text in ids.read_text().split())
        assert _alive(child)
    finally:
        supervisor.stop()
    _eventually(lambda: not _alive(child), "a process outlived its server", 10)


def test_a_server_that_ignores_the_request_is_killed(tmp_path, handlers):
    up = tmp_path / "up"
    supervisor = runtime.Supervisor(stop_timeout=0.2)
    supervisor.start(_python(STUBBORN, str(up)))
    _, child = supervisor.children[0]
    _eventually(up.exists, "the server did not start")
    supervisor.stop()
    assert child.returncode == -signal.SIGKILL


def test_a_server_never_ready_is_given_up_on(handlers):
    supervisor = runtime.Supervisor()
    supervisor.start(_python("import time; time.sleep(600)", name="API"))
    try:
        with pytest.raises(runtime.StartupError, match="API was not ready"):
            supervisor.until(lambda: None, "API", timeout=0.3)
    finally:
        supervisor.stop()


def test_an_answer_from_a_port_whose_own_server_ended_is_not_ready(handlers):
    """Something else took the port between the check and the bind: it is not ours."""
    supervisor = runtime.Supervisor()
    supervisor.start(_python("raise SystemExit(1)"))
    _, child = supervisor.children[0]

    def answers_once_ours_ended() -> str:
        child.wait(timeout=30)
        return "ready"

    try:
        with pytest.raises(runtime.StartupError, match="ended by itself"):
            supervisor.until(answers_once_ours_ended, "server", timeout=30)
    finally:
        supervisor.stop()


def test_a_command_that_cannot_run_is_a_startup_error(handlers):
    supervisor = runtime.Supervisor()
    missing = runtime.Command("front end", ("/nonexistent/node",), {}, PROJECT_ROOT)
    with pytest.raises(runtime.StartupError, match="front end could not be started"):
        supervisor.start(missing)
    supervisor.stop()


def test_the_script_never_looks_for_processes_to_stop():
    """Only the groups it made are signalled: no process list, name or port is searched."""
    source = SCRIPT.read_text()
    assert not re.search(r"killall|pkill|pgrep|\bps\b|psutil|\bos\.kill\(|fuser", source)
    # lsof is printed for the reader; what is run is the two servers and `node --version`.
    assert source.count("subprocess.run(") == source.count("subprocess.Popen(\n") == 1
    assert source.count("os.killpg(") == 1


# --- as a user runs it: the API -----------------------------------------------------------------


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_the_api_starts_with_its_switch_on_and_stops_when_asked(run, local_config, number):
    port = _free_port()
    one = run(local_config, "api", "--api-port", str(port))
    said = one.ready()
    assert f"http://127.0.0.1:{port}" in said and "deployment.mode local" in said
    assert "synthetic" in said and "31 October 2026" in said
    dataset = runtime.api_answer(port, "it")
    assert dataset is not None and dataset.source == "synthetic"
    (child,) = one.children()
    assert one.stop(number) == 0
    assert "Stopped." in one.output
    assert not _alive(child) and not runtime.port_in_use(port)


def test_the_running_api_listens_on_loopback_only(run, local_config):
    port = _free_port()
    run(local_config, "api", "--api-port", str(port)).ready()
    # Nothing holds the port on any other of this machine's addresses.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # no packet is sent
        except OSError:
            pytest.skip("this machine has no other address")
        address = probe.getsockname()[0]
    if address.startswith("127."):
        pytest.skip("this machine has no other address")
    with socket.socket() as other:
        other.settimeout(2)
        assert other.connect_ex((address, port)) != 0


def test_a_public_demo_configuration_starts_nothing(run):
    port = _free_port()
    one = run(PROJECT_ROOT / "configs" / "deploy.yaml", "api", "--api-port", str(port))
    assert one.process.wait(timeout=60) == 1
    assert "public_demo" in one.output and "Starting" not in one.output


def test_a_front_end_that_fails_takes_the_api_down_with_it(
    monkeypatch, capfd, tmp_path, local_config, handlers
):
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nexit 7\n")
    node.chmod(0o755)
    monkeypatch.setenv("CHURNSENSE_CONFIG", str(local_config))
    monkeypatch.setattr(runtime, "node_path", lambda: str(node))
    started = []
    start = runtime.Supervisor.start
    monkeypatch.setattr(
        runtime.Supervisor,
        "start",
        lambda self, command: (start(self, command), started.append(self.children[-1])),
    )
    api, web = _free_port(), _free_port()
    assert runtime.main(["dev", "--api-port", str(api), "--web-port", str(web)]) == 1
    assert "front end ended by itself (status 7)" in capfd.readouterr().err
    assert [name for name, _ in started] == ["API", "front end"]
    assert all(child.poll() is not None for _, child in started)
    assert not runtime.port_in_use(api)


def test_the_built_preview_is_not_served_without_its_api(monkeypatch, capfd, handlers):
    monkeypatch.setattr(runtime, "node_path", lambda: "/nonexistent/node")
    monkeypatch.setattr(runtime, "require_build", lambda: None)
    port = _free_port()
    assert runtime.main(["serve", "--api-port", str(port), "--web-port", str(_free_port())]) == 1
    assert "make v2-preview-api" in capfd.readouterr().err


# --- as a user runs it: the front end -----------------------------------------------------------


def _post_csv(port: int) -> tuple[int, dict]:
    """The Data Hub's own request, from the front end's own origin."""
    boundary = "v2-preview"
    csv = "account_external_id,as_of_date,status,mrr,currency\nA-1,2026-10-31,active,10.50,USD\n"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n{csv}\r\n--{boundary}--\r\n"
    )
    connection = http.client.HTTPConnection(runtime.HOST, port, timeout=30)
    connection.request(
        "POST",
        "/api/v2/imports/preview",
        body.encode(),
        {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": f"http://{runtime.HOST}:{port}",
        },
    )
    answer = connection.getresponse()
    return answer.status, json.loads(answer.read())


@needs_vite
def test_one_command_starts_and_stops_the_whole_preview(run, local_config):
    api, web = _free_port(), _free_port()
    one = run(local_config, "dev", "--api-port", str(api), "--web-port", str(web))
    said = one.ready()
    for text in (
        f"http://127.0.0.1:{web}/v2",
        f"http://127.0.0.1:{api}",
        "development",
        "31 October 2026",
    ):
        assert text in said
    runtime.check_pages(web)
    runtime.check_proxy(web)
    status, answer = _post_csv(web)
    assert status == 200 and answer["status"] == "valid" and answer["committed"] is False
    children = one.children()
    assert len(children) == 2
    assert one.stop() == 0
    assert not any(map(_alive, children))
    assert not runtime.port_in_use(api) and not runtime.port_in_use(web)


@needs_vite
@needs_v2_build
def test_the_built_preview_is_served_beside_an_api_it_never_stops(run, local_config):
    api, web = _free_port(), _free_port()
    service = run(local_config, "api", "--api-port", str(api))
    service.ready()
    one = run(local_config, "serve", "--api-port", str(api), "--web-port", str(web))
    said = one.ready()
    assert f"http://127.0.0.1:{web}/v2" in said and "build/v2-preview" in said
    runtime.check_pages(web)  # a direct visit and a reload are the same request
    runtime.check_served_build(web)
    runtime.check_proxy(web)
    status, answer = _post_csv(web)
    assert status == 200 and answer["committed"] is False
    assert len(one.children()) == 1
    assert one.stop() == 0
    assert not runtime.port_in_use(web)
    assert runtime.api_answer(api, "it") is not None
    assert service.stop() == 0


# --- the two builds -----------------------------------------------------------------------------


@needs_dist
def test_the_default_build_holds_no_v2():
    assert (DIST / "index.html").is_file()
    assert runtime.v2_markers(DIST / "assets") == set()


@needs_v2_build
def test_the_v2_preview_build_holds_the_workspace():
    runtime.require_build()
    names = {p.name.split("-")[0] for p in (runtime.BUILD / "assets").glob("*.js")}
    assert {"Workspace", "DataHubPage", "PortfolioPage", "AccountsPage", "AccountPage"} <= names


def _vite_build(out: Path, flag: str | None) -> subprocess.CompletedProcess[str]:
    environ = {name: value for name, value in os.environ.items() if name != runtime.WEB_FLAG}
    if flag is not None:
        environ[runtime.WEB_FLAG] = flag
    return subprocess.run(
        ["node", str(runtime.VITE), "build", "--outDir", str(out)],
        cwd=runtime.FRONTEND,
        env=environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


@needs_vite
def test_a_build_with_v2_on_is_refused_anywhere_but_its_own_folder(tmp_path):
    out = tmp_path / "out"
    refused = _vite_build(out, "1")
    assert refused.returncode != 0 and "make v2-preview-build" in refused.stderr
    assert not out.exists()


@needs_vite
@pytest.mark.parametrize("flag", [None, "", "0", "true"])
def test_any_other_value_builds_without_v2(tmp_path, flag):
    built = _vite_build(tmp_path / "out", flag)
    assert built.returncode == 0, built.stderr
    assert runtime.v2_markers(tmp_path / "out" / "assets") == set()


@needs_vite
@needs_v2_build
def test_a_build_with_v2_off_is_refused_in_the_previews_folder():
    before = (runtime.BUILD / "index.html").read_bytes()
    refused = _vite_build(runtime.BUILD, None)
    assert refused.returncode != 0 and "build/v2-preview" in refused.stderr
    assert (runtime.BUILD / "index.html").read_bytes() == before


def test_the_previews_build_folder_is_ignored_and_outside_the_image():
    inside = runtime.BUILD.relative_to(PROJECT_ROOT).as_posix()
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", f"{inside}/index.html"], cwd=PROJECT_ROOT, check=False
    )
    assert ignored.returncode == 0
    rules = (PROJECT_ROOT / ".dockerignore").read_text().splitlines()
    assert "*" in rules and not [rule for rule in rules if rule.startswith("!build")]


# --- nothing that is deployed, or run by default, sets a switch ---------------------------------


def test_only_the_explicit_preview_build_sets_the_front_ends_switch():
    scripts = json.loads((runtime.FRONTEND / "package.json").read_text())["scripts"]
    assert scripts["build"] == "vite build" and scripts["dev"] == "vite"
    assert [name for name, line in scripts.items() if runtime.WEB_FLAG in line] == [
        "build:v2-preview"
    ]
    assert scripts["build:v2-preview"] == (
        f"{runtime.WEB_FLAG}=1 vite build --outDir ../build/v2-preview"
    )


def test_no_deployed_or_default_file_sets_a_switch():
    files = [
        PROJECT_ROOT / "Dockerfile",
        PROJECT_ROOT / "Makefile",
        PROJECT_ROOT / ".env.example",
        *(p for p in (PROJECT_ROOT / "deploy").rglob("*") if p.is_file()),
        *(PROJECT_ROOT / "configs").glob("*"),
        *(PROJECT_ROOT / ".github" / "workflows").glob("*"),
    ]
    assert len(files) > 5
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert runtime.WEB_FLAG not in text and runtime.API_FLAG not in text, path
        assert "v2_preview.py" not in text or path.name == "Makefile", path


def test_ci_builds_the_preview_apart_and_requires_both_builds_checked():
    workflow = yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["e2e"]["steps"]
    lines = [step.get("run", "") for step in steps]
    build, preview = lines.index("npm run build"), lines.index("npm run build:v2-preview")
    (checked,) = [i for i, line in enumerate(lines) if "test_v2_preview_runtime" in line]
    assert build < preview < checked
    assert steps[checked]["env"] == {"CHURNSENSE_REQUIRE_FRONTEND_BUILD": "1"}
    assert "npm run openapi:v2" in "\n".join(lines)
