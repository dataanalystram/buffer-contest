"""Video THEMES (studio/media/styles.py THEMES, captions.py theme styles, theme_cards.py) and the theme picker plumbing:
every theme resolves, picks from words and content, its captions never clip, its cards and chrome stay in the frame at
9:16 / 4:5 / 1:1, it renders end to end on a synthetic clip, and REST / MCP / the agent carry the choice."""
import itertools
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from studio.media import captions, compose, ff, styles, textfit, theme_cards

from . import conftest
from .test_agent_core import TWEET, owner  # noqa: F401  (fixture)

WORDS = json.loads((Path(__file__).parent / "fixtures" / "media" / "aarush_words_tiny.json").read_text())["words"]
RATIOS = [("9:16", 1080, 1920), ("4:5", 1080, 1350), ("1:1", 1080, 1080)]
HOOK = "Glasses that let you talk without making a sound"


def test_every_theme_is_complete_and_credited():
    assert len(styles.THEMES) >= 12
    for tid, t in styles.THEMES.items():
        cfg = styles.config(tid)
        assert cfg["layout"] in styles.STYLE_PRESETS and cfg["captions"] in captions.STYLES, tid
        assert t["source"] and t["about"] and t["best_for"], tid
        assert t["music"] in ("cinematic", "pulse")
        assert styles.known(tid) and styles.known(tid.replace("_", "-"))
    lemo = [t for t in styles.THEMES.values() if "lemo-opuscar" in t["source"]]
    assert len(lemo) >= 12  # ported looks carry their MIT credit (NOTICE has the licence)
    assert "lemo-opuscar" in Path("NOTICE").read_text()
    ids = [t["id"] for t in styles.describe_themes()]
    assert ids[:4] == list(styles.STYLE_PRESETS) and set(styles.THEMES) <= set(ids)


@pytest.mark.parametrize("words,want", [
    ("make it more cinematic", "cinematic"), ("switch to the news theme", "huhbyte_news"), ("like opus clip", "opus"),
    ("news style please", "huhbyte_news"), ("do the weekly recap", "news_package"), ("give it a crt terminal look", "crt"),
    ("make it look like a silent film", "silent_film"), ("hologram hud style", "hologram_hud")])
def test_named_looks(words, want):
    assert styles.explicit_theme(words) == want


def test_pick_theme_from_words_then_content():
    assert styles.pick_theme("make it a short punchy explainer")[0] == "opus"
    tid, why = styles.pick_theme("post this", "A new open-source GitHub repo for developers: a CLI for your terminal")
    assert tid == "crt" and why
    assert styles.pick_theme("post this", "Nvidia's new GPU chip doubles the benchmark")[0] == "hologram_hud"
    assert styles.pick_theme("post this", "a launch")[0] == "huhbyte_news"  # default
    assert styles.named_theme("make the hook stronger") is None and styles.named_theme("Epic Games launches a jam") is None
    assert styles.explicit("make it a short punchy explainer") is None  # the fixed planner's rule is unchanged


def test_palette_keeps_logo_and_handle():
    b = styles.apply_palette({**compose.DEFAULT_BRAND, "logo": "x.png"}, styles.config("caption_news"))
    assert b["accent"] == "#FFC21A" and b["handle"] == "@huhbyte.ai" and b["logo"] == "x.png"
    keep = styles.apply_palette(dict(compose.DEFAULT_BRAND), styles.config("caption_news"), keep_brand=True)
    assert keep["accent"] == compose.DEFAULT_BRAND["accent"]


@pytest.mark.parametrize("style", sorted(captions.STYLES))
def test_caption_styles_fit_every_ratio(style):
    for _, W, H in RATIOS:
        ass = captions.build_ass(WORDS[:80], style, W=W, H=H, palette={"on_accent": "#111111", "muted": "#888888"})
        assert captions.check_ass_fits(ass, W, H) == [], (style, W, H)
        assert f"; huhqueue-style: {style}" in ass


def _inside(box, W, H, pad=0):
    return box[0] >= -pad and box[1] >= -pad and box[2] <= W + pad and box[3] <= H + pad


@pytest.mark.parametrize("tid", sorted(styles.THEMES))
def test_cards_and_chrome_stay_in_frame(tid, tmp_path):
    cfg = styles.config(tid)
    brand = styles.apply_palette(dict(compose.DEFAULT_BRAND), cfg)
    still = Image.new("RGB", (1920, 1080), (90, 120, 150))
    for ratio, W, H in RATIOS:
        L = compose.layout(cfg["layout"], W, H, 1920, 1080, brand, HOOK, True)
        ov, boxes = theme_cards.overlay(cfg, W, H, brand, HOOK, "Video: @someone on X", L, set())
        assert ov.size == (W, H) and ("hook" in boxes or cfg["layout"] == "hype")  # hype: the giant hook is its own first-beat layer
        l, t, r, b = compose.safe_area(W, H)
        for k in ("hook", "credit", "pill", "logo"):
            if k in boxes:
                assert textfit.inside(boxes[k], (l - 16, t - 16, r + 16, b + 16)), (tid, ratio, k, boxes[k])
        for ka, kb in itertools.combinations([k for k in ("hook", "credit", "pill", "logo", "bug", "clock", "label") if k in boxes], 2):
            assert not textfit.overlaps(boxes[ka], boxes[kb]), (tid, ratio, ka, kb)
        cb = theme_cards.cover(cfg, str(tmp_path / "c.png"), W, H, brand, HOOK, still)
        assert Image.open(tmp_path / "c.png").size == (W, H)
        assert _inside(cb["hook"], W, H), (tid, ratio, cb["hook"])
        fb = theme_cards.facts(cfg, str(tmp_path / "f.png"), W, H, brand, "What it does", ["One", "Two fact here", "Three"], still)
        assert all(_inside(v, W, H) for v in fb.values())
        if cfg.get("fill") == "stage":
            assert theme_cards.stage(cfg, W, H, brand, L["panel"]).size == (W, H)


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")


@pytest.fixture(scope="module")
def talk(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("th") / "talk.mp4")
    ff.run(["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3", "-f", "lavfi", "-i", "sine=f=330:d=3",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", p])
    return p


WORDS_SHORT = [{"w": w, "s": 0.2 + i * 0.4, "e": 0.55 + i * 0.4} for i, w in enumerate("Glasses decode silent speech into text".split())]


@needs_ffmpeg
@pytest.mark.parametrize("tid", sorted(styles.THEMES))
def test_every_theme_renders_a_complete_short(tid, talk, tmp_path):
    ratio = {"kino": "4:5", "swiss": "1:1", "screencast": "4:5", "countdown": "1:1"}.get(tid, "9:16")
    m = compose.render_style(talk, tid, out=str(tmp_path / "o.mp4"), ratio=ratio, hook=HOOK, facts=["One", "Two"], words=WORDS_SHORT,
                             credit="Video: test", clip=(0, 2), preset="ultrafast", crf=34, burned_subs=False)
    want = m["cover_secs"] + 2 + m["facts_secs"] + compose.CARD_SECS
    assert abs(m["duration"] - want) < 0.25 and m["theme"] == tid
    assert ff.probe(m["path"])["has_audio"] and m["caption_problems"] == []
    mid = str(tmp_path / "mid.png")
    ff.run(["-y", "-v", "error", "-ss", f"{m['cover_secs'] + 1:.2f}", "-i", m["path"], "-frames:v", "1", "-update", "1", mid])
    px = Image.open(mid).convert("L").resize((54, 96)).getdata()
    assert max(px) - min(px) > 40, "the main section is not a flat / black frame"


def test_api_lists_themes_and_a_run_keeps_the_choice(owner):  # noqa: F811
    th = owner.get("/api/themes").json()
    assert th["default"] == "auto" and {"cinematic", "crt", "news_package"} <= {t["id"] for t in th["themes"]}
    assert all(Path("studio" + t["thumb"]).name for t in th["themes"])
    body = {"prompt": f"post this {TWEET}", "brain": "external", "theme": "cinematic", "channel_ids": ["ch_li"]}
    run = owner.post("/api/agent/runs", json=body).json()
    assert run["input"]["theme"] == "cinematic"
    bad = owner.post("/api/agent/runs", json={**body, "theme": "glitter"}).json()
    assert bad["input"]["theme"] == "auto"
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    res, got = conftest.mcp_call(owner, tok, "list_themes", {})
    assert not res["isError"] and len(got["themes"]) == len(styles.ALL_IDS)
    res, out = conftest.mcp_call(owner, tok, "set_theme", {"run_id": run["id"], "theme": "crt"})
    assert not res["isError"] and out["theme"] == "crt"
    assert owner.get(f"/api/agent/runs/{run['id']}").json()["input"]["theme"] == "crt"
    r = owner.post(f"/api/agent/runs/{run['id']}/theme", json={"theme": "keynote"}).json()
    assert r["theme"] == "dark_keynote"
    res, tl = conftest.mcp_call(owner, tok, "post_list_templates", {"run_id": run["id"]})
    assert not res["isError"] and {"weekly_recap", "github_repo", "yc_launch", "viral_trend"} <= {t["id"] for t in tl["templates"]}


def test_theme_gallery_assets_exist_for_every_look():
    d = Path("studio/static/themes")
    missing = [t for t in styles.ALL_IDS if not ((d / f"{t}.jpg").exists() and (d / f"{t}.mp4").exists())]
    assert not missing, f"run scripts/theme_shots.py --gallery for {missing}"


def test_weekly_recap_template_is_the_recap_look(tmp_path):
    """The HuhByte weekly_recap template registers with the longform recap (studio/media/recap.py RecapLayout) and draws
    its cover and closing card at exactly the recap's frame sizes; story overlays keep the base layout."""
    from studio.media import recap
    lay = recap.layout_for(None)
    assert lay.name == "huhbyte_weekly_recap" and recap.layout_for("minimal").name == "minimal"
    still = Image.new("RGB", (1280, 720), (90, 60, 40))
    for W, H in recap.SIZES.values():
        cov, fol = tmp_path / f"c{H}.png", tmp_path / f"f{H}.png"
        assert lay.cover(str(cov), W, H, compose.DEFAULT_BRAND, recap.COVER_TEXT, still)["template"] == "weekly_recap"
        lay.follow(str(fol), W, H, compose.DEFAULT_BRAND)
        for p in (cov, fol):
            im = Image.open(p)
            assert im.size == (W, H) and im.convert("L").getextrema()[1] > 60  # the right frame, never a blank card
