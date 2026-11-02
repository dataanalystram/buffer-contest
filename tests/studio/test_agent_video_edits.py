

def test_with_the_youtube_fallback_on_the_launch_film_beats_the_site_hero(monkeypatch):
    from . import test_video_first as tv
    monkeypatch.setattr(tv.drafter, "_public", lambda h: True)
    monkeypatch.setenv("HQ_MEDIA_YTDLP", "0")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    http = tv._client({video_first.PH_GRAPHQL: (200, tv.PH_JSON, "application/json"), "https://screen.example/": (200, tv.SITE_HTML, "text/html")}, [])
    r = video_first.research_video(tv.PROMPT, http=http, ph_token="tok", upload_limit_mb=40)
    assert r["status"] == "found" and r["video"]["url"] == "https://www.youtube.com/watch?v=abc123def45"
