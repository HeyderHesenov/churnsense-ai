"""Host-header allowlisting, shared by the API and the dashboard.

Both servers bind to loopback and have no authentication. Binding keeps other
machines out; it does not stop DNS rebinding. A page on ``attacker.example``
re-resolves its own name to 127.0.0.1, and the victim's browser then talks to
the local server *as* ``attacker.example`` - same-origin as far as the browser
is concerned, so neither CORS nor Streamlit's origin check objects. The Host
header is the one thing that gives it away, so it is checked against a list.
"""

from __future__ import annotations

from collections.abc import Iterable


def hostname(header: str) -> str:
    """The host part of a Host header, lower-cased and without its port."""
    header = header.strip().lower()
    if header.startswith("["):  # an IPv6 literal, as in [::1]:8000
        end = header.find("]")
        return header[1:end] if end != -1 else ""
    return header.split(":", 1)[0]


def allowed_host_entry(entry: str) -> str:
    """A configured host name in the form a Host header is compared in.

    Port, brackets, case and surrounding space are dropped exactly as they are
    from the header, so ``localhost:8501`` and ``[::1]`` mean what they say.
    Anything that could never match a Host header - a URL, a ``*.domain``
    wildcard, an empty string - raises ``ValueError`` instead of loading as
    an entry that silently refuses every request.
    """
    text = entry.strip()
    if text == "*":
        return "*"
    # A bare IPv6 address (::1) is written without the brackets a header has.
    name = text.lower() if text.count(":") > 1 and not text.startswith("[") else hostname(text)
    if "://" in entry or not name or any(c in name for c in "/*@ \t"):
        raise ValueError(f"{entry!r} is not a host name (no scheme, path or wildcard; or '*')")
    return name


def is_allowed_host(header: str | None, allowed: Iterable[str]) -> bool:
    """Whether a request's Host header names one of the ``allowed`` hosts.

    ``"*"`` in ``allowed`` turns the check off. A missing or empty header is
    refused: every HTTP/1.1 request must carry one.
    """
    allowed = {allowed_host_entry(entry) for entry in allowed}
    if "*" in allowed:
        return True
    return bool(header) and hostname(header) in allowed
