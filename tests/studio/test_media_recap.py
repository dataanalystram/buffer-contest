"""Weekly recap with videos (studio/media/recap.py): gathering, ranking, media + rights per story, X posts through the
free X reader, and the HuhByte weekly-recap structure rendered for real (cover, one segment per story, Follow once)."""
import shutil
import subprocess
import time

import pytest

from studio.media import recap

NOW = 1_793_500_000.0


def lead(title, url, src="", hours=10, points=0, image=""):
    return {"title": title, "url": url, "source": src, "published": NOW - hours * 3600, "points": points, "summary": "", "image": image}


def test_recap_requests_are_recognised():
    r = recap.wants_recap("make a 7 days what happened in AI recap with videos")
    assert r["recap"] and r["videos"] and r["days"] == 7
    assert recap.wants_recap("weekly AI roundup, top 5 stories")["n"] == 5
    assert not recap.wants_recap("post about this launch")["recap"]


def test_rank_clusters_outlets_without_chaining_and_caps_each_company():
    leads = [
        lead("Anthropic orders Claude to stop remaking video games", "https://gamedeveloper.com/a"),
        lead("Anthropic's Claude AI blocks video game cloning and remaking", "https://pcgamer.com/b"),
        lead("Claude stops remaking video games, Anthropic says", "https://news.google.com/rss/x"),
        lead("Reading Anthropic's S-1 as a Claude Code user", "https://alexkras.com/s1", points=400),
        lead("Anthropic drops Ultra five-hour limit for Claude Code", "https://thenextweb.com/c"),
        lead("Google DeepMind introduces Gemini 4 Carbon model", "https://blog.google/g"),
        lead("Gemini 4 Carbon from Google DeepMind is here", "https://theverge.com/g"),
        lead("Old AI news from last month", "https://old.example/x", hours=24 * 20),
        lead("A football transfer story", "https://sport.example/x"),
    ]
    out = recap.rank(leads, now=NOW, n=5)
    titles = [s["title"] for s in out]
    assert titles[0].startswith("Anthropic") and len(out[0]["outlets"]) == 3        # three outlets, one story
    assert any("Gemini 4 Carbon" in t for t in titles)
    assert sum(1 for t in titles if "Anthropic" in t or "Claude" in t) <= recap.MAX_PER_COMPANY
    assert not any("football" in t or "Old AI" in t for t in titles)              # AI only, inside the 7-day window
    assert "news.google.com" not in out[0]["url"]                                   # the publisher's link, not a redirect


def test_plan_uses_official_media_freely_and_asks_once_for_the_rest():
    stories = [{"title": "Gemini Robotics 2 is now available on Google Cloud", "url": "https://cloud.google.com/blog/x", "source": "Google"},
               {"title": "Startup demo goes viral", "url": "https://example.com/news", "source": "Example"},
               {"title": "Nobody filmed this", "url": "https://example.org/n", "source": "Org"}]

    def find(u):
        if "cloud.google.com" in u:
            return {"best_downloadable": None, "images": [{"url": "https://storage.googleapis.com/cover.png", "credit": "Image: cloud.google.com"}]}
        if "example.com" in u:
            return {"best_downloadable": {"url": "https://cdn.example.com/demo.mp4", "credit": "Video: example.com", "official": False}}
        return {}
    p = recap.plan(stories, find=find)
    s1, s2, s3 = p["segments"]
    assert s1["media"]["official"] is True                                        # the company's own page and CDN
    assert s2["media"]["kind"] == "video" and not s2["media"]["official"]
    assert s3["media"] is None
    assert [a["i"] for a in p["needs_confirmation"]] == [2] and "Video: example.com" in p["question"]
    assert p["cover"] == recap.COVER_TEXT and p["follow_only_at_end"]
    declined = recap.apply_rights(p, False)
    assert declined["segments"][1]["media"] is None and declined["segments"][0]["media"]       # official stays
    assert "needs_confirmation" not in declined


def test_x_posts_get_their_video_through_the_free_x_reader():
    def read(handle, sid, http=None):
        assert (handle, sid) == ("AarushYadav47", "2116198463847408066")
        return {"handle": "AarushYadav47", "videos": [{"url": "https://video.twimg.com/v.mp4", "poster": "p.jpg", "duration": 77}], "photos": []}
    stories = [{"title": "Subvocal launches", "url": "https://news.example/s", "source": "News"}]
    p = recap.plan(stories, find=lambda u: {"best_video": {"url": "https://x.com/AarushYadav47/status/2116198463847408066"}}, x_read=read)
    m = p["segments"][0]["media"]
    assert m["kind"] == "video" and m["url"] == "https://video.twimg.com/v.mp4" and m["credit"] == "Video: @AarushYadav47 on X"
    assert m["candidate"]["source_page"].startswith("https://x.com/")              # yt-dlp fallback on the worker
    no_video = recap.plan(stories, find=lambda u: {"best_video": {"url": "https://x.com/a/status/12345"}},
                          x_read=lambda h, s, http=None: {"handle": "a", "videos": [], "photos": []})
    assert no_video["segments"][0]["media"] is None                               # a post without video isn't a video


def test_error_pages_are_not_media(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"<?xml version='1.0'?><Error><Code>AccessDenied</Code></Error>" * 30)
    assert recap.usable(str(bad), "image") is False
    assert recap.usable(None, "video") is False


def test_layouts_can_be_registered_by_the_theme_work():
    class Mine(recap.RecapLayout):
        name = "huhbyte_weekly_recap"
    recap.register_layout("huhbyte_weekly_recap", Mine())
    try:
        assert recap.layout_for(None).name == "huhbyte_weekly_recap"
        assert recap.layout_for("minimal").name == "minimal"
    finally:
        recap.RECAP_LAYOUTS.pop("huhbyte_weekly_recap")


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_compose_cover_one_segment_per_story_and_follow_only_at_the_end(tmp_path):
    from PIL import Image

    from studio.media import ff
    vid, img = tmp_path / "v.mp4", tmp_path / "i.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=5:r=30", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=5", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(vid)], check=True)
    Image.new("RGB", (800, 450), (40, 90, 160)).save(img)
    p = {"cover": recap.COVER_TEXT, "segments": [
        {"i": 1, "headline": "A video story", "url": "u", "source": "a.com", "outlets": ["a.com"],
         "media": {"kind": "video", "url": "v", "official": True, "credit": "Video: A"}},
        {"i": 2, "headline": "An image story", "url": "u", "source": "b.com", "outlets": ["b.com"],
         "media": {"kind": "image", "url": "i", "official": True, "credit": "Image: B"}},
        {"i": 3, "headline": "A story with no media", "url": "u", "source": "c.com", "outlets": ["c.com", "d.com"], "media": None}]}
    t0 = time.time()
    out = recap.compose(p, {1: str(vid), 2: str(img), 3: None}, str(tmp_path / "out"), reel_secs=2.0, slide_max=3.0)
    assert len(out["slides"]) == 3 and [s["i"] for s in out["slides"]] == [1, 2, 3]
    assert out["slides"][0]["duration"] == pytest.approx(3.0, abs=0.2)                 # capped (60 s in production)
    for s in out["slides"]:
        pr = ff.probe(s["path"])
        assert (pr["width"], pr["height"]) == (1080, 1350) and pr["has_audio"]
        assert "panel" in s["boxes"] and s["boxes"]["headline"][3] <= s["boxes"]["panel"][1]   # headline above the media
    assert out["slides"][0]["credit"] == "Video: A"
    r = out["reel"]
    assert r["segments"] == 1 + 3 + 1                                                   # cover, 3 stories, ONE Follow card
    assert r["duration"] == pytest.approx(recap.CARD_SECS + 3 * 2.0 + 3.0, abs=0.4)
    assert (ff.probe(r["path"])["width"], ff.probe(r["path"])["height"]) == (1080, 1920)
    assert time.time() - t0 < 240
