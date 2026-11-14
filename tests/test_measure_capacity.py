"""The capacity measurement helper reports what each request met, and loses nothing.

``scripts/measure_capacity.py`` once folded every failure to get an answer into
a status of ``0``, so a run could not say why nine uploads had no result. These
tests pin the diagnosis: an HTTP answer (a 429 or a 503 included) is one kind
of outcome, a transport failure another, each request is named, and the
deployment guide describes the same output. Everything here is synthetic:
throwaway servers on loopback, stand-in connections, no service, no container.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
from scripts import measure_capacity as measure

REPO = Path(__file__).resolve().parents[1]


def _read_request(conn: socket.socket) -> None:
    """Read one whole request (headers, then Content-Length bytes), so closing is clean."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value)
    while len(rest) < length:
        chunk = conn.recv(65536)
        if not chunk:
            return
        rest += chunk


def _reply(status: int, body: dict | None = None, **headers: str) -> bytes:
    payload = json.dumps(body or {}).encode()
    lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(payload)}", "Connection: close"]
    lines += [f"{name.replace('_', '-')}: {value}" for name, value in headers.items()]
    return "\r\n".join(lines).encode() + b"\r\n\r\n" + payload


def _refusal(error: str) -> dict:
    return {"error": error, "detail": "synthetic", "problems": []}


@pytest.fixture
def serve():
    """Start a loopback server that hands each connection to ``handler``; gives its base URL."""
    listeners: list[socket.socket] = []

    def start(handler) -> str:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listeners.append(listener)

        def loop() -> None:
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                with conn:
                    handler(conn)

        threading.Thread(target=loop, daemon=True).start()
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    yield start
    for listener in listeners:
        listener.close()


def _answering(status: int, body: dict | None = None, **headers: str):
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(_reply(status, body, **headers))

    return handler


def _post(base: str, request_id: str = "t-01", body: bytes = b"{}") -> dict:
    return measure._request(request_id, base, "POST", "/x", body, "application/json", timeout=10)


# --- HTTP answers --------------------------------------------------------------------------


def test_a_200_is_an_http_outcome_with_its_request_id(serve):
    result = _post(serve(_answering(200, {"rows": []})), "typical-batch-01")
    assert result["id"] == "typical-batch-01"
    assert result["outcome"] == "http" and result["status"] == 200
    assert result["error"] is None
    assert "stage" not in result and "exception" not in result
    assert result["seconds"] >= 0


@pytest.mark.parametrize(
    ("status", "error", "retry_after"),
    [
        (429, "rate_limited", "6"),
        (429, "batch_busy", "2"),
        (429, "too_many_in_flight", "1"),
        (503, "explanations_busy", "2"),
    ],
)
def test_a_refusal_is_an_http_outcome_never_a_transport_error(serve, status, error, retry_after):
    result = _post(serve(_answering(status, _refusal(error), Retry_After=retry_after)))
    assert result["outcome"] == "http"
    assert (result["status"], result["error"], result["retry_after"]) == (
        status,
        error,
        retry_after,
    )
    assert "stage" not in result and "exception" not in result
    assert measure.summarise([result]) == {"http": {f"{status} {error}": 1}, "transport_errors": {}}


def test_an_error_body_that_is_not_the_service_shape_still_reports_its_status(serve):
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"HTTP/1.1 502 X\r\nContent-Length: 4\r\nConnection: close\r\n\r\noops")

    result = _post(serve(handler))
    assert (result["outcome"], result["status"], result["error"]) == ("http", 502, None)
    assert measure.summarise([result])["http"] == {"502": 1}


# --- transport failures --------------------------------------------------------------------


def test_a_refused_connection_names_its_stage_class_errno_and_reason():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    result = _post(f"http://127.0.0.1:{port}", "idle-ready-01")
    assert result["id"] == "idle-ready-01"
    assert result["outcome"] == "transport" and result["stage"] == "connect"
    assert result["exception"] == "ConnectionRefusedError"
    assert isinstance(result["errno"], int) and result["errno"] > 0
    assert result["reason"]
    assert "status" not in result


def test_a_connection_closed_without_an_answer_is_a_response_stage_failure(serve):
    result = _post(serve(_read_request))
    assert result["outcome"] == "transport" and result["stage"] == "response"
    assert result["exception"] == "RemoteDisconnected"
    assert result["reason"]
    assert "status" not in result


def test_a_garbled_answer_is_a_transport_failure_not_a_crash(serve):
    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"not http at all\r\n\r\n")

    result = _post(serve(handler))
    assert result["outcome"] == "transport" and result["stage"] == "response"
    assert result["exception"] == "BadStatusLine"


def test_a_timeout_is_reported_as_one(serve):
    release = threading.Event()

    def handler(conn: socket.socket) -> None:
        _read_request(conn)
        release.wait(10)

    base = serve(handler)
    try:
        result = measure._request("slow-01", base, "GET", "/x", timeout=0.3)
    finally:
        release.set()
    assert result["outcome"] == "transport" and result["stage"] == "response"
    assert result["exception"] == "TimeoutError"


# --- an upload the server answered before reading it ---------------------------------------


class _Answer:
    status = 429

    def read(self) -> bytes:
        return json.dumps(_refusal("rate_limited")).encode()

    def getheader(self, name: str) -> str | None:
        return "6" if name == "Retry-After" else None


def _stub_connection(monkeypatch, *, send: BaseException, answer):
    """A connection whose send breaks with ``send`` and whose answer is ``answer``."""

    class Stub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = False

        def connect(self) -> None:
            pass

        def request(self, *_args, **_kwargs) -> None:
            raise send

        def getresponse(self):
            if isinstance(answer, BaseException):
                raise answer
            return answer

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(measure.http.client, "HTTPConnection", Stub)


@pytest.mark.parametrize(
    "broken",
    [BrokenPipeError(32, "Broken pipe"), ConnectionResetError(104, "Connection reset by peer")],
)
def test_an_early_answer_survives_a_broken_send(monkeypatch, broken):
    """The server answered 429 and closed while the upload was still being sent."""
    _stub_connection(monkeypatch, send=broken, answer=_Answer())
    result = _post("http://127.0.0.1:1", "one-client-max-batch-07")
    assert result["outcome"] == "http"
    assert (result["status"], result["error"], result["retry_after"]) == (429, "rate_limited", "6")
    assert result["send_interrupted"] == {
        "exception": type(broken).__name__,
        "errno": broken.errno,
        "reason": str(broken),
    }
    assert measure.summarise([result]) == {"http": {"429 rate_limited": 1}, "transport_errors": {}}


def test_a_broken_send_with_no_answer_reports_both_failures(monkeypatch):
    _stub_connection(
        monkeypatch,
        send=BrokenPipeError(32, "Broken pipe"),
        answer=ConnectionResetError(104, "Connection reset by peer"),
    )
    result = _post("http://127.0.0.1:1", "one-client-max-batch-08")
    assert result["outcome"] == "transport" and result["stage"] == "send"
    assert (result["exception"], result["errno"]) == ("BrokenPipeError", 32)
    assert result["no_answer"]["exception"] == "ConnectionResetError"
    assert result["no_answer"]["errno"] == 104
    assert "status" not in result
    assert measure.summarise([result]) == {
        "http": {},
        "transport_errors": {"send BrokenPipeError": 1},
    }


def test_a_send_that_times_out_does_not_wait_again_for_an_answer(monkeypatch):
    _stub_connection(
        monkeypatch, send=TimeoutError("timed out"), answer=AssertionError("must not be read")
    )
    result = _post("http://127.0.0.1:1")
    assert (result["outcome"], result["stage"], result["exception"]) == (
        "transport",
        "send",
        "TimeoutError",
    )


def test_a_real_early_refusal_of_a_large_upload_is_never_lost(serve):
    """Whatever the kernel does with the unsent body, the result names what happened."""

    def handler(conn: socket.socket) -> None:
        conn.recv(1024)
        conn.sendall(_reply(429, _refusal("rate_limited"), Retry_After="6"))

    result = _post(serve(handler), body=b"x" * (4 * 1024 * 1024))
    if result["outcome"] == "http":
        assert (result["status"], result["error"]) == (429, "rate_limited")
    else:
        assert result["stage"] in ("send", "response")
        assert result["exception"] and result["reason"]


# --- the summary and the phases ------------------------------------------------------------


def test_the_summary_counts_answers_and_failures_apart():
    results = [
        {"id": "p-01", "outcome": "http", "status": 200, "error": None},
        {"id": "p-02", "outcome": "http", "status": 200, "error": None},
        {"id": "p-03", "outcome": "http", "status": 429, "error": "batch_busy"},
        {"id": "p-04", "outcome": "http", "status": 429, "error": "rate_limited"},
        {"id": "p-05", "outcome": "http", "status": 503, "error": "explanations_busy"},
        {"id": "p-06", "outcome": "transport", "stage": "send", "exception": "BrokenPipeError"},
        {"id": "p-07", "outcome": "transport", "stage": "send", "exception": "BrokenPipeError"},
        {"id": "p-08", "outcome": "transport", "stage": "connect", "exception": "TimeoutError"},
    ]
    summary = measure.summarise(results)
    assert summary == {
        "http": {
            "200": 2,
            "429 batch_busy": 1,
            "429 rate_limited": 1,
            "503 explanations_busy": 1,
        },
        "transport_errors": {"connect TimeoutError": 1, "send BrokenPipeError": 2},
    }
    assert "0" not in summary["http"]
    assert sum(summary["http"].values()) + sum(summary["transport_errors"].values()) == len(results)


@pytest.fixture
def recorded(monkeypatch):
    """Replace the network: every request is recorded and answered 200."""
    sent: list[tuple] = []

    def fake(request_id, base, method, path, body=None, content_type=None, timeout=None):
        sent.append((request_id, method, path, content_type, len(body or b"")))
        return {"id": request_id, "outcome": "http", "status": 200, "error": None, "seconds": 0.0}

    monkeypatch.setattr(measure, "_request", fake)
    return sent


def test_the_typical_phase_names_each_of_its_ten_requests(recorded):
    results = measure.phase_typical("http://127.0.0.1:1")
    ids = [r["id"] for r in results]
    assert ids == [f"typical-batch-{i:02d}" for i in (1, 2)] + [
        f"typical-explain-{i:02d}" for i in range(1, 9)
    ]
    assert sorted(ids) == sorted(sent[0] for sent in recorded), "each id was sent exactly once"
    paths = {sent[0]: sent[2] for sent in recorded}
    assert all(paths[i] == "/api/batch/score" for i in ids[:2])
    assert all(paths[i] == "/api/explain" for i in ids[2:])


def test_the_one_client_phase_names_as_many_uploads_as_the_in_flight_cap(recorded, cfg):
    from churnsense.api.middleware import MAX_CLIENT_REQUESTS

    results = measure.phase_one_client_max("http://127.0.0.1:1", cfg.api.max_upload_bytes)
    ids = [r["id"] for r in results]
    assert ids == [f"one-client-max-batch-{i:02d}" for i in range(1, MAX_CLIENT_REQUESTS + 1)]
    assert len(set(ids)) == MAX_CLIENT_REQUESTS == len(recorded)
    assert {sent[2] for sent in recorded} == {"/api/batch/score"}


def test_a_run_prints_one_line_per_phase_with_every_request_in_it(monkeypatch, capsys, tmp_path):
    (tmp_path / "memory.peak").write_text("762073088\n")
    (tmp_path / "memory.current").write_text("500000000\n")
    (tmp_path / "memory.max").write_text("1610612736\n")
    (tmp_path / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n")
    monkeypatch.setattr(measure, "CGROUP", tmp_path)
    outcomes = [
        {"id": "typical-batch-01", "outcome": "http", "status": 200, "error": None},
        {
            "id": "typical-explain-01",
            "outcome": "http",
            "status": 503,
            "error": "explanations_busy",
        },
        {
            "id": "typical-explain-02",
            "outcome": "transport",
            "stage": "send",
            "exception": "ConnectionResetError",
            "errno": 104,
            "reason": "Connection reset by peer",
        },
    ]
    monkeypatch.setattr(measure, "phase_typical", lambda _base: outcomes)
    assert measure.main(["--phases", "typical"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["phase"] == "typical" and record["sent"] == 3
    assert record["http"] == {"200": 1, "503 explanations_busy": 1}
    assert record["transport_errors"] == {"send ConnectionResetError": 1}
    assert record["requests"] == outcomes
    assert "statuses" not in record
    assert record["memory_peak_before"] == record["memory_peak"] == "762073088"
    assert record["memory_max"] == "1610612736"
    assert record["events"] == {"max": 0, "oom": 0, "oom_kill": 0}


def test_no_failure_is_turned_into_a_status_of_zero():
    source = (REPO / "scripts" / "measure_capacity.py").read_text()
    assert "return 0\n\n\ndef cgroup_memory" not in source
    assert "urllib.request" not in source, "urlopen hides an answer behind a broken send"
    assert '"statuses"' not in source


# --- the deployment guide ------------------------------------------------------------------


@pytest.fixture(scope="module")
def section_16() -> str:
    docs = (REPO / "docs" / "DEPLOYMENT.md").read_text()
    return " ".join(docs.split("## 16. ")[1].split("## 17. ")[0].split())


def test_the_guide_expects_what_the_slots_allow_in_the_typical_phase(section_16):
    from churnsense.api import state

    explanations = 8
    assert (
        f"at least {state.BATCH_SLOTS + state.EXPLAIN_SLOTS} responses with 200 "
        f"({state.BATCH_SLOTS} batch slots + {state.EXPLAIN_SLOTS} explanation slots)"
    ) in section_16
    assert f"up to {explanations - state.EXPLAIN_SLOTS} of the eight" in section_16
    assert "503 `explanations_busy`" in section_16
    assert "200s for the typical phase" not in section_16


def test_the_guide_bounds_the_last_phase_by_the_batch_slots(section_16):
    from churnsense.api import state
    from churnsense.api.middleware import MAX_CLIENT_REQUESTS

    assert f"at most {state.BATCH_SLOTS} responses with 200" in section_16
    assert f"may have in flight ({MAX_CLIENT_REQUESTS})" in section_16
    for code in ("batch_busy", "rate_limited", "too_many_in_flight"):
        assert f"`{code}`" in section_16


def test_the_guide_keeps_refusals_and_transport_errors_apart(section_16):
    for field in ("`http`", "`transport_errors`", "`requests`", "`send_interrupted`"):
        assert field in section_16
    assert "`transport_errors` empty in every phase" in section_16
    for detail in ("`stage`", "`exception`", "`errno`", "`reason`"):
        assert detail in section_16


def test_the_guide_says_the_memory_peak_is_cumulative(section_16):
    assert "since the cgroup was created" in section_16
    assert "cumulative" in section_16
    assert "`memory_peak_before`" in section_16
    assert "not that phase's own peak" in section_16
    assert "`memory_peak` from the `typical` phase" not in section_16


def test_the_helper_says_the_memory_peak_is_cumulative():
    assert "since the container's cgroup was created" in " ".join(measure.__doc__.split())


def test_the_guide_records_the_local_measurement_and_leaves_the_vps_open(section_16):
    assert (
        "LOCAL DOCKER MEASUREMENT DONE (2026-11-10), FOR THE EARLIER LAYOUT. "
        "THE CURRENT IMAGE AND THE VPS ARE NOT MEASURED."
    ) in section_16
    assert "LOCAL DOCKER MEASUREMENT DONE (2026-11-10). VPS NOT MEASURED." not in section_16
    assert "PENDING REAL DOCKER MEASUREMENT" not in section_16
    assert "has not been measured: repeat the procedure there" in section_16
    assert "`churnsense-ai:c8495555f955-dirty`" in section_16
    assert "not a deployable tag" in section_16
    assert "bypassed Caddy" in section_16


def test_the_recorded_figures_are_the_measured_ones(section_16):
    limit, highest = 1_610_612_736, 762_073_088
    for measured in (560_562_176, 675_258_368, highest, limit, 439_963_648, 222_130_176):
        assert f"{measured:,}" in section_16
    assert f"{round(100 * highest / limit)}% of the limit" in section_16
    assert limit == 1536 * 1024 * 1024
    compose = (REPO / "deploy" / "compose.yaml").read_text()
    assert "memory: 1536M" in compose, "the recorded limit is the one Compose sets"
    assert "No OOM and no OOM kill" in section_16


def test_the_recorded_answers_add_up_to_what_each_phase_sends(section_16):
    from churnsense.api import state
    from churnsense.api.middleware import MAX_CLIENT_REQUESTS

    slots = state.BATCH_SLOTS + state.EXPLAIN_SLOTS
    x = "\N{MULTIPLICATION SIGN}"
    assert f"{slots} {x} 200, {10 - slots} {x} 503 `explanations_busy`" in section_16
    assert f"2 {x} 200, 3 {x} 429 `batch_busy`, 7 {x} 429 `rate_limited`" in section_16
    assert 2 + 3 + 7 == MAX_CLIENT_REQUESTS == 2 + 1 + 9
    assert "`transport_errors` = `{}`" in section_16


def test_the_guide_explains_the_nine_results_without_a_status(section_16):
    assert "The nine results without a status are explained." in section_16
    assert "all twelve uploads were answered over HTTP" in section_16
    assert "`send_interrupted`: `ConnectionResetError`" in section_16
    assert "the 429 was still read" in section_16
    assert "never failures to reach the service" in section_16


def test_the_guide_no_longer_calls_local_memory_unmeasured():
    docs = " ".join((REPO / "docs" / "DEPLOYMENT.md").read_text().split())
    assert "Memory is not measured" not in docs
    assert "which is not measured" not in docs
    assert "Nothing has run in Docker yet" not in docs
    assert "The VPS's real RAM use is not measured" not in docs
    assert "Neither the current image nor the VPS's real RAM use is measured" in docs
    assert "not a measurement of a VPS" in docs


def test_the_guide_does_not_call_the_earlier_figures_a_bound_on_the_current_image(section_16):
    """The figures predate the generated book; how they compare to it is not measured."""
    docs = " ".join((REPO / "docs" / "DEPLOYMENT.md").read_text().split())
    assert "upper estimate" not in docs
    assert docs.count("expected to overstate") == 2
    assert ("expected to overstate what is deployed, but that expectation is not measured") in docs
    assert (
        "expected to overstate the current image's use, but that expectation is not measured"
    ) in section_16


# --- the build command's context -----------------------------------------------------------

HEAD = "c8495555f9553c72dbaf9a8eae693e1e3ba3d148"


@pytest.mark.parametrize("porcelain", ["", " M Dockerfile\n"])
@pytest.mark.parametrize("extra", [(), ("--build-arg", "ALLOW_EXAMPLE_DOMAIN=1"), ("--no-cache",)])
def test_the_build_command_ends_with_the_one_build_context(porcelain, extra):
    from scripts import image_provenance as prov

    command = prov.provenance(HEAD, porcelain).build_command(*extra)
    assert command[:2] == ["docker", "build"]
    assert command[-1] == "." and command.count(".") == 1
    assert list(extra) == command[-1 - len(extra) : -1], "options come before the context"


def test_a_second_build_context_is_refused():
    from scripts import image_provenance as prov

    with pytest.raises(ValueError, match="build context"):
        prov.provenance(HEAD, "").build_command("--no-cache", ".")


def _printed(monkeypatch, capsys, argv):
    from scripts import image_provenance as prov

    monkeypatch.setattr(prov, "current", lambda: prov.provenance(HEAD, "?? src/new.py\n"))
    code = prov.main(argv)
    out = capsys.readouterr()
    return prov, code, out


def test_the_printed_command_ends_with_the_context_and_names_where_to_run_it(monkeypatch, capsys):
    prov, code, out = _printed(monkeypatch, capsys, ["--build-arg", "ALLOW_EXAMPLE_DOMAIN=1"])
    assert code == 0
    lines = out.out.splitlines()
    assert len(lines) == 1, "stdout is the command alone, so it can be copied or captured"
    assert lines[0].startswith("docker build --tag churnsense-ai:c8495555f955-dirty ")
    assert lines[0].endswith(" ALLOW_EXAMPLE_DOMAIN=1 .")
    assert str(prov.ROOT) in out.err and prov.ROOT == REPO
    assert (prov.ROOT / "Dockerfile").is_file(), "the context named is the one with the Dockerfile"


def test_a_context_passed_on_the_command_line_prints_no_command(monkeypatch, capsys):
    _prov, code, out = _printed(monkeypatch, capsys, ["."])
    assert code == 2
    assert out.out == ""
    assert "build context" in out.err


def test_the_guide_says_where_the_build_command_must_run():
    docs = " ".join((REPO / "docs" / "DEPLOYMENT.md").read_text().split())
    section = docs.split("## 8. ")[1].split("## 9. ")[0]
    assert "**from the repository root**" in section
    assert "always ends with the build context `.`" in section
