"""Build studio/static/{privacy,terms,security}.html from one template. Every claim here was checked against the code on
31 Oct 2026 (studio/store.py tables and delete_user, studio/app.py cookies and CSP, studio/demo.py TTL, studio/mcp_oauth.py
token lifetimes, studio/demo_guard.py caps, studio/sources.py research hosts) and again on 4 Nov 2026 for the Library,
Bin, Send a file and MCP call log (studio/library.py TRASH_DAYS and purge_trash, studio/house.py start_media_check,
studio/metastrip.py, studio/send.py TTL / MAX_FILES / code hashing, studio/mcp_runs.py KEEP_UNCLAIMED, studio/activity.py
AUDIT_RETENTION_DAYS and SIGNIN_IP_DAYS, /m/ Cache-Control in studio/app.py). Change the code, then change this file.

    python scripts/build_legal.py"""
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "studio" / "static"
UPDATED = "4 November 2026"
UPDATED_ISO = "2026-11-04"


def chrome(slug: str, title: str, desc: str, head: str, body: str) -> str:
    nav = "".join(f'<a href="/{s}"{" aria-current=page" if s == slug else ""}>{n}</a>' for s, n in
                  (("docs", "Docs"), ("privacy", "Privacy"), ("terms", "Terms"), ("security", "Security")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · HuhQueue by HuhByte AI</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="https://huhqueue-studio.onrender.com/{slug}">
<meta name="color-scheme" content="light dark">
<meta property="og:title" content="{title} · HuhQueue"><meta property="og:description" content="{desc}"><meta property="og:image" content="https://huhqueue-studio.onrender.com/og.png">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="apple-touch-icon" href="/static/brand/apple-touch-icon.png">
<link rel="stylesheet" href="/static/fonts.css"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/legal.css">
</head><body>
<a class="skip" href="#main">Skip to content</a>
<header class="lg-top"><a class="brand" href="/" data-testid="brand"><img src="/static/brand/hb-mark-96.png" width="32" height="32" alt=""><span class="brand-text"><span class="name">HuhQueue</span><span class="built bt"><small>by HuhByte AI</small></span></span></a>
<nav aria-label="Main">{nav}<a class="btn sm" href="/demo">Try the demo</a></nav></header>
<main id="main" class="lg" data-testid="{slug}">
{head}
{body}
</main>
<footer class="lg-foot"><span>HuhQueue by</span><img class="wm lt" src="/static/brand/hb-wordmark-light.png" width="333" height="64" alt="HuhByte AI"><img class="wm dk" src="/static/brand/hb-wordmark-dark.png" width="333" height="64" alt="HuhByte AI">
<span>Built on the Buffer API, not affiliated with Buffer.</span><a href="/docs">Docs</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/security">Security</a><a href="https://github.com/dataanalystram/buffer-contest">Source code</a></footer>
</body></html>
"""


def page(slug, title, eyebrow, lede, desc, summary, sections):
    head = f"""<div class="lg-head"><p class="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{lede}</p>
<p class="lg-meta"><span>Last updated <b><time datetime="{UPDATED_ISO}">{UPDATED}</time></b></span><span>Applies to <b>huhqueue-studio.onrender.com</b></span></p></div>
<section class="lg-sum" aria-labelledby="sumH" data-testid="{slug}-summary"><h2 id="sumH">In short</h2>{"".join(f"<div><b>{a}</b><span>{b}</span></div>" for a, b in summary)}</section>"""
    toc = '<nav class="lg-toc" aria-labelledby="tocH"><h2 id="tocH">On this page</h2><ol>' + "".join(
        f'<li><a href="#{sid}">{name}</a></li>' for sid, name, _ in sections) + "</ol></nav>"
    body = toc + '<div class="lg-body">' + "".join(
        f'<section id="{sid}" aria-labelledby="{sid}H"><h2 id="{sid}H">{name}</h2>{html}</section>' for sid, name, html in sections) + "</div>"
    (OUT / f"{slug}.html").write_text(chrome(slug, title, desc, head, body))


def table(head, rows):
    return ('<div class="tbl-wrap"><table><thead><tr>' + "".join(f'<th scope="col">{h}</th>' for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table></div>")


CONTACT = """<p>Questions, a data request or a correction: open an issue on the <a href="https://github.com/dataanalystram/buffer-contest/issues">project's GitHub</a>, or message <b>@huhbyte.ai</b> on Instagram. For a security problem, use GitHub's private <a href="https://github.com/dataanalystram/buffer-contest/security">Report a vulnerability</a> instead of a public issue.</p>"""

page("privacy", "Privacy policy", "Privacy", "What HuhQueue keeps, why, for how long, and how to delete it. Written from the code, which is open source, so you can check every line.",
     "What HuhQueue stores, why, for how long, who processes it and how to delete it.",
     [("Your Buffer tokens stay on the server", "Encrypted at rest, never sent to your browser."),
      ("Nothing posts without you", "Agent posts wait for your approval unless you trust an agent."),
      ("No ads, no tracking", "One session cookie. No analytics or advertising scripts."),
      ("Delete everything at once", "Account menu, Disconnect and delete my data.")],
     [("who", "Who we are", """<p>HuhQueue is an independent, open-source app built on the Buffer API by Ram Prasad Somaraju, published as HuhQueue by HuhByte AI. It is not made, endorsed or operated by Buffer. The code that runs this site is at <a href="https://github.com/dataanalystram/buffer-contest">github.com/dataanalystram/buffer-contest</a>.</p>"""),
      ("data", "Data we store", "<p>Only what the features need. Each row below is a table in our database.</p>" + table(
          ["What", "Why", "How it is kept"],
          [["Buffer connection", "To read your channels and posts and create posts you approve", "Access and refresh tokens encrypted at rest (Fernet), never sent to the browser"],
           ["Account details", "To show who is connected", "Buffer account id, name, email and organization name"],
           ["Session", "To keep you signed in", "A random id in an HttpOnly, Secure, SameSite=Lax cookie; stored only as a hash"],
           ["Agent tokens", "So your AI apps can act within your rules", "Stored only as a SHA-256 hash, with name, policy, daily cap and last use"],
           ["Connected AI apps (MCP OAuth)", "To let Claude, ChatGPT and others sign in", "Client name and redirect address; codes and refresh tokens as hashes"],
           ["Approvals and drafts", "Posts waiting for you, and agent runs", "The post text, media links and the run's steps"],
           ["Brand kit and Style Ledger", "To make posts in your style", "Colours, fonts, logo, voice and the rules you taught"],
           ["Library media", "Files you upload or send from a device, and media HuhQueue renders", "Stored in the database. Location and camera metadata is removed on upload: JPEG EXIF, XMP and IPTC, PNG text and EXIF chunks; GPS positions in MP4 and MOV are blanked. WebP, GIF and WebM files are stored as sent"],
           ["Media links (<code>/m/</code>)", "So Buffer and the networks can fetch your media", "Each link is a random 144-bit token, not a signed or expiring link: anyone who has it can open the file until you delete it, and it may be cached publicly for up to 1 day"],
           ["Send-a-file codes", "To let another device drop files into your Library", "Stored only as a hash; each code lasts 30 minutes and takes up to 10 files"],
           ["AI app call log", "So you can see what your AI apps asked for", "Tool name, a summary of allowed arguments, status, duration, client, app, token name, model hint, time and the run it belongs to"],
           ["Video link consents", "Proof that you confirmed the rights before One take fetched a video link", "Who confirmed it, the link and its hash, the time, which version of the rights statement you ticked, and any licence the platform states (for example Creative Commons)"],
           ["Feeds", "News radar", "The feed addresses you follow"],
           ["Your AI key (optional)", "To use your own model", "Encrypted at rest; never shown again after saving"],
           ["Activity log", "So you can see what you and your agents did", "Action, who did it, a short detail and the time; for sign-ins, the IP address and browser"]])),
      ("use", "How we use it", """<ul><li>To read your Buffer organization, channels and posts, and to create posts, drafts and ideas in your Buffer account when you, or an agent you authorized within the rules you set, submit them.</li>
<li>To check each post against the rules of its network before it goes to Buffer (preflight).</li>
<li>We do not sell or share your data, and we do not use it for advertising or to train models.</li></ul>"""),
      ("links", "Links, research and AI", """<p>When you write from a link, the server fetches that public page. When the agent researches a topic, it sends the topic (not your account details) to public news searches: Google News, Bing News and the Hacker News search API.</p>
<p><b>Video links (One take, AI apps).</b> When you give One take a link to a video (a YouTube, Vimeo or X post, or a video file), you first confirm: <i>"I own this video or have permission to use it (e.g. my own content, a licence, or Creative Commons); I'm responsible for rights."</i> An AI app you connect can't confirm this for you: its request waits in your Inbox until you tick it and approve. Only then does our video worker (an InstaBox machine, or a GitHub Actions runner as the fallback) fetch the video from that platform with the open-source tool yt-dlp, the same way Make a post fetches a story's video, or from the file's address. We never use your platform account, a password or cookies. The video is transcribed and cut on the worker; the clips land in your Library. If our operator has set up file storage (Cloudflare R2), the fetched source is kept there so the clips can be re-rendered without fetching it again, and deleted after 7 days; otherwise it stays on the worker only for the job and is deleted when the job ends. We keep a record of each confirmation (see the table above).</p>
<p>If the operator configured a model, or you saved your own key, the page's title, description and opening text go to that model provider to write captions. Without one, drafts are written on our server by rules.</p>"""),
      ("retention", "How long we keep it", table(["Data", "Kept for"],
          [["Your account and everything listed above", "Until you disconnect and delete, which removes it at once"],
           ["Sessions", "30 days, or until you sign out"],
           ["A video fetched from a link (raw source)", "7 days in file storage when the operator configured it (or until you purge the take); otherwise only while the worker's job runs"],
           ["Video link consent records", "Until you delete your data, so the confirmation can be shown if a rights question comes up"],
           ["Activity log", "90 days"],
           ["Sign-in IP address and browser (in the activity log)", "30 days, then blanked"],
           ["Library items you delete", "7 days in the Bin, then removed for good (checked every 6 hours). Delete permanently or Empty bin removes the records and files at once; media a scheduled post uses can't be deleted permanently"],
           ["Send-a-file codes", "30 minutes or 10 files, whichever comes first; Stop receiving or New code ends a code at once. A code an AI app made also stops when that app's token is revoked or expires, or while the kill switch is on"],
           ["AI app calls not tied to a post run", "24 hours"],
           ["AI app calls tied to a post run", "Until you delete your data"],
           ["Sample workspaces (/demo)", "1 day, then deleted with their sample data"],
           ["One-time device links", "10 minutes, single use"],
           ["MCP sign-in codes", "10 minutes, single use"],
           ["AI app access tokens", "30 days, renewable for up to 90 days, revocable any time"]]) +
       "<p class=\"muted\">Database backups made by our hosting providers follow their own schedules.</p>"),
      ("processors", "Subprocessors", table(["Provider", "What for", "Data it sees"],
          [["Buffer", "Your channels and publishing", "What you send to your own Buffer account"],
           ["Render", "Hosting the app", "All app traffic and data in transit and in use"],
           ["Supabase", "The production database (Postgres)", "The stored data listed above"],
           ["InstaBox", "The video worker: fetching video links, rendering Reels and One take clips", "The media and settings of a job, for that job only"],
           ["GitHub Actions", "Rendering videos (Reels, One take clips) when InstaBox is unavailable", "The media and settings of a render job, for that job only"],
           ["Cloudflare R2 (only when configured)", "Large One take uploads and fetched video links", "The video file, deleted after 7 days at most"],
           ["The video platform you link (YouTube, Vimeo, X)", "Serving the video you linked", "A request for that public video from our worker; no account of yours"],
           ["Your AI provider (optional)", "Writing captions with a model", "The page text and brief for a draft"]])),
      ("rights", "Your rights and choices", """<ul><li><b>Delete:</b> Account menu, <b>Disconnect and delete my data</b>, removes your account, tokens, sessions, agent tokens, approvals, brand kit, rules, media and Library (including the Bin), runs, renders, AI settings, feeds, send codes, AI app call log and activity log.</li>
<li><b>Revoke:</b> remove HuhQueue's access in your Buffer account settings at any time, and revoke any agent token on the Agents screen.</li>
<li><b>Stop agents:</b> one switch pauses every agent at once.</li>
<li><b>Access or correct:</b> most data is visible in the app; for anything else, contact us below. If you are in the EU or UK you also have the rights given by the GDPR, including complaining to your data protection authority.</li></ul>"""),
      ("cookies", "Cookies", "<p>One first-party cookie, <code>hq_sid</code>, keeps you signed in. Its CSRF partner token is sent in a header, not a cookie. There are no analytics, advertising or third-party cookies, and the site loads no third-party scripts or fonts.</p>"),
      ("contact", "Contact", CONTACT)])

page("terms", "Terms of use", "Terms", "The plain rules for using HuhQueue. Short on purpose.",
     "The rules for using HuhQueue, the open-source app built on the Buffer API.",
     [("Free and open source", "MIT licensed. Use it here or run it yourself."),
      ("You stay in control", "You approve what goes to Buffer and own what you post."),
      ("Not Buffer", "An independent app built on the Buffer API."),
      ("As is", "Provided without warranties, like most open-source software.")],
     [("service", "The service", "<p>HuhQueue helps you research, make, check and schedule social posts through your own Buffer account. The sample workspace at <a href=\"/demo\">/demo</a> uses made-up channels and never reaches Buffer.</p>"),
      ("account", "Your account and Buffer", "<p>You sign in with Buffer's own OAuth. Your use of Buffer stays subject to Buffer's terms and your Buffer plan's limits. You can disconnect at any time.</p>"),
      ("content", "Your content", "<p>You own what you upload and post. You confirm you have the rights to media you give HuhQueue, including videos you cut into clips with One take. When you give a link to a video (YouTube, Vimeo, X or a file), you confirm that you own it or have permission to use it (for example your own content, a licence, or Creative Commons) and that you are responsible for those rights; we keep a record of that confirmation. An AI app can't give it for you. Respect the platform's terms and the creator's licence: a Creative Commons flag shown by HuhQueue is a hint read from the platform, not legal advice. We fetch only public videos, never with your account, and remove a video when its rights holder asks. You are responsible for what you approve and publish, including posts drafted by AI or by an agent you connect.</p>"),
      ("agents", "AI apps and agents", "<p>Agents you connect act with the policy you choose: approval by default, Buffer drafts only, or trusted to schedule. Each has a daily cap, and the kill switch stops all of them. Review AI-written text before you approve it.</p>"),
      ("use", "Fair use", "<ul><li>Don't use HuhQueue to send spam, break a network's rules or post content you don't have rights to.</li><li>Don't try to reach other users' data or the server's internal network, or get around rate limits and caps.</li><li>The sample workspace has daily caps so it stays free for everyone.</li></ul>"),
      ("warranty", "No warranty, limited liability", "<p>HuhQueue is provided as is under the MIT licence, without warranties of any kind. To the extent the law allows, the authors are not liable for lost posts, missed schedules or other damages from using it. Buffer and the networks may change their APIs at any time.</p>"),
      ("changes", "Changes", "<p>We may update these terms; the date at the top shows the latest version, and the history is in the public repository.</p>"),
      ("contact", "Contact", CONTACT)])

page("security", "Security", "Security", "How HuhQueue protects your Buffer connection, your data and what your agents can do. The full threat model and audit are public.",
     "How HuhQueue protects your Buffer connection, your data and your AI agents.",
     [("Encrypted tokens", "Buffer tokens are encrypted at rest and stay on the server."),
      ("Strict browser policy", "Our own scripts only, no framing, a CSRF header on every write."),
      ("Agents on a leash", "Hashed scoped tokens, approval by default, caps and a kill switch."),
      ("Audited", "7 findings from the 31 Oct 2026 audit, all fixed with regression tests.")],
     [("signin", "Signing in", "<ul><li>Buffer OAuth 2.0 with PKCE; the session id is rotated at sign-in.</li><li>Session cookie: HttpOnly, Secure, SameSite=Lax. Every write also needs a CSRF token sent in a header.</li><li>Sign-in always opens as a full page in your browser, never inside a frame.</li></ul>"),
      ("browser", "In your browser", "<ul><li>Content Security Policy: scripts only from this site, no inline scripts, no third-party scripts or fonts, <code>object-src 'none'</code>, <code>frame-ancestors 'none'</code>.</li><li><code>X-Frame-Options: DENY</code>, <code>X-Content-Type-Options: nosniff</code> and a strict Referrer-Policy.</li></ul>"),
      ("data", "Your data", "<ul><li>Buffer tokens and your own AI key are encrypted at rest and never sent to the browser.</li><li>Agent tokens, session ids, sign-in codes and device links are stored only as hashes.</li><li>Disconnect and delete removes your data at once.</li></ul>"),
      ("agents", "AI apps and agents", "<ul><li>Each token has a policy (approval, drafts only or trusted), a daily cap and an expiry, and can be revoked.</li><li>The kill switch and caps run before every agent action, over MCP and REST.</li><li>The MCP sign-in screen shows where it will send you back and flags unverified apps, which can't be given trusted access.</li></ul>"),
      ("server", "On the server", "<ul><li>Every outbound fetch goes through one safe-fetch helper that re-checks each redirect against private, loopback and metadata addresses.</li><li>Request bodies are size-capped before parsing; anonymous and sample traffic is rate- and cost-limited.</li><li>Video workers authenticate with a GitHub OIDC identity and a single-use job token; no token appears in workflow logs.</li></ul>"),
      ("checks", "Checks on every change", "<p>gitleaks on every push, pip-audit on dependencies, the full test suite on SQLite and Postgres, and a real-browser smoke test. The threat model, the audit's 7 findings, their fixes and known limits are in <a href=\"https://github.com/dataanalystram/buffer-contest/blob/main/docs/SECURITY.md\">docs/SECURITY.md</a>.</p>"),
      ("report", "Report a vulnerability", CONTACT)])
print("wrote privacy.html, terms.html, security.html")
