"""The Host-header allowlist both servers use against DNS rebinding."""

from __future__ import annotations

import pytest

from churnsense.hosts import allowed_host_entry, hostname, is_allowed_host

LOOPBACK = ("localhost", "127.0.0.1")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("localhost:8501", "localhost"),
        ("LocalHost", "localhost"),
        (" 127.0.0.1:8000 ", "127.0.0.1"),
        ("[::1]:8000", "::1"),
        ("[::1]", "::1"),
        ("[::1", ""),
    ],
)
def test_hostname_drops_the_port_and_case(header: str, expected: str):
    assert hostname(header) == expected


@pytest.mark.parametrize("header", ["localhost", "localhost:8501", "127.0.0.1:8001", "LOCALHOST"])
def test_loopback_names_are_allowed(header: str):
    assert is_allowed_host(header, LOOPBACK)


@pytest.mark.parametrize(
    "header",
    [
        "attacker.example",
        "attacker.example:8501",
        "localhost.attacker.example",
        "attacker.example:localhost",
        "127.0.0.1.nip.io",
        "",
        None,
    ],
)
def test_anything_else_is_refused(header: str | None):
    assert not is_allowed_host(header, LOOPBACK)


def test_an_ipv6_loopback_must_be_listed_to_be_allowed():
    assert not is_allowed_host("[::1]:8000", LOOPBACK)
    assert is_allowed_host("[::1]:8000", (*LOOPBACK, "::1"))


def test_a_star_turns_the_check_off():
    assert is_allowed_host("anything.example", ["*"])
    assert is_allowed_host(None, ["*"])


def test_allowed_names_are_compared_without_case():
    assert is_allowed_host("my-app.streamlit.app", ["My-App.Streamlit.App"])


@pytest.mark.parametrize(
    ("entry", "header"),
    [
        ("localhost:8501", "localhost:8000"),
        (" my.host ", "my.host"),
        ("[::1]", "[::1]:8000"),
        ("::1", "[::1]:8000"),
    ],
)
def test_entries_are_normalised_like_the_header(entry: str, header: str):
    """Regression: these loaded fine and then matched nothing."""
    assert allowed_host_entry(entry) == hostname(header)
    assert is_allowed_host(header, [entry])


@pytest.mark.parametrize("entry", ["http://my.host", "*.streamlit.app", "", "my host", "a/b"])
def test_an_entry_that_could_never_match_is_refused(entry: str):
    with pytest.raises(ValueError, match="not a host name"):
        allowed_host_entry(entry)


def test_a_host_that_merely_starts_with_http_is_fine():
    assert allowed_host_entry("httpbin.org") == "httpbin.org"
