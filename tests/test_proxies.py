"""The trusted-proxy rules: which peers may speak for a client, and what they may say.

Behind Caddy the immediate peer is the proxy, not the visitor. These tests pin
the narrow contract that lets the service run there without trusting anyone
else: a configured proxy address, a client read from ``X-Forwarded-For``
starting at the trusted end, and a scheme taken only from that proxy.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from churnsense import proxies
from churnsense.config import ApiConfig, ConfigError

CADDY = "172.30.10.2"
PUBLIC_HOST = "demo.example.com"

# --- configuration -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "normalised"),
    [
        ("172.30.10.2", "172.30.10.2/32"),
        (" 172.30.10.0/29 ", "172.30.10.0/29"),
        ("10.1.2.0/24", "10.1.2.0/24"),
        ("fd00:30::2", "fd00:30::2/128"),
        ("fd00:30::/120", "fd00:30::/120"),
    ],
)
def test_a_proxy_address_or_narrow_range_is_accepted(entry, normalised):
    assert proxies.trusted_network(entry) == normalised


@pytest.mark.parametrize(
    "entry",
    [
        "*",
        "",
        "  ",
        "0.0.0.0/0",
        "::/0",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "172.30.0.0/16",
        "fd00::/64",
        "172.30.10.1/29",  # host bits set: ambiguous, so refused rather than rounded
        "172.30.10.300",
        "caddy",
        "http://172.30.10.2",
        "172.30.10.2:443",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "224.0.0.1",
        "::ffff:172.30.10.2",
    ],
)
def test_wildcards_broad_ranges_and_non_addresses_are_refused(entry):
    with pytest.raises(ValueError):
        proxies.trusted_network(entry)


def test_a_non_string_entry_is_refused():
    with pytest.raises(ValueError):
        proxies.trusted_network(17)  # type: ignore[arg-type]


def test_the_config_trusts_no_proxy_by_default(cfg):
    assert cfg.api.trusted_proxies == ()
    assert ApiConfig(max_batch_rows=10, max_upload_bytes=1024).trusted_proxies == ()


def test_the_config_normalises_and_deduplicates_proxies():
    api = ApiConfig(
        max_batch_rows=10,
        max_upload_bytes=1024,
        trusted_proxies=[CADDY, "172.30.10.2/32", "fd00:30::2"],
    )
    assert api.trusted_proxies == ("172.30.10.2/32", "fd00:30::2/128")


@pytest.mark.parametrize("value", ["172.30.10.2", ["*"], ["0.0.0.0/0"], [CADDY, "::/0"], [7]])
def test_the_config_refuses_an_unsafe_proxy_list(value):
    with pytest.raises(ConfigError):
        ApiConfig(max_batch_rows=10, max_upload_bytes=1024, trusted_proxies=value)


# --- resolving the client and the scheme ---------------------------------------------------

TRUSTED = proxies.networks(("172.30.10.2/32",))


def _resolve(forwarded_for=(), proto=(), peer=CADDY):
    return proxies.resolve(peer, list(forwarded_for), list(proto), TRUSTED)


def test_the_client_is_the_address_the_trusted_proxy_appended():
    assert _resolve(["203.0.113.7"], ["https"]) == ("203.0.113.7", "https")


def test_a_chain_is_read_from_the_trusted_end():
    # The left entries are whatever the visitor typed; only the proxy's own
    # entry - the rightmost untrusted one - names the client.
    assert _resolve(["1.2.3.4, 198.51.100.9, 203.0.113.7"])[0] == "203.0.113.7"
    assert _resolve(["1.2.3.4", "203.0.113.7"])[0] == "203.0.113.7"


def test_trusted_hops_are_skipped_from_the_right():
    assert _resolve([f"203.0.113.7, {CADDY}"])[0] == "203.0.113.7"


def test_garbage_left_of_the_client_is_never_read():
    assert _resolve(["not-an-ip, unknown, 203.0.113.7"])[0] == "203.0.113.7"


def test_a_chain_of_trusted_hops_only_resolves_to_its_leftmost():
    assert _resolve([f"{CADDY}, {CADDY}"])[0] == CADDY


@pytest.mark.parametrize(
    "value", ["", " , ", "not-an-ip", "203.0.113.7:5000", "[2001:db8::1]", "203.0.113.300"]
)
def test_a_malformed_client_address_from_the_proxy_is_an_error(value):
    with pytest.raises(proxies.ForwardedHeaderError):
        _resolve([value])


def test_no_forwarded_for_means_the_proxy_itself_is_the_client():
    assert _resolve([], []) == (CADDY, None)


def test_addresses_are_written_one_way():
    assert _resolve(["::ffff:203.0.113.7"])[0] == "203.0.113.7"
    assert _resolve(["2001:DB8:0:0::1"])[0] == "2001:db8::1"


@pytest.mark.parametrize(
    ("values", "scheme"),
    [(["https"], "https"), (["HTTPS "], "https"), (["http"], "http"), (["http, https"], "https")],
)
def test_the_scheme_is_the_one_the_nearest_proxy_set(values, scheme):
    assert _resolve(["203.0.113.7"], values)[1] == scheme


@pytest.mark.parametrize("values", [["ftp"], [""], ["https, "], ["wss"], ["https;"]])
def test_a_malformed_scheme_is_an_error_not_an_upgrade(values):
    with pytest.raises(proxies.ForwardedHeaderError):
        _resolve(["203.0.113.7"], values)


# --- the middleware, on its own ------------------------------------------------------------


def _scope(peer, headers=(), *, path="/health", method="GET", scheme="http") -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": scheme,
        "headers": [(k.encode(), v.encode()) for k, v in headers],
        "client": peer,
    }


def _through(middleware_cls, scope, **kwargs) -> tuple[dict | None, list[dict]]:
    """Run ``scope`` through one middleware; the scope the app saw (or None) and what was sent."""
    seen: list[dict] = []
    sent: list[dict] = []

    async def app(scope, receive, send):
        seen.append(dict(scope))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware_cls(app, **kwargs)(scope, receive, send))
    return (seen[0] if seen else None), sent


def test_with_no_trusted_proxy_a_loopback_peer_is_answered_unchanged():
    from churnsense.api.middleware import LoopbackOnly

    scope = _scope(("127.0.0.1", 5), [("x-forwarded-for", "203.0.113.7")])
    seen, _ = _through(LoopbackOnly, scope, trusted_proxies=())
    assert seen["client"] == ("127.0.0.1", 5), "a loopback peer's forwarded header is ignored"
    assert seen["scheme"] == "http"


def test_with_no_trusted_proxy_the_proxy_address_is_refused():
    from churnsense.api.middleware import LoopbackOnly

    seen, sent = _through(LoopbackOnly, _scope((CADDY, 5)), trusted_proxies=())
    assert seen is None and sent[0]["status"] == 403


def test_a_trusted_proxy_speaks_for_its_client():
    from churnsense.api.middleware import LoopbackOnly

    scope = _scope(
        (CADDY, 5), [("x-forwarded-for", "9.9.9.9, 203.0.113.7"), ("x-forwarded-proto", "https")]
    )
    seen, _ = _through(LoopbackOnly, scope, trusted_proxies=(CADDY,))
    assert seen["client"] == ("203.0.113.7", 0)
    assert seen["scheme"] == "https"


def test_a_peer_outside_the_list_cannot_speak_for_anyone():
    from churnsense.api.middleware import LoopbackOnly

    scope = _scope(
        ("172.30.10.3", 5), [("x-forwarded-for", "127.0.0.1"), ("x-forwarded-proto", "https")]
    )
    seen, sent = _through(LoopbackOnly, scope, trusted_proxies=(CADDY,))
    assert seen is None
    assert sent[0]["status"] == 403


def test_a_loopback_peer_cannot_claim_https_even_when_a_proxy_is_trusted():
    from churnsense.api.middleware import LoopbackOnly

    scope = _scope(
        ("127.0.0.1", 5), [("x-forwarded-for", "203.0.113.7"), ("x-forwarded-proto", "https")]
    )
    seen, _ = _through(LoopbackOnly, scope, trusted_proxies=(CADDY,))
    assert seen["client"] == ("127.0.0.1", 5)
    assert seen["scheme"] == "http"


def test_a_malformed_forwarded_header_from_the_proxy_is_a_400():
    import json

    from churnsense.api.middleware import LoopbackOnly

    scope = _scope((CADDY, 5), [("x-forwarded-for", "bogus"), ("x-forwarded-proto", "https")])
    seen, sent = _through(LoopbackOnly, scope, trusted_proxies=(CADDY,))
    assert seen is None
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"])["error"] == "invalid_forwarded_header"


def test_a_websocket_through_the_proxy_gets_the_secure_scheme():
    from churnsense.api.middleware import LoopbackOnly

    scope = _scope((CADDY, 5), [("x-forwarded-for", "203.0.113.7"), ("x-forwarded-proto", "https")])
    scope.update(type="websocket", scheme="ws")
    seen, _ = _through(LoopbackOnly, scope, trusted_proxies=(CADDY,))
    assert seen["scheme"] == "wss"


def test_the_middleware_reads_the_configured_proxies_only_for_a_non_loopback_peer(monkeypatch):
    from churnsense.api import middleware

    def broken_config():
        raise AssertionError("a loopback peer must not need the config")

    monkeypatch.setattr(middleware, "load_config", broken_config)
    seen, _ = _through(middleware.LoopbackOnly, _scope(("127.0.0.1", 5)))
    assert seen is not None


# --- the whole app, behind the proxy -------------------------------------------------------


@pytest.fixture
def behind_proxy(monkeypatch, tmp_path):
    """The real app, configured to trust CADDY and to answer the public host, with no model."""
    from churnsense.api import main as api_main
    from churnsense.api import middleware, state

    monkeypatch.setattr(middleware, "TRUSTED_PROXIES", (CADDY,))
    monkeypatch.setattr(middleware, "ALLOWED_HOSTS", (PUBLIC_HOST, "127.0.0.1"))
    monkeypatch.setattr(state, "ARTIFACTS_DIR", tmp_path / "no_model")
    state.reset()
    # Caddy terminates TLS: its connection to the app is plain HTTP.
    with TestClient(api_main.app, base_url=f"http://{PUBLIC_HOST}", client=(CADDY, 4000)) as client:
        yield client
    state.reset()


def _forwarded(client_ip="203.0.113.7", proto="https", **extra) -> dict:
    return {"x-forwarded-for": client_ip, "x-forwarded-proto": proto, **extra}


def test_a_visitor_through_the_proxy_is_answered(behind_proxy):
    response = behind_proxy.get("/health", headers=_forwarded())
    assert response.status_code == 200
    assert "max-age" in response.headers["strict-transport-security"]


def test_a_same_origin_https_post_through_the_proxy_reaches_the_route(behind_proxy):
    """The route answers 503 (no model here): the point is that no layer refused it."""
    response = behind_proxy.post(
        "/api/predict",
        json={},
        headers=_forwarded(origin=f"https://{PUBLIC_HOST}", **{"sec-fetch-site": "same-origin"}),
    )
    assert response.status_code == 422, response.json()


def test_without_the_proxys_scheme_an_https_post_is_still_cross_origin(behind_proxy):
    response = behind_proxy.post(
        "/api/predict",
        json={},
        headers={"x-forwarded-for": "203.0.113.7", "origin": f"https://{PUBLIC_HOST}"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "cross_origin"


def test_a_post_from_another_site_through_the_proxy_is_refused(behind_proxy):
    response = behind_proxy.post(
        "/api/predict", json={}, headers=_forwarded(origin="https://attacker.example")
    )
    assert response.status_code == 403


def test_a_foreign_host_through_the_proxy_is_refused(behind_proxy):
    response = behind_proxy.get("/health", headers={**_forwarded(), "host": "attacker.example"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_host"


def test_without_the_proxys_scheme_no_hsts_is_sent(behind_proxy):
    response = behind_proxy.get("/health", headers={"x-forwarded-for": "203.0.113.7"})
    assert response.status_code == 200
    assert "strict-transport-security" not in response.headers


@pytest.mark.parametrize("peer", [("172.30.10.3", 1), ("203.0.113.7", 2), ("10.0.0.5", 3)])
def test_a_direct_peer_is_still_refused_when_a_proxy_is_trusted(behind_proxy, peer):
    from churnsense.api import main as api_main

    with TestClient(api_main.app, base_url=f"http://{PUBLIC_HOST}", client=peer) as direct:
        response = direct.get("/health", headers=_forwarded(client_ip="127.0.0.1"))
    assert response.status_code == 403
    assert response.json()["error"] == "loopback_only"


def test_each_visitor_behind_the_proxy_has_its_own_rate_budget(behind_proxy, monkeypatch):
    """Not one bucket for every visitor (the proxy's address), and not one a visitor can pick."""
    from churnsense.api import middleware
    from churnsense.config import RateLimit

    monkeypatch.setattr(middleware, "RATE_LIMITS", {"batch": RateLimit(burst=1, per_seconds=3600)})

    def upload(client_ip: str, spoof: str | None = None):
        chain = client_ip if spoof is None else f"{spoof}, {client_ip}"
        return behind_proxy.post(
            "/api/predict/batch",
            files={"file": ("x.csv", b"tenure\n1\n", "text/csv")},
            headers=_forwarded(client_ip=chain),
        )

    assert upload("203.0.113.7").status_code != 429
    assert upload("203.0.113.7").status_code == 429
    assert upload("203.0.113.7", spoof="198.51.100.1").status_code == 429, "no spoofed fresh budget"
    assert upload("198.51.100.20").status_code != 429, "another visitor has a budget of their own"


def test_a_trusted_proxy_written_as_ipv4_mapped_ipv6_is_still_trusted():
    from churnsense.api.middleware import LoopbackOnly

    scope = _scope(
        (f"::ffff:{CADDY}", 5), [("x-forwarded-for", "203.0.113.7"), ("x-forwarded-proto", "https")]
    )
    seen, _ = _through(LoopbackOnly, scope, trusted_proxies=(CADDY,))
    assert seen["client"] == ("203.0.113.7", 0) and seen["scheme"] == "https"


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("::1", "::1"),
        ("testclient", "testclient"),
    ],
)
def test_an_ipv6_visitor_is_budgeted_by_its_slash_64(address, key):
    """One IPv6 visitor holds a whole /64: rotating within it must not mint fresh budgets."""
    from churnsense.api.middleware import client_key

    assert client_key(address) == key
