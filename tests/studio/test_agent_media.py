"""agent-core x studio.media: find_media -> render_reel(style) asks for rights, then renders a real styled Reel through the
media_render job (local worker mode), and the GitHub render worker routes media_render jobs to studio.media.worker."""
import json
import shutil

import httpx
import pytest

from studio import render_worker
from studio.media import ff, tools as media_tools
from studio.media import worker as media_worker

from .test_agent_core import TWEET, owner  # noqa: F401  (the owner fixture: recorded X oEmbed, FakeBuffer, sync brain)

CAND = {"url": "https://x.com/AarushYadav47/status/2116198463847408066", "platform": "x", "kind": "video", "how": "direct_post",
        "confidence": 0.97, "downloadable": True, "official": False, "credit": "Video: @AarushYadav47 on X", "uploader": "Aarush Yadav"}
FOUND = {"best_video": CAND, "best_downloadable": CAND, "candidates": [CAND], "images": [], "note": ""}


@pytest.fixture
def found(monkeypatch):
    q = []
    monkeypatch.setattr(media_tools, "media_find", lambda query, max_results=5, http=None: q.append(query) or json.loads(json.dumps(FOUND)))
    return q


def _start(owner, prompt):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": prompt, "channel_ids": ["ch_yt"], "format": "auto"}).json()
    assert r["kind"] == "agent", r
    return r["id"], owner.get(f"/api/agent/runs/{r['id']}").json()


def test_named_style_finds_the_launch_video_and_asks_before_using_it(owner, found):  # noqa: F811
    rid, run = _start(owner, f"post this on youtube {TWEET} as an opus clip")
    steps = [s["tool"] for s in run["steps"]]
    assert "find_media" in steps and steps.index("find_media") < steps.index("render_reel")
    assert found and found[0] == TWEET  # the person's link is what the finder looks up
    res = run["result"]
    assert res["found_media"]["best_downloadable"]["url"] == CAND["url"]
    assert "right to repost" in res["rights_question"] and "@AarushYadav47" in res["rights_question"]
    # nothing third-party was rendered without the OK: YouTube has slides for now, and no media_render job exists
    jobs = owner.app.state.store.q("SELECT spec FROM render_jobs")
    assert not any(json.loads(j["spec"])["kind"] == "media_render" for j in jobs)
    reel = next(s for s in run["steps"] if s["tool"] == "render_reel")
    assert "OK first" in json.dumps(reel)


def test_unstyled_prompt_keeps_the_standard_reel(owner, found):  # noqa: F811
    _, run = _start(owner, f"post this on youtube {TWEET}")
    assert "find_media" not in [s["tool"] for s in run["steps"]] and not found


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
def test_confirmed_style_renders_a_real_reel_through_studio_media(owner, found, monkeypatch, tmp_path):  # noqa: F811
    rid, _ = _start(owner, f"post this on youtube {TWEET} as an opus clip")
    clip = tmp_path / "clip.mp4"
    ff.run(["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3", "-f", "lavfi", "-i", "sine=f=330:d=3",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)])
    fetched = []

    def fetch(cand, run_dir=None, http=None):
        fetched.append(cand["url"])
        dst = f"{run_dir}/source.mp4"
        shutil.copy(clip, dst)
        return {"path": dst, "credit": cand.get("credit", "")}

    def transcribe(path, names=None, model=None, run_dir=None):
        wp = f"{run_dir}/words.json"
        words = [{"w": w, "s": 0.2 + i * 0.4, "e": 0.55 + i * 0.4} for i, w in enumerate("Glasses that hear silent speech".split())]
        open(wp, "w").write(json.dumps({"words": words}))
        return {"words_path": wp, "words": len(words)}
    monkeypatch.setattr(media_tools, "media_fetch", fetch)
    monkeypatch.setattr(media_tools, "media_transcribe", transcribe)
    out = owner.post(f"/api/agent/runs/{rid}/tools/render_reel",
                     json={"channel_ids": ["ch_yt"], "style": "opus", "rights_confirmed": True}).json()
    assert out["format"] == "reel" and out["media"][0].endswith(".mp4"), out
    assert fetched == [CAND["url"]] and "needs_confirmation" not in out
    row = owner.app.state.store.q("SELECT spec, status, media_token FROM render_jobs ORDER BY id DESC LIMIT 1")[0]
    spec = json.loads(row["spec"])
    assert row["status"] == "done" and spec["kind"] == "media_render" and spec["style"] == "opus" and spec["rights_confirmed"]
    assert spec["credit"] == CAND["credit"] and "_job_token" not in json.dumps(spec.get("candidate"))
    h = owner.app.state.house
    uid = owner.app.state.store.q("SELECT user_id FROM agent_runs")[0]["user_id"]
    from studio import house as house_mod
    meta = h.media_meta(house_mod.Actor(uid), row["media_token"])
    assert meta["renderer"] == "studio.media" and meta["style"] == "opus" and (meta["width"], meta["height"]) == (1080, 1920)
    assert meta["captions"] is True and meta["caption_problems"] == []


def test_find_media_tool_returns_the_rights_flag(owner, found):  # noqa: F811
    rid, _ = _start(owner, f"post this on youtube {TWEET}")
    out = owner.post(f"/api/agent/runs/{rid}/tools/find_media", json={}).json()
    assert out["best_downloadable"]["credit"] == CAND["credit"] and out["needs_rights_confirmation"] is True


def test_generate_images_tool_stores_labelled_images(owner, monkeypatch):  # noqa: F811
    for k in ("POLLINATIONS_KEY", "HF_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    rid, _ = _start(owner, f"post this on youtube {TWEET}")
    out = owner.post(f"/api/agent/runs/{rid}/tools/generate_images", json={"n": 2}).json()
    assert len(out["images"]) == 2 and out["providers"] == ["card"] and out["credit"] == "Graphic: brand card"


def test_render_worker_routes_media_render_jobs_to_studio_media(monkeypatch):
    spec = {"kind": "media_render", "style": "news", "hook": "H", "rights_confirmed": True, "credit": "Video: X", "kit": {}}
    posted = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"id": 7, "spec": spec, "urls": {"logo": "", "source": "https://app.test/m/src", "slides": []}})
        posted.update(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})
    real = httpx.Client
    monkeypatch.setattr(render_worker.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("JOB_TOKEN", "jt_test")
    got = {}
    monkeypatch.setattr(media_worker, "render_job", lambda s, get, urls=None, logo=None, http=None:
                        got.update(spec=s, urls=urls) or (b"MP4", {"type": "video", "renderer": "studio.media"}))
    assert render_worker.main(["--job", "7", "--app", "https://app.test"]) == 0
    assert got["spec"]["style"] == "news" and got["urls"]["source"] == "https://app.test/m/src"
    assert posted["meta"]["renderer"] == "studio.media" and posted["data"]


def test_captioned_render_reel_uses_the_active_brand_kits_caption_style(owner, monkeypatch):  # noqa: F811
    """render_reel(captions=True) with no look named: the plain Reel until a kit is active, then the active kit's caption
    style (the same default edit_video uses), mapped to its edit style ("news" -> the Newsroom look, huhbyte_news)."""
    from studio import agent_tools
    calls = []
    monkeypatch.setattr(agent_tools.Tools, "_has_video", lambda self, run: True)
    monkeypatch.setattr(agent_tools.Tools, "_set_media", lambda self, *a, **k: None)
    monkeypatch.setattr(agent_tools.Tools, "_reel", lambda self, run, **k: calls.append(("plain", None)) or ([], "plain"))
    monkeypatch.setattr(agent_tools.Tools, "_media_reel",
                        lambda self, run, style, **k: calls.append(("styled", style)) or ([], "styled", ""))
    rid, _ = _start(owner, f"post this on youtube {TWEET}")
    calls.clear()
    owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "captions": True})
    assert calls == [("plain", None)], "no kit was ever activated: the standard Reel"
    kid = owner.post("/api/brand-kits", json={"name": "Calm", "caption_style": "news", "activate": True}).json()["id"]
    calls.clear()
    out = owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "captions": True}).json()
    assert calls == [("styled", "news")] and out["style"] == "news"
    owner.patch(f"/api/brand-kits/{kid}", json={"caption_style": "hype"})
    calls.clear()
    owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "captions": True})
    assert calls == [("styled", "hype")]
    calls.clear()
    owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "captions": False})
    assert calls == [("plain", None)], "no captions asked: the kit's caption style isn't forced on"
    calls.clear()
    owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "captions": True, "style": "cinematic"})
    assert calls == [("styled", "cinematic")], "a named look beats the kit"
