"""CodeRabbit (4 Nov 2026), HIGH: studio/mediareach.py read user-supplied https media links (HEAD, then a byte-range GET)
straight through an httpx client, so a post's media URL could make the hosted app probe 127.0.0.1, the cloud metadata
address or any private host. Both probes now go through studio.netguard: every address the host resolves to is vetted,
the connection is pinned to the vetted IP (DNS rebinding), and a redirect is reported, never followed.

Nothing here touches the network: DNS is faked and HTTP goes to a recording transport."""
import socket

import httpx
import pytest

from studio import drafter, mediareach, netguard

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 4000


class Recorder(httpx.HTTPTransport):
    """A real network transport class (so netguard pins) that records instead of connecting."""

    def __init__(self, routes=None):
        super().__init__()
        self.seen = []
        self.routes = routes

    def handle_request(self, request):
        self.seen.append((request.method, str(request.url), request.headers.get("host"),
                          dict(request.extensions).get("sni_hostname")))
        if self.routes:
            return self.routes(request)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-type": "video/mp4", "content-length": str(len(MP4))}, request=request)
        return httpx.Response(206, content=MP4[:1024], headers={"content-type": "video/mp4",
                                                                  "content-range": f"bytes 0-1023/{len(MP4)}"}, request=request)


def _dns(monkeypatch, table):
    """Each lookup pops the next answer for the host (rebinding = a different answer the next time)."""
    def fake(host, *a, **k):
        seq = table.get(host)
        if seq is None:
            raise socket.gaierror("no such host")
        ip = seq.pop(0) if len(seq) > 1 else seq[0]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(netguard, "_getaddrinfo", fake)
    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture(autouse=True)
def _fresh():
    mediareach.reset()
    yield
    mediareach.reset()


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/v.mp4", "https://169.254.169.254/latest/meta-data/x.jpg", "https://10.1.2.3/i.jpg",
    "https://192.168.1.10/i.jpg", "https://172.16.0.9/i.jpg", "https://100.64.0.1/i.jpg", "https://0.0.0.0/i.jpg",
    "https://[::1]/v.mp4", "https://[fd00::1]/v.mp4", "https://[fe80::1]/v.mp4", "https://[::ffff:127.0.0.1]/v.mp4",
    "https://[::ffff:169.254.169.254]/i.jpg", "https://[64:ff9b::a9fe:a9fe]/i.jpg",
    "https://localhost/v.mp4", "https://metadata.google.internal/i.jpg",
])
def test_private_literal_and_alias_hosts_are_never_probed(url, monkeypatch):
    _dns(monkeypatch, {"localhost": ["127.0.0.1"], "metadata.google.internal": ["169.254.169.254"]})
    t = Recorder()
    msg = mediareach.check_one(url, httpx.Client(transport=t))
    assert msg and "private" in msg.lower()
    assert t.seen == []           # neither the HEAD nor the GET left the box


def test_hostname_resolving_inside_is_blocked(monkeypatch):
    _dns(monkeypatch, {"cdn.evil.test": ["93.184.216.34", "169.254.169.254"]})   # the second lookup answers metadata
    t = Recorder()
    msg = mediareach.check_one("https://cdn.evil.test/v.mp4", httpx.Client(transport=t))
    assert msg and "private" in msg.lower() and t.seen == []


def test_dns_rebinding_both_probes_connect_to_the_vetted_ip(monkeypatch):
    # first answers public, later answers loopback: HEAD and GET must still go to the public IP that was vetted
    _dns(monkeypatch, {"rebind.test": ["93.184.216.34", "93.184.216.34", "127.0.0.1"]})
    t = Recorder()
    assert mediareach.check_one("https://rebind.test/v.mp4", httpx.Client(transport=t)) is None
    assert [s[0] for s in t.seen] == ["HEAD", "GET"]
    for _m, url, host, sni in t.seen:
        assert url == "https://93.184.216.34/v.mp4" and host == "rebind.test" and sni == "rebind.test"


def test_head_and_get_share_one_dns_lookup(monkeypatch):
    # the GET reuses the IP the HEAD vetted: a later loopback answer is never asked for, let alone used
    calls = []
    _dns(monkeypatch, {"once.test": ["93.184.216.34", "93.184.216.34", "127.0.0.1", "127.0.0.1"]})
    orig = netguard._getaddrinfo
    monkeypatch.setattr(netguard, "_getaddrinfo", lambda h, *a, **k: calls.append(h) or orig(h, *a, **k))
    t = Recorder()
    assert mediareach.check_one("https://once.test/v.mp4", httpx.Client(transport=t)) is None
    assert calls == ["once.test"] and all(s[1].startswith("https://93.184.216.34/") for s in t.seen)


def test_redirect_to_internal_host_is_reported_never_followed(monkeypatch):
    _dns(monkeypatch, {"pub.test": ["93.184.216.34"]})
    t = Recorder(lambda req: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}, request=req))
    msg = mediareach.check_one("https://pub.test/i.jpg", httpx.Client(transport=t))
    assert "redirects" in msg and len(t.seen) == 1          # one HEAD to the public host, nothing to the metadata IP
    assert "169.254" not in msg                               # the internal target is not echoed back either


def test_non_https_and_credentials_in_url_are_refused(monkeypatch):
    _dns(monkeypatch, {"pub.test": ["93.184.216.34"]})
    t = Recorder()
    assert "https" in mediareach.check_one("http://pub.test/v.mp4", httpx.Client(transport=t))
    assert "https" in mediareach.check_one("file:///etc/passwd", httpx.Client(transport=t))
    assert mediareach.check_one("https://u:p@pub.test/v.mp4", httpx.Client(transport=t))
    assert t.seen == []


def test_range_read_is_capped_when_the_server_ignores_range(monkeypatch):
    _dns(monkeypatch, {"big.test": ["93.184.216.34"]})
    sent = {}

    def routes(req):
        if req.method == "HEAD":
            return httpx.Response(200, headers={"content-type": "video/mp4", "content-length": "9"}, request=req)
        body = MP4[:24] + b"\x00" * 5_000_000
        sent["n"] = len(body)
        return httpx.Response(206, content=body, headers={"content-type": "video/mp4"}, request=req)
    seen = {}
    real = netguard.safe_once

    def spy(*a, **k):
        r, ip = real(*a, **k)
        seen[k.get("method")] = len(r.content)
        return r, ip
    monkeypatch.setattr(netguard, "safe_once", spy)
    assert mediareach.check_one("https://big.test/v.mp4", httpx.Client(transport=Recorder(routes))) is None
    assert seen["GET"] <= mediareach.RANGE_MAX < sent["n"]


def test_public_link_still_passes(monkeypatch):
    _dns(monkeypatch, {"cdn.example.com": ["93.184.216.34"]})
    monkeypatch.setattr(drafter, "_public", netguard.public_host)
    assert mediareach.check_one("https://cdn.example.com/v.mp4", httpx.Client(transport=Recorder())) is None
