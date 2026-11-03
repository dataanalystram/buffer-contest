"""A YouTube link in Make a post (owner bug, 3 Nov 2026): "https://youtu.be/MxAa8g8qFz4 can you get this video with
huhbyte ai brand kit" read the watch page as an article ("- YouTube"), invented facts from the prompt and made a carousel
about our own brand kit. Now: real details (Data API / oEmbed), the style phrase isn't the topic, the video comes from the
YouTube fallback or the maker's own site, and the upload ask says the real limit. Hermetic (respx), no network."""
import json

import httpx
import pytest
import respx

from studio import agent_tools, intake, netguard
from studio.library import FILE_MAX, MB
from studio.media import find, video_first, youtube_info
from tests.studio.test_house import client  # noqa: F401  (the fixture)

URL = "https://youtu.be/MxAa8g8qFz4"
OEMBED = {"title": "Introducing Screen Studio 4.0", "author_name": "Screen Studio", "author_url": "https://www.youtube.com/@screenstudioapp",
          "thumbnail_url": "https://i.ytimg.com/vi/MxAa8g8qFz4/hqdefault.jpg", "provider_name": "YouTube"}
SITE = ('<html><head><title>Screen Studio — Professional screen recorder for macOS</title>'
        '<meta property="og:site_name" content="Screen Studio"><meta property="og:video" content="https://screen.studio/videos/hero/hero-demo.mp4">'
        '</head><body><video src="/landing/hero/hero-recording.mp4"></video></body></html>')
API = {"items": [{"snippet": {"title": "Introducing Screen Studio 4.0", "channelTitle": "Screen Studio", "channelId": "UCabc",
                              "description": "Screen Studio 4.0 brings zoom presets and a new editor.\nTry it: https://screen.studio/download\nFollow https://x.com/screenstudio",
                              "publishedAt": "2026-10-28T16:00:00Z", "thumbnails": {"maxres": {"url": "https://i.ytimg.com/vi/MxAa8g8qFz4/maxresdefault.jpg"}}},
                  "contentDetails": {"duration": "PT1M23S"}}]}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.setattr(netguard, "_host_ok", lambda host: True)
    monkeypatch.setattr(netguard, "_pinnable", lambda c, url: False)  # respx matches host names; CI has no proxy, so it would pin to IPs
    monkeypatch.setenv("HQ_MEDIA_YTDLP", "0")
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)


def _mock_web(site_status=200):
    respx.get(url__startswith="https://www.youtube.com/oembed").mock(return_value=httpx.Response(200, json=OEMBED))
    respx.get("https://screen.studio/").mock(return_value=httpx.Response(site_status, text=SITE if site_status == 200 else "no",
                                                                       headers={"content-type": "text/html"}))
    respx.get(url__regex=r"https://(?!screen\.studio/$).*").mock(return_value=httpx.Response(404, text="no", headers={"content-type": "text/html"}))


def test_style_phrase_is_not_the_topic():
    p = intake.parse(URL + " can you get this video with huhbyte ai brand kit")
    assert p["topic"] == "" and p["source_url"] == URL and p["format"] == "reel"
    assert p["style"] == {"text": "with huhbyte ai brand kit", "kit": "huhbyte ai", "what": "brand kit"}
    assert intake.parse("make a reel about Cursor's new agent in my theme")["topic"] == "Cursor's new agent"
    assert intake.parse("OpenAI's new model")["style"] == {}


@respx.mock
def test_oembed_details_and_the_maker_site_fallback():
    _mock_web()
    info = youtube_info.video_info(URL)
    assert info["title"] == "Introducing Screen Studio 4.0" and info["channel"] == "Screen Studio" and info["via"] == "youtube_oembed"
    assert youtube_info.find_site("Screen Studio", []) == ("https://screen.studio/", "screen.studio carries the channel's name")
    r = video_first.research_video(URL, upload_limit_mb=100)
    # YouTube can't be downloaded here (no fallback route): the maker's own copy on its site, credited to the channel
    assert r["status"] == "found" and r["credit"] == "Video: Screen Studio"
    assert r["video"]["url"].startswith("https://screen.studio/") and r["video"]["official"]
    assert "own copy on screen.studio" in r["plan_line"]
    assert r["launch_video"]["url"] == r["video"]["url"] and r["youtube"]["title"] == "Introducing Screen Studio 4.0"


@respx.mock
def test_the_linked_video_itself_when_the_youtube_fallback_is_on(monkeypatch):
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    _mock_web()
    r = video_first.research_video(URL, upload_limit_mb=100)
    assert r["status"] == "found" and r["video"]["url"] == "https://www.youtube.com/watch?v=MxAa8g8qFz4" and r["video"]["how"] == "linked"
    assert r["credit"] == "Video: Screen Studio" and "the video in your link" in r["plan_line"]


@respx.mock
def test_data_api_details_and_honest_upload_ask_when_nothing_can_be_fetched():
    respx.get(url__startswith="https://www.googleapis.com/youtube/v3/videos").mock(return_value=httpx.Response(200, json=API))
    respx.get(url__regex=r"https://.*").mock(return_value=httpx.Response(404, text="no", headers={"content-type": "text/html"}))
    info = youtube_info.video_info(URL, api_key="AIzaFAKEKEY000000000000000000000")
    assert info["via"] == "youtube_api" and info["duration"] == 83 and "zoom presets" in info["description"]
    assert info["links"] == ["https://screen.studio/"]  # social links left out
    r = video_first.research_video(URL, youtube_api_key="AIzaFAKEKEY000000000000000000000", upload_limit_mb=100)
    assert r["status"] == "youtube_only" and r["video"] is None
    assert "with your YouTube Data API key" in r["ask"] and "gives details, not the video file" in r["ask"]
    assert "up to 100 MB" in r["ask"] and "Video: Screen Studio" in r["ask"]
    assert "AIza" not in json.dumps(r)


@respx.mock
def test_api_errors_never_leak_the_key():
    respx.get(url__startswith="https://www.googleapis.com/").mock(return_value=httpx.Response(403, json={"error": "quota"}))
    respx.get(url__startswith="https://www.youtube.com/oembed").mock(return_value=httpx.Response(200, json=OEMBED))
    info = youtube_info.video_info(URL, api_key="AIzaSECRETSECRETSECRETSECRET0000")
    assert info["via"] == "youtube_oembed" and "403" in info["note"] and "AIza" not in json.dumps(info)


@respx.mock
def test_someone_elses_channel_is_not_official():
    respx.get(url__startswith="https://www.youtube.com/oembed").mock(
        return_value=httpx.Response(200, json={**OEMBED, "author_name": "Random Reviewer Guy"}))
    respx.get(url__regex=r"https://.*").mock(return_value=httpx.Response(404, text="no", headers={"content-type": "text/html"}))
    r = video_first.research_video(URL, upload_limit_mb=100)
    assert r["status"] == "youtube_only" and not r["launch_video"]["official"]


def test_upload_limit_is_the_library_limit():
    t = agent_tools.Tools.__new__(agent_tools.Tools)
    t.demo = False
    assert t.upload_limit_mb() == FILE_MAX // MB == 100


def test_make_a_post_with_a_youtube_link_uses_the_real_video(client, monkeypatch):
    info = {"id": "MxAa8g8qFz4", "url": "https://www.youtube.com/watch?v=MxAa8g8qFz4", "title": "Introducing Screen Studio 4.0",
            "channel": "Screen Studio", "channel_url": "https://www.youtube.com/@screenstudioapp",
            "description": "Screen Studio 4.0 adds zoom presets, a faster editor and 4K exports for every plan.",
            "thumbnail": "https://i.ytimg.com/vi/MxAa8g8qFz4/hqdefault.jpg", "published": "", "duration": 83, "via": "youtube_oembed",
            "links": [], "note": ""}
    monkeypatch.setattr(youtube_info, "video_info", lambda url, http=None, api_key="": dict(info))
    monkeypatch.setattr(youtube_info, "find_site", lambda name, links, http=None, tries=5: ("https://screen.studio/", "test"))
    monkeypatch.setattr(find, "from_page", lambda url, http=None: (
        [find._cand("https://screen.studio/videos/hero/hero-demo.mp4", "og_video", source_page=url)], {"title": "Screen Studio", "site_name": "Screen Studio"}))
    r = client.post("/api/agent/runs", json={"prompt": URL + " can you get this video with huhbyte ai brand kit"}).json()
    res = r["result"]
    assert res["source"]["title"] == "Introducing Screen Studio 4.0" and res["source"]["site"] == "Screen Studio"
    assert "brand kit" not in (res.get("story") or {}).get("headline", "").lower()
    vr = res.get("video_research") or {}
    assert vr.get("status") == "found" and vr.get("credit") == "Video: Screen Studio"
