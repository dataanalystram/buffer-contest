"""OpenFilm motion-graphics templates (beta, HQ_TEMPLATES=1), drawn by headless Chromium into a transparent overlay that
the One take render composites over the clip.

* Templates are pages from OpenFilm's library (CC0 1.0, github.com/openfilm/openfilm @ 69489069, 25 Nov 2026), vendored
  here unchanged as ``<name>.html``. Each page draws frame ``t`` from ``t`` alone: ``window.film.frame(t)``, with its CSS
  animations held at ``t`` by the host (``filmHost``). Credited in NOTICE (not required by CC0, kept as courtesy).
* Our host is a few lines of our own (``_HOST`` below): flag the page as hosted, then per frame pause every animation at
  ``t`` and call ``film.frame(t)``. Pages get no network (every request but the page's own file and our fonts is
  refused) and run only our vendored files.
* Words are set through the template's own ``data-props`` selectors, as text (never HTML), and the brand kit's font and
  accent through its CSS variables. Fonts are the bundled OFL files (studio/fonts).
* Chromium comes from Playwright (``pip install playwright`` + ``python -m playwright install chromium``) on the take
  worker. Missing -> TemplateError, and the caller falls back to the plain ASS motion moment. Never fails a render.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONTS = HERE.parent.parent / "fonts"
FPS = 30
TEMPLATES = {
    # name: {file, duration (s), props: {name: selector}, vars}
    "info-count": {"file": "info-count.html", "dur": 2.4, "h": 640, "props": {"label": ".t1", "number": ".t2", "note": ".t3"}},
    "info-compare": {"file": "info-compare.html", "dur": 2.6, "h": 500,   # two 88vh cards + 22vh gap fit 1080 wide
                     "props": {"left_label": ".t1", "left": ".t2", "right_label": ".t3", "right": ".t4", "mark": ".t5"}},
}
FONT_FILES = {"Poppins": "Poppins-Bold.ttf", "Inter": "Inter-Bold.ttf", "Anton": "Anton.ttf", "Figtree": "Figtree-ExtraBold.ttf"}

_HOST = """
window.filmHost = { version: 'hq-0.1' };
window.__hqFrame = (t) => {
  for (const a of document.getAnimations ? document.getAnimations() : []) { a.pause(); a.currentTime = t * 1000; }
  if (window.film && window.film.frame) window.film.frame(t);
};
"""


class TemplateError(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get("HQ_TEMPLATES", "").strip() == "1"


def available() -> str | None:
    """None when templates can render here, else why not."""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return f"Playwright isn't installed on this worker ({e.__class__.__name__})."
    return None


def _style(font: str | None, accent: str | None, text: str | None) -> str:
    fam = font if font in FONT_FILES else "Poppins"
    face = f"@font-face {{ font-family: '{fam}'; src: url('{(FONTS / FONT_FILES[fam]).as_uri()}'); font-weight: 100 900; }}"
    vars_ = [f"--font: '{fam}', sans-serif"]
    if accent:
        vars_ += [f"--accent: {accent}", f"--right: {accent}"]
    if text:
        vars_.append(f"--color: {text}")
    # the number in info-count names its own fonts: bring it onto the brand font as well
    # the see-through source number under the count-up keeps its text-shadow: a ghost of the final figure; drop it
    return (face + f":root {{ {'; '.join(vars_)}; }} .n {{ font-family: '{fam}', sans-serif !important; }}"
            " .src { text-shadow: none !important; }")


def render(name: str, props: dict, out_dir: str, *, W: int = 1080, H: int | None = None, dur: float | None = None,
           font: str | None = None, accent: str | None = None, text: str | None = None, timeout: float = 60.0) -> dict:
    """PNG frames (transparent) of template `name` with `props` ({prop: text}; a missing prop is hidden) into out_dir as
    f_00001.png ... Returns {frames, fps, dur, w, h, ms}. TemplateError on any trouble."""
    tpl = TEMPLATES.get(name)
    if not tpl:
        raise TemplateError(f"No template called {name}.")
    why = available()
    if why:
        raise TemplateError(why)
    from playwright.sync_api import sync_playwright
    page_file = HERE / tpl["file"]
    H = int(H or tpl.get("h") or 640)
    dur = float(dur or tpl["dur"])
    n = max(1, int(round(dur * FPS)))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    exe = os.environ.get("HQ_CHROMIUM") or None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else pw.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                allowed = {page_file.as_uri()} | {(FONTS / f).as_uri() for f in FONT_FILES.values()}
                page.route("**/*", lambda r: r.continue_() if r.request.url in allowed else r.abort())
                page.add_init_script(_HOST)
                page.goto(page_file.as_uri(), wait_until="load", timeout=timeout * 1000)
                page.add_style_tag(content=_style(font, accent, text))
                sets = {tpl["props"][k]: str(v)[:60] for k, v in props.items() if k in tpl["props"] and v is not None}
                hide = [sel for k, sel in tpl["props"].items() if k not in props]
                page.evaluate("""([sets, hide]) => {
                    for (const [sel, v] of Object.entries(sets)) { const el = document.querySelector(sel); if (el) el.textContent = v; }
                    for (const sel of hide) { const el = document.querySelector(sel); if (el) el.style.visibility = 'hidden'; }
                }""", [sets, hide])
                page.evaluate("document.fonts.ready")
                for k in range(n):
                    if time.time() - t0 > timeout:
                        raise TemplateError("The template took too long to draw.")
                    page.evaluate("t => window.__hqFrame(t)", k / FPS)
                    page.screenshot(path=str(out / f"f_{k + 1:05d}.png"), omit_background=True)
            finally:
                browser.close()
    except TemplateError:
        raise
    except Exception as e:  # noqa: BLE001 - no Chromium, a crash: the caller keeps the plain motion moment
        raise TemplateError(f"Chromium couldn't draw the template ({e.__class__.__name__}: {str(e)[:80]}).") from e
    return {"frames": n, "fps": FPS, "dur": round(n / FPS, 3), "w": W, "h": H, "ms": int((time.time() - t0) * 1000),
            "template": name}


def render_tmp(name: str, props: dict, **kw) -> tuple[str, dict]:
    """render() into a new temp dir; the caller removes it (shutil.rmtree)."""
    d = tempfile.mkdtemp(prefix="hq_openfilm_")
    try:
        return d, render(name, props, d, **kw)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise


def describe() -> str:
    return json.dumps({k: list(v["props"]) for k, v in TEMPLATES.items()})
