"""Repo-wide sweep after the CodeRabbit SSRF finding (4 Nov 2026): self-hosted HuhQueue fetched user-controlled URLs with
core.http (follow_redirects=True) and no address check:

* POST /api/media/from-url -> services/media.download (an agent token could read http://127.0.0.1:8787/... or the cloud
  metadata address into the media library, then download it);
* RSS automations / POST /api/rss/check -> services/growth.fetch_feed;
* LinkedIn "article" posts -> connectors/linkedin.read_preview + its og:image fetch (bytes uploaded to LinkedIn);
* webhooks and the notification webhook only refused a literal link-local IP (a host NAME resolving to 169.254.169.254
  passed) and the notification webhook followed redirects.

All now go through huhqueue/netguard.py. DNS is faked; HTTP goes to a recording transport."""
import socket

import httpx
import pytest

try:
    from huhqueue import netguard
except ImportError:          # main before the fix: no guard module; the tests below still run (and fail) against it
    netguard = None
from huhqueue.services import events, growth, webhooks

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class Recorder(httpx.HTTPTransport):
    def __init__(self, routes=None):
        super().__init__()
        self.seen = []
        self.routes = routes or (lambda req: httpx.Response(200, content=b"ok", request=req))

    def handle_request(self, request):
        self.seen.append((request.method, str(request.url), request.headers.get("host")))
        return self.routes(request)


def _dns(monkeypatch, table):
    def fake(host, *a, **k):
        seq = table.get(host)
        if seq is None:
            raise socket.gaierror("no such host")
        ip = seq.pop(0) if len(seq) > 1 else seq[0]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    if netguard is not None:
        monkeypatch.setattr(netguard, "_getaddrinfo", fake)


INTERNAL = ["http://127.0.0.1:8787/api/settings", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/x",
            "http://[::1]/x", "http://[::ffff:127.0.0.1]/x", "http://100.64.0.1/x", "http://localhost/x",
            "http://metadata.google.internal/x", "http://inside.test/x", "file:///etc/passwd", "http://u:p@pub.test/x"]


@pytest.mark.parametrize("url", INTERNAL)
def test_media_from_url_refuses_internal(api, core, monkeypatch, url):
    _dns(monkeypatch, {"inside.test": ["10.9.9.9"], "localhost": ["127.0.0.1"], "pub.test": ["93.184.216.34"]})
    t = Recorder()
    monkeypatch.setattr(core, "http", httpx.Client(transport=t, follow_redirects=True))
    r = api.post("/api/media/from-url", {"url": url})
    assert r.status_code >= 400 and t.seen == []


def test_media_from_url_redirect_to_internal_is_refused(api, core, monkeypatch):
    _dns(monkeypatch, {"pub.test": ["93.184.216.34"]})
    t = Recorder(lambda req: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"}, request=req))
    monkeypatch.setattr(core, "http", httpx.Client(transport=t, follow_redirects=True))
    r = api.post("/api/media/from-url", {"url": "https://pub.test/a.png"})
    assert r.status_code >= 400 and len(t.seen) == 1 and "169.254" not in t.seen[0][1]


def test_media_from_url_is_pinned_and_works_for_public(api, core, monkeypatch):
    _dns(monkeypatch, {"cdn.test": ["93.184.216.34", "127.0.0.1"]})   # rebinding: the second answer is loopback
    t = Recorder(lambda req: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}, request=req))
    monkeypatch.setattr(core, "http", httpx.Client(transport=t, follow_redirects=True))
    r = api.post("/api/media/from-url", {"url": "https://cdn.test/a.png"})
    assert t.seen == [("GET", "https://93.184.216.34/a.png", "cdn.test")]


def test_allow_private_urls_opt_in_still_blocks_metadata(core, monkeypatch):
    assert netguard is not None
    core.settings.allow_private_urls = True
    _dns(monkeypatch, {"nas.lan": ["192.168.1.20"], "meta.test": ["169.254.169.254"]})
    assert netguard.check_url("http://nas.lan/a.png", netguard.fetch_policy(core)) == "192.168.1.20"
    for u in ("http://meta.test/", "http://169.254.169.254/", "http://[fe80::1]/", "http://metadata.google.internal/",
              "http://[::ffff:169.254.169.254]/"):
        with pytest.raises(netguard.FetchError):
            netguard.check_url(u, netguard.fetch_policy(core))


@pytest.mark.parametrize("url", INTERNAL[:4])
def test_rss_feed_refuses_internal(core, monkeypatch, url):
    _dns(monkeypatch, {})
    t = Recorder()
    monkeypatch.setattr(core, "http", httpx.Client(transport=t, follow_redirects=True))
    with pytest.raises(growth.GrowthError):
        growth.fetch_feed(core, url)
    assert t.seen == []


def test_linkedin_preview_refuses_internal_page_and_image(monkeypatch):
    from huhqueue.connectors.linkedin import LinkedInConnector
    _dns(monkeypatch, {"blog.test": ["93.184.216.34"]})
    page = b'<meta property="og:title" content="T"><meta property="og:image" content="http://169.254.169.254/latest/x.png">'
    t = Recorder(lambda req: httpx.Response(200, content=page, headers={"content-type": "text/html"}, request=req))
    li = LinkedInConnector(type("S", (), {})(), http=httpx.Client(transport=t))
    pv = li.read_preview("http://127.0.0.1:8787/api/settings")
    assert pv["description"] == "" and pv["image"] == "" and t.seen == []   # title falls back to the link itself
    pv = li.read_preview("https://blog.test/post")
    assert pv["title"] == "T" and len(t.seen) == 1
    assert li._preview_image(pv["image"]) is None and len(t.seen) == 1     # the internal og:image is never fetched


def test_webhook_host_resolving_to_metadata_is_refused_at_send(core, monkeypatch):
    _dns(monkeypatch, {"hook.test": ["169.254.169.254"], "n8n.lan": ["192.168.1.30"]})
    t = Recorder()
    monkeypatch.setattr(core, "http", httpx.Client(transport=t))
    w = type("W", (), {"url": "http://hook.test/x", "secret_enc": core.cipher.encrypt("s")})()
    d = type("D", (), {"id": "wd_1", "event": "ping", "payload": {}})()
    ok, code, _ = webhooks._send(core, w, d)
    assert not ok and code == 0 and t.seen == []
    w.url = "http://n8n.lan/hook"                     # a LAN n8n stays allowed (documented self-host use)
    assert webhooks._send(core, w, d)[0] and t.seen[0][1] == "http://192.168.1.30/hook"
    with pytest.raises(webhooks.WebhookError):
        webhooks.check_url("http://metadata.google.internal/x")


def test_notify_webhook_never_follows_redirects_inside(core, monkeypatch):
    _dns(monkeypatch, {"ntfy.test": ["93.184.216.34"]})
    t = Recorder(lambda req: httpx.Response(307, headers={"location": "http://169.254.169.254/"}, request=req))
    monkeypatch.setattr(core, "http", httpx.Client(transport=t, follow_redirects=True))
    monkeypatch.setattr(core, "prefs", lambda: {"notify_webhook_url": "https://ntfy.test/topic"})
    events.webhook(core, {"message": "hi", "level": "info"})
    assert len(t.seen) == 1


@pytest.mark.parametrize("ip", ["fd00:ec2::254", "100.100.100.200", "192.0.0.192", "::ffff:100.100.100.200"])
def test_cloud_metadata_ips_outside_link_local_are_refused_under_lan_too(ip):
    """CodeRabbit (4 Nov 2026): the 'lan' policy (webhooks, HUHQ_ALLOW_PRIVATE_URLS=1) allowed the AWS IPv6, Alibaba and
    Oracle metadata addresses because they are not link-local. They are refused under every policy now."""
    assert netguard.ip_blocked(ip, "lan") and netguard.ip_blocked(ip, "public")
    assert not netguard.ip_blocked("192.168.1.20", "lan")   # a LAN n8n stays allowed


def test_gzip_bomb_is_capped_before_it_is_inflated():
    """CodeRabbit (4 Nov 2026): iter_bytes() inflates a whole chunk before the size check. capped_body reads raw bytes and
    inflates with an output bound, so a small gzip answer can't grow past the cap; a normal gzip answer still reads."""
    import gzip
    bomb = gzip.compress(b"\x00" * 50_000_000)          # ~50 KB on the wire, 50 MB inflated
    r = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(bomb))
    with pytest.raises(netguard.FetchError):
        for _ in netguard.capped_body(r, 1_000_000):
            pass
    ok = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(gzip.compress(b"hello" * 10)))
    assert b"".join(netguard.capped_body(ok, 1_000)) == b"hello" * 10
    br = httpx.Response(200, headers={"content-encoding": "br"}, stream=httpx.ByteStream(b"x"))
    with pytest.raises(netguard.FetchError):
        list(netguard.capped_body(br, 1_000))


def test_raw_bytes_are_capped_even_when_they_inflate_to_nothing():
    """CodeRabbit (4 Nov 2026): endless empty deflate blocks never reach the decoded cap; the raw bytes are capped too."""
    import zlib
    co = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
    head = co.compress(b"") + co.flush(zlib.Z_SYNC_FLUSH)   # gzip header
    empty = b"\x00\x00\x00\xff\xff"                         # an empty stored block: inflates to nothing
    assert empty and zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(head + empty * 10) == b""

    def chunks():
        yield head
        while True:
            yield empty * 1000

    class S(httpx.SyncByteStream):
        def __iter__(self):
            return chunks()
    r = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=S())
    with pytest.raises(netguard.FetchError):
        for _ in netguard.capped_body(r, 10_000):
            pass
