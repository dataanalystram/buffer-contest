"""find_launch_video (studio/media/find.py): the cascade on recorded pages (no network) plus an opt-in live run.

Live: HQ_MEDIA_LIVE=1 python -m pytest tests/studio/test_media_find.py -k live   (hits X, YC, HN, GitHub, a company site)
"""
import json
import os

import httpx
import pytest

from studio import drafter
from studio.media import find

YC_JSON = {"id": 1, "title": "The Subvocal Company: Talk without talking", "tagline": "Glasses",
           "body": "TL;DR: glasses.\n\n<https://youtu.be/9nW3RA7MOBU>\n\nMore at the site.",
           "share_image_url": "/media/?type=post&id=1&key=x.png",
           "company": {"name": "The Subvocal Company", "url": "https://subvocal.example"}}
COMPANY_HTML = """<html><head><meta property="og:title" content="Subvocal">
<meta property="og:video:secure_url" content="https://cdn.subvocal.example/launch.mp4">
<meta property="og:image" content="/hero.jpg">
<script type="application/ld+json">{"@type":"VideoObject","name":"Demo","contentUrl":"https://cdn.subvocal.example/demo.mp4"}</script>
</head><body><iframe src="https://player.vimeo.com/video/76979871"></iframe>
<a href="https://www.youtube.com/@subvocal">channel</a><a href="https://x.com/subvocal/status/123">post</a>
<video src="/v/hero.webm"></video></body></html>"""


def _client(routes: dict) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        for prefix, (status, body, ctype) in routes.items():
            if str(req.url).startswith(prefix):
                return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode(),
                                      headers={"content-type": ctype})
        return httpx.Response(404, content=b"{}", headers={"content-type": "application/json"})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(drafter, "_public", lambda h: True)   # mock transport: no DNS
    monkeypatch.setenv("HQ_MEDIA_YTDLP", "0")                   # no yt-dlp network calls in unit tests


def test_url_helpers():
    assert find.platform_of("https://youtu.be/abc") == "youtube"
    assert find.youtube_id("https://www.youtube.com/embed/9nW3RA7MOBU?x=1") == "9nW3RA7MOBU"
    assert find.canonical("https://youtu.be/9nW3RA7MOBU") == find.canonical("https://www.youtube.com/watch?v=9nW3RA7MOBU")
    assert find.is_video_post("https://x.com/AarushYadav47/status/2116198463847408066")
    assert not find.is_video_post("https://www.youtube.com/@subvocal")      # a channel, not a video
    assert not find.is_video_post("https://x.com/AarushYadav47")
    assert find.watch_url("https://player.vimeo.com/video/1") == "https://vimeo.com/1"


def test_scan_page_finds_every_kind_of_video_reference_in_priority_order():
    sc = find.scan_page(COMPANY_HTML, "https://subvocal.example/")
    hows = [v["how"] for v in sc["videos"]]
    assert hows[0] == "og_video" and "jsonld_video" in hows and "iframe" in hows and "video_tag" in hows
    urls = [v["url"] for v in sc["videos"]]
    assert "https://subvocal.example/v/hero.webm" in urls                  # relative src resolved
    assert not any("@subvocal" in u for u in urls)                         # channel link is not a video
    assert sc["images"] == ["https://subvocal.example/hero.jpg"]


def test_yc_launch_cascade_ranks_launch_video_then_company_files_then_images():
    http = _client({
        "https://www.ycombinator.com/launches/x": (200, json.dumps(YC_JSON), "application/json"),
        "https://subvocal.example": (200, COMPANY_HTML, "text/html"),
        "https://www.youtube.com/oembed": (200, json.dumps({"title": "Introducing Subvocal", "author_name": "The Subvocal Company",
                                                            "author_url": "https://www.youtube.com/@thesubvocalcompany"}), "application/json"),
    })
    got = find.find_launch_video("post about this https://www.ycombinator.com/launches/x make it punchy", http=http, max_results=8)
    assert got[0]["platform"] == "youtube" and got[0]["how"] == "yc_launch_body" and got[0]["official"] is True
    assert got[0]["credit"] == "Video: The Subvocal Company on YouTube"
    assert any(c["url"] == "https://cdn.subvocal.example/launch.mp4" and c["official"] for c in got)
    kinds = [c["kind"] for c in got]
    assert kinds == sorted(kinds, key=lambda k: {"video": 0, "image": 1}.get(k, 2))  # videos before images
    assert json.dumps(got)                                                  # plain JSON for agent-core run state


def test_page_without_video_falls_back_to_images_and_never_raises():
    http = _client({"https://plain.example": (200, '<meta property="og:image" content="https://plain.example/og.png">', "text/html")})
    got = find.find_launch_video("https://plain.example/blog/launch", http=http)
    assert got and all(c["kind"] == "image" for c in got)
    assert find.find_launch_video("https://down.example/x", http=_client({})) == []


def test_private_addresses_are_refused(monkeypatch):
    monkeypatch.setattr(drafter, "_public", lambda h: False)
    logs = []
    assert find.find_launch_video("http://169.254.169.254/latest/meta-data", log=logs.append) == []
    assert logs and "skip" in logs[0]


def test_youtube_search_only_with_a_key(monkeypatch):
    called = []
    monkeypatch.setattr(find, "youtube_api_search", lambda q, key, http=None, n=5: called.append(q) or [])
    find.find_launch_video("Subvocal glasses launch", http=_client({}))
    assert called == []
    find.find_launch_video("Subvocal glasses launch", http=_client({}), youtube_api_key="k")
    assert called == ["Subvocal glasses launch launch demo"]


LIVE = [
    ("x_post", "https://x.com/AarushYadav47/status/2116198463847408066", "x"),
    ("yc_subvocal", "https://www.ycombinator.com/launches/Wf9-the-subvocal-company-talk-without-talking", "youtube"),
    ("yc_axonnic", "https://www.ycombinator.com/launches/WfE-axonnic-muscle-sensing-sleeves-for-robot-training", None),
    ("hn_launch", "https://news.ycombinator.com/item?id=50291116", "youtube"),
    ("github", "https://github.com/mutonby/openshorts", "direct"),
]


@pytest.mark.skipif(os.environ.get("HQ_MEDIA_LIVE") != "1", reason="live network test (HQ_MEDIA_LIVE=1)")
@pytest.mark.parametrize("name,url,platform", LIVE)
def test_live_launch_urls(name, url, platform, monkeypatch):
    monkeypatch.undo()
    got = find.find_launch_video(url)
    vids = [c for c in got if c["kind"] == "video"]
    assert vids, f"{name}: no video found"
    if platform:
        assert vids[0]["platform"] == platform
    assert all(c["credit"] for c in got)
