"""One safe way for HuhQueue to fetch a URL that a user, an agent token or a fetched page gave it (SECURITY_RULES 1).

The hosted studio has its own copy of these rules (studio/netguard.py); the self-hosted package can't import it, so the
same checks live here, small:

* only http(s), no user:pass in the URL;
* every address the host resolves to is vetted, and the connection is made to that vetted IP (the host stays in the Host
  header and as the TLS SNI / certificate name), so a DNS answer that flips after the check can't steer it (rebinding);
* redirects are never followed automatically: each Location is vetted the same way before it is fetched (MAX_HOPS);
* the body is read with a byte cap.

Two policies:

* ``public`` (default): only plain public internet addresses. Refused: private (10/8, 172.16/12, 192.168/16, fc00::/7),
  loopback, link-local (169.254/16 with the cloud metadata address, fe80::/10), CGNAT 100.64/10, "this network",
  benchmarking, documentation, multicast, reserved, and any of these hidden in IPv6 (::ffff:a.b.c.d, 64:ff9b::/96,
  2002::/16); metadata host names (metadata.google.internal, ...) and localhost by name.
* ``lan``: a self-hosted install may talk to its own network (a local n8n webhook, a LAN media server), so private and
  loopback are allowed, but link-local / cloud metadata, multicast, unspecified and metadata names never are.

Outbound fetches of user-supplied URLs (media from a link, RSS feeds, LinkedIn link previews) use ``public`` unless the
operator sets HUHQ_ALLOW_PRIVATE_URLS=1; webhooks and the notification webhook (set by an owner) use ``lan``.

Tests pin DNS by monkeypatching ``_getaddrinfo``. Mock transports are not pinned (they open no socket), but every host
is still resolved and vetted.
"""
from __future__ import annotations

import ipaddress
import socket
import zlib
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
METADATA_IPS = [ipaddress.ip_network(n) for n in ("fd00:ec2::254/128", "100.100.100.200/32", "192.0.0.192/32")]  # AWS v6, Alibaba, Oracle
METADATA_HOSTS = {"metadata", "metadata.google.internal", "metadata.goog", "instance-data", "instance-data.ec2.internal"}


class FetchError(ValueError):
    pass


ACCEPT_ENCODING = "gzip"   # an encoding capped_body inflates under a bound (never br / zstd)


def _with_encoding(headers: dict | None, value: str = ACCEPT_ENCODING) -> dict:
    return {**{k: v for k, v in (headers or {}).items() if k.lower() != "accept-encoding"}, "Accept-Encoding": value}


def capped_body(r: httpx.Response, limit: int):
    """Yield ``r``'s body decoded, never more than ``limit`` bytes in all (FetchError past it). Raw bytes are read off the
    wire and gzip / deflate are inflated with an output bound on every step, so a small compressed answer can't expand
    past the cap in memory before it is checked (httpx's iter_bytes() decodes a whole chunk first). Other encodings are
    refused. An in-memory response (a test transport's) was read and decoded when it was built: its content is used."""
    if r.is_stream_consumed:
        if len(r.content) > limit:
            raise FetchError("That page is too large to read.")
        yield r.content
        return
    enc = r.headers.get("content-encoding", "").strip().lower()
    if enc in ("", "identity"):
        dec = None
    elif enc in ("gzip", "x-gzip"):
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif enc == "deflate":
        dec = zlib.decompressobj()
    else:
        raise FetchError("That link answered in an encoding that can't be read.")
    total = wire = 0
    wire_cap = limit + 65_536   # raw bytes too: endless empty deflate blocks never reach the decoded cap (CodeRabbit)
    try:
        for raw in r.iter_raw():
            wire += len(raw)
            if wire > wire_cap:
                raise FetchError("That page is too large to read.")
            out = raw if dec is None else dec.decompress(raw, limit + 1 - total)
            total += len(out)
            if total > limit or (dec is not None and dec.unconsumed_tail):
                raise FetchError("That page is too large to read.")
            if out:
                yield out
        if dec is not None:
            out = dec.flush()
            total += len(out)
            if total > limit:
                raise FetchError("That page is too large to read.")
            if out:
                yield out
    except zlib.error:
        raise FetchError("That link's compressed answer is broken, so it can't be read.")


def _inner_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    inner = ip.ipv4_mapped or ip.sixtofour
    if inner is None and ip in ipaddress.ip_network("64:ff9b::/96"):
        inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return inner


def ip_blocked(ip, policy: str = "public") -> bool:
    """True when ``ip`` may not be contacted under ``policy`` ('public' or 'lan')."""
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _inner_v4(ip)
        if inner is not None and ip_blocked(inner, policy):
            return True
        if ip.ipv4_mapped:
            return False
    if any(ip in n for n in METADATA_IPS if n.version == ip.version):   # cloud metadata outside link-local: every policy
        return True
    if policy == "lan":
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return True
        return ip.version == 4 and (ip in ipaddress.ip_network("0.0.0.0/8") or ip == ipaddress.IPv4Address("255.255.255.255"))
    if any(ip in n for n in BLOCKED_NETS if n.version == ip.version):
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _getaddrinfo(host: str):
    return socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)


def _name_blocked(h: str, policy: str) -> bool:
    if not h or h in METADATA_HOSTS or h.endswith(".internal"):
        return True
    return policy == "public" and (h == "localhost" or h.endswith(".localhost"))


def resolve(host: str, policy: str = "public") -> str:
    """Resolve ``host`` once, vet EVERY address it resolves to, and return one to connect to (FetchError otherwise)."""
    h = (host or "").strip().strip("[]").rstrip(".").lower()
    if _name_blocked(h, policy):
        raise FetchError("That link points to a private address, so it can't be read.")
    try:
        infos = _getaddrinfo(h)
    except (socket.gaierror, UnicodeError, ValueError, OSError):
        raise FetchError("That link's host name doesn't resolve.")
    ips = []
    for info in infos or []:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            raise FetchError("That link points to a private address, so it can't be read.")
        if ip_blocked(ip, policy):
            raise FetchError("That link points to a private address, so it can't be read.")
        ips.append(ip)
    if not ips:
        raise FetchError("That link's host name doesn't resolve.")
    return str(next((i for i in ips if i.version == 4), ips[0]))


def check_url(url: str, policy: str = "public") -> str:
    """Scheme + credentials + host check (resolves the host). Returns the vetted IP."""
    try:
        p = urlparse(url or "")
        p.port   # noqa: B018 - a malformed port raises here, not later while the request is built
    except ValueError as e:
        raise FetchError("That link isn't a valid address.") from e
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("The link must start with http:// or https://")
    if p.username or p.password:
        raise FetchError("Links with a user name or password in them can't be read.")
    return resolve(p.hostname, policy)


def _pinnable(c: httpx.Client, url: str) -> bool:
    t = getattr(c, "_transport", None)
    if not isinstance(t, httpx.HTTPTransport):
        return False
    try:
        return c._transport_for_url(httpx.URL(url)) is t
    except Exception:  # noqa: BLE001 - an httpx without this helper: pin anyway
        return True


def _build(c: httpx.Client, method: str, url: str, ip: str, headers: dict | None, **kw) -> tuple[httpx.Request, bool]:
    if not _pinnable(c, url):
        return c.build_request(method, url, headers=headers, **kw), False
    p = urlparse(url)
    netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{p.port}" if p.port else "")
    host_hdr = (f"[{p.hostname}]" if ":" in (p.hostname or "") else (p.hostname or "")) + (f":{p.port}" if p.port else "")
    ext = {"sni_hostname": p.hostname} if p.scheme == "https" else {}
    return c.build_request(method, p._replace(netloc=netloc).geturl(), headers={**(headers or {}), "Host": host_hdr},
                           extensions=ext, **kw), True


def request(c: httpx.Client, method: str, url: str, *, policy: str = "public", headers: dict | None = None,
            limit: int = 5_000_000, max_hops: int = MAX_HOPS, follow: bool = True, stream_to=None, **kw) -> httpx.Response:
    """``method`` ``url`` with every hop vetted and pinned. Redirects are followed by hand (GET/HEAD only, at most
    ``max_hops``) when ``follow``; otherwise the redirect response is returned. The body is capped at ``limit`` bytes; with
    ``stream_to`` (a callable taking each chunk) it is handed over chunk by chunk instead of kept. Returns a read
    response whose ``.url`` is the logical URL it ended at."""
    headers = _with_encoding(headers)   # gzip at most, inflated under the cap (capped_body)
    for _ in range(max_hops + 1):
        ip = check_url(url, policy)
        req, pinned = _build(c, method, url, ip, headers, **kw)
        r = c.send(req, stream=True, follow_redirects=False)
        try:
            if r.status_code in REDIRECTS and r.headers.get("location") and follow and method in ("GET", "HEAD"):
                try:
                    url = urljoin(url, r.headers["location"])
                except ValueError as e:   # a malformed Location (CodeRabbit, 4 Nov 2026)
                    raise FetchError("That link redirects to an invalid address.") from e
                continue
            buf = bytearray()
            for chunk in capped_body(r, limit):   # the cap bounds memory before decoding (CodeRabbit, 4 Nov 2026)
                if stream_to is not None:
                    stream_to(chunk)
                else:
                    buf += chunk
            hdrs = [(k, v) for k, v in r.headers.multi_items()
                    if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
            return httpx.Response(r.status_code, headers=hdrs, content=bytes(buf),
                                  request=c.build_request(method, url, headers=headers) if pinned else r.request)
        finally:
            r.close()
    raise FetchError("That link redirects too many times.")


def fetch_policy(core) -> str:
    """'lan' when the operator opted in (HUHQ_ALLOW_PRIVATE_URLS=1) for user-supplied links, else 'public'."""
    return "lan" if getattr(getattr(core, "settings", None), "allow_private_urls", False) else "public"
