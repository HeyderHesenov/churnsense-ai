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
from urllib.parse import urlsplit


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


def is_allowed_origin(origin: str | None, allowed: Iterable[str]) -> bool:
    """Whether a browser's ``Origin`` header names one of the ``allowed`` hosts.

    Browsers attach an Origin to every POST and to every cross-origin fetch, so
    a page on another site that fires uploads at the local API is recognisable
    even though its Host header is a legitimate ``localhost``. A request with
    no Origin comes from a non-browser client (curl, Python) or a same-origin
    navigation and passes. ``null`` - sandboxed frames, ``file://`` pages - and
    anything that is not a plain http(s) origin is refused.
    """
    if origin is None:
        return True
    parts = urlsplit(origin.strip())
    if parts.scheme not in {"http", "https"} or "@" in parts.netloc or parts.path:
        return False
    return is_allowed_host(parts.netloc, allowed)


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _port(netloc: str, default: int) -> int | None:
    """The port of a ``host[:port]`` or ``[v6][:port]``, ``default`` if none; None if malformed."""
    tail = netloc.rsplit("]", 1)[-1] if netloc.startswith("[") else netloc
    if ":" not in tail:
        return default
    text = tail.rsplit(":", 1)[1]
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 5 else None


def is_same_origin(origin: str | None, host: str | None, scheme: str) -> bool:
    """Whether a browser's ``Origin`` is the very site the request was sent to.

    Stricter than ``is_allowed_origin``, which accepts any allowed host name on
    any port: a page served by another local program (``localhost:3000``) is
    an allowed host, but it is not this site, and a state-changing request
    from it is a cross-site request. Scheme, host name and port must all match
    ``scheme`` (the request's own) and the Host header; a port left out means
    the scheme's default.
    """
    if origin is None or host is None or scheme not in _DEFAULT_PORTS:
        return False
    parts = urlsplit(origin.strip())
    if parts.scheme != scheme or "@" in parts.netloc or parts.path or not parts.netloc:
        return False
    default = _DEFAULT_PORTS[scheme]
    origin_port, host_port = _port(parts.netloc, default), _port(host.strip(), default)
    return (
        origin_port is not None
        and origin_port == host_port
        and hostname(parts.netloc) == hostname(host)
        and bool(hostname(host))
    )
