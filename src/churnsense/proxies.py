"""Which reverse proxy may speak for a client, and how its word is read.

The service has no authentication, so ``LoopbackOnly`` answers only peers on
this machine. Deployed, the peer is a reverse proxy (Caddy) instead, and the
visitor's address and scheme reach the service only as the proxy's
``X-Forwarded-For`` and ``X-Forwarded-Proto`` headers - which anyone can write.

The rules here make that safe:

* A proxy is trusted only by its socket address, listed in
  ``api.trusted_proxies``: one address, or a range no broader than /24 (IPv4)
  or /120 (IPv6). A wildcard, a broad range, loopback and the unspecified
  address are refused at load time, so a typo cannot trust the internet.
* Forwarded headers are read only from a trusted peer; from anyone else they
  are ignored, so they can neither choose a client address nor claim HTTPS.
* ``X-Forwarded-For`` is read from the right, the end the trusted proxy wrote:
  trusted hops are skipped and the first other address is the client. What a
  visitor typed further left is never parsed, so it cannot pick its own address.
* A value the trusted proxy sent that is not an IP address, or a scheme other
  than ``http`` or ``https``, is an error (400) rather than a guess: a broken
  proxy must be visible, and a malformed header must never upgrade a request
  to HTTPS. A request with no ``X-Forwarded-For`` counts as the proxy's own,
  and one with no ``X-Forwarded-Proto`` keeps the connection's scheme.

Standard library only: the config layer imports this.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from functools import lru_cache

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: The broadest range that may be trusted, by IP version: 256 addresses either way.
NARROWEST_PREFIX = {4: 24, 6: 120}

#: The schemes ``X-Forwarded-Proto`` may name.
SCHEMES = frozenset({"http", "https"})


class ForwardedHeaderError(ValueError):
    """A trusted proxy sent a forwarded header the service cannot read."""


def trusted_network(entry: str) -> str:
    """A configured proxy address or range, normalised (``172.30.10.2`` -> ``172.30.10.2/32``).

    Raises ``ValueError`` for anything that must never be trusted: a wildcard,
    a range broader than ``NARROWEST_PREFIX``, a range written with host bits
    set (ambiguous), loopback (already answered, and every local program shares
    it), the unspecified or a multicast address, or an IPv4 address written as
    IPv6.
    """
    if not isinstance(entry, str):
        raise ValueError(f"{entry!r} is not an IP address or range")
    text = entry.strip()
    if not text or text == "*":
        raise ValueError(f"{entry!r}: a trusted proxy must be one address or a narrow range")
    try:
        network = ipaddress.ip_network(text, strict=True)
    except ValueError as exc:
        raise ValueError(f"{entry!r} is not an IP address or range ({exc})") from exc
    if network.prefixlen < NARROWEST_PREFIX[network.version]:
        raise ValueError(
            f"{entry!r} is broader than /{NARROWEST_PREFIX[network.version]}; "
            "trust the proxy's own address or its dedicated network only"
        )
    first = network.network_address
    if isinstance(first, ipaddress.IPv6Address) and first.ipv4_mapped is not None:
        raise ValueError(f"{entry!r}: write an IPv4 address as IPv4")
    if first.is_loopback or first.is_unspecified or first.is_multicast:
        raise ValueError(f"{entry!r} cannot be a trusted proxy")
    return str(network)


@lru_cache(maxsize=8)
def networks(entries: tuple[str, ...]) -> tuple[IPNetwork, ...]:
    """The trusted ranges for already-validated ``entries`` (cached: read per request)."""
    return tuple(ipaddress.ip_network(trusted_network(e)) for e in entries)


def address(text: str | None) -> IPAddress | None:
    """``text`` as an IP address, an IPv4-mapped IPv6 address as IPv4; ``None`` if it is not one."""
    if not text:
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_trusted(ip: IPAddress | None, trusted: Sequence[IPNetwork]) -> bool:
    return ip is not None and any(ip in network for network in trusted)


def _items(values: Iterable[str]) -> list[str]:
    """Every comma-separated item of every copy of a header, in order, stripped."""
    return [item.strip() for value in values for item in value.split(",")]


def resolve(
    peer: str,
    forwarded_for: Sequence[str],
    forwarded_proto: Sequence[str],
    trusted: Sequence[IPNetwork],
) -> tuple[str, str | None]:
    """The client address and scheme a trusted ``peer`` vouches for.

    ``forwarded_for`` and ``forwarded_proto`` are every copy of each header, in
    the order received. The scheme is ``None`` when the proxy sent none. The
    caller has already checked that ``peer`` is trusted.
    """
    client = str(address(peer) or peer)
    if forwarded_for:
        hops = _items(forwarded_for)
        for item in reversed(hops):
            ip = address(item)
            if ip is None:
                raise ForwardedHeaderError("X-Forwarded-For holds a value that is not an address")
            client = str(ip)
            if not is_trusted(ip, trusted):
                break
    scheme = None
    if forwarded_proto:
        nearest = _items(forwarded_proto)[-1].lower()
        if nearest not in SCHEMES:
            raise ForwardedHeaderError("X-Forwarded-Proto is neither http nor https")
        scheme = nearest
    return client, scheme
