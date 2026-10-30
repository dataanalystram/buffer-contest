"""Never Bounced preflight: Buffer-exact counting, per-network rules, fixes, and UI = REST = MCP parity."""
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from studio import preflight as pf
from studio.preflight import check, count_text

from .conftest import agent_token, image_bytes, mcp_call
from .test_studio import bot

ROOT = Path(__file__).resolve().parents[2]
CH = {s: {"id": f"c_{s}", "service": s, "name": s} for s in pf.NETWORK}


def codes(res, level=None):
    return [f["rule"] for f in res["findings"] if level is None or f["level"] == level]


# ------------------------------------------------------------------ counting: Buffer's own examples
@pytest.mark.parametrize("text,utf16,bsky,x", [
    ("Hello", 5, 5, 5), ("𝗛𝗲𝗹𝗹𝗼", 10, 5, 10), ("𝘏𝘦𝘭𝘭𝘰", 10, 5, 10), ("🚀", 2, 1, 2),
    ("👨‍👩‍👧", 8, 1, 2), ("café", 4, 4, 4), ("cafe\u0301", 5, 4, 4)])
def test_counting_matches_buffers_table(text, utf16, bsky, x):
    # developers.buffer.com/guides/character-limits.html, "Unicode bold and italic formatting"
    assert count_text("linkedin", text) == utf16 and count_text("facebook", text) == utf16
    assert count_text("bluesky", text) == bsky
    assert count_text("twitter", text) == x


def test_network_specific_counting():
    assert count_text("instagram", "a\nb") == 4                      # a line break counts 2
    assert count_text("instagram", "a\r\nb") == 4                    # CRLF is one break
    assert count_text("linkedin", "https://example.com/" + "x" * 80) == 24
    assert count_text("twitter", "https://example.com/" + "x" * 80) == 23
    assert count_text("mastodon", "@ram@mastodon.social") == 4       # server part not counted
    assert count_text("mastodon", "https://example.com/" + "x" * 80) == 23
    assert count_text("bluesky", "https://example.com/abcdefghijklmnopqrstuvwxyz") == len("example.com") + 16
    assert count_text("twitter", "日本") == 4                         # CJK weighs 2 on X


# ------------------------------------------------------------------ text limits per network
@pytest.mark.parametrize("svc,limit", [("facebook", 5000), ("instagram", 2196), ("twitter", 280), ("linkedin", 3000),
                                       ("pinterest", 500), ("tiktok", 4000), ("threads", 500), ("bluesky", 300),
                                       ("youtube", 5000), ("googlebusiness", 4000), ("mastodon", 500)])
def test_text_limits_per_network(svc, limit):
    ok = check(CH[svc], "a" * limit, mode="draft")
    assert ok["limit"] == limit and "error" not in [f["level"] for f in ok["findings"] if f["rule"].startswith("text.")]
    bad = check(CH[svc], "a" * (limit + 1), mode="draft")
    assert bad["status"] == "error" and any(r.startswith("text.") for r in codes(bad, "error"))


def test_instagram_line_breaks_push_a_caption_over():
    text = "a\n" * 1098 + "a"  # 2,197 UTF-16 units but 3,295 by Buffer's Instagram count
    r = check(CH["instagram"], text, mode="draft")
    assert r["count"] == 3295 and r["status"] == "error"
    assert "line breaks count 2" in r["findings"][0]["message"]


def test_tiktok_limit_drops_to_2200_with_a_video():
    r = check(CH["tiktok"], "a" * 3000, ["https://cdn.example.com/v.mp4"], mode="draft")
    assert r["limit"] == 2200 and "text.tiktok.video" in codes(r, "error")
    assert check(CH["tiktok"], "a" * 3000, mode="draft")["status"] != "error"


def test_mastodon_uses_the_servers_limit_capped_at_20000():
    assert check({**CH["mastodon"], "metadata": {"maxCharacters": 1000}}, "a" * 900)["status"] == "ok"
    assert check({**CH["mastodon"], "metadata": {"maxCharacters": 99999}}, "a")["limit"] == 20000
    assert "info" in [f["level"] for f in check(CH["mastodon"], "hi")["findings"]]  # default 500, said out loud


def test_x_long_posts_is_opt_in():
    assert check(CH["twitter"], "a" * 1000, options={"x_long_posts": True})["status"] == "ok"
    assert check(CH["twitter"], "a" * 1000)["status"] == "error"


# ------------------------------------------------------------------ text fixes
def test_x_thread_fix_splits_under_280_and_trim_fits():
    long = " ".join(f"Sentence number {i} about the launch." for i in range(30))
    r = check(CH["twitter"], long)
    assert {"thread", "trim"} <= {f["id"] for f in r["fixes"]}
    text, _, applied = pf.apply_text_fixes(CH["twitter"], long, {}, ["thread"])
    assert applied == ["thread"] and all(count_text("twitter", p) <= 280 for p in pf.split_thread("twitter", text))
    assert check(CH["twitter"], text)["status"] == "ok" and check(CH["twitter"], text)["parts"] > 1
    trimmed, _, _ = pf.apply_text_fixes(CH["linkedin"], "word " * 900 + "https://example.com/a", {}, ["trim"])
    assert count_text("linkedin", trimmed) <= 3000 and trimmed.endswith("https://example.com/a")


def test_trim_counts_utf16_not_glyphs():
    bold = "𝗛" * 2000  # 2,000 glyphs, 4,000 UTF-16 units
    text, _, _ = pf.apply_text_fixes(CH["linkedin"], bold, {}, ["trim"])
    assert count_text("linkedin", text) <= 3000 and check(CH["linkedin"], text)["status"] == "ok"


def test_instagram_hashtag_and_mention_limits():
    tags = " ".join(f"#tag{i}" for i in range(31))
    r = check(CH["instagram"], "Launch day " + tags, mode="draft")
    assert "tags.instagram.hashtags" in codes(r, "error")
    fixed, _, _ = pf.apply_text_fixes(CH["instagram"], "Launch day " + tags, {}, ["drop_hashtags"])
    assert fixed.count("#") == 30 and check(CH["instagram"], fixed, mode="draft")["status"] == "ok"
    r = check(CH["instagram"], " ".join(f"@user{i}" for i in range(21)), mode="draft")
    assert "tags.instagram.mentions" in codes(r, "error")


# ------------------------------------------------------------------ required fields
def test_youtube_needs_title_and_category_on_create():
    r = check(CH["youtube"], "My video\nAbout it", ["https://cdn.example.com/v.mp4"])
    assert "field.youtube.category" in codes(r, "error")
    assert check(CH["youtube"], "My video", options={"category_id": "28"})["status"] == "ok"
    assert "field.youtube.category" in codes(check(CH["youtube"], "x", mode="draft"), "info")   # drafts: not blocking
    assert "field.youtube.title" in codes(check(CH["youtube"], "T" * 101, options={"category_id": "22"}), "error")
    assert "field.youtube.category" in codes(check(CH["youtube"], "x", options={"category_id": "99"}), "error")
    assert check(CH["youtube"], "x", creating=False)["status"] == "ok"   # optional on edit
    _, opts, _ = pf.apply_text_fixes(CH["youtube"], "T" * 150, {}, ["youtube_title", "youtube_category"])
    assert opts["category_id"] == "22" and len(opts["title"]) <= 100


def test_pinterest_board_and_threads_topic():
    assert "field.pinterest.board" in codes(check(CH["pinterest"], "Pin"), "error")
    assert check(CH["pinterest"], "Pin", options={"board_id": "b1"})["status"] == "ok"
    assert "field.threads.topic" in codes(check(CH["threads"], "hi", options={"topic": "A.I. & you"}), "error")


def test_build_post_input_carries_the_fixed_fields():
    from studio.actions import build_post_input
    yt = build_post_input({"id": "y", "service": "youtube"}, "Title\nbody", "https://c/v.mp4", "now", None,
                          options={"category_id": "22", "title": "Better title"})
    assert yt["metadata"]["youtube"] == {"title": "Better title", "categoryId": "22"}
    pin = build_post_input({"id": "p", "service": "pinterest"}, "Pin", "https://c/i.jpg", "queue", None, options={"board_id": "b1"})
    assert pin["metadata"]["pinterest"]["boardServiceId"] == "b1"


# ------------------------------------------------------------------ media count and mix
def _imgs(n):
    return [f"https://cdn.example.com/{i}.jpg" for i in range(n)]


def test_media_counts_per_network():
    r = check(CH["twitter"], "hi", _imgs(5))
    assert "count.twitter" in codes(r, "error") and any(f["id"] == "keep_first:4" for f in r["fixes"])
    assert "count.twitter" in codes(check(CH["twitter"], "hi", _imgs(1) + ["https://c.example/v.mp4"]), "error")
    assert "count.bluesky" in codes(check(CH["bluesky"], "hi", _imgs(5)), "error")
    assert "count.instagram" in codes(check(CH["instagram"], "hi", _imgs(21)), "error")
    assert check(CH["instagram"], "hi", _imgs(20))["status"] == "ok"
    assert "count.threads" in codes(check(CH["threads"], "hi", _imgs(21)), "error")
    assert "count.linkedin" in codes(check(CH["linkedin"], "hi", _imgs(2) + ["https://c.example/v.mp4"]), "error")


# ------------------------------------------------------------------ images (Pillow)
def big_noise_jpeg(w, h):
    import os
    im = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=100)
    return buf.getvalue()


def test_instagram_image_ratio_and_format_then_fit_image_fixes_it():
    tall_png = image_bytes(1000, 2000)  # 1:2, PNG
    r = check(CH["instagram"], "hi", ["https://c.example/tall.png"], loader=lambda u: tall_png)
    assert r["status"] == "error" and "image.instagram" in codes(r, "error") and "image.instagram" in codes(r, "warning")
    assert any(f["id"] == "image:0" for f in r["fixes"])
    out, ext = pf.fit_image("instagram", tall_png)
    im = Image.open(io.BytesIO(out))
    assert ext == "jpg" and im.format == "JPEG" and abs(im.width / im.height - 0.8) < 0.01
    ok = check(CH["instagram"], "hi", ["https://c.example/fixed.jpg"], loader=lambda u: out)
    assert ok["status"] == "ok" and "×" in ok["chip"]


def test_wide_image_is_cropped_to_191_and_width_capped():
    out, _ = pf.fit_image("instagram", image_bytes(4000, 1000))
    im = Image.open(io.BytesIO(out))
    assert im.width == 1440 and abs(im.width / im.height - 1.91) < 0.02


def test_size_limits_and_recompression():
    noisy = big_noise_jpeg(1400, 1400)
    assert len(noisy) > 1_000_000
    r = check(CH["bluesky"], "hi", ["https://c.example/n.jpg"], loader=lambda u: noisy)
    assert "image.bluesky" in codes(r, "warning")
    out, _ = pf.fit_image("bluesky", noisy)
    assert len(out) <= 1_000_000
    big = big_noise_jpeg(1800, 1800)
    if len(big) > 5 * pf.MB:
        assert "image.twitter" in codes(check(CH["twitter"], "hi", ["https://c.example/b.jpg"], loader=lambda u: big), "error")
    assert "image.tiktok" in codes(check(CH["tiktok"], "hi", ["https://c.example/p.png"], loader=lambda u: image_bytes(800, 800)), "error")


def test_image_rule_branches_from_probe_facts():
    fixes = {}
    li = pf._image_rules("linkedin", "LinkedIn", "Image", 0, {"bytes": 1, "format": "JPEG", "width": 7000, "height": 6000}, fixes)
    assert li[0]["rule"] == "image.linkedin" and li[0]["level"] == "error"
    th = pf._image_rules("threads", "Threads", "Image", 0, {"bytes": 1, "format": "JPEG", "width": 3000, "height": 200}, fixes)
    assert th and th[0]["level"] == "error"
    x = pf._image_rules("twitter", "X", "Image", 0, {"bytes": 6 * pf.MB, "format": "PNG", "width": 10, "height": 10}, fixes)
    assert x and "5 MB" in x[0]["message"]


# ------------------------------------------------------------------ video (ffprobe facts)
def probe(**kw):
    base = {"kind": "video", "probed": True, "codec": "h264", "width": 1080, "height": 1920, "fps": 30.0, "duration": 30.0,
            "video_kbps": 8000, "audio_kbps": 128, "bytes": 20 * pf.MB}
    return {**base, **kw}


def test_video_rules():
    rules = pf._video_rules
    assert rules("instagram", "Instagram", "Video", probe(), {}) == []
    short = rules("instagram", "Instagram", "Video", probe(duration=2.5), {})
    assert short[0]["level"] == "error" and "3 s" in short[0]["message"]
    fx = {}
    hot = rules("instagram", "Instagram", "Video", probe(video_kbps=30000, audio_kbps=192, fps=120), fx)
    assert {f["level"] for f in hot} == {"error", "warning"} and "reencode" in fx
    assert rules("twitter", "X", "Video", probe(width=4000, height=1000), {})[0]["level"] == "error"
    assert rules("tiktok", "TikTok", "Video", probe(duration=300), {})[0]["level"] == "warning"
    assert rules("youtube", "YouTube", "Video", probe(duration=240), {})[0]["level"] == "info"
    assert rules("threads", "Threads", "Video", probe(duration=400), {})[0]["level"] == "error"


def test_parse_ffprobe_output():
    j = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280, "avg_frame_rate": "30000/1001",
                      "bit_rate": "2500000"}, {"codec_type": "audio", "bit_rate": "192000"}],
         "format": {"duration": "12.5", "format_name": "mov,mp4"}}
    p = pf.parse_ffprobe(j)
    assert p["fps"] == 29.97 and p["video_kbps"] == 2500 and p["audio_kbps"] == 192 and p["duration"] == 12.5


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_two_second_clip_fails_instagram(tmp_path):
    out = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=360x640:d=2:r=30", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(out)], check=True, timeout=60)
    data = out.read_bytes()
    r = check(CH["instagram"], "hi", ["https://c.example/c.mp4"], loader=lambda u: data)
    assert r["media"][0]["kind"] == "video" and "video.instagram" in codes(r, "error")


# ------------------------------------------------------------------ media URLs
def test_media_url_rules():
    pf._CACHE.clear()
    assert "url.public" in codes(check(CH["linkedin"], "hi", ["https://drive.google.com/file/d/abc/view"]), "error")
    signed = "https://bucket.s3.amazonaws.com/a.jpg?X-Amz-Signature=abc&X-Amz-Expires=600"
    assert "url.public" in codes(check(CH["linkedin"], "hi", [signed]), "warning")
    gone = check(CH["linkedin"], "hi", ["https://cdn.example.com/gone.jpg"], loader=lambda u: None)
    assert gone["status"] == "warning" and "couldn't download" in gone["findings"][0]["message"]


# ------------------------------------------------------------------ the rule pack is honest
def test_every_rule_cites_an_official_source_and_docs_table_is_current():
    official = ("developers.buffer.com", "developers.facebook.com", "docs.x.com", "docs.bsky.app", "learn.microsoft.com",
                "developers.tiktok.com", "support.google.com")
    for r in pf.RULES:
        assert r["source"] and all(any(d in s for d in official) for s in r["source"].split(";")), r["id"]
    doc = (ROOT / "docs" / "PREFLIGHT.md").read_text()
    assert pf.markdown_table() in doc, "regenerate docs/PREFLIGHT.md with python -m studio.preflight --markdown"


# ------------------------------------------------------------------ wired into every surface
def test_rest_and_mcp_preflight_return_the_same_result(human):
    tok = agent_token(human, "trusted")
    body = {"channel_ids": ["ch_x", "ch_li", "ch_ig"], "text": "x" * 300, "media_url": "https://cdn.example.com/a.png"}
    rest = human.post("/api/preflight", json=body).json()
    _, mcp = mcp_call(human, tok, "preflight_post", body)
    agent = bot(human).post("/api/preflight", json=body, headers={"Authorization": f"Bearer {tok}"}).json()
    assert rest == mcp == agent
    by = {c["service"]: c["status"] for c in rest["channels"]}
    assert by == {"twitter": "error", "linkedin": "ok", "instagram": "warning"}  # IG: PNG not JPEG
    assert rest["status"] == "error" and not rest["ok"]


def test_preview_blocks_errors_on_every_surface_with_the_same_message(human, fake):
    tok = agent_token(human, "trusted")
    body = {"channel_id": "ch_x", "text": "x" * 300, "mode": "queue"}
    r = human.post("/api/posts/preview", json={**body, "override_warnings": True})
    assert r.status_code == 400 and "300/280" in r.json()["error"]  # errors can't be overridden
    res, msg = mcp_call(human, tok, "preview_post", body)
    assert res["isError"] and msg == r.json()["error"]
    assert fake.created == []


def test_warnings_need_an_explicit_override_and_then_match_across_surfaces(human, fake):
    tok = agent_token(human, "trusted")
    post = {"channel_id": "ch_ig", "text": "Launch", "mode": "queue", "media_url": "https://cdn.example.com/a.png"}
    r = human.post("/api/posts/preview", json=post)
    assert r.status_code == 400 and "override_warnings" in r.json()["error"]
    pv = human.post("/api/posts/preview", json={**post, "override_warnings": True}).json()
    assert pv["preflight"]["status"] == "warning"
    human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    _, pv2 = mcp_call(human, tok, "preview_post", {**post, "override_warnings": True})
    mcp_call(human, tok, "submit_post", {"confirm_token": pv2["confirm_token"]})
    assert len(fake.created) == 2 and fake.created[0] == fake.created[1]


def test_fix_it_returns_a_passing_post_on_rest_and_mcp(human, fake):
    tok = agent_token(human, "trusted")
    long = " ".join(f"Point {i} about the model launch today." for i in range(20))
    rest = human.post("/api/preflight/fix", json={"channel_id": "ch_x", "text": long, "fixes": ["thread"]}).json()
    _, mcp = mcp_call(human, tok, "autofix_post", {"channel_id": "ch_x", "text": long, "fixes": ["thread"]})
    assert rest["text"] == mcp["text"] and rest["preflight"]["status"] == "ok" and rest["applied"] == ["thread"]
    assert rest["before"]["status"] == "error"
    # image fix: Pillow crops/converts and stores new HuhQueue media, which then passes
    fake.media["https://cdn.example.com/tall.png"] = image_bytes(900, 2000)
    img = human.post("/api/preflight/fix", json={"channel_id": "ch_ig", "text": "Launch",
                                                 "media_url": "https://cdn.example.com/tall.png"}).json()
    assert img["preflight"]["status"] == "ok" and img["media_url"].startswith("https://hq.test/m/")
    assert "image:0" in img["applied"]
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_ig", "text": img["text"], "media_url": img["media_url"],
                                                "mode": "queue"})
    assert pv.status_code == 200, pv.text
    m = human.get(img["media_url"].replace("https://hq.test", ""))
    assert m.status_code == 200 and Image.open(io.BytesIO(m.content)).format == "JPEG"
    rules = human.get("/api/preflight/rules?service=instagram").json()
    _, mrules = mcp_call(human, tok, "list_rules", {"network": "instagram"})
    assert rules == mrules and all(r["service"] in ("instagram", "*") for r in rules["rules"]) and rules["unverified"]


def test_buffer_200_with_an_error_is_a_failure_everywhere(human, fake):
    fake.reject_message = "Instagram posts cannot exceed 2196 characters."
    tok = agent_token(human, "trusted")
    post = {"channel_id": "ch_li", "text": "Same post", "mode": "queue"}
    pv = human.post("/api/posts/preview", json=post).json()
    r = human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    assert r.status_code == 422 and r.json()["error"] == fake.reject_message
    _, pv = mcp_call(human, tok, "preview_post", post)
    res, msg = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert res["isError"] and msg == fake.reject_message
    row = human.get("/api/audit").json()[0]
    assert row["action"] == "submit_post" and not row["ok"] and row["detail"]["error"] == fake.reject_message


def test_edit_post_runs_preflight_before_editpost(human, fake):
    r = human.patch("/api/posts/p9", json={"text": "#a " * 10 + "x" * 300})
    assert r.status_code == 400 and "Preflight" in r.json()["error"]
    tok = agent_token(human, "trusted")
    res, msg = mcp_call(human, tok, "update_post", {"post_id": "p9", "text": "x" * 300})
    assert res["isError"] and "300/280" in msg
    assert fake.edits == []


def test_approval_rechecks_right_before_createpost(human, fake):
    tok = agent_token(human, "review")
    post = {"channel_id": "ch_li", "text": "Agent post", "mode": "queue", "media_url": "https://cdn.example.com/a.png"}
    _, pv = mcp_call(human, tok, "preview_post", post)
    _, held = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    pf._CACHE.clear()
    fake.media.clear()  # the image went away while the post waited for approval
    r = human.post(f"/api/approvals/{held['approval_id']}/approve", json={})
    assert r.status_code == 400 and "couldn't download" in r.json()["error"] and fake.created == []
