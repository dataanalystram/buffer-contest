"""Owner bugs, 2 Nov 2026 ("make sure no bugs"). One regression test (or more) per bug."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _wi():
    spec = importlib.util.spec_from_file_location("worker_imports", ROOT / "scripts" / "worker_imports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reqs(name: str) -> set[str]:
    out = set()
    for line in (ROOT / name).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.add(re.split(r"[<>=\[ ;]", line, 1)[0].lower())
    return out


# ---- Bug 1: the worker venv lacked `cryptography` (Actions run 41363638009) ----

def test_worker_reaches_store_and_needs_cryptography():
    mods, third, hard = _wi().scan()
    assert "studio.takes" in mods and "studio.store" in mods
    assert "cryptography" in hard


def test_worker_requirements_cover_every_reachable_import():
    wi = _wi()
    _, third, hard = wi.scan()
    full, core = _reqs("requirements-worker.txt"), _reqs("requirements-worker-core.txt")
    need = {wi.PIP_NAME.get(t, t).lower() for t in third if t not in wi.WEB_ONLY}
    assert "cryptography" in full and "cryptography" in core
    assert need <= full, f"requirements-worker.txt is missing {sorted(need - full)}"
    hard_need = {wi.PIP_NAME.get(t, t).lower() for t in hard if t not in wi.WEB_ONLY}
    assert hard_need <= core, f"requirements-worker-core.txt is missing {sorted(hard_need - core)}"


def test_worker_workflows_install_from_the_checked_files():
    take = (ROOT / ".github/workflows/take.yml").read_text()
    render = (ROOT / ".github/workflows/render.yml").read_text()
    ci = (ROOT / ".github/workflows/studio-ci.yml").read_text()
    assert "-r requirements-worker.txt" in take and "-r requirements-worker-core.txt" in take
    assert "-r requirements-worker-core.txt" in render
    assert "worker_imports.py --import" in ci  # the clean-venv import job


def test_scan_follows_function_level_imports(tmp_path, monkeypatch):
    wi = _wi()
    pkg = tmp_path / "studio"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    from .b import g\n")
    (pkg / "b.py").write_text("import somepkg_xyz\ntry:\n    import optional_xyz\nexcept ImportError:\n    pass\n")
    monkeypatch.setattr(wi, "ROOT", tmp_path)
    mods, third, hard = wi.scan(["studio.a"])
    assert "studio.b" in mods
    assert "somepkg_xyz" in hard and "optional_xyz" in third and "optional_xyz" not in hard


# ---- Bug 2: media import from a URL failed in production for the server's own files ----
# Root cause (Render logs + live probes, 2 Nov 2026): a request from the Render service to its own public hostname never
# arrives (no hairpin; no GET /sample/clip4.mp4 from the server in the logs), so /sample/, /static/ and /m/ imports all
# failed. Public third-party URLs worked (avatars.githubusercontent.com, wikimedia); "GitHub images" failed because the
# repo is private (raw.githubusercontent.com answers 404), which the old message hid.

from .conftest import agent_token, mcp_call  # noqa: E402


@pytest.fixture
def no_network(monkeypatch):
    """Any outbound fetch is recorded and fails, as Render's own-host hairpin effectively does."""
    import httpx

    from studio import netguard
    seen = []

    def stream(url, *a, **k):
        seen.append(url)
        raise httpx.ConnectError("test: no network")
    monkeypatch.setattr(netguard, "safe_stream", stream)
    return seen


def test_import_of_own_sample_file_reads_it_locally(human, no_network):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "library_upload_url", {"url": "https://hq.test/sample/clip4.mp4"})
    assert not res["isError"], out
    assert out["type"] == "video" and out["bytes"] == (ROOT / "studio/sample_take/clip4.mp4").stat().st_size
    assert no_network == []  # never went over the network


def test_import_of_own_static_and_media_urls(human, no_network):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "library_upload_url", {"url": "https://hq.test/sample/poster.jpg"})
    assert not res["isError"], out
    res2, out2 = mcp_call(human, tok, "library_upload_url", {"url": out["url"]})  # its own /m/ link
    assert not res2["isError"], out2 and out2["bytes"] == out["bytes"]
    static = next(p for p in (ROOT / "studio/static").rglob("*.png") if p.parent.name != "src")
    rel = static.relative_to(ROOT / "studio/static").as_posix()
    res3, out3 = mcp_call(human, tok, "library_upload_url", {"url": f"https://hq.test/static/{rel}"})
    assert not res3["isError"], out3
    assert no_network == []


def test_own_host_paths_are_confined(human, no_network):
    tok = agent_token(human)
    for u in ("https://hq.test/sample/../store.py", "https://hq.test/sample/sample.json", "https://hq.test/static/../app.py",
              "https://hq.test/m/nope.jpg", "https://hq.test/sample/%2e%2e/house.py"):
        res, out = mcp_call(human, tok, "library_upload_url", {"url": u})
        assert res["isError"], u
        assert "404" in out or "not found" in out.lower(), (u, out)
    assert no_network == []


def test_edit_video_with_own_sample_url_queues(human, no_network, monkeypatch):
    from studio import house as house_mod
    monkeypatch.setattr(house_mod.House, "media_meta", lambda self, a, t: {"type": "video", "duration": 15})
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "edit_video", {"url": "https://hq.test/sample/clip4.mp4", "start_s": 2, "end_s": 10,
                                                   "rights_confirmed": True})
    assert not ("Couldn't download" in str(out)), out


@pytest.mark.parametrize("status,needle", [(404, "404"), (403, "sign-in"), (500, "HTTP 500")])
def test_import_failure_says_why(human, monkeypatch, status, needle):
    from contextlib import contextmanager

    import httpx

    from studio import netguard

    @contextmanager
    def stream(url, *a, **k):
        yield httpx.Response(status, request=httpx.Request("GET", url), content=b"x")
    monkeypatch.setattr(netguard, "safe_stream", stream)
    res, out = mcp_call(human, agent_token(human), "library_upload_url", {"url": "https://raw.githubusercontent.com/o/private/main/a.png"})
    assert res["isError"] and needle in out, out


# ---- Bug 3: edit_video double-captioned a clip that already had captions burned in ----
import shutil  # noqa: E402
import subprocess  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
CLIP4 = ROOT / "studio/sample_take/clip4.mp4"  # a finished 9:16 clip with Opus-style captions burned in at ~75%


def _plain_video(path: Path, text: str | None = None, x: str = "(w-text_w)/2"):
    font = ROOT / "studio/fonts/Inter-Bold.ttf"
    vf = "format=yuv420p"
    if text:
        vf = (f"drawtext=fontfile={font}:text='{text}':x={x}:y=h*0.8:fontsize=48:fontcolor=white:borderw=3:bordercolor=black,"
              + vf)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=8:r=10", "-vf", vf, str(path)],
                   check=True, timeout=120)
    return path


@needs_ffmpeg
def test_detects_captions_already_burned_in():
    from studio.media.compose import detect_burned_captions
    band = detect_burned_captions(str(CLIP4), 0, 30)
    assert band and 0.6 <= band["top"] <= 0.8, band


@needs_ffmpeg
def test_no_captions_and_static_strap_are_not_captions(tmp_path):
    from studio.media.compose import detect_burned_captions
    assert detect_burned_captions(str(_plain_video(tmp_path / "plain.mp4"))) is None
    assert detect_burned_captions(str(_plain_video(tmp_path / "strap.mp4", "Jane Doe - CEO", x="40"))) is None  # static, left


@needs_ffmpeg
def test_render_skips_captions_on_a_captioned_clip_with_a_note(tmp_path):
    from studio.media.compose import render_style
    words = [{"w": w, "s": 2.2 + i * 0.4, "e": 2.5 + i * 0.4} for i, w in enumerate("this clip already has captions".split())]
    out = render_style(str(CLIP4), "opus", out=str(tmp_path / "o.mp4"), ratio="1:1", words=words, clip=(2.0, 5.0),
                       music_mood="none", detect_own_captions=True, credit="Video: Buffer")
    assert out["captions"] is False
    assert "already has captions" in out["captions_note"]
    assert out["source_captions"]["top"] >= 0.6


def test_edit_video_spec_maps_captions_modes(human, monkeypatch):
    from studio import mcp_client_brain as mcb
    assert mcb._captions_mode(None) == "auto" and mcb._captions_mode(True) == "auto"
    assert mcb._captions_mode(False) == "off" and mcb._captions_mode("off") == "off"
    assert mcb._captions_mode("replace") == "replace" and mcb._captions_mode("on") == "on"
    from studio.media.worker import _captions_args
    assert _captions_args({"captions_mode": "auto"}) == {"captions_mode": "auto", "detect_own_captions": True}
    assert _captions_args({"captions_mode": "replace"}) == {"captions_mode": "replace", "detect_own_captions": False}
    assert _captions_args({"captions": True}) == {}  # older specs: unchanged behaviour


def test_edit_video_tool_schema_offers_replace(human):
    r = human.post("/mcp", headers={"Authorization": f"Bearer {agent_token(human)}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    cap = tools["edit_video"]["inputSchema"]["properties"]["captions"]
    assert "replace" in cap["enum"] and "already has captions" in cap["description"].replace("already have captions", "already has captions")


@needs_ffmpeg
def test_render_replace_crops_the_old_captions_and_captions_again(tmp_path):
    from studio.media.compose import render_style
    words = [{"w": w, "s": 2.2 + i * 0.4, "e": 2.5 + i * 0.4} for i, w in enumerate("new captions go here now".split())]
    out = render_style(str(CLIP4), "opus", out=str(tmp_path / "r.mp4"), ratio="1:1", words=words, clip=(2.0, 5.0),
                       music_mood="none", captions_mode="replace", credit="Video: Buffer")
    assert out["captions"] is True and "cropped off" in out["captions_note"]


# ---- Sweep: GET /api/recommendations 500'd when one source's answer couldn't be parsed (ui-smoke log, 2 Nov 2026) ----

def test_one_bad_source_never_500s_the_scan(human, monkeypatch):
    from studio import sources_agent as sa

    def boom(self, cfg, secret, http):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")  # what json.loads raises on an HTML error page
    monkeypatch.setattr(sa.RSS, "live", boom)
    r = human.post("/api/sources/scan", json={"sources": ["rss"]})
    assert r.status_code == 200, r.text
    rss = next(x for x in r.json()["status"] if x["source"] == "rss")
    assert rss["ok"] is False and "unreadable" in rss["error"]


# ---- Bug 4: the ernd carousel cover used the 4x-upscaled logo and wrote "Ernd" (owner, 1-2 Nov 2026) ----

def _brand():
    from studio import templates as T
    return T.Brand({"name": "huh byte", "handle": "huhbyte.ai", "colors": {"primary": "#0b0b0e", "accent": "#c9acfa"}})


def _red_box(im):
    from PIL import Image as _I
    px = im.convert("RGB")
    mask = _I.eval(px.split()[0], lambda v: 255 if v > 200 else 0)
    g = _I.eval(px.split()[1], lambda v: 255 if v < 60 else 0)
    from PIL import ImageChops
    return ImageChops.multiply(mask, g).getbbox()


def test_small_cover_picture_is_an_inset_never_upscaled_past_1_5x():
    from PIL import Image as _I

    from studio import editorial as E
    logo = _I.new("RGB", (200, 200), (230, 20, 20))
    fit = E.Fit()
    s = E.cover(_brand(), logo, "YC F26", "ernd Is The Card For AI", "One line.", fit=fit)
    box = _red_box(s)
    assert box is not None
    w, h = box[2] - box[0], box[3] - box[1]
    assert w <= 200 * E.MAX_UPSCALE + 2 and h <= 200 * E.MAX_UPSCALE + 2  # at most 1.5x, not 1350/200 = 6.75x
    assert abs((box[0] + box[2]) / 2 - 540) < 4  # centred
    assert any("inset" in p for p in fit.problems)


def test_big_cover_picture_stays_full_bleed():
    from PIL import Image as _I

    from studio import editorial as E
    hero = _I.new("RGB", (1000, 1250), (230, 20, 20))  # 1.08x to fill 1080x1350: allowed
    assert E.upscale_needed(hero, 1080, 1350) <= E.MAX_UPSCALE
    fit = E.Fit()
    s = E.cover(_brand(), hero, "YC F26", "A Card", "One line.", fit=fit)
    assert s.getpixel((540, 300))[0] > 150  # the picture fills the top of the slide
    assert not any("inset" in p for p in fit.problems)


@pytest.mark.parametrize("text,want", [
    ("ernd raises $10M for the AI stack", "ernd Raises $10M For the AI Stack"),
    ("why xai and iphone matter", "Why xAI and iPhone Matter"),
    ("xAI ships, iPhone sales up 4%", "xAI Ships, iPhone Sales Up 4%"),
    ("meet ernd.", "Meet ernd."),
])
def test_title_case_keeps_brand_casing(text, want):
    from studio import editorial as E
    assert E._title(text) == want


def test_title_case_keeps_a_stylised_name_from_the_copy_and_spec():
    from studio import editorial as E
    keep = E.keep_words("acmeco is the card for agents. the rest is noise.", extra=["huhQ"])
    assert keep == {"acmeco": "acmeco", "huhq": "huhQ"}  # 'the' at a sentence start is never taken for a brand
    assert E._title("acmeco and huhq ship", keep) == "acmeco and huhQ Ship"
    tok = E._KEEP.set(keep)
    try:
        assert E._title("the acmeco card") == "The acmeco Card"
    finally:
        E._KEEP.reset(tok)


def test_render_applies_keep_case_from_the_spec(monkeypatch):
    from studio import editorial as E
    seen = []
    real = E._title
    monkeypatch.setattr(E, "_title", lambda s, keep=None: seen.append(real(s, keep)) or seen[-1])
    spec = {"eyebrow": "X", "headline": "zeta pays you back", "dek": "A card.", "keep_case": ["zeta"],
            "slides": [{"eyebrow": "A", "blocks": [{"head": "zeta in numbers", "body": "4% back."}]}]}
    E.render(_brand(), spec)
    assert "zeta Pays You Back" in seen and "zeta In Numbers" in seen
    assert not E._KEEP.get()  # reset after the render


def test_research_link_ranks_share_images_ahead_of_logos():
    from studio.mcp_client_brain import rank_images
    imgs, logos = rank_images("https://ernd.ai/brand/logo.png",
                              ["https://ernd.ai/favicon.ico", "https://ernd.ai/og/share.jpg", "https://ernd.ai/art/hero.webp",
                               "https://ernd.ai/og/share.jpg", "https://cdn.x.com/icons/x.svg"])
    assert imgs[:2] == ["https://ernd.ai/og/share.jpg", "https://ernd.ai/art/hero.webp"]
    assert set(logos) == {"https://ernd.ai/brand/logo.png", "https://ernd.ai/favicon.ico", "https://cdn.x.com/icons/x.svg"}
    assert imgs[2:] == logos and len(imgs) == 5  # deduped; logos last, kept as a fallback
    yc = "https://bookface-images.s3.us-west-2.amazonaws.com/logos/ecba2f.png?X-Amz-Date=1&X-Amz-Signature=abc"
    assert rank_images(yc, ["https://cdn.ernd.ai/hero.jpg?logo=1"]) == (["https://cdn.ernd.ai/hero.jpg?logo=1", yc], [yc])
    imgs, logos = rank_images("https://site.com/og-image.png", ["https://site.com/wp/logo-dark.png"])
    assert imgs[0] == "https://site.com/og-image.png" and logos == ["https://site.com/wp/logo-dark.png"]


def test_render_carousel_tool_offers_keep_case(human):
    from studio.mcp_client_brain import TOOLS
    rc = next(t for t in TOOLS if t["name"] == "render_carousel")
    assert rc["inputSchema"]["properties"]["keep_case"]["type"] == "array"
