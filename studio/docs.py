"""HuhQueue documentation at /docs: server-rendered, crawlable pages with a sidebar, search and fresh screenshots of the
real app (light and dark, desktop and phone, from scripts/premium_shots.py ... docshots). Content lives here as data so
the sitemap, the search index and the pages never drift apart. Pages owned by the brand-studio work (Brand kit and
templates, Sources agent) are short stubs until that work lands."""
from __future__ import annotations

import html
import json
import re

LASTMOD = "2026-10-31"
BASE = "https://huhqueue-studio.onrender.com"

# (slug, title, one-line summary, screenshot key or None, body html)
PAGES: list[tuple[str, str, str, str | None, str]] = [
    ("getting-started", "Getting started", "From a link to an approved week in five minutes, with the sample or your own Buffer.", "composer", """
<p>HuhQueue turns a link, a topic or one long video into posts in your brand, checks each one for every channel, and plans them into your Buffer week. Nothing posts until you approve it.</p>
<h2 id="try">1. Try the sample, no sign-up</h2>
<p>Open <a href="/demo">the demo</a>. You get a sample workspace with made-up channels and a seven-step tour. Everything works the same as with a real account, and nothing reaches Buffer. The sample is deleted after a day.</p>
<h2 id="connect">2. Connect your Buffer account</h2>
<p>Choose <b>Connect Buffer</b> on the home page or in the sample bar. You sign in on Buffer's own page; HuhQueue never sees your password. See <a href="/docs/connect-buffer">Connect Buffer</a>.</p>
<h2 id="make">3. Make a post</h2>
<ol><li>Open <b>Make a post</b> and choose <b>Research a topic</b>, <b>From a link</b> or <b>My photo or video</b>.</li>
<li>Pick the channels and a format, or let the agent pick, then choose <b>Make it</b>.</li>
<li>Watch the timeline: it opens the source, checks it against other outlets, renders the media in your brand and writes a caption per channel.</li>
<li>Say what to change ("logo smaller", "no hashtags"). Each correction becomes a rule in your Style Ledger.</li></ol>
<h2 id="plan">4. Plan and approve</h2>
<p>Ready posts land in the <a href="/docs/planner">Planner</a> and anything waiting for a person shows in the <a href="/docs/inbox">Inbox</a>. Approve, and it goes to Buffer at its time.</p>
<h2 id="keys">Keyboard</h2>
<p>Press <kbd>Ctrl</kbd> <kbd>K</kbd> (<kbd>⌘</kbd> <kbd>K</kbd> on a Mac) anywhere in the app to jump to a screen, a Planner view or a doc. Press <kbd>?</kbd> for every shortcut. <b>Help</b> in the header opens the tour and these docs from any screen.</p>"""),
    ("connect-buffer", "Connect Buffer", "Sign in with Buffer's own OAuth, including from a phone, and what HuhQueue can do with it.", "landing", """
<p>HuhQueue works on your real Buffer channels through the Buffer API. Connecting takes one sign-in.</p>
<h2 id="how">How to connect</h2>
<ol><li>Choose <b>Connect Buffer</b> next to <b>Try the demo</b> at the top of the home page, or <b>Connect your Buffer account</b> under the link field.</li>
<li>Buffer's sign-in page opens as a full page. Sign in and allow HuhQueue.</li>
<li>You come back to your Inbox, with your channels loaded from Buffer.</li></ol>
<h2 id="phone">On a phone</h2>
<p>Buffer's sign-in can fail inside the browser built into apps like Instagram, Facebook, TikTok, X or LinkedIn. If you open HuhQueue from one of those, <b>Connect Buffer</b> shows a short sheet that opens the sign-in in Chrome or Safari, or copies the link. Sign-in is never shown inside a frame.</p>
<h2 id="what">What HuhQueue can do</h2>
<ul><li>Read your organization, channels, posting times and posts.</li><li>Create posts, drafts and ideas when you, or an agent within your rules, submit them.</li><li>Edit, reschedule or delete posts you change in the Planner.</li></ul>
<p>Your Buffer tokens are encrypted on the server and never reach the browser. Every Buffer call counts against your Buffer plan's API budget, shown in the header.</p>
<h2 id="disconnect">Disconnect</h2>
<p>Account menu, <b>Disconnect and delete my data</b>, removes everything HuhQueue stored for you at once. You can also revoke access from your Buffer account settings.</p>"""),
    ("brand-kit", "Brand kit and templates", "Colours, fonts, logo, end card and voice, and the Style Ledger that remembers corrections.", "brand", """
<p class="callout">This page is being expanded with the new brand studio and templates. What is here describes the app today.</p>
<h2 id="kit">Brand kit</h2>
<p>Open <b>Brand and ledger</b>. Import your colours and logo from your website, or set the ground, accent and text colours, a headline font, your logo, handle, end card and voice by hand. The preview shows how your posts look now.</p>
<h2 id="ledger">Style Ledger</h2>
<p>Every correction you give becomes a written rule ("logo bottom right", "max 2 hashtags"). Each render and caption is checked against all of them, and a failed check names the rule. You can add rules in plain words and turn them on or off; only a person can change a rule.</p>"""),
    ("sources-agent", "Sources agent", "Where stories come from: followed feeds, research and the news radar.", "radar", """
<p class="callout">This page is being expanded with the new sources agent. What is here describes the app today.</p>
<h2 id="radar">News radar</h2>
<p>Follow the RSS or Atom feeds of sites you share from. New stories show up in <b>News radar</b> and on your Inbox, one click from a post for every channel. Agents can read them too, with the <code>list_stories</code> tool.</p>
<h2 id="research">Research</h2>
<p>When you give the agent a topic, it searches recent news, opens the source page, checks that other outlets carry the story and pulls out the facts, each with its link. Every step is on the run's timeline.</p>"""),
    ("inbox", "Inbox", "Everything that needs a person, most urgent first: approvals, failures, preflight warnings and open slots.", "inbox", """
<p>The Inbox is the first screen after you sign in. It groups what is waiting for you by urgency: <b>Now</b>, <b>Soon</b> and <b>This week</b>. Empty groups fold into one calm line.</p>
<h2 id="groups">What shows up</h2>
<ul><li><b>Approvals:</b> posts your agents wrote, waiting for you. Approve and they go to Buffer at their time.</li>
<li><b>Failed in Buffer:</b> with Buffer's own reason and a way to fix and reschedule.</li>
<li><b>Preflight warnings:</b> a post that would bounce, with <b>Fix it</b>.</li>
<li><b>Channels to reconnect</b>, <b>drafts with no date</b> and <b>open posting times</b> this week.</li></ul>
<p>The same view is the <code>get_inbox</code> tool for agents. When the kill switch is on, the Inbox says so at the top.</p>"""),
    ("planner", "Planner views", "Week, Month, Day, List, Board and Grid on the same posts, with drag to reschedule and a phone agenda.", "planner-week", """
<p>The Planner is your Buffer queue and calendar in one place. Every view reads the same posts, so switching never loses your filters.</p>
<h2 id="views">The six views</h2>
<ul><li><b>Week</b> (<kbd>1</kbd>): hours down, days across, a line for now and Buffer's open posting times as dashed slots. Drag a post to move it, or click an empty hour to create one.</li>
<li><b>Month</b> (<kbd>2</kbd>): the whole month at a glance; <b>+ more</b> opens a busy day.</li>
<li><b>Day</b> (<kbd>3</kbd>): one lane per channel for a single day. Drop on a lane to move a post, click a lane's hour to create one for that channel.</li>
<li><b>List</b> (<kbd>4</kbd>): the next four weeks grouped by day, with every action on the card.</li>
<li><b>Board</b> (<kbd>5</kbd>): one column per status, from Draft to Sent. Drop a draft or a ready post on <b>Scheduled in Buffer</b> to give it a time. Sent and Failed are set by Buffer alone.</li>
<li><b>Grid</b> (<kbd>6</kbd>): how your Instagram feed will look, newest first. Planned posts are dashed with their time.</li></ul>
<h2 id="phone">On a phone</h2>
<p>Week and Day become an agenda: every day listed with its posts and open posting times you can tap to create a post.</p>
<h2 id="filters">Filters and time zone</h2>
<p>Filter by channel, post type, status or caption text, pick the time zone, show <b>No date</b> drafts and Buffer's posting times. The view and filters are kept in the address, so a link opens the same view. <kbd>T</kbd> jumps to today, the arrow keys move back and forward.</p>
<h2 id="checks">Every change is checked</h2>
<p>New posts and edits go through preflight, the same check as the composer, REST and MCP. Moves go through the same actions agents use.</p>"""),
    ("one-take", "One take", "One long video becomes three to seven captioned vertical clips, spread into your week as Buffer drafts.", "take", """
<p>Drop one long video you recorded: a talk, a podcast, a tutorial. HuhQueue finds the best things you already said and cuts 3 to 7 vertical clips. It only uses your words.</p>
<h2 id="steps">What it does</h2>
<ol><li>Transcribes with word timings.</li><li>Finds moments that end on a sentence.</li><li>Reframes to 9:16 around the speaker.</li><li>Burns captions in your font above the bottom fifth, adds a cover and your end card.</li><li>Checks each clip for every channel.</li></ol>
<h2 id="spread">The Spread</h2>
<p>Choose <b>Spread into my week</b>. The clips appear on the Planner in your open posting times as a plan first; <b>Save as Buffer drafts</b> creates them. You can trim each clip by half a second and edit its hook and caption.</p>
<p>Try <b>Try the sample take</b> to see it on a public-domain talk. Upload up to 40 MB, or link to a file you host for longer talks. You confirm you own the video or have the rights to it.</p>"""),
    ("agents", "Agents, MCP and API", "Use HuhQueue from Claude, ChatGPT, Grok, Claude Code, Codex, Cursor or your own code, on a leash.", "connect", """
<p>Everything the app can do is also a tool for your AI apps over MCP and an endpoint over REST. Open <b>Connect AI apps</b> in the app for per-app steps, your MCP address and when each app last used it.</p>
<h2 id="oauth">Apps that sign in (Claude, ChatGPT)</h2>
<p>Add a custom connector and paste your MCP address. The app sends you to HuhQueue to sign in with Buffer and choose what it may do. The sign-in screen shows where it will send you back and flags apps it can't verify.</p>
<h2 id="token">Apps that use a token (Claude Code, Codex, Cursor, Grok, scripts)</h2>
<p>Create a token on <b>Connect AI apps</b> or <b>Agents</b>. It is shown once and stored only as a hash. Claude Code:</p>
<pre class="code">claude mcp add --transport http huhqueue \\
  https://huhqueue-studio.onrender.com/mcp \\
  --header "Authorization: Bearer hq_YOUR_TOKEN"</pre>
<h2 id="leash">The leash</h2>
<ul><li><b>Policy:</b> needs my approval (default), Buffer drafts only, or schedule without asking.</li><li><b>Daily cap</b> per token.</li><li><b>Kill switch:</b> one switch stops every agent at once. You can still post yourself.</li><li><b>Activity:</b> every action by you or an agent is logged.</li></ul>
<h2 id="rest">REST</h2>
<p>The endpoints the interface uses accept the same token as <code>Authorization: Bearer hq_…</code>. The reference is at <a href="/api/docs">/api/docs</a>.</p>"""),
    ("security", "Security and privacy", "How your Buffer connection, data and agents are protected, and how to delete everything.", "activity", """
<p>The short version: Buffer tokens are encrypted at rest and never reach the browser, the site loads only its own scripts, agents work on a leash, and one button deletes your data.</p>
<ul><li><a href="/privacy">Privacy policy</a>: what we store, why, for how long, and subprocessors.</li><li><a href="/security">Security</a>: sign-in, browser policy, agents, server checks and how to report a problem.</li><li><a href="/terms">Terms of use</a>.</li></ul>
<h2 id="activity">See what happened</h2>
<p><b>Activity</b> lists every action on your account, by you or your agents, with the time.</p>
<h2 id="delete">Delete everything</h2>
<p>Account menu, <b>Disconnect and delete my data</b>.</p>"""),
    ("faq", "FAQ", "Short answers to what people ask first.", None, """
<h2 id="post">Does HuhQueue post without me?</h2><p>No. Posts made by HuhQueue or an agent wait in your Inbox until you approve them, unless you give an agent permission to schedule, with a daily cap and a kill switch.</p>
<h2 id="buffer">Is HuhQueue made by Buffer?</h2><p>No. It is an independent, open-source app built on the Buffer API, not affiliated with Buffer.</p>
<h2 id="channels">Which channels does it work with?</h2><p>The channels connected in your Buffer account. Preflight knows the rules of X, LinkedIn, Instagram, Threads, Bluesky, Mastodon, TikTok, YouTube, Pinterest and more.</p>
<h2 id="cost">What does it cost?</h2><p>HuhQueue is free and MIT licensed. Your Buffer plan's limits still apply. With your own AI key, your provider bills you directly.</p>
<h2 id="phone">Buffer's sign-in fails on my phone</h2><p>You are probably inside another app's built-in browser. Use the sheet that appears to open the sign-in in Chrome or Safari. See <a href="/docs/connect-buffer#phone">Connect Buffer</a>.</p>
<h2 id="data">What happens to my data when I disconnect?</h2><p>Disconnect and delete removes everything HuhQueue stored for you at once. See the <a href="/privacy">privacy policy</a>.</p>
<h2 id="self">Can I run it myself?</h2><p>Yes. The code is on <a href="https://github.com/dataanalystram/buffer-contest">GitHub</a>, with a Dockerfile and setup notes.</p>"""),
]
_BY = {p[0]: p for p in PAGES}


def slugs() -> list[str]:
    return [""] + [p[0] for p in PAGES]


def _text(h: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def search_index() -> list[dict]:
    out = []
    for slug, title, summary, _, body in PAGES:
        for m in re.finditer(r'<h2 id="([^"]+)">(.*?)</h2>(.*?)(?=<h2 |\Z)', body, re.S):
            out.append({"url": f"/docs/{slug}#{m.group(1)}", "page": title, "title": _text(m.group(2)), "text": _text(m.group(3))[:300]})
        out.append({"url": f"/docs/{slug}", "page": title, "title": title, "text": summary})
    return out


def _shots(key: str, title: str) -> str:
    def pic(dev: str, w: int, h: int) -> str:
        return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="/static/docs-shots/{key}-dark-{dev}.webp">'
                f'<img src="/static/docs-shots/{key}-light-{dev}.webp" width="{w}" height="{h}" loading="lazy" decoding="async" '
                f'alt="{html.escape(title)} in HuhQueue, {"desktop" if dev == "desktop" else "phone"}"></picture>')
    return (f'<figure class="dshots" data-testid="docs-shots"><div class="dshot desk">{pic("desktop", 1200, 750)}</div>'
            f'<div class="dshot phone">{pic("phone", 390, 844)}</div>'
            f'<figcaption>Captured from the sample workspace. Follows your light or dark setting.</figcaption></figure>')


def _layout(slug: str, title: str, desc: str, main: str) -> str:
    side = "".join(f'<li><a href="/docs/{s}"{" aria-current=page" if s == slug else ""}>{html.escape(t)}</a></li>' for s, t, *_ in PAGES)
    url = f"{BASE}/docs/{slug}" if slug else f"{BASE}/docs"
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Docs", "item": f"{BASE}/docs"}] + (
        [{"@type": "ListItem", "position": 2, "name": title, "item": url}] if slug else [])}
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · HuhQueue docs</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{url}">
<meta name="color-scheme" content="light dark">
<meta property="og:type" content="article"><meta property="og:title" content="{html.escape(title)} · HuhQueue docs"><meta property="og:description" content="{html.escape(desc)}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{BASE}/og.png"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="apple-touch-icon" href="/static/brand/apple-touch-icon.png">
<link rel="stylesheet" href="/static/fonts.css"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/legal.css"><link rel="stylesheet" href="/static/docs.css">
<script type="application/ld+json">{json.dumps(crumbs)}</script>
<script src="/static/docs.js" defer></script>
</head><body>
<a class="skip" href="#main">Skip to content</a>
<header class="lg-top"><a class="brand" href="/" data-testid="brand"><img src="/static/brand/hb-mark-96.png" width="32" height="32" alt=""><span class="brand-text"><span class="name">HuhQueue</span><span class="built bt"><small>by HuhByte AI</small></span></span></a>
<nav aria-label="Main"><a href="/docs" aria-current="page">Docs</a><a href="/api/docs">API</a><a href="/login">Connect Buffer</a><a class="btn sm" href="/demo" data-testid="docs-tour">Take the tour</a></nav></header>
<div class="dk">
<aside class="dk-side" aria-label="Documentation">
  <form class="dk-search" role="search" action="/docs" method="get"><label class="sr" for="dq">Search the docs</label>
    <input id="dq" name="q" type="search" placeholder="Search the docs" autocomplete="off" data-testid="docs-search" aria-controls="dres" aria-expanded="false"></form>
  <ul class="dk-res" id="dres" role="listbox" aria-label="Search results" hidden data-testid="docs-results"></ul>
  <nav aria-label="Docs pages"><ul class="dk-nav" data-testid="docs-nav">{side}</ul></nav>
  <p class="dk-side-cta"><a class="btn2 sm" href="/demo">Open the sample</a></p>
</aside>
<main id="main" class="dk-main" data-testid="docs">{main}</main>
</div>
<footer class="lg-foot"><span>HuhQueue by</span><img class="wm lt" src="/static/brand/hb-wordmark-light.png" width="333" height="64" alt="HuhByte AI"><img class="wm dk" src="/static/brand/hb-wordmark-dark.png" width="333" height="64" alt="HuhByte AI">
<span>Built on the Buffer API, not affiliated with Buffer.</span><a href="/docs">Docs</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/security">Security</a><a href="https://github.com/dataanalystram/buffer-contest">Source code</a></footer>
</body></html>"""


def page(slug: str) -> str | None:
    if slug == "":
        cards = "".join(f'<a class="dk-card" href="/docs/{s}" data-testid="docs-card"><b>{html.escape(t)}</b><span>{html.escape(d)}</span></a>' for s, t, d, *_ in PAGES)
        main = (f'<p class="eyebrow">Documentation</p><h1>Learn HuhQueue</h1><p class="dk-lede">Start with the sample, connect Buffer when you are ready, and bring your own AI apps. '
                f'Press <kbd>Ctrl</kbd> <kbd>K</kbd> in the app to jump anywhere.</p>'
                f'<div class="dk-start"><a class="btn" href="/demo">Take the tour</a><a class="btn2" href="/docs/getting-started">Getting started</a><a class="btn2" href="/login">Connect Buffer</a></div>'
                f'<div class="dk-cards">{cards}</div>')
        return _layout("", "Docs", "Guides for HuhQueue: getting started, Connect Buffer, Planner views, Inbox, One take, agents over MCP and API, security and FAQ.", main)
    if slug == "search.json":
        return None
    p = _BY.get(slug)
    if not p:
        return None
    s, title, summary, shot, body = p
    i = [x[0] for x in PAGES].index(s)
    prev = PAGES[i - 1] if i else None
    nxt = PAGES[i + 1] if i + 1 < len(PAGES) else None
    toc = "".join(f'<li><a href="#{m.group(1)}">{_text(m.group(2))}</a></li>' for m in re.finditer(r'<h2 id="([^"]+)">(.*?)</h2>', body))
    pager = ('<nav class="dk-pager" aria-label="Next and previous">' + (f'<a href="/docs/{prev[0]}" rel="prev"><span>Previous</span>{html.escape(prev[1])}</a>' if prev else "<span></span>")
             + (f'<a href="/docs/{nxt[0]}" rel="next" class="nx"><span>Next</span>{html.escape(nxt[1])}</a>' if nxt else "") + "</nav>")
    main = (f'<p class="eyebrow"><a href="/docs">Docs</a></p><h1>{html.escape(title)}</h1><p class="dk-lede">{html.escape(summary)}</p>'
            + (f'<nav class="dk-toc" aria-label="On this page"><ul>{toc}</ul></nav>' if toc else "")
            + (_shots(shot, title) if shot else "") + f'<article class="dk-body">{body}</article>{pager}')
    return _layout(s, title, summary, main)


def missing() -> str:
    return _layout("", "Page not found", "That docs page does not exist.", '<h1>Page not found</h1><p class="dk-lede">That page does not exist. Try search, or start at <a href="/docs">the docs home</a>.</p>')
