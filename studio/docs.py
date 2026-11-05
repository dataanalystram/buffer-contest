"""HuhQueue documentation at /docs: server-rendered, crawlable pages with a sidebar, search and fresh screenshots of the
real app (light and dark, desktop and phone, from scripts/premium_shots.py ... docshots). Content lives here as data so
the sitemap, the search index and the pages never drift apart. The Brand and News pages follow
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
<ol><li>Open <b>Make a post</b>, stay on <b>With the agent</b> and choose <b>Research a topic</b>, <b>From a link</b> or <b>My photo or video</b>.</li>
<li>Pick the channels and a format, or let the agent pick, then choose <b>Make it</b>.</li>
<li>Watch the timeline: it opens the source, checks it against other outlets, renders the media in your brand and writes a caption per channel.</li>
<li>Say what to change ("logo smaller", "no hashtags"). Each correction becomes a rule in your Style Ledger.</li></ol>
<p>For a fast text-only post from a link, switch to <b>Quick text posts</b> on the same screen.</p>
<h2 id="plan">4. Plan and approve</h2>
<p>Ready posts land in the <a href="/docs/planner">Planner</a>, and anything waiting for a person shows in the <a href="/docs/inbox">Inbox</a> under <b>Waiting for your approval</b>. Approve, and it goes to Buffer at its time.</p>
<h2 id="screens">The ten screens</h2>
<p>One job, one place. On a computer the side rail lists all ten; on a phone the bottom bar holds the everyday four and <b>Menu</b> holds the rest.</p>
<div class="dk-table"><table><thead><tr><th>Screen</th><th>What it is for</th></tr></thead><tbody>
<tr><td><b>Inbox</b></td><td>Your day: posts waiting for your approval, today's queue, what needs attention, agent activity and posts recommended for you</td></tr>
<tr><td><b>Make a post</b></td><td>With the agent, or <b>Quick text posts</b> from a link</td></tr>
<tr><td><b>Library</b></td><td>Every file you uploaded or sent from a device and every image, carousel and Reel the agent made; deleted items wait 7 days in the <b>Bin</b></td></tr>
<tr><td><b>One take</b></td><td>One long video into a week of clips</td></tr>
<tr><td><b>Planner</b></td><td>Week, Month, Day, List, Board and Grid</td></tr>
<tr><td><b>News</b></td><td><b>For you</b>, <b>Story scan</b> and the <b>Sources</b> you follow</td></tr>
<tr><td><b>Brand</b></td><td>Your <b>Kit</b>, <b>Templates</b> and the <b>Style Ledger</b></td></tr>
<tr><td><b>AI apps</b></td><td>Connect Claude, ChatGPT, Grok, Cursor, Codex or Claude Code; tokens and the kill switch</td></tr>
<tr><td><b>Activity</b></td><td>Everything you and your agents did</td></tr>
<tr><td><b>Settings</b></td><td>Setup, connected channels, AI engine and your account</td></tr></tbody></table></div>
<p>Old links still work: Approvals opens the Inbox, Write from a link opens Quick text posts, Brand studio opens Brand, and so on.</p>
<h2 id="keys">Keyboard</h2>
<p>Press <kbd>Ctrl</kbd> <kbd>K</kbd> (<kbd>⌘</kbd> <kbd>K</kbd> on a Mac) anywhere in the app to jump to a screen, a Planner view or a doc. Press <kbd>?</kbd> for every shortcut. <b>Help</b> in the header opens the tour and these docs from any screen.</p>"""),
    ("connect-buffer", "Connect Buffer", "Sign in with Buffer's own OAuth, including from a phone, and what HuhQueue can do with it.", "landing", """
<p>HuhQueue works on your real Buffer channels through the Buffer API. Connecting takes one sign-in.</p>
<h2 id="how">How to connect</h2>
<ol><li>Choose <b>Connect Buffer</b> next to <b>Try the demo</b> at the top of the home page, or <b>Connect your Buffer account</b> under the link field.</li>
<li>Buffer's sign-in page opens as a full page. Sign in and allow HuhQueue.</li>
<li>You come back to your Inbox, with your channels loaded from Buffer. A short setup asks what you post, which channels to use and your website for the brand kit; skip it any time and reopen it from <b>Settings</b>, <b>Setup</b>.</li></ol>
<h2 id="phone">On a phone</h2>
<p>Buffer's sign-in can fail inside the browser built into apps like Instagram, Facebook, TikTok, X or LinkedIn. If you open HuhQueue from one of those, <b>Connect Buffer</b> shows a short sheet that opens the sign-in in Chrome or Safari, or copies the link. Sign-in is never shown inside a frame.</p>
<h2 id="what">What HuhQueue can do</h2>
<ul><li>Read your organization, channels, posting times and posts.</li><li>Create posts, drafts and ideas when you, or an agent within your rules, submit them.</li><li>Edit, reschedule or delete posts you change in the Planner.</li></ul>
<p>Your Buffer tokens are encrypted on the server and never reach the browser. Every Buffer call counts against your Buffer plan's API budget, shown in the header.</p>
<h2 id="disconnect">Disconnect</h2>
<p><b>Settings</b>, <b>Account</b> (or the account menu), <b>Disconnect and delete my data</b>, removes everything HuhQueue stored for you at once. You can also revoke access from your Buffer account settings.</p>"""),
    ("brand-kit", "Brand: kit, templates and Style Ledger", "Logo, colours, fonts, front and end cards, six templates for every channel, two example brands and the Style Ledger.", "brandstudio", """
<p><b>Brand</b> makes one brand look the same on every channel without redesigning anything per network. Set the kit once; every template, cover and Reel follows it. It has three tabs: <b>Kit</b> (website import, logo, colours, fonts, voice, front and end cards), <b>Templates</b> (templates, examples and recurring slots) and <b>Style Ledger</b>. There is one kit: every tab and every render reads the same one.</p>
<h2 id="first-run">First run</h2>
<p>Four steps sit at the top of the <b>Kit</b> tab: <b>upload your logo</b>, <b>pick a style</b>, <b>see examples</b>, <b>connect Buffer</b>. The live preview redraws the selected template at the selected channel's frame a moment after each edit, with your unsaved changes laid over the saved kit. Nothing is stored until you save.</p>
<h2 id="kit">The kit</h2>
<p>Logo, name, handle, four colours (ground, accent, text, second), a headline font (Anton, Poppins, Inter or Figtree) and a body font, headline case, a <b>front card</b> and an <b>end card</b>. Each card is an image you upload or one of four styles:</p>
<div class="dk-table"><table><thead><tr><th>Style</th><th>Look</th></tr></thead><tbody>
<tr><td><code>bold</code></td><td>Solid ground, a thick accent bar, the headline set big</td></tr>
<tr><td><code>glow</code></td><td>Dark ground with a soft accent glow behind the headline</td></tr>
<tr><td><code>grid</code></td><td>A fine grid, like an editor's layout sheet</td></tr>
<tr><td><code>minimal</code></td><td>Light ground, dark text, the accent as a marker-pen highlight</td></tr></tbody></table></div>
<p>Built-in guards: text that would not read on the ground (contrast under 3:1) falls back to black or white; a dark logo on a dark card, or a light one on a light card, is recoloured to the ink; glyphs a font cannot draw are replaced, never shown as boxes.</p>
<h2 id="templates">Templates</h2>
<p>On the <b>Templates</b> tab. Every template puts your front card first and your end card last.</p>
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
<p>Put a template on a schedule, for example the weekly recap every Monday at 20:00 Europe/Dublin. When the slot fires it renders the template from that week's posts and saves one <b>Buffer draft</b> per channel at the slot time, with the slides attached. A person approves it in Buffer, or in the Inbox under <b>Waiting for your approval</b> when an agent set it up. <b>Run now</b> fires a slot at once; the hosted app also checks slots every ten minutes, never for the sample.</p>
<h2 id="examples">Example brands</h2>
<p>The sample workspace has two example brands, each with a gallery of pre-rendered slides, covers and Reels. Switch between them from the <b>Example brands</b> row: a brand change is one click, and every channel follows.</p>
<ul><li><b>HuhByte AI</b>: black ground, lavender accent, Anton headlines, Poppins body; Instagram, YouTube Shorts, X and LinkedIn.</li>
<li><b>Buffer (concept example, not affiliated with Buffer)</b>: Buffer's public mark and the palette from buffer.com's own colour tokens, with Figtree. Every render carries "Concept example, not affiliated with Buffer". Eight channels, same stories and templates as HuhByte.</li></ul>
<figure class="dk-ex"><img src="/static/examples/huhbyte/overview-recap.jpg" alt="HuhByte AI weekly recap carousel, front card to end card" loading="lazy" decoding="async"><figcaption>HuhByte AI: the weekly recap carousel.</figcaption></figure>
<figure class="dk-ex"><img src="/static/examples/buffer/overview-covers.jpg" alt="Buffer concept example: one breaking cover rendered for eight channels" loading="lazy" decoding="async"><figcaption>Buffer concept example: one cover, eight channels.</figcaption></figure>
<h2 id="ledger">Style Ledger</h2>
<p>Every correction you give becomes a written rule ("logo bottom right", "max 2 hashtags", "say launch, not drop"). Rules are grouped (voice, words to avoid or use, hashtags, emojis, calls to action, visual and brand), each shows where it came from (taught by you, learned from your edits, added by an AI app, or your brand kit), which channels it applies to, and before and after examples of what it changed. Each render and caption is checked against all of them, and a failed check names the rule.</p><p><b>Learned from your edits.</b> When you edit a caption the agent wrote, HuhQueue notes what kind of change it was. After the same change in three posts it suggests a rule ("Suggested: never use game-changer, from 3 of your edits"). Nothing applies until you accept it, and a dismissed suggestion does not come back.</p><p><b>On every draft.</b> Each draft lists the rules it followed and, for each fact, the line of the source it came from (or a warning when it is not in the source). <b>Share for review</b> makes a read-only link that expires (72 hours by default) where someone can approve or ask for changes; their answer shows in the post's thread. Rules come only from your own words: text on a page or in a video that says "never say X" cannot add one. AI apps use <code>list_style_rules</code>, <code>add_style_rule</code>, <code>update_style_rule</code> and <code>delete_style_rule</code> (both with a confirm step), <code>list_rule_suggestions</code>, <code>decide_rule_suggestion</code>, <code>check_draft</code> and <code>create_review_link</code>.</p>
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
    ("sources-agent", "News: For you, Story scan and Sources", "Scan YC, Hacker News, lab blogs, Product Hunt and X; cluster the news; write it per channel; save Buffer drafts a person approves.", "sources", """
<p><b>News</b> is where stories come from. <b>For you</b> is a card feed from the feeds you follow; <b>Story scan</b> finds news across many sources at once, writes it in your brand for each channel and saves it to Buffer as <b>drafts</b> a person approves. Nothing it does publishes.</p>
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
<h2 id="keys">Adding your keys (Settings, Integrations)</h2>
<p>Product Hunt, X and the YouTube Data API each have a card in <b>Settings</b>, <b>Integrations</b>: what the key unlocks, how to get one, a password field, <b>Test</b> (a live check that reports a status only), <b>Save</b> and <b>Remove</b>, and a masked status such as "Connected · …abcd · tested 2 min ago". Your AI model key stays in <b>Settings</b>, <b>AI engine</b>.</p>
<p>You don't have to add anything: HuhQueue reads Product Hunt for everyone with its own app (or Product Hunt's public feed), and reads a linked X post without any key through public embeds (X's oEmbed, the syndication endpoint used by Vercel's react-tweet, and FxTwitter). Your own keys are optional overrides. Those X endpoints are unofficial and can break; HuhQueue only reads the one public post you link, never bulk-scrapes X. Product Hunt's API is non-commercial by default.</p>
<p>Product Hunt: open <a href="https://api.producthunt.com/v2/oauth/applications" rel="noopener">api.producthunt.com/v2/oauth/applications</a>, choose <b>Add an application</b>, set the Redirect URI to <code>https://huhqueue-studio.onrender.com/integrations/producthunt/callback</code>, create it, then choose <b>Create Token</b> and paste the Developer Token into the Product Hunt card.</p>
<h2 id="judging">Judging and deciding</h2>
<p>The model makes judgments; code makes decisions. Each story is judged once (is it about AI, topic, kind, relevance, unconfirmed, sensitive, an allegation), by Jev first, then SubQ, then deterministic rules past the daily cap, without keys or in the sample. Each card says which one judged it. Code then decides:</p>
<ul><li><b>Lane</b>: postable, idea bank (evergreen), low, or hidden (off-topic HN or X).</li>
<li><b>Agent permission</b>: <i>person only</i> for sensitive stories or allegations (an agent may not draft them), <i>verify first</i> for unconfirmed stories or a single X post, otherwise <i>agent OK</i>.</li>
<li><b>Rank</b>: relevance, freshness over 7 days, engagement and how many sources carry it.</li></ul>
<h2 id="writing">How posts are written</h2>
<p>Each channel gets its own shape: X a headline, one line, the link and one tag; LinkedIn a hook, the points, why it matters and the source; Instagram a headline, bullets, "link in bio" and up to 5 tags; YouTube and TikTok title first with the source; Bluesky no tags. Your Style Ledger rules are applied and each caption fits Buffer's character count. The writer is your own AI key when you set one in <b>Settings</b>, <b>AI engine</b>, otherwise the app's capped starter model, otherwise the built-in writer. Drafts go through the same preview and submit as everything else, so an agent token's approval rules still hold.</p>
<h2 id="sample">In the sample</h2>
<p>The <a href="/demo">demo</a> uses recorded sources from 31 October 2026 (YC Launches, the HN front page and four lab feeds; Product Hunt and X items are made up and titled "Sample:"). It makes no live calls, its clusters are always the same, drafts go to the sample's pretend Buffer, and it shows <b>0 Buffer requests</b>.</p>
<h2 id="radar">For you and Sources</h2>
<p><b>For you</b> shows the newest stories from the RSS or Atom feeds you follow (up to 12), each a card with the site's icon, the headline, one line on why it matters and <b>Make post</b>; agents read it with <code>list_stories</code>. <b>Sources</b> is the one feed list both For you and Story scan read. Story scan adds many sources at once, clustering, judging and per-channel drafts on top.</p>
<h2 id="api">API and MCP</h2>
<div class="dk-table"><table><thead><tr><th>In the app</th><th>REST</th><th>MCP</th></tr></thead><tbody>
<tr><td>Settings</td><td><code>GET /api/sources</code>, <code>POST /api/sources/settings</code></td><td><code>sources_settings</code></td></tr>
<tr><td>Scan now</td><td><code>POST /api/sources/scan</code></td><td><code>sources_scan</code></td></tr>
<tr><td>Source library (News › Sources)</td><td><code>GET /api/sources/library</code>, <code>POST /api/sources/refresh</code>, <code>POST /api/sources/enabled</code></td><td><code>list_sources</code>, <code>refresh_sources</code>, <code>set_source_enabled</code></td></tr>
<tr><td>A past scan</td><td><code>GET /api/sources/runs/{id}</code></td><td>-</td></tr>
<tr><td>Write posts, save as Buffer drafts</td><td><code>POST /api/sources/draft</code> (<code>dry_run</code>, <code>cluster_ids</code>, <code>channel_ids</code>, <code>texts</code>)</td><td><code>sources_draft</code></td></tr></tbody></table></div>"""),
    ("inbox", "Inbox", "Your day on one screen: posts to approve, today's queue, what needs attention, agent activity and posts recommended for you.", "inbox", """
<p>The Inbox is the first screen after you sign in: a dashboard of your day. From the top:</p>
<ul><li><b>Stats</b>: posts scheduled, posts awaiting approval, failed posts and the Buffer requests you have left.</li>
<li><b>Waiting for your approval</b>: posts your agents wrote, as cards with each channel's media and the app that wrote it. Approve and they go to Buffer at their time. This is the only place approvals live.</li>
<li><b>Video links from your AI apps</b>: a card with the link, a note that nothing was fetched yet, and the rights statement to tick before Approve. Tired of approving every video? Settings → Privacy &amp; data → <b>Auto-approve video links</b> (off by default) approves them for all your apps or the ones you pick, logs each one in Activity as auto-approved, and turns off in one click. It never covers paid features or posting.</li>
<li><b>Today's queue</b>: what Buffer sends in the next 24 hours, in order (<b>Coming up</b> when nothing is due today).</li>
<li><b>Needs attention</b>: grouped by urgency, <b>Now</b>, <b>Soon</b> and <b>This week</b>.</li>
<li><b>Agent activity</b>: what each AI app did, with its logo.</li>
<li><b>Recommended for you</b>: stories worth a post, from News.</li></ul>
<h2 id="groups">What needs attention</h2>
<ul><li><b>Failed in Buffer:</b> with Buffer's own reason and a way to fix and reschedule.</li>
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
<p>Try <b>Try the sample take</b> to see it on Buffer's own 89-minute Buffer Commit kickoff Q&amp;A, split into 5 credited clips. Upload a video up to the limit shown on the drop zone (4 GB in parts that resume after a dropped connection when the server has a storage bucket, 100 MB otherwise), or link to a file you host. You confirm you own the video or have the rights to it.</p>
<h2 id="bin">The Bin</h2>
<p>Deleting a take or one clip moves it to the <b>Bin</b> under <b>Your takes</b> for 7 days; the toast right after has <b>Undo</b>. In the Bin each item shows the days left, with <b>Restore</b> and <b>Delete forever</b> (it asks first, and can't be undone). After 7 days it and its files are removed for good.</p>"""),
    ("agents", "AI apps, MCP and API", "Use HuhQueue from Claude, ChatGPT, Grok, Claude Code, Codex, Cursor or your own code, on a leash.", "connect", """
<p>Everything the app can do is also a tool for your AI apps over MCP and an endpoint over REST. Open <b>AI apps</b> in the app for per-app steps, your MCP address and when each app last used it.</p>
<h2 id="oauth">Apps that sign in (Claude, ChatGPT, Grok on grok.com)</h2>
<p>Add a custom connector and paste your MCP address (in ChatGPT: <a href="https://chatgpt.com/plugins">Plugins</a>, the plus button, <b>Add custom MCP server</b>, OAuth, then install the plugin; on grok.com: Connectors, New Connector, Custom). The app sends you to HuhQueue to sign in with Buffer and choose what it may do. The sign-in screen shows where it will send you back and flags apps it can't verify.</p>
<h2 id="token">Apps that use a token (Claude Code, Codex, Cursor, the xAI API, scripts)</h2>
<p>Create a token on <b>AI apps</b>. It is shown once and stored only as a hash. Claude Code:</p>
<pre class="code">claude mcp add --transport http huhqueue \\
  https://huhqueue-studio.onrender.com/mcp \\
  --header "Authorization: Bearer hq_YOUR_TOKEN"</pre>
<p>Codex (the CLI, IDE extension and ChatGPT desktop app share <code>~/.codex/config.toml</code>): export the token as <code>HUHQUEUE_TOKEN</code>, then</p>
<pre class="code">codex mcp add huhqueue --url https://huhqueue-studio.onrender.com/mcp \
  --bearer-token-env-var HUHQUEUE_TOKEN</pre>
<p>The xAI API: add <code>{"type": "mcp", "server_url": "https://huhqueue-studio.onrender.com/mcp", "server_label": "huhqueue", "headers": {"Authorization": "Bearer hq_YOUR_TOKEN"}}</code> to the request's <code>tools</code>.</p>
<h2 id="leash">The leash</h2>
<ul><li><b>Policy:</b> needs my approval (default), Buffer drafts only, or schedule without asking.</li><li><b>Daily cap</b> per token.</li><li><b>Kill switch:</b> <b>Pause all agents</b> on AI apps stops every agent at once. You can still post yourself.</li><li><b>Activity:</b> every action by you or an agent is logged.</li></ul>
<h2 id="files">Getting a video in from a chat app</h2>
<p>Claude.ai and ChatGPT run their code in a sandbox that can't reach outside hosts, so they can't upload a file to HuhQueue themselves. Two ways that work:</p>
<ul><li><b>Send a file:</b> the app calls <code>create_send_code</code> and gives you a short code and a link. Open <a href="/send">/send</a> on any device (your phone works: scan the QR code), drop the file in, and the app finds it with <code>list_recent_uploads</code>. In HuhQueue: Library, <b>Send from a device</b>.</li>
<li><b>A share link:</b> share the file from Google Drive, Dropbox, OneDrive or iCloud with "anyone with the link" and give the link to the app (<code>import_from_link</code>), or paste it in Library, <b>From a link</b>.</li></ul>
<p>Files up to 100 MB each, in a 200 MB Library. Apps that can run curl (Claude Code, Codex, Cursor) use <code>create_upload</code> instead.</p>
<p><b>How a send code is kept.</b> A code lasts 30 minutes and takes up to 10 files, and HuhQueue stores only its hash. <b>Stop receiving</b> or <b>New code</b> ends it at once. A code an AI app made also stops when that app's token is revoked or expires, or while the kill switch is on.</p>
<h2 id="rest">REST</h2>
<p>The endpoints the interface uses accept the same token as <code>Authorization: Bearer hq_…</code>. The reference is at <a href="/docs/api">/docs/api</a>.</p>"""),
    ("security", "Security and privacy", "How your Buffer connection, data and agents are protected, and how to delete everything.", "activity", """
<p>The short version: Buffer tokens are encrypted at rest and never reach the browser, the site loads only its own scripts, agents work on a leash, and one button deletes your data.</p>
<ul><li><a href="/privacy">Privacy policy</a>: what we store, why, for how long, and subprocessors.</li><li><a href="/security">Security</a>: sign-in, browser policy, agents, server checks and how to report a problem.</li><li><a href="/terms">Terms of use</a>.</li></ul>
<h2 id="activity">See what happened</h2>
<p><b>Activity</b> lists every action on your account, by you or your agents, with the time. Entries are kept 90 days; the IP address and browser of a sign-in are blanked after 30 days. Calls an AI app makes are logged with the tool name, a summary of allowed arguments, status, duration and time: calls not tied to a post run are removed after 24 hours, and calls tied to a run stay with that run until you delete your data.</p>
<h2 id="media">Your media</h2>
<p>Location and camera metadata is removed when a file is stored: EXIF, XMP and IPTC from JPEG, text and EXIF chunks from PNG, and GPS positions in MP4 and MOV are blanked. WebP, GIF and WebM files are stored as sent. Buffer fetches your media from <code>/m/</code> links. Each is a random 144-bit token, not a signed or expiring link: anyone who has the link can open the file until you delete it, and it may be cached publicly for up to 1 day.</p>
<p>A deleted Library item waits 7 days in the <b>Bin</b>, then is removed for good (checked every 6 hours). <b>Delete permanently</b> and <b>Empty bin</b> remove the records and files at once. Media a post scheduled in Buffer uses can't be deleted permanently.</p>
<h2 id="delete">Delete everything</h2>
<p><b>Settings</b>, <b>Account</b> (or the account menu), <b>Disconnect and delete my data</b>.</p>"""),
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
    side += f'<li><a href="/docs/api"{" aria-current=page" if slug == "api" else ""}>API reference</a></li>'
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
<nav aria-label="Main"><a href="/docs" aria-current="page">Docs</a><a href="/docs/api">API</a><a href="/login">Connect Buffer</a><a class="btn sm" href="/demo" data-testid="docs-tour">Take the tour</a></nav></header>
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
<span>Built on the Buffer API, not affiliated with Buffer.</span><a href="/docs">Docs</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/security">Security</a><a href="/docs/api">API</a><a href="https://github.com/dataanalystram/buffer-contest">Source code</a></footer>
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


# ---------------------------------------------------------------- API reference (/docs/api)
# A first-party page, rendered on the server from the app's own OpenAPI schema and MCP tool list. It replaces FastAPI's
# Swagger UI at /api/docs, which loads its script and CSS from a CDN and runs an inline script: the site's
# Content-Security-Policy (script-src 'self') blocks both, so that page was blank and the footer's API link looked dead.
_GROUPS = [  # (path prefix, section title)
    ("/api/session", "Account and session"), ("/api/me", "Account and session"), ("/api/logout", "Account and session"),
    ("/api/disconnect", "Account and session"), ("/api/device-link", "Account and session"),
    ("/api/channels", "Channels and queue"), ("/api/queue", "Channels and queue"), ("/api/calendar", "Channels and queue"),
    ("/api/inbox", "Channels and queue"),
    ("/api/draft", "Posts"), ("/api/posts", "Posts"), ("/api/preflight", "Posts"), ("/api/check", "Posts"),
    ("/api/media", "Posts"), ("/api/ideas", "Posts"), ("/api/instagram", "Posts"), ("/api/library", "Library"),
    ("/api/images", "Images"),
    ("/api/approvals", "Approvals"),
    ("/api/agent", "Make a post (agent)"), ("/api/recommendations", "Make a post (agent)"), ("/api/onboarding", "Make a post (agent)"),
    ("/api/takes", "One take"),
    ("/api/radar", "News"), ("/api/sources", "News"), ("/api/news", "News"),
    ("/api/brand", "Brand"), ("/api/templates", "Brand"), ("/api/ledger", "Brand"), ("/api/review-links", "Make a post (agent)"),
    ("/api/ai", "AI engine"),
    ("/api/integrations", "Integrations (your keys)"),
    ("/api/agent-tokens", "AI apps, tokens and safety"), ("/api/safety", "AI apps, tokens and safety"),
    ("/api/plan", "Limits"), ("/api/limits", "Limits"),
    ("/api/activity", "Activity"), ("/api/audit", "Activity"),
    ("/api/stats", "Public"), ("/healthz", "Public"),
]
# Plain-English line per endpoint; anything missing falls back to the route's OpenAPI summary (a test checks none is missing)
_DESC = {
    "GET /api/integrations": "Your key-based integrations (Product Hunt, X, YouTube): status and a masked hint, never the key",
    "POST /api/integrations/{key}": "Save a key for one integration (person only; encrypted at rest)",
    "POST /api/integrations/{key}/test": "Live-check a pasted or saved key; returns a status only (person only, rate-capped)",
    "DELETE /api/integrations/{key}": "Remove one integration's key",
    "POST /api/agent/runs/{rid}/packages/{channel_id}/media": "Replace or regenerate one channel's media in an agent run",
    "GET /api/instagram/audio": "search_instagram_audio: Instagram music and original sounds for one channel (blank q = trending; needs the channel connected through a Facebook Page)",
    "POST /api/agent/runs/{rid}/packages/{channel_id}/audio": "set_reel_audio: put an Instagram music track (volumes, start) on one Reel package, or remove it",
    "POST /api/agent/runs/{rid}/packages/{channel_id}/cover": "Use another of the 3 branded cover variants for one channel's Reel",
    "POST /api/agent/runs/{rid}/packages/{channel_id}/update-draft": "update_package_draft: a package that changed after it was saved as a Buffer draft puts its new media and caption on that same draft (a deleted draft is saved again; scheduled posts are never changed)",
    "POST /api/agent/runs/{rid}/undo": "undo_package_media: go back to a channel's previous media ({channel}), e.g. Back to the Reel after a template switch; {} undoes the latest change",
    "GET /api/agent/runs/{rid}/music": "list_music: each package's Music (none by default, or the Instagram track), what it supports (a Reel takes Instagram music; a carousel's music is added in the Instagram app), and Instagram tracks for q",
    "POST /api/agent/runs/{rid}/music": "set_post_music: {music: none} removes it; {music: instagram, audio | query} puts an Instagram track on the Reel (no audio or query: needs_choice)",
    "POST /api/agent/runs/{rid}/kit": "use_brand_kit: this run's brand kit by name or id ({default: true} also makes it the account's); no kit -> needs_choice with the account's available kits",
    "GET /api/media/{token}/preview": "get_media_preview: frame stills (JPEG, default at 0, 1 and 2 s) of your own video or image and a 15-minute signed download link",
    "POST /api/buffer/drafts/{post_id}/media": "replace_draft_media: new media on any Buffer draft (edit in place, or a new draft with the same caption; the old one is deleted only with delete_old and confirm)",
    "GET /api/agent/pages": "get_page_context: what each app page shows and which MCP tools do the same things there",
    "GET /api/library": "List your Library: uploads and generated media, with search and type filters",
    "POST /api/library/upload": "Upload a video or image to your Library (100 MB per file, 200 MB per account)",
    "POST /api/uploads": "create_upload: a one-time upload URL (PUT /u/<token>, 15 minutes, one use) and the curl command",
    "POST /api/uploads/chunk": "upload_media_chunk: one base64 chunk (at most 2 MB decoded) of a file, in order, under an upload_id",
    "POST /api/uploads/{upload_id}/finish": "finish_upload: join the chunks into one Library item",
    "GET /api/uploads/limits": "The upload limits: per-file MB, Library quota, chunk size, link imports, send codes",
    "GET /api/uploads/recent": "list_recent_uploads: the newest files you uploaded, sent from a device or imported from a link",
    "POST /api/send-codes": "create_send_code: a short code, link and QR for the Send a file page (any device, 30 minutes, 10 files)",
    "DELETE /api/send-codes": "Stop receiving: ?code=ABCD-EFGH ends that send code at once; no code ends every code you made (an AI app: only its own)",
    "POST /api/library/import": "import_from_link: a Google Drive, Dropbox, OneDrive or iCloud share link (or a direct link) into your Library",
    "GET /api/library/{iid}": "One Library item, with the posts that use it",
    "GET /api/library/{iid}/file": "Stream a Library file (supports range requests for video)",
    "DELETE /api/library/{iid}": "Delete a Library item: mode=bin (default, restorable for 7 days) or mode=permanent (can't be undone; refused for media a scheduled post uses)",
    "GET /api/library/trash": "The Bin (same as GET /api/library/bin)",
    "GET /api/library/bin": "The Bin: items deleted in the last 7 days, with days left before each is removed for good",
    "GET /api/library/bin/{iid}/file": "A binned item's file, for its owner (the Bin's previews)",
    "POST /api/library/bin/restore": "Restore selected items from the Bin (ids)",
    "POST /api/library/bin/purge": "Delete Bin items permanently (ids, or all=true to empty the Bin)",
    "POST /api/library/bulk-delete": "Delete several Library items (ids, mode=bin|permanent, force)",
    "GET /api/library/{iid}/draft": "Create draft prefill: the item's media and its run's caption per channel",
    "POST /api/library/{iid}/draft": "Save a Library item as Buffer drafts on the channels you pick (never published)",
    "POST /api/library/{iid}/restore": "Restore a deleted Library item from the Bin, with its file",
    "POST /api/library/{iid}/use": "Start a Make a post run with this Library item attached",
    "POST /api/device-link": "One-time link (10 minutes, single use) to open this account on another device",
    "POST /api/disconnect": "Disconnect Buffer and delete everything HuhQueue stored for you",
    "POST /api/logout": "Sign this browser out", "GET /api/me": "The Buffer account and organization you are signed in as",
    "GET /api/session": "Whether this browser is connected, plus the CSRF token for writes",
    "GET /api/calendar": "Planner data: scheduled, sent, draft and waiting posts plus each channel's posting times",
    "GET /api/channels": "Your Buffer channels with each network's limits", "GET /api/inbox": "The Inbox: approvals, failures and warnings, most urgent first",
    "GET /api/queue": "Posts in your Buffer queue", "POST /api/check": "Check a caption (and media) against your brand kit and Style Ledger rules",
    "POST /api/draft": "Read a link and draft one caption per channel (nothing is posted)", "POST /api/ideas": "Save an idea (title and text) to Buffer",
    "POST /api/media": "Upload an image or video (multipart; size checked before parsing)",
    "GET /api/media/health": "Ready posts whose files were deleted (the startup and periodic missing-media check)",
    "POST /api/commerce/search": "search_products: Channel3 product search (1 free credit, 0 when cached); hard stop at the free monthly credits; text cards, no catalogue images",
    "GET /api/commerce/products/{product_id}": "get_product: one product's fresh price and offers (free)",
    "POST /api/commerce/attach": "attach_product_to_post: a stable /go shop link on the post, the text product card and the caption with #Ad first and the disclosure line",
    "POST /api/commerce/caption": "The caption helper: #Ad first, the link and the disclosure line for a shop link",
    "GET /api/commerce/usage": "The Channel3 credit meter: whose key, credits used and left this month, searches left, reset date",
    "GET /api/commerce/key": "Your own Channel3 key: saved or not, and its last four characters only",
    "POST /api/commerce/key": "Save your own Channel3 key (tested with a free call, sealed; a signed-in person only)",
    "DELETE /api/commerce/key": "Remove your own Channel3 key",
    "POST /api/images/fetch": "fetch_image: the server fetches a public https image (SSRF-guarded) and keeps it as run media",
    "POST /api/images/cutout": "cutout_product: transparent PNG of the product; engine fast | auto | ml (ISNet) | hq (BiRefNet-lite); ml/hq, and auto when the fast cut-out is not confident, answer a job_id",
    "POST /api/images/edit": "edit_image: up to 12 ordered ops (crop, fit, pad, cutout, card, round, grade, sharpen) on your own image",
    "POST /api/images/card": "compose_product_card: the product on the brand ground with a soft shadow",
    "POST /api/posts/preview": "Validate a post for one channel and get a single-use confirm token",
    "POST /api/posts/submit": "Submit exactly what the preview validated", "DELETE /api/posts/{post_id}": "Delete a Buffer post",
    "PATCH /api/posts/{post_id}": "Edit a Buffer post's text or time", "POST /api/posts/{post_id}/reschedule": "Move a post to a new time",
    "POST /api/preflight": "Dry-run a post against every chosen channel's Buffer and network rules",
    "POST /api/preflight/fix": "Apply preflight fixes (trim, crop, recompress) for one channel",
    "GET /api/preflight/rules": "The preflight rule pack with the official doc each rule comes from",
    "GET /api/approvals": "Posts waiting for a person to approve", "POST /api/approvals/{approval_id}/approve": "Approve a waiting post; it goes to Buffer",
    "POST /api/approvals/{approval_id}/reject": "Reject a waiting post",
    "GET /api/settings/link-auto-approve": "Whether video links from your AI apps are approved without asking, and for which apps",
    "PUT /api/settings/link-auto-approve": "Turn auto-approve for video links on (with the rights statement) or off, for every app or the ones you pick", "POST /api/approvals/{approval_id}/reschedule": "Change a waiting post's time",
    "GET /api/agent/brain": "Status of the post agent's AI engine", "GET /api/agent/runs": "Your recent Make a post runs",
    "POST /api/agent/runs": "Start a Make a post run from a topic, a link or your media",
    "GET /api/agent/runs/{rid}": "One run: its timeline, AI calls, cost and the post per channel",
    "POST /api/agent/runs/{rid}/messages": "Tell a run what to change, in plain words", "GET /api/themes": "Every video look (edit styles + themes) with its gallery still and loop",
    "POST /api/agent/runs/{rid}/theme": "Set a run's video theme and re-render its videos", "GET /api/agent/runs/{rid}/templates": "A run's template, why it was picked, and suggestions with previews",
    "POST /api/agent/runs/{rid}/template": "Switch a run's template by name, for the post or one slide",
    "POST /api/agent/runs/{rid}/resume": "Resume a run a server restart cut off", "POST /api/agent/runs/{rid}/stop": "Stop the agent working on a run after its current step (Resume carries on)",
    "GET /api/agent/runs/{rid}/context": "What the agent knows for a thread (source, kit, template, channels, rights, rules) and its compaction",
    "POST /api/agent/runs/{rid}/title": "Rename a Make a post thread in the sidebar", "POST /api/agent/runs/{rid}/packages/{channel_id}": "Edit one channel's post in a run",
    "POST /api/agent/runs/{rid}/packages/{channel_id}/schedule": "Send one channel's post to Buffer (or to approval)",
    "POST /api/agent/runs/{rid}/revise": "Revise a run with a correction (it becomes a Style Ledger rule)",
    "POST /api/agent/runs/{rid}/tools/{name}": "Run one agent tool inside a run (docs/AGENT_PARITY.md)",
    "POST /api/link/preview": "Preview a pasted link (title, site, picture) for From a link",
    "GET /api/prefs/appearance": "Light, dark or system appearance", "PUT /api/prefs/appearance": "Save light, dark or system appearance", "GET /api/onboarding": "Setup answers so far",
    "PUT /api/onboarding": "Save setup answers", "GET /api/recommendations": "Stories recommended for your brand",
    "GET /api/takes": "Your One take runs", "POST /api/takes": "Start a One take: one long video into clips",
    "POST /api/takes/sample": "Run One take on the sample talk", "GET /api/takes/{rid}": "One take run with its clips",
    "PATCH /api/takes/{rid}/clips/{clip_id}": "Trim & caption one clip: in/out, transcript, cover hook, captions per network, music",
    "POST /api/takes/{rid}/clips/{clip_id}/rewrite": "An AI rewrite of one clip's caption for one network (not saved)", "POST /api/takes/{rid}/place": "Place the clips into your Buffer week",
    "DELETE /api/takes/{rid}": "delete_take: move a take to the Bin for 7 days (a running render is cancelled)",
    "DELETE /api/takes/{rid}/clips/{clip_id}": "delete_take with clip_id: move one clip to the Bin for 7 days",
    "GET /api/takes/bin": "list_take_bin: takes and clips in the Bin with days left", "POST /api/takes/{rid}/restore": "restore_take: bring a take back from the Bin",
    "POST /api/takes/{rid}/clips/{clip_id}/restore": "restore_take with clip_id: bring a clip back from the Bin",
    "POST /api/takes/bin/purge": "purge_take_bin: delete Bin takes / clips and their files for good",
    "GET /api/takes/{rid}/clips/{clip_id}/layout": "get_clip_layout: a One take clip's layout plan scene by scene, overrides and caption preset",
    "POST /api/takes/{rid}/clips/{clip_id}/layout": "set_clip_layout: pin a scene's (or the clip's) layout and/or the caption preset; re-renders",
    "GET /api/takes/settings/smart-layout": "Smart layout (beta): whether keyframes may go to a free vision model",
    "POST /api/takes/settings/smart-layout": "Smart layout (beta): the account owner turns it on or off",
    "GET /api/threads": "list_threads: your earlier threads (id thr_..., title, updated_at, run_ids, summary), paginated",
    "GET /api/threads/{tid}": "get_thread: one thread's messages (paginated) and the runs / takes inside with their outputs",
    "DELETE /api/threads/{tid}": "delete_thread: move any thread to the Bin for 7 days, like deleting chat history (media is never deleted)",
    "POST /api/threads/{tid}/restore": "restore_thread: bring a deleted thread back", "GET /api/threads/bin": "list_thread_bin: threads in the Bin",
    "POST /api/threads/bin/purge": "purge_thread_bin: remove Bin threads' conversations for good (media stays)",
    "GET /api/takes/uploads/config": "How One take takes a file here: multipart to the bucket (up to 4 GB) or the 100 MB Library upload",
    "POST /api/takes/uploads": "Open (or resume) a multipart upload of a long video straight to the bucket",
    "GET /api/takes/uploads/{upload_id}": "A multipart upload's parts so far (resume)", "POST /api/takes/uploads/{upload_id}/parts": "Presigned URLs for parts",
    "POST /api/takes/uploads/{upload_id}/complete": "Join and verify the parts (sizes, ETags, video container)",
    "DELETE /api/takes/uploads/{upload_id}": "Abort an upload and remove its parts",
    "GET /api/thumb": "A story picture (og:image read and re-encoded server-side, cached); 204 means no picture, use the favicon", "GET /api/radar": "The For you news feed", "POST /api/radar/feeds": "Follow a feed", "DELETE /api/radar/feeds/{feed_id}": "Unfollow a feed",
    "GET /api/sources": "Story scan sources and settings", "POST /api/sources/draft": "Write posts for the picked stories",
    "GET /api/sources/runs/{rid}": "One story scan run", "POST /api/sources/scan": "Scan every source you turned on",
    "POST /api/sources/settings": "Turn sources on or off", "GET /api/sources/library": "The source library: every source with its status and the fresh items",
    "POST /api/sources/refresh": "Re-read the source library now (once a minute)", "POST /api/sources/enabled": "Turn one library source on or off", "GET /api/brand": "Your brand kit and Style Ledger rules", "POST /api/brand": "Save your brand kit",
    "GET /api/brand-kits": "Your brand kits, which one is active, fonts, caption styles and the sample brands",
    "POST /api/brand-kits": "Create a brand kit from fields, a website (url) or a sample brand (sample); activate: true makes it active",
    "POST /api/brand-kits/palette": "Colours suggested from an uploaded logo (background, primary, accent, text)",
    "POST /api/brand-kits/preview": "The whole gallery in a kit as you edit: every video theme, the carousel cover, the thumbnail and the end card (part: kit | themes)",
    "DELETE /api/brand-kits/{kid}": "Delete a brand kit (never the active one)", "PATCH /api/brand-kits/{kid}": "Change a brand kit; the active one restyles every template at once",
    "POST /api/brand-kits/{kid}/activate": "Make a brand kit the active one: every template renders from it",
    "POST /api/brand/extract": "Read a brand from a website: logo, colours, fonts and voice (no AI, nothing saved)",
    "POST /api/brand/onboard": "Save an extracted or sample brand as your brand kit", "GET /api/brand/samples": "The sample brands (not affiliated) and their renders",
    "POST /api/brand/samples/{bid}/schedule": "Put the sample brand story in the sample workspace's queue (sample only)",
    "POST /api/brand/themes-preview": "Three stills of a kit being edited (Reel cover, cover, square)",
    "GET /api/brand-studio": "Your brand kit, cards and templates", "POST /api/brand-studio": "Save the brand kit and cards",
    "GET /api/brand-studio/examples": "The example brands", "POST /api/brand-studio/examples/{example_id}": "Use an example brand",
    "POST /api/brand-studio/preview": "Live preview of a template in your kit", "POST /api/brand/edit": "Change the brand in plain words",
    "POST /api/brand/from-website": "Import logo, colours and fonts from your website", "POST /api/brand/preview": "Preview the brand kit as rendered cards",
    "POST /api/ledger": "Add a Style Ledger rule", "DELETE /api/ledger/{rule_id}": "Delete a Style Ledger rule",
    "GET /api/ledger": "The Style Ledger: rules by group with source, scope and examples, rules from the kit, and suggestions",
    "GET /api/ledger/rules/{rule_id}": "One rule with its examples and the edits it was learned from",
    "PATCH /api/ledger/rules/{rule_id}": "Change a rule's wording, its channels, or turn it on or off (people only)",
    "GET /api/ledger/suggestions": "Rules suggested from your repeated caption edits",
    "POST /api/ledger/suggestions/{sid}": "Accept or dismiss a suggested rule",
    "POST /api/check/draft": "Check a draft for one network: rules applied, failures, the fixed text and the source evidence",
    "GET /api/agent/runs/{rid}/review-links": "The review links made for a run and what each reviewer decided",
    "POST /api/agent/runs/{rid}/review-links": "Make a read-only, expiring review link for a run's drafts",
    "DELETE /api/review-links/{lid}": "Turn a review link off",
    "POST /api/ledger/{rule_id}": "Turn a Style Ledger rule on or off (people only)", "GET /api/templates": "The templates you can render",
    "GET /api/templates/recap": "This week's recap stories from your posts", "POST /api/templates/render": "Render a template for your channels",
    "GET /api/templates/slots": "Recurring template slots", "POST /api/templates/slots": "Add a recurring slot", "DELETE /api/templates/slots/{sid}": "Delete a recurring slot",
    "POST /api/templates/slots/{sid}/run": "Run a recurring slot now", "GET /api/templates/video/{jid}": "Status of a template video render",
    "GET /api/templates/custom": "Your custom post templates, the slide types and base layouts",
    "POST /api/templates/custom": "Build a new custom template (MCP create_template)",
    "PATCH /api/templates/custom/{ref}": "Change a custom template", "DELETE /api/templates/custom/{ref}": "Delete a custom template",
    "GET /api/ai": "Your AI engine settings (keys never returned)", "POST /api/ai": "Save your AI engine and key",
    "GET /api/ai/engine": "Which model runs your agent, with each model's credit", "PUT /api/ai/engine": "Choose the model that runs your agent",
    "GET /api/ai/logs": "Recent AI calls: engine, model, task, latency, tokens, cost, fallback, ok/error (filter, page)",
    "GET /api/ai/keys": "Your own AI keys per provider (masked)", "POST /api/ai/keys/{provider}": "Test and save your own key for one provider (people only)",
    "DELETE /api/ai/keys/{provider}": "Delete your own key for one provider (people only)",
    "POST /api/agent-tokens": "Create an agent token (shown once)", "DELETE /api/agent-tokens/{token_id}": "Revoke an agent token",
    "PATCH /api/agent-tokens/{token_id}": "Change a token's daily limit (1-100) or its Allow paid APIs switch (humans only)",
    "POST /api/agent/start-post": "start_post: open one post for a request and get its run_id (every follow-up updates it)",
    "POST /api/agent/continue-run": "continue_run: join an existing run for a follow-up about the same content (run_id, thread_url)",
    "POST /api/agent/cleanup": "cleanup_agent_drafts: list what AI apps left (dry run), then archive those runs and move those Library items to the Bin",
    "GET /api/safety": "Kill switch and daily caps", "POST /api/safety/kill": "Pause or resume every agent (people only)",
    "GET /api/limits": "Your Buffer API budget right now", "GET /api/plan": "Your Buffer plan's limits",
    "GET /api/activity": "Activity log, newest first (tabs, filters, cursor)", "GET /api/activity/export.csv": "Activity log as CSV",
    "GET /api/audit": "Audit log of writes", "GET /api/stats": "Public counters for the home page", "GET /healthz": "Uptime check",
}
_ORDER = ["Account and session", "Channels and queue", "Posts", "Approvals", "Make a post (agent)", "One take", "News", "Brand",
          "AI engine", "AI apps, tokens and safety", "Limits", "Activity", "Public", "Other"]
_PUBLIC = ("/healthz", "/api/stats")


def _group(path: str) -> str:
    best = ""
    title = "Other"
    for pre, t in _GROUPS:
        if (path == pre or path.startswith(pre + "/") or path.startswith(pre + "-")) and len(pre) > len(best):
            best, title = pre, t
    return title


def api_endpoints(schema: dict) -> list[dict]:
    out = []
    for path, ops in (schema.get("paths") or {}).items():
        for method, op in ops.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE") or path == "/mcp":  # /mcp has its own section
                continue
            params = [p.get("name", "") for p in op.get("parameters") or [] if p.get("in") in ("query", "path")]
            out.append({"method": method.upper(), "path": path, "summary": _DESC.get(f"{method.upper()} {path}") or op.get("summary") or "", "described": f"{method.upper()} {path}" in _DESC, "group": _group(path),
                        "params": params, "body": bool(op.get("requestBody")), "public": path in _PUBLIC})
    out.sort(key=lambda e: (_ORDER.index(e["group"]) if e["group"] in _ORDER else 99, e["path"], e["method"]))
    return out


def api_page(schema: dict, tools: list[dict]) -> str:
    eps = api_endpoints(schema)
    groups: dict[str, list[dict]] = {}
    for e in eps:
        groups.setdefault(e["group"], []).append(e)
    gid = lambda t: re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    secs = []
    for title, rows in groups.items():
        trs = "".join(
            f'<tr data-testid="api-endpoint"><td><span class="api-m m-{e["method"].lower()}">{e["method"]}</span></td>'
            f'<td><code>{html.escape(e["path"])}</code></td><td>{html.escape(e["summary"])}'
            + (f'<br><small>Parameters: {", ".join(f"<code>{html.escape(p)}</code>" for p in e["params"])}</small>' if e["params"] else "")
            + (' <small>JSON body.</small>' if e["body"] and e["path"] != "/api/media" else "")
            + f'</td><td>{"None" if e["public"] else ("Session or token" if e["method"] == "GET" else "Session + CSRF, or token")}</td></tr>'
            for e in rows)
        secs.append(f'<h2 id="{gid(title)}">{html.escape(title)}</h2><div class="dk-table"><table><thead><tr><th>Method</th><th>Path</th>'
                    f'<th>What it does</th><th>Auth</th></tr></thead><tbody>{trs}</tbody></table></div>')
    tool_rows = "".join(f'<tr data-testid="api-mcp-tool"><td><code>{html.escape(t.get("name", ""))}</code></td>'
                        f'<td>{html.escape((t.get("description") or "").split(". ")[0].rstrip("."))}.</td></tr>' for t in tools)
    body = f"""
<h2 id="auth">Authentication</h2>
<p>Two ways in, and every endpoint below accepts both unless it says <i>None</i>.</p>
<ul><li><b>From a script or agent:</b> create a token on <b>AI apps</b> in the app and send <code>Authorization: Bearer hq_…</code>. Tokens are shown once and stored only as a hash. Each token has a policy (needs approval, Buffer drafts only, or schedule), a daily cap, and obeys the kill switch.</li>
<li><b>From the browser:</b> the session cookie set when you connect Buffer. Writes (POST) also need the <code>X-HQ-CSRF</code> header, whose value comes from <code>GET /api/session</code>.</li></ul>
<pre class="code">export HUHQUEUE_TOKEN=hq_…   # from AI apps
curl -H "Authorization: Bearer $HUHQUEUE_TOKEN" {BASE}/api/channels</pre>
<p>Errors come back as JSON <code>{{"error": "…"}}</code> with the HTTP status. A 429 from Buffer passes its <code>Retry-After</code> on.</p>
<h2 id="mcp">MCP endpoint</h2>
<p><code>POST {BASE}/mcp</code> speaks the Model Context Protocol (JSON-RPC over streamable HTTP). Claude, ChatGPT and Grok sign in with OAuth (discovery at <code>/.well-known/oauth-protected-resource</code>); Claude Code, Codex, Cursor and scripts send the same bearer token. See <a href="/docs/agents">AI apps over MCP</a> for per-app setup.</p>
<pre class="code">curl -X POST {BASE}/mcp -H "Authorization: Bearer $HUHQUEUE_TOKEN" \\
  -H "Content-Type: application/json" -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}'</pre>
<details class="api-tools"><summary>The {len(tools)} MCP tools</summary><div class="dk-table"><table><thead><tr><th>Tool</th><th>What it does</th></tr></thead><tbody>{tool_rows}</tbody></table></div></details>
<h2 id="schema">OpenAPI schema</h2>
<p>The machine-readable schema is at <a href="/openapi.json">/openapi.json</a> (OpenAPI 3). Import it into Postman, Insomnia or a code generator.</p>
{''.join(secs)}"""
    toc = ('<li><a href="#auth">Authentication</a></li><li><a href="#mcp">MCP endpoint</a></li><li><a href="#schema">OpenAPI schema</a></li>'
           + "".join(f'<li><a href="#{gid(t)}">{html.escape(t)}</a></li>' for t in groups))
    main = (f'<p class="eyebrow"><a href="/docs">Docs</a></p><h1 data-testid="api-title">API reference</h1>'
            f'<p class="dk-lede">Everything the app does is a REST endpoint and an MCP tool. {len(eps)} endpoints, {len(tools)} MCP tools, one token.</p>'
            f'<nav class="dk-toc" aria-label="On this page"><ul>{toc}</ul></nav><article class="dk-body" data-testid="api-reference">{body}</article>')
    return _layout("api", "API reference", "HuhQueue REST endpoints, the MCP endpoint and its tools, and how to authenticate with a token or a session.", main)
