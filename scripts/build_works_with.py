"""Write the landing page's integrations section, "Works with the tools you already use" (studio/static/landing.html,
between the WORKS-WITH markers), so the page needs no script for it.

Marks come from the files the app already ships: brand-logos-data.js and net-logos-data.js (simple-icons 16.36.0, CC0)
and ai-logos.js (Cursor from simple-icons; OpenAI's Blossom from openai.com/brand). LinkedIn and Groq are not in
simple-icons, so they are monograms, as in brand-mark.js. Every mark is its owner's trademark, used only to say HuhQueue
works with that service (NOTICE).

Each card is a brand-coloured tile with the mark (white or near-black, whichever reads better on that colour), the
service's name and one line on what HuhQueue does with it. The mark is decorative (the name is the text next to it).

    python scripts/build_works_with.py
"""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "studio" / "static"

# (group id, title, one line about the group, [(mark key, display name, value line)])
GROUPS = [
    ("publish", "Publish", "Through your Buffer account, to every channel you connected there.", [
        ("buffer", "Buffer", "Sign in with Buffer. Every post goes out through your own queue."),
        ("instagram", "Instagram", "Carousels and Reels, sized and checked before they're sent."),
        ("youtube", "YouTube", "Shorts with a title and a branded cover."),
        ("tiktok", "TikTok", "9:16 clips with captions burned in."),
        ("linkedin", "LinkedIn", "A longer, plainer caption, up to 3,000 characters."),
        ("facebook", "Facebook", "Page posts checked against Facebook's rules."),
        ("x", "X", "Posts fitted to 280 characters, or split into a thread."),
        ("threads", "Threads", "Short captions under 500 characters."),
        ("pinterest", "Pinterest", "Pins checked against the 500-character limit."),
        ("bluesky", "Bluesky", "Posts fitted to 300 characters, links kept."),
    ]),
    ("create", "Create & research", "Where the stories, videos and facts come from.", [
        ("producthunt", "Product Hunt", "Today's launches, with the maker's own video."),
        ("huggingface", "Hugging Face", "New models and papers in your news radar."),
        ("github", "GitHub", "Trending repos and releases as story sources."),
        ("youtube", "YouTube", "Paste a talk; One take cuts it into credited clips."),
        ("x", "X", "Read a post or a thread from its link, no key needed."),
    ]),
    ("engines", "AI engines", "Pick the model that writes. Free engines first, your own key if you like.", [
        ("nvidia", "NVIDIA", "Nemotron on NVIDIA NIM. Free, and the default."),
        ("gemini", "Gemini", "Google AI Studio's free tier for fast drafts."),
        ("huggingface", "Hugging Face", "Qwen3 235B on Hugging Face, free."),
        ("claude", "Claude", "Best writing and tool use, with an allowance per account."),
        ("openai", "OpenAI", "Bring your own key; your provider bills you."),
        ("groq", "Groq & OpenRouter", "Your own key for any model they host."),
    ]),
    ("mcp", "AI apps over MCP", "Your assistant uses HuhQueue's tools. Its posts wait for your yes.", [
        ("claude", "Claude", "Add HuhQueue as a custom connector and sign in with Buffer."),
        ("chatgpt", "ChatGPT", "Add a custom MCP server with OAuth."),
        ("cursor", "Cursor", "Point Cursor's MCP settings at /mcp with a token."),
        ("claude-code", "Claude Code", "One command adds HuhQueue to your terminal."),
        ("mcp", "Any MCP client", "The same tools over MCP or REST, with scoped tokens."),
    ]),
]

MONO = {"linkedin": ("in", "#0A66C2"), "groq": ("gq", "#F55036")}
SAME = {"chatgpt": "openai"}          # ChatGPT's mark is OpenAI's Blossom
HEX = {"chatgpt": "#10A37F", "openai": "#000000", "cursor": "#000000", "claude-code": "#D97757", "mcp": "#000000"}
DARKS = {"#000000", "#181717", "#231f20", "#191919"}   # near-black brands: drawn in the page's ink colour in dark mode


def _load(name: str) -> dict:
    t = (ROOT / name).read_text()
    return json.loads(t[t.index("{"):t.rindex("}") + 1])


def _lum(hexv: str) -> float:
    h = hexv.lstrip("#")
    def ch(i):
        c = int(h[i:i + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(0) + 0.7152 * ch(2) + 0.0722 * ch(4)


def on_colour(hexv: str) -> str:
    """White or near-black mark on this tile, whichever has the higher contrast."""
    lum = _lum(hexv)
    white = 1.05 / (lum + 0.05)
    dark = (lum + 0.05) / (_lum("#111111") + 0.05)
    return "#ffffff" if white >= dark else "#111111"


def mark(key: str, marks: dict) -> tuple[str, str]:
    """(svg, brand hex) for one card; the svg is decorative."""
    if key in MONO:
        txt, hexv = MONO[key]
        return (f'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><text x="12" y="17" text-anchor="middle" '
                f'font-size="{15 if len(txt) == 2 and txt == MONO['linkedin'][0] else 12}" font-weight="800" font-family="Arial,sans-serif" fill="currentColor">{txt}</text></svg>'), hexv
    d = marks[SAME.get(key, key)]
    return (f'<svg viewBox="{d["vb"]}" aria-hidden="true" focusable="false">{d["svg"]}</svg>'), HEX.get(key, d["hex"])


def build() -> str:
    marks = {**_load("net-logos-data.js"), **_load("ai-logos.js"), **_load("brand-logos-data.js")}
    out = []
    for gid, title, about, items in GROUPS:
        cards = []
        for key, name, value in items:
            svg, hexv = mark(key, marks)
            dark = " ink" if hexv.lower() in DARKS else ""
            cards.append(f'<li class="ig-card" data-testid="integ-{gid}-{key}"><span class="ig-tile{dark}" style="--brand:{hexv};'
                         f'--on:{on_colour(hexv)}">{svg}</span><span class="ig-txt"><b>{html.escape(name)}</b>'
                         f'<span>{html.escape(value)}</span></span></li>')
        out.append(f'    <div class="ig-group" data-testid="integ-group-{gid}" aria-labelledby="igH-{gid}">\n'
                   f'      <div class="ig-ghead"><h3 id="igH-{gid}">{html.escape(title)}</h3><p>{html.escape(about)}</p></div>\n'
                   f'      <ul class="ig-grid" aria-labelledby="igH-{gid}">' + "".join(cards) + '</ul>\n    </div>')
    n = sum(len(g[3]) for g in GROUPS)
    return ('<!-- WORKS-WITH:start (generated by scripts/build_works_with.py) -->\n'
            '  <section class="beat wrap integ" id="works-with" aria-labelledby="wwH" data-testid="works-with" data-cards="' + str(n) + '">\n'
            '    <div class="ig-head"><p class="ig-eyebrow">Integrations</p>'
            '<h2 class="h2" id="wwH">Works with the tools you already use</h2>\n'
            '    <p class="sub">Buffer publishes. The research comes from where launches happen. The writing runs on the engine you pick, '
            'and your own AI app can drive all of it.</p></div>\n'
            + "\n".join(out) + '\n'
            '    <p class="works-note">Logos are trademarks of their owners; HuhQueue is independent and not endorsed by them. '
            'Icons: simple-icons (CC0); the OpenAI mark from openai.com/brand.</p>\n'
            '  </section>\n  <!-- WORKS-WITH:end -->')


def main():
    block = build()
    p = ROOT / "landing.html"
    s = p.read_text()
    if "<!-- WORKS-WITH:start" in s:
        s = re.sub(r"<!-- WORKS-WITH:start.*?<!-- WORKS-WITH:end -->", lambda m: block, s, flags=re.S)
    else:
        s = s.replace('  <section class="made-wall" id="made"', "  " + block + '\n\n  <section class="made-wall" id="made"', 1)
    p.write_text(s)
    print("works-with:", block.count('class="ig-card"'), "cards")


if __name__ == "__main__":
    main()
