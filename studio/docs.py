"""HuhQueue documentation at /docs: server-rendered, crawlable pages with a sidebar, search and fresh screenshots of the
real app (light and dark, desktop and phone, from scripts/premium_shots.py ... docshots). Content lives here as data so
the sitemap, the search index and the pages never drift apart. The Brand kit and Sources agent pages follow
docs/BRANDS.md and docs/SOURCES_AGENT.md."""
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
    ("brand-kit", "Brand kit and templates", "Logo, colours, fonts, front and end cards, six templates for every channel, two example brands and the Style Ledger.", "brandstudio", """
<p>The <b>Brand studio</b> (More, <b>Brand studio</b>) makes one brand look the same on every channel without redesigning anything per network. Set the kit once; every template, cover and Reel follows it.</p>
<h2 id="first-run">First run</h2>
<p>Four steps sit at the top of the screen: <b>upload your logo</b>, <b>pick a style</b>, <b>see examples</b>, <b>connect Buffer</b>. The live preview redraws the selected template at the selected channel's frame a moment after each edit, with your unsaved changes laid over the saved kit. Nothing is stored until you save.</p>
<h2 id="kit">The kit</h2>
<p>Logo, name, handle, four colours (ground, accent, text, second), a headline font (Anton, Poppins, Inter or Figtree) and a body font, headline case, a <b>front card</b> and an <b>end card</b>. Each card is an image you upload or one of four styles:</p>
<div class="dk-table"><table><thead><tr><th>Style</th><th>Look</th></tr></thead><tbody>
<tr><td><code>bold</code></td><td>Solid ground, a thick accent bar, the headline set big</td></tr>
<tr><td><code>glow</code></td><td>Dark ground with a soft accent glow behind the headline</td></tr>
<tr><td><code>grid</code></td><td>A fine grid, like an editor's layout sheet</td></tr>
<tr><td><code>minimal</code></td><td>Light ground, dark text, the accent as a marker-pen highlight</td></tr></tbody></table></div>
<p>Built-in guards: text that would not read on the ground (contrast under 3:1) falls back to black or white; a dark logo on a dark card, or a light one on a light card, is recoloured to the ink; glyphs a font cannot draw are replaced, never shown as boxes.</p>
<h2 id="templates">Templates</h2>
<p>Every template puts your front card first and your end card last.</p>
<div class="dk-table"><table><thead><tr><th>Template</th><th>What it is</th><th>Video</th></tr></thead><tbody>
<tr><td>Weekly recap</td><td>"What happened in AI in the last 7 days": one numbered slide per story, built from the week's sent and scheduled Buffer posts (the same story on four channels counts once)</td><td>Reel</td></tr>
<tr><td>Mid-week recap</td><td>Monday to Wednesday in three stories</td><td>Reel</td></tr>
<tr><td>Breaking news</td><td>BREAKING pill, time, headline, source and a faint copy of your mark</td><td>No</td></tr>
<tr><td>Launch carousel</td><td>What it does, the details, why it matters</td><td>Reel</td></tr>
<tr><td>Quote card</td><td>A quote or CEO card, plus a 9:16 clip with word-by-word captions in your font</td><td>Captioned clip</td></tr>
<tr><td>Tutorial</td><td>"Free way to do X": a free tool and numbered steps</td><td>Reel</td></tr></tbody></table></div>
<h2 id="channels">One render, every channel</h2>
<p>Instagram, LinkedIn, Threads and Facebook get 4:5; X, Bluesky and Mastodon 16:9; YouTube Shorts and TikTok 9:16 (video only). Carousels follow each network's slide cap (X and Bluesky 4, LinkedIn 9, Instagram 10), and when a carousel is cut your end card stays last. Reels are silent on purpose: add licensed music in the network's own app.</p>
<h2 id="slots">Recurring slots</h2>
<p>Put a template on a schedule, for example the weekly recap every Monday at 20:00 Europe/Dublin. When the slot fires it renders the template from that week's posts and saves one <b>Buffer draft</b> per channel at the slot time, with the slides attached. A person approves it in Buffer, or in Approvals when an agent set it up. <b>Run now</b> fires a slot at once; the hosted app also checks slots every ten minutes, never for the sample.</p>
<h2 id="examples">Example brands</h2>
<p>The sample workspace has two example brands, each with a gallery of pre-rendered slides, covers and Reels. Switch between them from the <b>Example brands</b> row: a brand change is one click, and every channel follows.</p>
<ul><li><b>HuhByte AI</b>: black ground, lavender accent, Anton headlines, Poppins body; Instagram, YouTube Shorts, X and LinkedIn.</li>
<li><b>Buffer (concept example, not affiliated with Buffer)</b>: Buffer's public mark and the palette from buffer.com's own colour tokens, with Figtree. Every render carries "Concept example, not affiliated with Buffer". Eight channels, same stories and templates as HuhByte.</li></ul>
<figure class="dk-ex"><img src="/static/examples/huhbyte/overview-recap.jpg" alt="HuhByte AI weekly recap carousel, front card to end card" loading="lazy" decoding="async"><figcaption>HuhByte AI: the weekly recap carousel.</figcaption></figure>
<figure class="dk-ex"><img src="/static/examples/buffer/overview-covers.jpg" alt="Buffer concept example: one breaking cover rendered for eight channels" loading="lazy" decoding="async"><figcaption>Buffer concept example: one cover, eight channels.</figcaption></figure>
<h2 id="ledger">Style Ledger</h2>
<p>Every correction you give becomes a written rule ("logo bottom right", "max 2 hashtags"). Each render and caption is checked against all of them, and a failed check names the rule. Add rules in plain words and turn them on or off in <b>Brand and ledger</b>; only a person can change a rule, and agents propose them with <code>remember_rule</code>.</p>
<h2 id="api">API and MCP</h2>
<div class="dk-table"><table><thead><tr><th>In the app</th><th>REST</th><th>MCP</th></tr></thead><tbody>
<tr><td>Load the screen</td><td><code>GET /api/brand-studio</code></td><td><code>get_brand</code>, <code>list_templates</code>, <code>list_brand_examples</code></td></tr>
<tr><td>Save kit and cards</td><td><code>POST /api/brand-studio</code></td><td>People only</td></tr>
<tr><td>Live preview</td><td><code>POST /api/brand-studio/preview</code></td><td>-</td></tr>
<tr><td>Use an example brand</td><td><code>POST /api/brand-studio/examples/{huhbyte,buffer}</code></td><td><code>apply_brand_example</code></td></tr>
<tr><td>Render for my channels</td><td><code>POST /api/templates/render</code></td><td><code>render_template</code></td></tr>
<tr><td>Recurring slots</td><td><code>GET/POST /api/templates/slots</code>, <code>POST /api/templates/slots/{id}/run</code></td><td><code>schedule_template_slot</code>, <code>templates_slot_run</code></td></tr></tbody></table></div>
<p>Every write reports its <code>buffer_cost</code>: the real Buffer requests it made on a connected account. The sample has no Buffer behind it, so it is always 0.</p>
<h2 id="limits">Limits</h2>
<p>Slides render in the app with Pillow. Video needs FFmpeg, which the hosted app does not have, so the hosted app renders slides live and shows the pre-rendered example Reels; self-hosted installs render video. The sample's renders count against its daily cap, and it never renders video live.</p>"""),
    ("sources-agent", "Sources agent", "Scan YC, Hacker News, lab blogs, Product Hunt and X; cluster the news; write it per channel; save Buffer drafts a person approves.", "sources", """
<p>The <b>Sources agent</b> (More, <b>Sources agent</b>) finds news, writes it in your brand for each channel and saves it to Buffer as <b>drafts</b> a person approves. Nothing it does publishes.</p>
<h2 id="flow">Scan, pick, write, save</h2>
<ol><li><b>Scan now</b> reads every source you turned on, removes duplicates (same link or same title) and groups the same story from several sources into one card that says where else it ran.</li>
<li>Tick the stories you want and the channels to write for.</li>
<li><b>Write posts</b> shows one post per channel, in that channel's shape, with a breaking cover in your brand. Edit any of them.</li>
<li><b>Save as Buffer drafts</b> sends exactly what you previewed, with your edits. Each result shows the Buffer request it made.</li></ol>
<h2 id="sources">Sources (no paid keys)</h2>
<div class="dk-table"><table><thead><tr><th>Source</th><th>How it reads</th><th>Key</th></tr></thead><tbody>
<tr><td>YC Launches</td><td>The public launches page</td><td>None</td></tr>
<tr><td>Hacker News</td><td>The front page, through Algolia's free HN API</td><td>None</td></tr>
<tr><td>RSS / Atom</td><td>Up to 8 feeds you list (defaults: OpenAI, Google DeepMind, Hugging Face and Google AI blogs)</td><td>None</td></tr>
<tr><td>Product Hunt</td><td>Today's top launches, API v2</td><td>Your free developer token</td></tr>
<tr><td>X</td><td>Recent search with your query, API v2</td><td>Your own bearer token. A logged-in X is never scraped</td></tr></tbody></table></div>
<p>Every live fetch goes through the safe-fetch guard: no automatic redirects, every hop's host re-checked, size caps. Keys are sealed at rest, shown only as their last four characters, and only a person (never an agent token) can add or remove them. Real accounts get 30 live scans a day.</p>
<h2 id="judging">Judging and deciding</h2>
<p>The model makes judgments; code makes decisions. Each story is judged once (is it about AI, topic, kind, relevance, unconfirmed, sensitive, an allegation), by Jev first, then SubQ, then deterministic rules past the daily cap, without keys or in the sample. Each card says which one judged it. Code then decides:</p>
<ul><li><b>Lane</b>: postable, idea bank (evergreen), low, or hidden (off-topic HN or X).</li>
<li><b>Agent permission</b>: <i>person only</i> for sensitive stories or allegations (an agent may not draft them), <i>verify first</i> for unconfirmed stories or a single X post, otherwise <i>agent OK</i>.</li>
<li><b>Rank</b>: relevance, freshness over 7 days, engagement and how many sources carry it.</li></ul>
<h2 id="writing">How posts are written</h2>
<p>Each channel gets its own shape: X a headline, one line, the link and one tag; LinkedIn a hook, the points, why it matters and the source; Instagram a headline, bullets, "link in bio" and up to 5 tags; YouTube and TikTok title first with the source; Bluesky no tags. Your Style Ledger rules are applied and each caption fits Buffer's character count. The writer is your own AI key when you set one in <b>AI engine</b>, otherwise the app's capped starter model, otherwise the built-in writer. Drafts go through the same preview and submit as everything else, so an agent token's approval rules still hold.</p>
<h2 id="sample">In the sample</h2>
<p>The <a href="/demo">demo</a> uses recorded sources from 31 October 2026 (YC Launches, the HN front page and four lab feeds; Product Hunt and X items are made up and titled "Sample:"). It makes no live calls, its clusters are always the same, drafts go to the sample's pretend Buffer, and it shows <b>0 Buffer requests</b>.</p>
<h2 id="radar">News radar</h2>
<p><b>News radar</b> still follows up to 12 RSS or Atom feeds, newest first, one click from a post for every channel; agents read it with <code>list_stories</code>. The Sources agent adds many sources at once, clustering, judging and per-channel drafts on top.</p>
<h2 id="api">API and MCP</h2>
<div class="dk-table"><table><thead><tr><th>In the app</th><th>REST</th><th>MCP</th></tr></thead><tbody>
<tr><td>Settings</td><td><code>GET /api/sources</code>, <code>POST /api/sources/settings</code></td><td><code>sources_settings</code></td></tr>
<tr><td>Scan now</td><td><code>POST /api/sources/scan</code></td><td><code>sources_scan</code></td></tr>
<tr><td>A past scan</td><td><code>GET /api/sources/runs/{id}</code></td><td>-</td></tr>
<tr><td>Write posts, save as Buffer drafts</td><td><code>POST /api/sources/draft</code> (<code>dry_run</code>, <code>cluster_ids</code>, <code>channel_ids</code>, <code>texts</code>)</td><td><code>sources_draft</code></td></tr></tbody></table></div>"""),
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
<h2 id="oauth">Apps that sign in (Claude, ChatGPT, Grok on grok.com)</h2>
<p>Add a custom connector and paste your MCP address (on grok.com: Connectors, New Connector, Custom). The app sends you to HuhQueue to sign in with Buffer and choose what it may do. The sign-in screen shows where it will send you back and flags apps it can't verify.</p>
<h2 id="token">Apps that use a token (Claude Code, Codex, Cursor, the xAI API, scripts)</h2>
<p>Create a token on <b>Connect AI apps</b> or <b>Agents</b>. It is shown once and stored only as a hash. Claude Code:</p>
<pre class="code">claude mcp add --transport http huhqueue \\
  https://huhqueue-studio.onrender.com/mcp \\
  --header "Authorization: Bearer hq_YOUR_TOKEN"</pre>
<p>Codex (the CLI, IDE extension and ChatGPT desktop app share <code>~/.codex/config.toml</code>): export the token as <code>HUHQUEUE_TOKEN</code>, then</p>
<pre class="code">codex mcp add huhqueue --url https://huhqueue-studio.onrender.com/mcp \
  --bearer-token-env-var HUHQUEUE_TOKEN</pre>
<p>The xAI API: add <code>{"type": "mcp", "server_url": "https://huhqueue-studio.onrender.com/mcp", "server_label": "huhqueue", "headers": {"Authorization": "Bearer hq_YOUR_TOKEN"}}</code> to the request's <code>tools</code>.</p>
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
