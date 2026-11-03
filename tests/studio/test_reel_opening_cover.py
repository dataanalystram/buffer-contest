"""Owner bug, 3 Nov 2026 (the Click "managed agents API for any model" reel): played from 0.00 the Reel showed the chosen
cover for 0.6 s, then the theme's title card with the same title for another 0.5-0.9 s, then the video; on every loop the
cover showed again. Buffer's API takes no cover image for a video (VideoAssetInput.thumbnailUrl is rejected; only
metadata.thumbnailOffset picks a frame), so the cover stays in the video, but as ONE short opening still that the
thumbnailOffset points at, never followed by a second title still. The MCP re-render of that reel also stacked a second end
card (it re-rendered the finished reel). Frame evidence: work/reelcover/ (scripts/reel_opening_audit.sh)."""
import re
import shutil
import subprocess

import pytest
from PIL import Image

from studio import actions
from studio.actions import ActionError, Actor, build_post_input, clean_options
from studio.library import parse_range, range_unsatisfiable
from studio.media import compose, ff

needs_ff = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")


def _clip(tmp_path, secs=4):
    p = tmp_path / "clip.mp4"
    ff.run(["-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={secs}", "-f", "lavfi", "-i", f"sine=f=330:d={secs}",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(p)])
    return str(p)


def _stills(path: str, start: float = 0.0, dur: float | None = None, min_d: float = 0.15) -> list[tuple[float, float]]:
    """(start, duration) of every held still (freezedetect) in [start, start+dur)."""
    cmd = ["ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-i", path]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vf", f"freezedetect=n=0.003:d={min_d}", "-an", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stderr
    starts = [float(x) for x in re.findall(r"freeze_start: ([0-9.]+)", err)]
    durs = [float(x) for x in re.findall(r"freeze_duration: ([0-9.]+)", err)]
    total = dur if dur else ff.probe(path)["duration"] - start
    out = []
    for i, s in enumerate(starts):
        out.append((round(start + s, 3), round(durs[i] if i < len(durs) else total - s, 3)))
    return out


def _cuts(path: str, dur: float = 3.0) -> list[float]:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-t", f"{dur}", "-i", path, "-vf", "select='gt(scene,0.25)',showinfo", "-an",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=300).stderr
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)]


@needs_ff
def test_cover_is_one_short_opening_still_never_followed_by_the_title_card(tmp_path):
    src = _clip(tmp_path)
    cover = tmp_path / "cover.png"
    Image.new("RGB", (1080, 1920), (201, 172, 250)).save(cover)
    m = compose.render_style(src, "huhbyte_news", out=str(tmp_path / "o.mp4"), hook="Managed AI agents for any model",
                             facts=["One API for any model", "Sandbox and browser built in"], credit="Video: Click",
                             cover_png=str(cover), music_mood="none", preset="ultrafast", crf=32, burned_subs=False)
    # one opening still: the cover frame, and no theme title card after it (huhbyte_news has a 0.9 s title card otherwise)
    assert m["cover_frame_secs"] == compose.COVER_FRAME_SECS <= 0.25 and m["cover_secs"] == 0
    assert m["thumbnail_offset_ms"] == compose.cover_thumb_ms(compose.COVER_FRAME_SECS) == 100
    assert 0 < m["thumbnail_offset_ms"] < m["cover_frame_secs"] * 1000  # Buffer's thumbnailOffset lands on the cover
    want = compose.COVER_FRAME_SECS + 4 + m["facts_secs"] + compose.CARD_SECS
    assert abs(m["duration"] - want) < 0.25, (m["duration"], want)
    head = _stills(m["path"], 0, 3.0)
    assert head and head[0][0] == 0 and head[0][1] <= 0.25, head  # the cover: at most 0.25 s
    assert all(s[1] < 0.15 for s in head[1:]), f"a second still segment in the first 3 s: {head}"
    cuts = [c for c in _cuts(m["path"]) if c > 0.05]
    assert len(cuts) == 1 and cuts[0] <= 0.25, cuts  # cover -> video, nothing in between
    px = Image.open(compose.frame_at(m["path"], 0.1, str(tmp_path / "f0.png"))).convert("RGB").getpixel((540, 960))
    assert abs(px[0] - 201) < 14 and abs(px[2] - 250) < 14  # the frame the thumbnailOffset points at is the cover
    # the end: the facts card then ONE end card (never two end cards)
    tail = [s for s in _stills(m["path"], max(0.0, m["duration"] - 9.0)) if s[1] >= 1.0]
    assert len(tail) == 2, tail  # the facts card, then the end card
    last = Image.open(compose.frame_at(m["path"], m["duration"] - 0.3, str(tmp_path / "last.png"))).convert("L").resize((54, 96))

    def same(t):
        im = Image.open(compose.frame_at(m["path"], t, str(tmp_path / f"s{t:.2f}.png"))).convert("L").resize((54, 96))
        return sum(abs(x - y) for x, y in zip(im.getdata(), last.getdata())) / (54 * 96) < 6
    assert sum(1 for st, d in tail if same(st + d / 2)) == 1, tail  # the end card shows once


@needs_ff
def test_without_a_cover_the_theme_title_card_still_opens_the_video(tmp_path):
    src = _clip(tmp_path, secs=3)
    m = compose.render_style(src, "huhbyte_news", out=str(tmp_path / "t.mp4"), hook="Managed AI agents for any model",
                             facts=["One API"], credit="Video: Click", music_mood="none", preset="ultrafast", crf=32, burned_subs=False)
    assert m["cover_secs"] > 0 and m["cover_frame_secs"] == 0 and m["thumbnail_offset_ms"] is None


def test_cover_frame_length_is_clamped_to_a_few_frames():
    assert compose.cover_frame_secs(None) == compose.COVER_FRAME_SECS
    assert compose.cover_frame_secs(0.6) == 0.25 and compose.cover_frame_secs(0.0) == 0.1 and compose.cover_frame_secs("x") == 0.2


# ---------------------------------------------------------------------------------------------- the cover reaches Buffer
IG = {"id": "ig", "service": "instagram"}
FB = {"id": "fb", "service": "facebook"}
VIDEO = "https://hq.example/m/abc.mp4"


def test_cover_goes_to_buffer_as_the_thumbnail_offset_never_a_thumbnail_url():
    opts = clean_options({"thumbnail_offset_ms": compose.cover_thumb_ms(compose.COVER_FRAME_SECS)})
    inp = build_post_input(IG, "hi", VIDEO, "draft", None, None, opts)
    v = inp["assets"][0]["video"]
    assert v == {"url": VIDEO, "metadata": {"thumbnailOffset": 100}}  # Buffer rejects video thumbnailUrl
    assert inp["metadata"]["instagram"] == {"type": "reel", "shouldShareToFeed": True}  # a video is a Reel, said explicitly
    li = build_post_input({"id": "li", "service": "linkedin"}, "hi", VIDEO, "draft", None, None, opts)
    assert "metadata" not in li["assets"][0]["video"]  # thumbnailOffset only where Buffer honours it


def test_meta_post_types_first_comment_and_youtube_kids_flag():
    reel = build_post_input(FB, "hi", VIDEO, "draft", None, None, clean_options({"fb_type": "reel"}))
    assert reel["metadata"]["facebook"]["type"] == "reel"
    img = build_post_input(FB, "hi", "https://hq.example/m/a.jpg", "draft", None, None, clean_options({"fb_type": "reel"}))
    assert img["metadata"]["facebook"]["type"] == "post"  # an image can't be a Reel
    plain = build_post_input(FB, "hi", VIDEO, "draft", None)
    assert plain["metadata"]["facebook"]["type"] == "post"  # no fb_type: unknown shape, a feed video
    story = build_post_input(IG, "hi", VIDEO, "draft", None, None, clean_options({"ig_type": "story"}))
    assert story["metadata"]["instagram"]["type"] == "story"
    photo = build_post_input(IG, "hi", "https://hq.example/m/a.jpg", "draft", None)
    assert photo["metadata"]["instagram"]["type"] == "post"
    fc = build_post_input(IG, "hi", VIDEO, "draft", None, None, clean_options({"first_comment": "#ai " + "x" * 400}))
    assert fc["metadata"]["instagram"]["firstComment"].startswith("#ai ") and len(fc["metadata"]["instagram"]["firstComment"]) > 300
    x = build_post_input({"id": "x", "service": "twitter"}, "hi", "", "draft", None, None, clean_options({"first_comment": "c"}))
    assert "metadata" not in x  # X has no firstComment in Buffer's schema
    yt = build_post_input({"id": "y", "service": "youtube"}, "Title\nbody", VIDEO, "draft", None, None, {"category_id": "28"})
    assert yt["metadata"]["youtube"] == {"title": "Title", "categoryId": "28", "madeForKids": False}


# ---------------------------------------------------------------------------------------------- MCP re-render of a reel
class _H:
    def __init__(self, metas):
        self.metas = metas

    def media_meta(self, a, tok):
        return self.metas.get(tok)


def test_edit_video_of_a_finished_reel_starts_from_its_original_upload():
    from studio.mcp_client_brain import _unfinished
    a = Actor("u1")
    up = {"kind": "upload", "type": "video", "duration": 40}
    reel = {"kind": "render", "type": "video", "renderer": "studio.media", "cover_frame_secs": 0.6, "source_token": "up.mp4"}
    h = _H({"up.mp4": up, "reel.mp4": reel, "reel2.mp4": {**reel, "source_token": "reel.mp4"}})
    tok, meta = _unfinished(h, a, "reel.mp4", reel, {})
    assert tok == "up.mp4" and meta["_from_finished"] == "reel.mp4"
    assert _unfinished(h, a, "reel2.mp4", h.metas["reel2.mp4"], {})[0] == "up.mp4"  # a re-render of a re-render too
    assert _unfinished(h, a, "up.mp4", up, {}) == ("up.mp4", up)  # an upload is edited as it is
    old = {"kind": "render", "type": "video", "renderer": "studio.media", "cover_frame_secs": 0.6}  # before source_token
    with pytest.raises(ActionError) as e:
        _unfinished(h, a, "old.mp4", old, {})
    assert e.value.status == 409 and "second end card" in str(e.value)
    assert _unfinished(h, a, "old.mp4", old, {"rerender_finished": True})[0] == "old.mp4"


# ---------------------------------------------------------------------------------------------- /m/ and Range
def test_range_rules_never_refuse_a_legit_get():
    assert parse_range("bytes=0-10", 100) == (0, 10)
    assert not range_unsatisfiable("bytes=0-10,20-30", 100)  # multi-range: serve the whole file (200)
    assert not range_unsatisfiable("items=0-1", 100) and not range_unsatisfiable("bytes=abc", 100)
    assert range_unsatisfiable("bytes=100-", 100) and range_unsatisfiable("bytes=500-600", 100)


def test_media_url_serves_multi_and_odd_ranges_whole(human):
    tok = human.app.state.house.put_media(Actor(human.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]),
                                          b"\x00" * 5000, "reel.mp4", kind="render", meta={"type": "video"})
    anon = human.__class__(human.app, base_url=str(human.base_url))  # a fetcher with no session (Buffer, an agent's proxy)
    for hdr in ({"Range": "bytes=0-10,20-30"}, {"Range": "items=0-5"}, {"User-Agent": ""}, {"User-Agent": "python-httpx/0.28"}):
        r = anon.get(f"/m/{tok}", headers=hdr)
        assert r.status_code == 200 and len(r.content) == 5000, (hdr, r.status_code)
    assert anon.get(f"/m/{tok}", headers={"Range": "bytes=0-9"}).status_code == 206
    assert anon.get(f"/m/{tok}", headers={"Range": "bytes=9999-"}).status_code == 416
    assert anon.head(f"/m/{tok}").status_code == 200
    assert actions.THUMB_OFFSET_SERVICES == ("instagram", "tiktok", "pinterest")


# ---------------------------------------------------------------------------------------------- createPost idempotency
def test_create_post_timeout_finds_the_post_buffer_already_made_instead_of_a_second_draft():
    from datetime import datetime, timezone

    import httpx

    from studio.buffer_api import BufferClient, BufferError
    calls = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    class Store:
        def user(self, uid):
            return {"org_id": "org1"}

    def gql(q, variables=None, uid=None, **k):
        calls.append(q.split("(")[0].strip())
        if "createPost" in q:
            raise httpx.ReadTimeout("lost answer")
        return {"posts": {"edges": [{"node": {"id": "p_old", "text": "other", "status": "draft", "channelId": "ig", "createdAt": now}},
                                    {"node": {"id": "p1", "text": "hi", "status": "draft", "channelId": "ig", "createdAt": now}}]}}
    c = BufferClient.__new__(BufferClient)
    c.store, c.gql, c._drop_posts = Store(), gql, lambda uid=None: None
    inp = {"channelId": "ig", "text": "hi", "mode": "addToQueue", "saveToDraft": True, "assets": []}
    assert c.create_post("u1", inp)["id"] == "p1"
    assert calls == ["mutation", "query"]  # one createPost, one read: never a second createPost

    def gql_none(q, variables=None, uid=None, **k):
        if "createPost" in q:
            raise BufferError("Buffer returned HTTP 502", 502)
        return {"posts": {"edges": []}}
    c.gql = gql_none
    with pytest.raises(BufferError):
        c.create_post("u1", inp)  # nothing was made: the failure is reported, the caller may retry

    def gql_refused(q, variables=None, uid=None, **k):
        raise BufferError("Buffer could not complete the request (BAD_USER_INPUT: x)")
    c.gql = gql_refused
    with pytest.raises(BufferError):
        c.create_post("u1", inp)  # a refusal is not looked up
