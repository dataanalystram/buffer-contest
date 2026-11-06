"""News › Story scan: picking stories never writes anything (owner bug, 6 Nov 2026: "2 stories · Write posts" generated every
carousel inline on the News screen). Scan now only reads the sources; the bar's "Make a post (N)" and each card's "Make post"
open a NEW Make a post thread with the stories' titles and links in the composer, and nothing runs until the person
presses send. Nothing goes to Buffer from the scan."""
import re
from pathlib import Path

from studio import llm

from .test_sources_agent import live_sources  # noqa: F401  (fixture)

STATIC = Path(__file__).resolve().parents[2] / "studio" / "static"


def _js(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_story_scan_screen_never_drafts_inline():
    bs = _js("brandstudio.js")
    assert "/api/sources/draft" not in bs  # the inline bulk writer is gone: the screen only reads
    assert "saPkgs" not in bs and "sa-packages" not in bs
    assert "hqNewPost" in bs and "hqStoryPrompt" in bs
    assert re.search(r"Make a post \(\$\{", bs)  # the bar button is "Make a post (N)"
    assert "MAX_PICK = 10" in bs


def test_story_scan_copy_says_scan_only_reads():
    bs = _js("brandstudio.js")
    assert "drafts into your Buffer" not in bs and "saved to Buffer as" not in bs
    assert "Nothing goes to Buffer" in bs


def test_story_prompt_helper_is_shared_and_capped():
    app = _js("app.js")
    assert "window.hqStoryPrompt" in app
    assert "Make posts from these stories:" in app
    assert "MAX_STORIES = 10" in app


def test_scan_reads_only_no_generation_no_buffer(live_sources, fake, monkeypatch):  # noqa: F811
    c, _ = live_sources
    called = []
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: called.append("resolve") or None)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: called.append("chat_json") or {})
    before = len(fake.calls)
    r = c.post("/api/sources/scan", json={"sources": ["yc", "hn"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["clusters"] and "drafts" not in d
    assert not fake.created and not called
    assert len(fake.calls) == before  # not a single Buffer request from a scan
    run = c.get(f"/api/sources/runs/{d['run_id']}").json()
    assert not run.get("drafts")


def test_story_prompt_fits_the_composer_and_a_bad_link_stays_put():
    app = _js("app.js")
    assert "PROMPT_BUDGET = 3200" in app and "out.length + ln.length > PROMPT_BUDGET" in app  # < maxlength 4000
    wm = app[app.index("function wireMake"):]
    wm = wm[:wm.index("hqNewPost")]
    assert "if (!text)" in wm and "return;" in wm  # no link: no new thread, the person stays on the story
