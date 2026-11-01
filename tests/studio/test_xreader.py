"""The free X reader (studio/xreader.py), on recorded fixtures of one real public post (the Aarush/Subvocal launch):
react-tweet's token matches JavaScript exactly, each layer parses its fixture, the cascade fills words + video without
any key, falls back to FxTwitter, caches per post globally and rate-limits live lookups, and reader.read_url turns it
into the article the agent uses (video kept with its post URL for the yt-dlp worker). No network: a MockTransport."""
import json
from pathlib import Path

import httpx
import pytest

from studio import reader, xreader

FIX = Path(__file__).parent / "fixtures"
SID, HANDLE = "2116198463847408066", "AarushYadav47"
TWEET = f"https://x.com/{HANDLE}/status/{SID}"
OEMBED = json.loads((FIX / "x_oembed_aarush.json").read_text())
SYN = json.loads((FIX / "x" / "syndication_aarush.json").read_text())
FX = json.loads((FIX / "x" / "fxtwitter_aarush.json").read_text())


class XWorld:
    def __init__(self, oembed=200, syn=200, fx=200):
        self.status = {"publish.x.com": oembed, "cdn.syndication.twimg.com": syn, "api.fxtwitter.com": fx}
        self.calls = []

    def __call__(self, req):
        host = req.url.host
        self.calls.append(f"{host}{req.url.path}?{req.url.query.decode()}")
        st = self.status.get(host, 404)
        if st != 200:
            return httpx.Response(st, json={})
        body = {"publish.x.com": OEMBED, "cdn.syndication.twimg.com": SYN, "api.fxtwitter.com": FX}[host]
        return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    from studio import drafter
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)


def _http(w):
    return httpx.Client(transport=httpx.MockTransport(w))


def test_token_matches_react_tweet_in_javascript():
    # values computed with node: ((Number(id)/1e15)*Math.PI).toString(36).replace(/(0+|\.)/g, '')
    assert xreader.syndication_token(SID) == "54o8eoe7xg9"
    assert xreader.syndication_token("20") == "6dq1a2xwd93"
    assert xreader.syndication_token("1683920951807971329") == "42y6zv7ufp"


def test_each_layer_parses_its_recorded_fixture():
    syn = xreader.parse_syndication(SYN)
    assert syn["handle"] == HANDLE and syn["videos"] and syn["videos"][0]["url"].startswith("https://video.twimg.com/")
    assert syn["videos"][0]["poster"].startswith("https://pbs.twimg.com/") and syn["videos"][0]["duration"] > 60
    assert "/3840x2160/" not in syn["videos"][0]["url"]  # a size the download cap can take, not the 4K master
    fx = xreader.parse_fxtwitter(FX)
    assert fx["handle"] == HANDLE and fx["videos"] and "$1.3M" in fx["text"]
    assert len(fx["text"]) >= len(syn["text"])  # FxTwitter carries the long post's full text


def test_cascade_reads_words_and_video_with_no_key():
    w = XWorld()
    got = xreader.read(HANDLE, SID, _http(w))
    assert got["layers"] == {"oembed": "ok", "syndication": "ok", "fxtwitter": "ok"}  # long post: FxTwitter for the full text
    assert "$1.3M" in got["text"] and got["author_name"] == "Aarush Yadav" and got["videos"] and got["media_via"] == "syndication"
    syn_call = next(c for c in w.calls if c.startswith("cdn.syndication.twimg.com"))
    assert f"id={SID}" in syn_call and "token=54o8eoe7xg9" in syn_call


def test_syndication_down_falls_back_to_fxtwitter_for_media():
    got = xreader.read(HANDLE, SID, _http(XWorld(syn=404)))
    assert got["layers"]["syndication"].endswith("HTTP 404.") and got["media_via"] == "fxtwitter" and got["videos"]


def test_only_oembed_left_still_reads_the_words():
    got = xreader.read(HANDLE, SID, _http(XWorld(syn=503, fx=500)))
    assert got["text"] and not got["videos"] and got["has_media"]


def test_everything_down_is_a_clean_error():
    with pytest.raises(xreader.XReadError):
        xreader.read(HANDLE, SID, _http(XWorld(oembed=404, syn=404, fx=404)))


def test_cached_globally_and_rate_limited(monkeypatch):
    w = XWorld()
    xreader.read(HANDLE, SID, _http(w))
    n = len(w.calls)
    xreader.read(HANDLE, SID, _http(w))
    assert len(w.calls) == n  # one read per post for everyone, for 20 minutes
    monkeypatch.setattr(xreader, "PER_WINDOW", 1)
    with pytest.raises(xreader.XReadError) as e:
        xreader.read("someone", "1683920951807971329", _http(w))
    assert "try again" in str(e.value)


def test_read_url_gives_the_agent_an_article_with_the_video_and_its_post_url():
    art = reader.read_url(TWEET, _http(XWorld()))
    assert art["site"] == "X" and art["post"]["credit"] == f"X @{HANDLE}" and "$1.3M" in art["description"]
    vid = next(m for m in art["media"] if m["type"] == "video")
    assert vid["url"].startswith("https://video.twimg.com/") and vid["source_page"] == TWEET  # yt-dlp fallback on the worker
    assert art["image"].startswith("https://pbs.twimg.com/") and art["media_note"] == ""


def test_a_users_bearer_is_an_optional_override_and_never_echoed():
    calls = []

    def handler(req):
        calls.append(req.url.host)
        if req.url.host == "api.x.com":
            return httpx.Response(401)
        return XWorld()(req)
    art = reader.read_url(TWEET, httpx.Client(transport=httpx.MockTransport(handler)), x_bearer="userBearer" + "Q" * 40)
    assert "api.x.com" in calls and art["media"]  # the refused key falls back to the free layers' media
    assert "QQQQ" not in json.dumps(art)


def test_settings_say_x_works_without_a_key(human):
    d = human.get("/api/integrations").json()
    x = next(c for c in d["integrations"] if c["key"] == "x")
    assert "works without a key (public embeds)" in x["app"]["detail"].lower() and x["app"]["on"]
