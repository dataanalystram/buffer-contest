"""One safe way to fetch a URL a user (or an agent, or a web page) gave us. SECURITY_RULES 1.

Every outbound fetch of a user-influenced URL goes through ``safe_get`` / ``safe_stream``:

* redirects are never followed automatically; each hop's Location is resolved and checked again before it is fetched
  (at most ``MAX_HOPS`` hops), so a public page that answers ``302 Location: http://127.0.0.1/`` can't reach inside;
* a host is allowed only when every address it resolves to is public: not private (10/8, 172.16/12, 192.168/16, fc00::/7),
  loopback, link-local (169.254/16 and fe80::/10, which holds the cloud metadata address 169.254.169.254), shared
  carrier-grade NAT (100.64.0.0/10), "this network", benchmarking, documentation, multicast or reserved ranges, and not
  an IPv4 address hidden inside IPv6 (::ffff:a.b.c.d, 64:ff9b::/96, 2002::/16) that is one of those;
* metadata host names (metadata.google.internal and friends) are refused by name too;
* only http and https; the body is read with a byte cap.

The per-hop host check calls ``drafter._public`` at call time, so tests that pin DNS by monkeypatching it still apply.
Known limit: the host is resolved again by the HTTP client when it connects, so a DNS answer that flips between the check
and the connect (rebinding) is not pinned here; redirects, the common case, are.
"""
from __future__ import annotations

import ipaddress
import socket
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urljoin, urlparse

import httpx

MAX_HOPS = 3
REDIRECTS = (301, 302, 303, 307, 308)

BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
    "::/128", "::1/128", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32", "fc00::/7", "fe80::/10",
    "fec0::/10", "ff00::/8",
)]
BLOCKED_HOSTS = {"metadata", "metadata.google.internal", "metadata.goog", "instance-data", "instance-data.ec2.internal"}


class FetchError(ValueError):
    pass


def ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that is not a plain public internet address."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour
        if inner is None and ip in ipaddress.ip_network("64:ff9b::/96"):
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None and ip_blocked(inner):
            return True
        if ip.ipv4_mapped:
            return False
    if any(ip in n for n in BLOCKED_NETS if n.version == ip.version):
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def public_host(host: str) -> bool:
    """True only when the host name is not a metadata alias and every address it resolves to is public."""
    h = (host or "").strip().strip("[]").rstrip(".").lower()
    if not h or h in BLOCKED_HOSTS or h.endswith(".internal") or h == "localhost" or h.endswith(".localhost"):
        return False
    try:
        infos = socket.getaddrinfo(h, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if ip_blocked(ip):
            return False
    return True


def _host_ok(host: str) -> bool:
    from . import drafter  # call time: tests pin DNS through drafter._public
    return drafter._public(host)


def check_url(url: str, *, check_host: bool = True, https_only: bool = False) -> str:
    p = urlparse(url)
    if p.scheme not in (("https",) if https_only else ("http", "https")) or not p.hostname:
        raise FetchError("The link must start with https://" if https_only else "The link must start with http:// or https://")
    if p.username or p.password:
        raise FetchError("Links with a user name or password in them can't be read.")
    if check_host and not _host_ok(p.hostname):
        raise FetchError("That link points to a private address, so it can't be read.")
    return url


def client(timeout: float = 12, headers: dict | None = None) -> httpx.Client:
    """The default client for outside fetches: never follows redirects on its own."""
    return httpx.Client(timeout=timeout, follow_redirects=False, headers=headers or {})


@contextmanager
def safe_stream(url: str, *, http: httpx.Client | None = None, check_host: bool = True, https_only: bool = False,
                headers: dict | None = None, timeout: float = 12, max_hops: int = MAX_HOPS) -> Iterator[httpx.Response]:
    """Open a GET as a stream, following at most ``max_hops`` redirects by hand and checking every hop's host first.
    Yields the final (non-redirect) response; ``response.url`` is where it ended up."""
    own = http is None
    c = http or client(timeout, headers)
    try:
        for _ in range(max_hops + 1):
            check_url(url, check_host=check_host, https_only=https_only)
            r = c.send(c.build_request("GET", url, headers=headers), stream=True, follow_redirects=False)
            if r.status_code in REDIRECTS and r.headers.get("location"):
                nxt = urljoin(str(r.url), r.headers["location"])
                r.close()
                url = nxt
                continue
            try:
                yield r
            finally:
                r.close()
            return
        raise FetchError("That link redirects too many times.")
    finally:
        if own:
            c.close()


def safe_get(url: str, *, limit: int = 5_000_000, http: httpx.Client | None = None, check_host: bool = True,
             https_only: bool = False, headers: dict | None = None, timeout: float = 12,
             max_hops: int = MAX_HOPS) -> httpx.Response:
    """GET with every hop checked and the body capped at ``limit`` bytes (FetchError past it). Returns a read response
    (``.text``, ``.content``, ``.url`` and ``.status_code`` work as usual)."""
    with safe_stream(url, http=http, check_host=check_host, https_only=https_only, headers=headers, timeout=timeout,
                     max_hops=max_hops) as r:
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > limit:
                raise FetchError("That page is too large to read.")
        hdrs = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(r.status_code, headers=hdrs, content=bytes(buf), request=r.request)  # body already decoded
