"""Measure the running app container's memory under the load the limits allow.

Run INSIDE the app container, against the service on loopback, so its own
cgroup is the one measured (docs/DEPLOYMENT.md, section 16):

    docker compose exec -T app python - --phases all < ../scripts/measure_capacity.py

Every customer it sends is synthetic, made from the contract's two example
records with tenure and charges varied; it never reads the dataset file, never
trains, evaluates or scores anything itself - the service scores what it is
sent, as it would for a visitor. Requests come from 127.0.0.1, so the
per-client budgets and the in-flight cap apply to it exactly as to one visitor:
the third phase therefore measures what ONE client can do; the many-client
worst case is a calculation (section 16), not something this can produce.

For each phase it prints one JSON line:

* ``http`` - the HTTP answers received, counted by status and the service's own
  ``error`` code ("429 rate_limited"). A 429 or a 503 is an answer: the service
  turning a request away as designed.
* ``transport_errors`` - the requests that got NO HTTP answer, counted by the
  stage that failed and the exception class ("send ConnectionResetError").
  These are never folded into the HTTP counts.
* ``requests`` - every request by its id ("one-client-max-batch-07"), with its
  own outcome: status, error code and Retry-After, or stage, exception class,
  errno and reason.
* the cgroup's memory.current, .peak and .max and the oom / oom_kill counters.

``memory_peak`` is the kernel's high-water mark since the container's cgroup
was created - startup, the model load and every earlier phase included - not
the peak of the phase it is printed with. ``memory_peak_before`` is the same
counter read as the phase began: a phase raised the mark only if the two
differ. To attribute a peak to one phase, run that phase alone in a freshly
started container.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import random
import sys
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

CGROUP = Path("/sys/fs/cgroup")
BOUNDARY = "churnsense-measure-boundary"
TIMEOUT_SECONDS = 120


def synthetic_rows(n: int, seed: int = 0) -> list[dict[str, object]]:
    """``n`` customers that pass the upload contract: the two examples, varied consistently."""
    from churnsense.data import schema

    rng = random.Random(seed)
    bases = (dict(schema.EXAMPLE_HIGH_RISK), dict(schema.EXAMPLE_LOW_RISK))
    rows = []
    for i in range(n):
        row = dict(bases[i % 2])
        tenure = rng.randint(1, 72)
        monthly = round(rng.uniform(20.0, 110.0), 2)
        row.update(
            customerID=f"LOAD-{i:05d}",
            tenure=tenure,
            MonthlyCharges=monthly,
            TotalCharges=round(tenure * monthly, 2),
        )
        rows.append(row)
    return rows


def csv_payload(rows: list[dict[str, object]], target_bytes: int | None = None) -> bytes:
    """The rows as CSV; padded with an ignored column up to ``target_bytes``, if given."""
    columns = list(rows[0])
    pad = 0
    if target_bytes:
        plain = len(_csv(rows, columns, 0))
        pad = max(0, (target_bytes - plain) // len(rows) - 2)
    return _csv(rows, columns, pad)


def _csv(rows: list[dict[str, object]], columns: list[str], pad: int) -> bytes:
    out = io.StringIO()
    out.write(",".join(columns + (["note"] if pad else [])) + "\n")
    filler = "x" * pad
    for row in rows:
        values = [str(row[c]) for c in columns] + ([filler] if pad else [])
        out.write(",".join(values) + "\n")
    return out.getvalue().encode()


def _multipart(payload: bytes) -> bytes:
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="synthetic.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode()
    return head + payload + f"\r\n--{BOUNDARY}--\r\n".encode()


def _failure(exc: BaseException) -> dict[str, object]:
    """What went wrong, as it was raised: the class, the errno if any, the reason."""
    return {
        "exception": type(exc).__name__,
        "errno": getattr(exc, "errno", None),
        "reason": str(exc),
    }


def _error_code(payload: bytes) -> str | None:
    """The service's ``error`` code from an ErrorResponse body, if that is what it is."""
    try:
        body = json.loads(payload)
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _exchange(
    base: str, method: str, path: str, body: bytes | None, content_type: str | None, timeout: float
) -> dict[str, object]:
    """One request on its own connection: the HTTP answer, or how the transport failed.

    The service may answer before it has read an upload (a 429 from a spent
    budget) and close the connection; the rest of the body then fails to send.
    So after a send that broke, the answer is still read: if there is one the
    outcome is that HTTP answer, with ``send_interrupted`` saying how the send
    ended. Only a request with no answer at all is a transport outcome.
    """
    target = urlsplit(base)
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=timeout)
    headers = {"Connection": "close"}
    if content_type:
        headers["Content-Type"] = content_type
    interrupted: OSError | None = None
    try:
        try:
            connection.connect()
        except OSError as exc:
            return {"outcome": "transport", "stage": "connect", **_failure(exc)}
        try:
            connection.request(method, path, body=body, headers=headers)
        except TimeoutError as exc:
            return {"outcome": "transport", "stage": "send", **_failure(exc)}
        except OSError as exc:
            interrupted = exc
        except http.client.HTTPException as exc:
            return {"outcome": "transport", "stage": "send", **_failure(exc)}
        try:
            response = connection.getresponse()
            payload = response.read()
        except (OSError, http.client.HTTPException) as exc:
            if interrupted is None:
                return {"outcome": "transport", "stage": "response", **_failure(exc)}
            return {
                "outcome": "transport",
                "stage": "send",
                **_failure(interrupted),
                "no_answer": _failure(exc),
            }
        answer: dict[str, object] = {
            "outcome": "http",
            "status": response.status,
            "error": _error_code(payload) if response.status >= 400 else None,
            "retry_after": response.getheader("Retry-After"),
        }
        if interrupted is not None:
            answer["send_interrupted"] = _failure(interrupted)
        return answer
    finally:
        connection.close()


def _request(
    request_id: str,
    base: str,
    method: str,
    path: str,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, object]:
    """``_exchange``, named: every result carries the id of the request it belongs to."""
    started = time.monotonic()
    outcome = _exchange(base, method, path, body, content_type, timeout)
    return {"id": request_id, **outcome, "seconds": round(time.monotonic() - started, 2)}


def summarise(results: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    """HTTP answers and transport failures, counted apart; no status stands in for a failure."""
    answered: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    for result in results:
        if result["outcome"] == "http":
            code = result.get("error")
            answered[f"{result['status']} {code}" if code else str(result["status"])] += 1
        else:
            failed[f"{result['stage']} {result['exception']}"] += 1
    return {
        "http": dict(sorted(answered.items())),
        "transport_errors": dict(sorted(failed.items())),
    }


def cgroup_memory() -> dict[str, object]:
    def read(name: str) -> str | None:
        try:
            return (CGROUP / name).read_text().strip()
        except OSError:
            return None

    events = {}
    for line in (read("memory.events") or "").splitlines():
        key, _, value = line.partition(" ")
        if key in ("oom", "oom_kill", "max"):
            events[key] = int(value)
    return {
        "memory_current": read("memory.current"),
        "memory_peak": read("memory.peak"),
        "memory_max": read("memory.max"),
        "events": events,
    }


Call = Callable[[], dict[str, object]]


def _concurrently(calls: list[Call]) -> list[dict[str, object]]:
    """Every call at once; the results in the order of the calls."""
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(lambda call: call(), calls))


def phase_idle(base: str) -> list[dict[str, object]]:
    return [_request("idle-ready-01", base, "GET", "/ready", timeout=10)]


def typical_calls(base: str) -> list[Call]:
    batch = _multipart(csv_payload(synthetic_rows(10_000)))
    form = f"multipart/form-data; boundary={BOUNDARY}"
    explain = [
        json.dumps({"record": {k: v for k, v in row.items() if k != "customerID"}}).encode()
        for row in synthetic_rows(8, seed=1)
    ]
    calls: list[Call] = [
        lambda i=i: _request(
            f"typical-batch-{i:02d}", base, "POST", "/api/batch/score", batch, form
        )
        for i in range(1, 3)
    ]
    calls += [
        lambda i=i, b=b: _request(
            f"typical-explain-{i:02d}", base, "POST", "/api/explain", b, "application/json"
        )
        for i, b in enumerate(explain, start=1)
    ]
    return calls


def phase_typical(base: str) -> list[dict[str, object]]:
    """Two full 10,000-row batches (every batch slot) and eight explanations, at once.

    Both batches are scored (200). The explanations meet the service's
    explanation slots: as many as there are slots are answered 200 and the rest
    are turned away with 503 ``explanations_busy``, by design - eight at once
    are more than the slots, so that this phase holds every slot of both kinds.
    """
    return _concurrently(typical_calls(base))


def one_client_max_calls(base: str, upload_limit: int) -> list[Call]:
    from churnsense.api.middleware import MAX_CLIENT_REQUESTS

    body = _multipart(csv_payload(synthetic_rows(10_000, seed=2), target_bytes=upload_limit - 4096))
    form = f"multipart/form-data; boundary={BOUNDARY}"
    return [
        lambda i=i: _request(
            f"one-client-max-batch-{i:02d}", base, "POST", "/api/batch/score", body, form
        )
        for i in range(1, MAX_CLIENT_REQUESTS + 1)
    ]


def phase_one_client_max(base: str, upload_limit: int) -> list[dict[str, object]]:
    """As many ~5 MiB uploads at once as one client may hold in flight."""
    return _concurrently(one_client_max_calls(base, upload_limit))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--phases", default="all", help="idle,typical,one-client-max or all")
    args = parser.parse_args(argv)
    from churnsense.config import load_config

    upload_limit = load_config().api.max_upload_bytes
    phases = {
        "idle": lambda: phase_idle(args.base_url),
        "typical": lambda: phase_typical(args.base_url),
        "one-client-max": lambda: phase_one_client_max(args.base_url, upload_limit),
    }
    chosen = list(phases) if args.phases == "all" else args.phases.split(",")
    for name in chosen:
        peak_before = cgroup_memory()["memory_peak"]
        started = time.monotonic()
        results = phases[name]()
        record = {
            "phase": name,
            "seconds": round(time.monotonic() - started, 2),
            "sent": len(results),
            **summarise(results),
            "memory_peak_before": peak_before,
            **cgroup_memory(),
            "requests": results,
        }
        print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
