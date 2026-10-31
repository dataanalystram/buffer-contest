"""Responsive audit of the HuhQueue app: every tab of the sample workspace (and the landing page) at phone, tablet and
desktop widths, light and dark. Reports horizontal overflow (and the elements causing it) and tap targets under 44 px.

    python scripts/responsive_audit.py http://127.0.0.1:8899 [shots_dir]

Exits 1 if any page scrolls sideways. The same checks run in tests/studio/test_responsive.py."""
import json
import os
import sys

from playwright.sync_api import sync_playwright

WIDTHS = [360, 390, 768, 1024, 1440]
SCHEMES = ["light", "dark"]
TABS = ["home", "agent", "take", "planner", "brand", "studio", "radar", "approvals", "agents", "ai", "activity"]

# Everything a page can do wrong at this width: sideways scroll, who causes it, and controls too small to tap.
PROBE = """(minTap) => {
  const W = document.documentElement.clientWidth;
  const over = document.documentElement.scrollWidth - W;
  const culprits = [];
  if (over > 0) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (!r.width || getComputedStyle(el).position === 'fixed') continue;
      if (r.right > W + 1) {
        let p = el.parentElement, clipped = false;
        while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') { clipped = true; break; } p = p.parentElement; }
        if (!clipped) culprits.push((el.dataset.testid ? '[' + el.dataset.testid + ']' : '') + el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ').join('.') : '') + ' right=' + Math.round(r.right));
      }
    }
  }
  const small = [];
  if (W < 1024) {
    for (const el of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=tab], [role=button]')) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || el.closest('[hidden]')) continue;
      if (el.matches('input[type=checkbox], input[type=radio]') && el.closest('label')) continue; // the label is the target
      if (el.matches('a') && cs.display === 'inline' && el.closest('p, li, small, span')) continue; // links inside a sentence
      if (r.height < minTap - 0.5 || (r.width < minTap - 0.5 && !el.matches('input, select, textarea'))) small.push((el.dataset.testid || el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
    }
  }
  return {over, culprits: culprits.slice(0, 8), small: [...new Set(small)].slice(0, 12)};
}"""


def audit(base: str, shots: str | None = None, widths=WIDTHS, schemes=SCHEMES, tabs=TABS, chromium=None) -> list[dict]:
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium or os.environ.get("CHROMIUM_PATH") or None)
        for scheme in schemes:
            for w in widths:
                ctx = browser.new_context(viewport={"width": w, "height": 900 if w > 500 else 780}, color_scheme=scheme,
                                          timezone_id="Europe/Dublin", has_touch=w < 1024)
                ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
                page = ctx.new_page()
                page.goto(base + "/")
                page.wait_for_timeout(300)
                out.append({"page": "landing", "width": w, "scheme": scheme, **page.evaluate(PROBE, 44)})
                if shots:
                    page.screenshot(path=f"{shots}/landing-{w}-{scheme}.png")
                page.goto(base + "/demo")
                page.locator("#app:not([hidden])").wait_for()
                page.evaluate("() => localStorage.setItem('hq_tour_done', '1')")
                skip = page.get_by_test_id("tour-skip")
                if skip.count() and skip.is_visible():
                    skip.click()
                for t in tabs:
                    page.evaluate("(t) => window.showTab && window.showTab(t)", t)
                    page.wait_for_timeout(450)
                    out.append({"page": t, "width": w, "scheme": scheme, **page.evaluate(PROBE, 44)})
                    if shots:
                        page.screenshot(path=f"{shots}/{t}-{w}-{scheme}.png")
                ctx.close()
        browser.close()
    return out


if __name__ == "__main__":
    base = sys.argv[1].rstrip("/")
    shots = sys.argv[2] if len(sys.argv) > 2 else None
    if shots:
        os.makedirs(shots, exist_ok=True)
    rows = audit(base, shots)
    bad = [r for r in rows if r["over"] > 0]
    for r in rows:
        flag = "OVERFLOW" if r["over"] > 0 else ("small-taps" if r["small"] else "ok")
        print(f"{r['scheme']:5} {r['width']:5} {r['page']:10} {flag:10} over={r['over']} {json.dumps(r['culprits'][:3])} {json.dumps(r['small'][:6])}")
    sys.exit(1 if bad else 0)
