"""The media agent's tool interface (studio/media/tools.py): specs, dispatch, the rights gate, style aliases, and the
light tools end to end. Heavy rendering is covered by test_media_render.py; here compose.render_style is stubbed."""
import json
import wave

import pytest

from studio.media import compose, styles, tools

STYLE_IDS = ["opus", "news", "minimal", "hype"]


def test_specs_are_valid_tool_definitions():
    names = [s["name"] for s in tools.SPECS]
    assert len(names) == len(set(names)) == len(tools.NAMES)
    assert {"media_find", "media_render", "media_generate_images", "media_music", "media_plan"} <= set(names)
    for s in tools.SPECS:
        assert s["description"] and isinstance(s["worker"], bool)
        sch = s["input_schema"]
        assert sch["type"] == "object"
        for r in sch.get("required", []):
            assert r in sch["properties"], (s["name"], r)
        json.dumps(s)  # plain JSON, so the brain / REST / MCP layers can hand it to a model as-is
    heavy = {s["name"] for s in tools.SPECS if s["worker"]}
    assert {"media_render", "media_fetch", "media_transcribe", "media_generate_images", "media_music"} == heavy
    enum = next(s for s in tools.SPECS if s["name"] == "media_render")["input_schema"]["properties"]["style"]["enum"]
    assert set(STYLE_IDS) <= set(enum)


def test_style_aliases_and_pick():
    assert styles.resolve("news") == "huhbyte_news" and styles.resolve("Opus") == "opus"
    assert styles.resolve("glitter") == "glitter"  # unknown names pass through to be refused
    assert styles.pick("make it a short punchy explainer") == "opus"
    assert styles.pick("calm tutorial for linkedin") == "minimal"
    assert styles.pick("hype it up") == "hype"
    assert styles.pick("big announcement") == "huhbyte_news"
    got = tools.call("media_styles")["styles"]
    assert [s["id"] for s in got] == ["opus", "huhbyte_news", "minimal", "hype"]


def test_unknown_tool_is_a_clear_error():
    with pytest.raises(KeyError, match="media_render"):
        tools.call("media_explode", {})


def test_plan_maps_channels_to_ratios_without_a_link():
    p = tools.call("media_plan", {"prompt": "short punchy 30s explainer about glasses", "channels": ["instagram_reel", "tiktok", "linkedin", "x"]})
    assert p["style"] == "opus" and p["target_seconds"] == 30 and p["links"] == []
    assert p["ratios"] == {"9:16": ["instagram_reel", "tiktok"], "4:5": ["linkedin"], "1:1": ["x"]}
    assert "media" not in p  # no link: nothing to find


def test_plan_with_a_link_finds_media_and_asks_for_rights(monkeypatch):
    vid = {"url": "https://x.com/a/status/1", "platform": "x", "kind": "video", "how": "x_post", "confidence": 0.9,
           "downloadable": True, "official": False, "credit": "Video: @a on X"}
    monkeypatch.setattr(tools, "find_launch_video", lambda q, http=None, max_results=6: [vid])
    p = tools.call("media_plan", {"prompt": "post about this https://x.com/a/status/1"})
    assert p["media"]["best_downloadable"]["url"] == vid["url"]
    assert p["needs_rights_confirmation"] is True
    assert p["next"].startswith("media_fetch")


def test_find_notes_when_the_launch_video_cant_be_fetched(monkeypatch):
    yt = {"url": "https://youtu.be/x", "kind": "video", "how": "yc", "confidence": 0.95, "downloadable": False, "blocked": "bot check"}
    mp4 = {"url": "https://cdn.example/launch.mp4", "kind": "video", "how": "og_video", "confidence": 0.8, "downloadable": True, "official": True}
    img = {"url": "https://cdn.example/a.jpg", "kind": "image", "how": "og_image", "confidence": 0.5}
    monkeypatch.setattr(tools, "find_launch_video", lambda q, http=None, max_results=6: [yt, mp4, img])
    r = tools.call("media_find", {"query": "subvocal launch"})
    assert r["best_video"]["url"] == yt["url"] and r["best_downloadable"]["url"] == mp4["url"]
    assert "bot check" in r["note"] and r["images"][0]["url"] == img["url"]
    monkeypatch.setattr(tools, "find_launch_video", lambda q, http=None, max_results=6: [])
    r = tools.call("media_find", {"query": "nothing"})
    assert r["best_video"] is None and "media_generate_images" in r["note"]


@pytest.fixture
def fake_render(monkeypatch):
    calls = []

    def render_style(source, style, *, out, ratio="9:16", **kw):
        calls.append({"source": source, "style": style, "out": out, "ratio": ratio, **kw})
        w, h = compose.SIZES[ratio]
        return {"path": out, "ratio": ratio, "width": w, "height": h, "duration": 9.0, "clip": [0, 5, "given"], "captions": True,
                "burned_subs": False, "music": "pulse", "caption_problems": []}
    monkeypatch.setattr(compose, "render_style", render_style)
    return calls


def test_render_needs_rights_and_credit_for_third_party_video(fake_render, tmp_path):
    base = {"style": "opus", "hook": "Glasses that hear silent speech", "source_path": "/tmp/v.mp4", "run_dir": str(tmp_path)}
    r = tools.call("media_render", base)
    assert r["needs_confirmation"] and "right to repost" in r["question"] and not fake_render
    r = tools.call("media_render", {**base, "rights_confirmed": True})
    assert "credit" in r["error"].lower() and not fake_render
    r = tools.call("media_render", {**base, "style": "glitter", "rights_confirmed": True, "credit": "Video: X"})
    assert "Unknown style" in r["error"]


def test_render_every_ratio_with_the_news_alias(fake_render, tmp_path):
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": [{"w": "hi", "s": 0.1, "e": 0.4}]}))
    r = tools.call("media_render", {"style": "news", "hook": "Hook", "source_path": "/tmp/v.mp4", "credit": "Video: @a on X",
                                    "rights_confirmed": True, "ratios": ["9:16", "4:5", "1:1"], "words_path": str(words),
                                    "clip": [28, 67], "run_dir": str(tmp_path), "junk_arg": 1})
    assert [x["ratio"] for x in r["renders"]] == ["9:16", "4:5", "1:1"]
    assert [(x["width"], x["height"]) for x in r["renders"]] == [(1080, 1920), (1080, 1350), (1080, 1080)]
    assert all(c["style"] == "huhbyte_news" and c["clip"] == (28, 67) and c["words"][0]["w"] == "hi" for c in fake_render)
    assert fake_render[0]["out"].endswith("huhbyte_news_9x16.mp4")


def test_official_video_and_images_need_no_confirmation(fake_render, tmp_path):
    r = tools.call("media_render", {"style": "minimal", "hook": "h", "source_path": "/tmp/v.mp4", "official": True,
                                    "credit": "Video: Subvocal", "run_dir": str(tmp_path)})
    assert len(r["renders"]) == 1
    r = tools.call("media_render", {"style": "hype", "hook": "h", "images": ["/tmp/a.jpg"], "run_dir": str(tmp_path)})
    assert fake_render[-1]["source"] is None and fake_render[-1]["images"] == ["/tmp/a.jpg"]


def test_generate_images_falls_back_to_the_brand_card(monkeypatch, tmp_path):
    for k in ("POLLINATIONS_KEY", "HF_TOKEN", "HQ_POLLINATIONS_LEGACY"):
        monkeypatch.delenv(k, raising=False)
    r = tools.call("media_generate_images", {"prompt": "silent speech glasses", "headline": "Talk without talking", "n": 2,
                                             "ratio": "4:5", "run_dir": str(tmp_path)})
    assert len(r["images"]) == 2 and all(i["provider"] == "card" for i in r["images"])
    from PIL import Image
    assert Image.open(r["images"][0]["path"]).size == (1080, 1350)


def test_music_is_a_real_wav_of_the_asked_length(tmp_path):
    r = tools.call("media_music", {"seconds": 3, "mood": "pulse", "run_dir": str(tmp_path)})
    with wave.open(r["path"]) as w:
        assert abs(w.getnframes() / w.getframerate() - 3) < 0.6
    assert "royalty-free" in r["licence"]


# ------------------------------------------------------------------ the worker job (studio/media/worker.py)
from studio.media import worker  # noqa: E402

KIT = {"name": "HuhByte AI", "handle": "huhbyte.ai", "colors": {"primary": "#0E0E10", "accent": "#FF5A1F", "text": "#FFFFFF"},
       "end_card": {"on": True, "line": "AI news, simply.", "cta": "Follow"}}


def test_brand_from_kit_maps_agent_core_kit():
    b = worker.brand_from_kit(KIT, "/tmp/logo.png")
    assert b == {"name": "HuhByte AI", "handle": "@huhbyte.ai", "accent": "#FF5A1F", "bg": "#0E0E10", "text": "#FFFFFF",
                 "end_line": "AI news, simply.", "logo": "/tmp/logo.png"}
    assert worker.brand_from_kit({}) == {}


@pytest.fixture
def stub_tools(monkeypatch, tmp_path):
    seen = {}

    def fetch(cand, run_dir=None, http=None):
        p = tmp_path / "fetched.mp4"
        p.write_bytes(b"v")
        seen["fetch"] = cand
        return {"path": str(p), "credit": cand.get("credit", "")}

    def transcribe(path, names=None, model=None, run_dir=None):
        seen["transcribe"] = path
        wp = tmp_path / "w.json"
        wp.write_text(json.dumps({"words": [{"w": "x", "s": 0, "e": 1}]}))
        return {"words_path": str(wp), "words": 1}

    def render(style, hook, **kw):
        seen["render"] = {"style": style, "hook": hook, **kw}
        if kw.get("source_path") and not (kw.get("rights_confirmed") or kw.get("official")):
            return {"needs_confirmation": True, "question": "OK to use it?"}
        out = tmp_path / "out.mp4"
        out.write_bytes(b"MP4DATA")
        return {"renders": [{"path": str(out), "ratio": kw["ratios"][0], "width": 1080, "height": 1920, "duration": 12.0,
                             "clip": [0, 9, "given"], "captions": True, "burned_subs": False, "music": "pulse", "caption_problems": []}]}
    monkeypatch.setattr(tools, "media_fetch", fetch)
    monkeypatch.setattr(tools, "media_transcribe", transcribe)
    monkeypatch.setattr(tools, "media_render", render)
    return seen


def test_job_with_an_uploaded_source(stub_tools):
    spec = {"kind": "media_render", "style": "news", "hook": "Hook", "credit": "Video: @a on X", "rights_confirmed": True, "kit": KIT}
    data, meta = worker.render_job(spec, get=lambda u: b"SRC" if u == "https://app/m/src" else None,
                                   urls={"source": "https://app/m/src"}, logo=b"PNG")
    assert data == b"MP4DATA" and meta["style"] == "huhbyte_news" and meta["renderer"] == "studio.media"
    r = stub_tools["render"]
    assert r["style"] == "huhbyte_news" and r["words_path"] and r["brand"]["handle"] == "@huhbyte.ai" and r["brand"]["logo"]
    assert "fetch" not in stub_tools


def test_job_fetches_the_found_candidate_and_keeps_its_credit(stub_tools):
    cand = {"url": "https://cdn.example/launch.mp4", "official": True, "credit": "Video: Subvocal"}
    data, meta = worker.render_job({"style": "opus", "hook": "H", "candidate": cand}, get=lambda u: None)
    assert stub_tools["fetch"]["url"] == cand["url"] and meta["credit"] == "Video: Subvocal" and stub_tools["render"]["official"]


def test_job_refuses_unconfirmed_third_party_video(stub_tools):
    with pytest.raises(worker.JobError, match="Rights not confirmed"):
        worker.render_job({"style": "hype", "hook": "H", "candidate": {"url": "https://x.com/a/status/1", "credit": "Video: @a"}},
                          get=lambda u: None)


def test_job_without_media_generates_images(stub_tools, monkeypatch):
    for k in ("POLLINATIONS_KEY", "HF_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    data, meta = worker.render_job({"style": "minimal", "hook": "Talk without talking", "kit": KIT}, get=lambda u: None)
    r = stub_tools["render"]
    assert r["source_path"] is None and len(r["images"]) == 3 and meta["credit"] == "Graphic: brand card"
    assert "transcribe" not in stub_tools
