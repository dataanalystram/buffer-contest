"""Owner complaints, 2 Nov 2026: video-first research, branding throughout the video, "No style", branded covers."""
from __future__ import annotations

import base64
import json
import shutil

import httpx
import pytest
from PIL import Image, ImageDraw

from studio import drafter
from studio.media import compose, covers, ff, video_first
from studio.media import tools as media_tools

from .test_agent_core import owner  # noqa: F401  (recorded X oEmbed, FakeBuffer, sync brain, local video worker)

PH = "https://www.producthunt.com/products/screen-studio"
PROMPT = f"{PH} Can you try to fetch the image as well as the video from this product? Edit it according to my branding for the HuhByte AI"
SITE_HTML = """<html><head><meta property="og:site_name" content="Screen Studio">
<meta property="og:video" content="https://screen.example/videos/hero-demo.mp4"><meta property="og:image" content="/og.jpg"></head>
<body><video><source src="/landing/hero.mp4"></video></body></html>"""
PH_JSON = {"data": {"p0": {"name": "Screen Studio", "tagline": "Beautiful screen recordings in minutes", "description": "Record, edit.",
                           "slug": "screen-studio", "url": "https://www.producthunt.com/posts/screen-studio", "website": "https://screen.example/?ref=producthunt",
                           "createdAt": "2023-03-01T00:00:00Z", "featuredAt": "2023-03-01T00:00:00Z", "votesCount": 3000,
                           "thumbnail": {"type": "image", "url": "https://ph-files.example/thumb.png", "videoUrl": None},
                           "media": [{"type": "video", "url": "https://ph-files.example/v.jpg", "videoUrl": "https://www.youtube.com/watch?v=abc123def45"},
                                     {"type": "image", "url": "https://ph-files.example/shot1.png", "videoUrl": None}]},
                    "p1": None, "p2": None}}


def _client(routes: dict, seen: list | None = None) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(req.url))
        for prefix, (status, body, ctype) in routes.items():
            if str(req.url).startswith(prefix):
                raw = json.dumps(body).encode() if isinstance(body, dict) else (body if isinstance(body, bytes) else body.encode())
                return httpx.Response(status, content=raw, headers={"content-type": ctype})
        return httpx.Response(404, content=b"{}", headers={"content-type": "application/json"})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(drafter, "_public", lambda h: True)
    monkeypatch.setenv("HQ_MEDIA_YTDLP", "0")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)


# ------------------------------------------------------------------------------------------------ video-first research
def test_product_hunt_helpers():
    assert video_first.ph_slug(PH) == ("product", "screen-studio")
    assert video_first.ph_slug("https://www.producthunt.com/posts/screen-studio-2") == ("post", "screen-studio-2")
    assert video_first.ph_slug("https://example.com/products/x") is None
    assert video_first.slug_name(PH) == "Screen Studio"
    assert video_first.clean_site("https://screen.studio/?ref=producthunt&utm_source=x") == "https://screen.studio/"
    q = video_first.ph_query("screen-studio")
    assert 'post(slug: "screen-studio")' in q and 'post(slug: "screen-studio-6")' in q and "videoUrl" in q
    got = video_first.parse_ph(PH_JSON)
    assert got[0]["videos"] == ["https://www.youtube.com/watch?v=abc123def45"] and got[0]["website"] == "https://screen.example/"
    assert video_first.wants_video(PROMPT) and not video_first.wants_video("post about this funding round")


def test_maker_site_mp4_wins_over_the_youtube_launch_video(offline):
    seen = []
    http = _client({video_first.PH_GRAPHQL: (200, PH_JSON, "application/json"), "https://screen.example/": (200, SITE_HTML, "text/html")}, seen)
    r = video_first.research_video(PROMPT, http=http, ph_token="tok", upload_limit_mb=40)
    assert r["status"] == "found" and r["video"]["url"] == "https://screen.example/videos/hero-demo.mp4"
    assert r["video"]["official"] and r["credit"] == "Video: Screen Studio"
    assert r["launch_video"]["url"].startswith("https://screen.example/")  # the maker's own file is the one we use
    assert [s["step"] for s in r["steps"]][:2] == ["product_hunt", "website"]
    assert seen[0] == video_first.PH_GRAPHQL  # Product Hunt first, then the site
    assert "Video found" in r["plan_line"] and r["ask"] == ""
    yt = next(c for c in r["candidates"] if c["platform"] == "youtube")
    assert yt["downloadable"] is False and yt["blocked"] == "youtube_blocks_servers"


def test_youtube_only_asks_for_an_upload_with_the_real_limit(offline):
    http = _client({video_first.PH_GRAPHQL: (200, PH_JSON, "application/json"),
                    "https://screen.example/": (200, "<html><head><title>Screen Studio</title></head></html>", "text/html")})
    r = video_first.research_video(PH, http=http, ph_token="tok", upload_limit_mb=40)
    assert r["status"] == "youtube_only" and r["video"] is None and r["best_downloadable"] is None
    assert r["ask"] == ("I found the launch video on YouTube but can't download it here. "
                        "Upload the file (MP4/MOV, up to 40 MB) and I'll brand it.")
    assert "YouTube only" in r["plan_line"] and r["launch_video"]["url"] == "https://www.youtube.com/watch?v=abc123def45"


def test_youtube_is_fetched_only_with_the_operator_fallback(offline, monkeypatch):
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    http = _client({video_first.PH_GRAPHQL: (200, PH_JSON, "application/json"), "https://screen.example/": (200, "<html></html>", "text/html")})
    r = video_first.research_video(PH, http=http, ph_token="tok")
    assert r["status"] == "found" and r["video"]["platform"] == "youtube"


def test_no_video_anywhere_says_so_and_offers_the_upload(offline):
    http = _client({"https://plain.example/": (200, "<html><head><meta property='og:image' content='/a.jpg'></head></html>", "text/html")})
    r = video_first.research_video("https://plain.example/ make a reel", http=http, upload_limit_mb=8)
    assert r["status"] == "none" and r["ask"].startswith("No video found.") and "up to 8 MB" in r["ask"]
    assert r["plan_line"].startswith("No video found") and r["images"] == ["https://plain.example/a.jpg"]


def test_without_product_hunt_the_slug_and_site_still_work(offline):
    http = _client({"https://screen.example/": (200, SITE_HTML, "text/html")})
    r = video_first.research_video(PH, http=http, ph_token="", website_hint="https://screen.example/")
    assert r["steps"][0]["step"] == "product_hunt" and not r["steps"][0]["ok"] and "isn't connected" in r["steps"][0]["note"]
    assert r["status"] == "found" and r["maker"] == "Screen Studio"



# ------------------------------------------------------------------------------------------------ Product Hunt /r/ redirects
PH_REDIRECT = "https://www.producthunt.com/r/ABC123XYZ?utm_campaign=producthunt-api&utm_medium=api-v2"


def _ph_json_redirect():
    d = json.loads(json.dumps(PH_JSON))
    d["data"]["p0"]["website"] = PH_REDIRECT
    return d


def _redir_client(redirect_status: int, location: str = "", seen: list | None = None, extra: dict | None = None) -> httpx.Client:
    routes = {video_first.PH_GRAPHQL: (200, _ph_json_redirect(), "application/json"),
              "https://screen.example/": (200, SITE_HTML, "text/html"), **(extra or {})}

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if seen is not None:
            seen.append((u, req.headers.get("user-agent", "")))
        if u.startswith("https://www.producthunt.com/r/"):
            h = {"content-type": "text/html"}
            if location:
                h["location"] = location
            return httpx.Response(redirect_status, content=b"", headers=h)
        for prefix, (status, body, ctype) in routes.items():
            if u.startswith(prefix):
                raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
                return httpx.Response(status, content=raw, headers={"content-type": ctype})
        return httpx.Response(404, content=b"{}", headers={"content-type": "application/json"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ph_redirect_helpers():
    assert video_first.is_ph_redirect(PH_REDIRECT)
    assert not video_first.is_ph_redirect("https://screen.studio/")
    assert not video_first.is_ph_redirect(PH)  # a product page is not the website redirect
    assert not video_first.is_ph_redirect("")
    assert video_first.resolve_site("https://screen.studio/", "Screen Studio") == ("https://screen.studio/", "")


def test_ph_redirect_is_followed_to_the_makers_site_before_youtube(offline):
    seen: list = []
    http = _redir_client(302, "https://screen.example/?ref=producthunt", seen)
    r = video_first.research_video(PROMPT, http=http, ph_token="tok")
    assert r["website"] == "https://screen.example/"
    assert r["status"] == "found" and r["video"]["url"] == "https://screen.example/videos/hero-demo.mp4"
    assert r["video"]["official"] and r["credit"] == "Video: Screen Studio"
    urls = [u for u, _ in seen]
    i_redirect = next(i for i, u in enumerate(urls) if u.startswith("https://www.producthunt.com/r/"))
    i_site = next(i for i, u in enumerate(urls) if u.startswith("https://screen.example/"))
    assert i_redirect < i_site
    assert "Chrome/" in seen[i_redirect][1]  # a browser User-Agent on the redirect hop
    site_step = next(s for s in r["steps"] if s["step"] == "website")
    assert site_step["ok"] and site_step["found"] >= 1 and site_step["url"] == "https://screen.example/"


def test_ph_redirect_403_falls_back_to_a_search_for_the_site(offline, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    search = {"success": True, "data": [{"url": "https://www.producthunt.com/products/screen-studio", "title": "PH"},
                                        {"url": "https://screen.example/pricing", "title": "Screen Studio"}]}
    http = _redir_client(403, extra={"https://api.firecrawl.dev/v1/search": (200, search, "application/json")})
    r = video_first.research_video(PROMPT, http=http, ph_token="tok")
    assert r["website"] == "https://screen.example/"
    assert r["status"] == "found" and r["video"]["url"] == "https://screen.example/videos/hero-demo.mp4"
    site_step = next(s for s in r["steps"] if s["step"] == "website")
    assert "found by search" in site_step["note"]


def test_ph_redirect_403_without_search_says_so_and_never_fetches_producthunt_as_the_site(offline):
    seen: list = []
    http = _redir_client(403, seen=seen)
    r = video_first.research_video(PROMPT, http=http, ph_token="tok")
    assert r["website"] == "" and r["status"] == "youtube_only"
    site_step = next(s for s in r["steps"] if s["step"] == "website")
    assert not site_step["ok"] and "blocked" in site_step["note"]
    assert sum(1 for u, _ in seen if u.startswith("https://www.producthunt.com/r/")) == 1


# ------------------------------------------------------------------------------------------------ covers
def _frame(color, w=1280, h=720, shapes=True):
    im = Image.new("RGB", (w, h), color)
    if shapes:
        d = ImageDraw.Draw(im)
        for i in range(0, w, 80):
            d.rectangle((i, h // 3, i + 40, h // 3 + 200), fill=(250 - i % 200, 120, 40 + i % 180))
        d.ellipse((w // 2 - 120, h // 2 - 120, w // 2 + 120, h // 2 + 120), fill=(30, 200, 220))
    return im


def test_cover_variants_are_scored_branded_and_grid_safe():
    frames = [(0.2, Image.new("RGB", (1280, 720), (0, 0, 0))),  # a black transition frame: never picked
              (1.0, _frame((90, 60, 140))), (2.0, _frame((20, 110, 90))), (3.0, _frame((200, 200, 205)))]
    brand = {"accent": "#C9ACFA", "bg": "#111114", "text": "#FFFFFF", "font": "anton"}
    r = covers.make_covers(frames=frames, hook="Screen Studio turns screen recordings into polished demos", brand=brand, name="Screen Studio")
    v = r["variants"]
    assert len(v) == 3 and [x["score"] for x in v] == sorted((x["score"] for x in v), reverse=True)
    assert all(x["frame_t"] != 0.2 for x in v)
    assert r["words"] == ["Screen", "Studio:", "polished", "demos"] and r["key"] == 3  # 2-5 words, the payoff in the accent
    for x in v:
        assert {k: im.size for k, im in x["images"].items()} == {"9:16": (1080, 1920), "4:5": (1080, 1350), "16:9": (1280, 720)}
        for ratio, info in x["info"].items():
            assert info["inside_safe"] and info["fit_ok"] and info["contrast"] >= 4.5, (x["layout"], ratio, info)
    # 9:16 text stays inside the centre 4:5 band (the profile grid crop)
    band = covers.safe_box("9:16", 1080, 1920)
    assert band[1] >= (1920 - 1350) // 2 and band[3] <= (1920 + 1350) // 2


def test_cover_text_never_sits_on_a_face(monkeypatch):
    monkeypatch.setattr(covers, "faces", lambda im: [(0.3, 0.55, 0.7, 0.98)])  # a face in the lower half
    r = covers.make_covers(frames=[(1.0, _frame((70, 70, 90)))], hook="Founders demo it live", brand={}, n_variants=3)
    for x in r["variants"]:
        for ratio, info in x["info"].items():
            if x["layout"] != "inset":
                assert info["face_overlap"] <= 0.15, (x["layout"], ratio, info)


def test_cover_falls_back_to_the_hero_image():
    r = covers.make_covers(frames=[(0.5, Image.new("RGB", (640, 360), (5, 5, 5)))], image=_frame((120, 90, 60)), hook="New launch", brand={})
    assert r["variants"] and r["variants"][0]["source"] == "hero"


# ------------------------------------------------------------------------------------------------ branding throughout / no style
needs_ff = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")


def _clip(tmp_path, secs=6, size="1280x720"):
    p = tmp_path / "clip.mp4"
    ff.run(["-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={secs}", "-f", "lavfi", "-i", f"sine=f=330:d={secs}",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(p)])
    return str(p)


@needs_ff
def test_brand_pass_brands_the_whole_video_and_opens_on_the_cover(tmp_path):
    src = _clip(tmp_path)
    cover = tmp_path / "cover.png"
    Image.new("RGB", (1080, 1920), (201, 172, 250)).save(cover)
    brand = {"handle": "@huhbyte.ai", "accent": "#C9ACFA"}
    m = compose.render_style(src, "minimal", out=str(tmp_path / "o.mp4"), hook="Screen Studio makes demos", brand=brand,
                             credit="Video: Screen Studio", brand_pass=True, cover_png=str(cover), music_mood="none", workdir=str(tmp_path / "w"))
    assert m["brand_pass"] == ["bug"]  # minimal already keeps its hook and credit chip on screen: only the logo bug is added
    assert m["thumbnail_offset_ms"] == 300 and m["cover_frame_secs"] == 0.6
    assert abs(m["duration"] - (0.6 + 6 + 3.5 * 0 + compose.CARD_SECS)) < 0.6
    first = compose.frame_at(m["path"], 0.2, str(tmp_path / "f0.png"))
    px = Image.open(first).convert("RGB").getpixel((540, 960))
    assert abs(px[0] - 201) < 12 and abs(px[2] - 250) < 12  # the chosen cover is the opening frame
    m2 = compose.render_style(src, "cinematic", out=str(tmp_path / "c.mp4"), hook="Screen Studio makes demos", brand=brand,
                              credit="own video", brand_pass={"lower": True}, music_mood="none")
    assert "bug" in m2["brand_pass"] and "lower" in m2["brand_pass"]  # own video: the lower-third shows the handle


@needs_ff
def test_no_style_posts_the_video_as_it_is(tmp_path):
    src = _clip(tmp_path, secs=3)
    m = compose.render_style(src, "none", out=str(tmp_path / "n.mp4"), hook="ignored", brand={"handle": "@huhbyte.ai"}, logo=True)
    assert m["style"] == "none" and m["captions"] is False and m["music"] == "none" and m["fit"] == "blurred sides"
    assert m["logo"] and (m["width"], m["height"]) == (1080, 1920) and abs(m["duration"] - (3 + compose.CARD_SECS)) < 0.5
    m2 = compose.render_style(src, "none", out=str(tmp_path / "n2.mp4"), brand={}, logo=False, ratio="4:5")
    assert not m2["logo"] and (m2["width"], m2["height"]) == (1080, 1350)
    from studio.media import styles
    assert styles.known("none") and styles.config("no style")["id"] == "none" and "none" not in styles.ALL_IDS


@needs_ff
def test_cover_swap_keeps_the_sound_and_length(tmp_path):
    from studio.media.worker import cover_swap
    src = _clip(tmp_path, secs=3, size="1080x1920")
    buf = tmp_path / "c.jpg"
    Image.new("RGB", (1080, 1920), (10, 200, 30)).save(buf, "JPEG")
    data, meta = cover_swap(open(src, "rb").read(), buf.read_bytes(), 0.6, 0.6)
    out = tmp_path / "s.mp4"
    out.write_bytes(data)
    pr = ff.probe(str(out))
    assert abs(pr["duration"] - 3.0) < 0.2 and pr["has_audio"] and meta["thumbnail_offset_ms"] == 300
    data2, _ = cover_swap(open(src, "rb").read(), buf.read_bytes(), 0.6, 0)  # prepend (a "No style" video)
    (tmp_path / "p.mp4").write_bytes(data2)
    assert abs(ff.probe(str(tmp_path / "p.mp4"))["duration"] - 3.6) < 0.25


# ------------------------------------------------------------------------------------------------ the agent, end to end
FOUND = {"status": "found", "video": {"url": "https://screen.example/videos/hero-demo.mp4", "platform": "direct", "kind": "video",
                                      "how": "og_video", "confidence": 0.94, "official": True, "downloadable": True, "credit": "Video: Screen Studio"},
         "credit": "Video: Screen Studio", "maker": "Screen Studio", "website": "https://screen.example/",
         "product": {"name": "Screen Studio", "tagline": "Beautiful screen recordings in minutes", "description": "Record and edit demos fast.",
                     "url": "https://www.producthunt.com/posts/screen-studio", "images": ["https://ph-files.example/shot1.png"], "thumbnail": "",
                     "website": "https://screen.example/"},
         "images": ["https://ph-files.example/shot1.png"], "steps": [{"step": "product_hunt", "ok": True, "found": 1, "note": ""}],
         "plan_line": "Video found: Screen Studio's launch video (its website), branded as a Reel with credit", "ask": "",
         "upload": {"accept": "video/mp4,video/quicktime", "max_mb": 40, "label": "MP4/MOV"}}
FOUND["best_downloadable"] = FOUND["best_video"] = FOUND["launch_video"] = FOUND["video"]
YT_ONLY = {**FOUND, "status": "youtube_only", "video": None, "best_downloadable": None,
           "launch_video": {"url": "https://www.youtube.com/watch?v=abc123def45", "platform": "youtube", "kind": "video", "official": True,
                            "downloadable": False, "credit": "Video: Screen Studio"},
           "plan_line": "Launch video is on YouTube only and can't be downloaded here: asking you to upload it",
           "ask": "I found the launch video on YouTube but can't download it here. Upload the file (MP4/MOV, up to 40 MB) and I'll brand it."}
YT_ONLY["best_video"] = YT_ONLY["launch_video"]


@pytest.fixture
def research(monkeypatch):
    box = {"r": FOUND, "calls": []}

    def fake(query, **kw):
        box["calls"].append(query)
        return json.loads(json.dumps(box["r"]))
    monkeypatch.setattr(video_first, "research_video", fake)
    return box


def _start(owner, prompt=PROMPT, chans=("ch_ig", "ch_yt"), **extra):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": prompt, "channel_ids": list(chans), "format": "auto", **extra}).json()
    assert r.get("kind") == "agent", r
    return r["id"], owner.get(f"/api/agent/runs/{r['id']}").json()


@needs_ff
def test_screen_studio_prompt_makes_a_branded_reel_from_the_real_launch_video(owner, research, monkeypatch, tmp_path):  # noqa: F811
    clip = _clip(tmp_path, secs=3)
    fetched = []

    def fetch(cand, run_dir=None, http=None):
        fetched.append(cand["url"])
        dst = f"{run_dir}/source.mp4"
        shutil.copy(clip, dst)
        return {"path": dst}
    monkeypatch.setattr(media_tools, "media_fetch", fetch)
    monkeypatch.setattr(media_tools, "media_transcribe", lambda path, names=None, model=None, run_dir=None: {"words": 0})
    rid, run = _start(owner)
    steps = [s["tool"] for s in run["steps"]]
    assert steps[0] == "find_video" and research["calls"] and PH in research["calls"][0]
    res = run["result"]
    assert res["plan"][0].startswith("Video found") and not res.get("video_ask")
    assert res["source"]["via"] == "product_hunt_api" and "Screen Studio" in res["source"]["title"]
    pk = {p["service"]: p for p in res["packages"]}
    assert pk["instagram"]["format"] == "reel" and pk["youtube"]["format"] == "reel"
    assert fetched == ["https://screen.example/videos/hero-demo.mp4"]
    job = json.loads(owner.app.state.store.q("SELECT spec FROM render_jobs WHERE spec LIKE '%media_render%' ORDER BY id DESC LIMIT 1")[0]["spec"])
    assert job["credit"] == "Video: Screen Studio" and job["brand_pass"] is True and job["official"] and "Screen Studio" in job["names"]
    assert len(pk["instagram"]["covers"]) == 3 and pk["instagram"]["options"]["thumbnail_offset_ms"] == 300
    assert pk["youtube"]["thumbnail"].endswith(".jpg") and set(pk["youtube"]["covers"][0]["urls"]) == {"9:16", "4:5", "16:9"}
    # swap the cover: the opening frame changes, the package follows
    old = pk["instagram"]["media_urls"][0]
    owner.post(f"/api/agent/runs/{rid}/packages/ch_ig/cover", json={"index": 2})
    pk2 = {p["service"]: p for p in owner.get(f"/api/agent/runs/{rid}").json()["result"]["packages"]}
    assert pk2["instagram"]["cover_index"] == 2 and pk2["instagram"]["media_urls"][0] != old


def test_youtube_only_asks_for_the_upload_then_brands_it(owner, research, monkeypatch, tmp_path):  # noqa: F811
    research["r"] = YT_ONLY
    rid, run = _start(owner)
    res = run["result"]
    assert res["video_ask"]["text"] == YT_ONLY["ask"] and res["video_ask"]["max_mb"] == 40
    assert any(t["text"] == YT_ONLY["ask"] for t in res["thread"]) and res["plan"][0].startswith("Launch video is on YouTube only")
    pk = {p["service"]: p for p in res["packages"]}
    assert pk["instagram"]["format"] == "carousel" and "youtube" not in pk  # no Reel without a real video
    if not shutil.which("ffmpeg"):
        return
    monkeypatch.setattr(media_tools, "media_transcribe", lambda path, names=None, model=None, run_dir=None: {"words": 0})
    clip = _clip(tmp_path, secs=3)
    tok = owner.post("/api/media", json={"name": "launch.mp4", "data": "data:video/mp4;base64," + base64.b64encode(open(clip, "rb").read()).decode()}).json()["token"]
    out = owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "here it is", "media_token": tok}).json()
    res = owner.get(f"/api/agent/runs/{out['id']}").json()["result"]
    pk = {p["service"]: p for p in res["packages"]}
    assert pk["instagram"]["format"] == "reel" and pk["youtube"]["format"] == "reel" and not res.get("video_ask")
    job = json.loads(owner.app.state.store.q("SELECT spec FROM render_jobs WHERE spec LIKE '%media_render%' ORDER BY id DESC LIMIT 1")[0]["spec"])
    assert job["credit"] == "Video: Screen Studio" and job["source"] == tok and job["brand_pass"] is True


def test_theme_none_is_respected(owner, research, monkeypatch, tmp_path):  # noqa: F811
    if not shutil.which("ffmpeg"):
        pytest.skip("needs FFmpeg")
    monkeypatch.setattr(media_tools, "media_transcribe", lambda path, names=None, model=None, run_dir=None: {"words": 0})
    clip = _clip(tmp_path, secs=3)
    tok = owner.post("/api/media", json={"name": "mine.mp4", "data": "data:video/mp4;base64," + base64.b64encode(open(clip, "rb").read()).decode()}).json()["token"]
    rid, run = _start(owner, prompt="Post my video: a quick demo of the new auto-zoom in our screen recorder", chans=("ch_ig",), theme="none", add_logo=False, media_token=tok)
    job = json.loads(owner.app.state.store.q("SELECT spec FROM render_jobs WHERE spec LIKE '%media_render%' ORDER BY id DESC LIMIT 1")[0]["spec"])
    assert job["style"] == "none" and job["brand_pass"] is False and job["logo"] is False
    assert not research["calls"]  # their own upload: nothing to look for
    owner.post(f"/api/agent/runs/{rid}/theme", json={"theme": "none"})


def test_mcp_exposes_find_video_and_theme_none():
    from studio import agent_routes, mcp_client_brain
    names = {t["name"] for t in mcp_client_brain.TOOLS}
    assert "find_video" in names and "find_video" in mcp_client_brain.READ_ONLY
    st = next(t for t in agent_routes.TOOLS if t["name"] == "set_theme")
    assert "none" in json.dumps(st) and any(t["name"] == "set_cover" for t in agent_routes.TOOLS)
    rl = next(t for t in mcp_client_brain.TOOLS if t["name"] == "research_link")
    assert "find_video" in rl["inputSchema"]["properties"]
