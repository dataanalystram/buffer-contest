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
    except ImportError as e:
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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:   # optional on the worker: no Playwright keeps the plain motion moment
        raise TemplateError(f"Playwright isn't installed on this worker ({e.__class__.__name__}).") from e
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
                # words longer than the template's sample ("20 MINUTES" on a card made for "20 min") shrink to fit
                # their card rather than run off it
                page.evaluate("""async ([sels, fam]) => {
                    // measure in the brand font itself: fonts.ready can settle before the face has even started loading
                    try { await document.fonts.load(`800 100px '${fam}'`); await document.fonts.load(`700 100px '${fam}'`); } catch (e) {}
                    await document.fonts.ready;
                    window.__hqFrame(60);   // the last frame: counted-up numbers at their full width
                    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                    const docW = document.documentElement.clientWidth;
                    for (const sel of sels) {
                        const el = document.querySelector(sel); if (!el) continue;
                        // what is drawn can be a copy beside the element (count-ups, letter effects): measure the
                        // element's own line box too, and keep it inside its card and the frame (26 Nov: "0 PEOPL")
                        const line = el.closest('.ln') || el;
                        const card = el.parentElement && el.parentElement !== line ? el.parentElement : null;
                        const room = Math.min(card ? card.clientWidth : docW, docW) * 0.88;
                        const wide = () => Math.max(el.getBoundingClientRect().width, line.getBoundingClientRect().width,
                                                    ...Array.from(line.children).map(c => c.getBoundingClientRect().width));
                        let fs = parseFloat(getComputedStyle(el).fontSize);
                        for (let i = 0; i < 40 && wide() > room && fs > 8; i++) {
                            fs *= 0.93; el.style.fontSize = fs + 'px';
                            await new Promise(r => requestAnimationFrame(r));
                        }
                    }
                }""", [list(sets), font if font in FONT_FILES else "Poppins"])
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


def _props(f: dict) -> tuple[str, dict] | None:
    """The template and its words for one planned motion moment (studio/media/motion.plan), or None. Only the
    speaker's own figures go on the card: a missing label stays hidden rather than invented."""
    if f.get("kind") == "stat":
        return "info-count", {"number": f.get("text")}
    if f.get("kind") == "compare":
        props = {"left": f.get("left"), "right": f.get("right")}
        if f.get("from_to"):
            props.update(left_label="FROM", right_label="TO")
        else:
            props["mark"] = f.get("mark") or "VS"
        return "info-compare", props
    return None


def prepare(fx: list[dict], tmp_dir: str, *, font: str | None = None, accent: str | None = None,
            text: str | None = None, budget_s: float = 45.0) -> list[dict]:
    """Draw a template for each stat / comparison in `fx` (in place: f["template"] = {template, ms, frames, w, h, dir}).
    A comparison whose template can't be drawn turns back into the plain stat pop of its second figure, and the reason
    is kept in f["template_skipped"]. Returns the overlays to composite: [{dir, s, e, w, h, frames}]. Never raises."""
    out, t0 = [], time.time()
    if not enabled():
        return out
    for k, f in enumerate(list(fx)):
        pick = _props(f)
        if not pick:
            continue
        name, props = pick
        why = None
        if time.time() - t0 > budget_s:
            why = "the template budget for this clip was used up"
        else:
            try:
                d = str(Path(tmp_dir) / f"tpl_{k}")
                meta = render(name, props, d, font=font, accent=accent, text=text,
                              timeout=max(5.0, budget_s - (time.time() - t0)))
                f["template"] = {"template": name, "ms": meta["ms"], "frames": meta["frames"], "w": meta["w"], "h": meta["h"]}
                f["e"] = round(max(float(f["e"]), float(f["s"]) + meta["dur"]), 3)
                out.append({"dir": d, "s": float(f["s"]), "e": float(f["e"]), "w": meta["w"], "h": meta["h"],
                            "frames": meta["frames"]})
                continue
            except TemplateError as e:
                why = str(e)
        f["template_skipped"] = why
        if f.get("kind") == "compare" and isinstance(f.get("fallback"), dict):
            fb = {**f["fallback"], "s": f["s"], "e": round(float(f["s"]) + 1.8, 3), "template_skipped": why,
                  "why": f"\"{f['fallback'].get('text')}\" is a concrete number: counted up so it lands "
                         f"(the comparison card couldn't be drawn: {why})."}
            fx[fx.index(f)] = fb
    return out
