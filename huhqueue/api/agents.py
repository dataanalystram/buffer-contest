"""Public, read-only pages for AI agents: /llms.txt (llmstxt.org format) and /agents (human-readable).

They describe how to drive HuhQueue headlessly - REST (OpenAPI), the MCP server and deep links - and
contain no data from the instance (no channels, no posts, no keys), so they are safe to leave public.
"""
from __future__ import annotations

from html import escape

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import __version__
from .. import actions as reg
from ..actions import mcp_tool_names
from ..security import SCOPES

DEEP_LINKS = [
    ("#/home", "Home: scorecard, up next, recent comments"),
    ("#/queue?tab=queue", "Publish list (tabs: queue, drafts, approvals, action, sent, failed)"),
    ("#/queue/<channel_id>", "One channel's queue"),
    ("#/calendar?view=week&date=YYYY-MM-DD", "Calendar (view=week|month)"),
    ("#/home?compose=new&channels=all&text=...", "Open the composer pre-filled (channels=all or comma-separated ids)"),
    ("#/home?compose=new&when=custom&at=YYYY-MM-DDTHH:MM", "Composer with a custom time"),
    ("#/ideas?new=1", "New idea on the Ideas board"),
    ("#/community?status=open", "Unanswered comments"),
    ("#/insights?range=30", "Insights (range=7|30|90|mtd|custom&start=&end=)"),
    ("#/channel/<channel_id>?tab=publish", "Channel page (tab=publish|community|insights|history|schedule|overview)"),
    ("#/settings?section=api", "Create a scoped API token (Settings > API & MCP agents)"),
]


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def llms_txt(base: str) -> str:
    tools = "\n".join(f"- `{t}`" for t in mcp_tool_names())
    scopes = "\n".join(f"- `{k}`: {v}" for k, v in SCOPES.items())
    links = "\n".join(f"- `{base}/{h}`: {d}" for h, d in DEEP_LINKS)
    actions = "\n".join(f"- `{a.id}` ({a.risk}, scope `{a.scope}`): {a.title} - `{a.method} {a.path}`"
                        for a in reg.ACTIONS if a.method and a.agent_visible())
    return f"""# HuhQueue

> Free, self-hosted social media scheduler (Buffer-style) for Instagram, YouTube, X, TikTok and LinkedIn. Everything the web UI does is available to agents through a REST API (OpenAPI 3) and an MCP server, authenticated with scoped bearer tokens. Version {__version__}.

Authenticate with `Authorization: Bearer hq_...`. A human creates the token in Settings > API & MCP agents and chooses its scopes, allowed MCP tools, rate limit and whether posts it schedules need human approval. Every token call is written to the audit log.

## Docs

- [Agent guide]({base}/agents): how to connect an agent, with MCP config
- [OpenAPI schema]({base}/openapi.json): machine-readable REST API (also at {base}/api/openapi.json)
- [Page context]({base}/api/context?page=/home): what any UI page shows, its actions (REST route, MCP tool, risk, required fields) and the IDs on it. Every UI page also embeds it as `<script type="application/json" id="agent-context">`, and every control has a stable `data-testid` or `aria-label`.
- [Interactive REST docs]({base}/api/docs): Swagger UI

## MCP

Run the stdio MCP server next to your agent: `huhqueue mcp` with env `HUHQ_URL={base}` and `HUHQ_API_TOKEN=hq_...`.

Tools:
{tools}

## Token scopes

{scopes}

## Actions (generated from the action registry)

Run any action with `POST {base}/api/actions/<id>:run` and body `{{"input": {{...}}}}` (add `"dry_run": true` for a preview,
`"idempotency_key"` to make retries safe). The MCP tool name is the id with `.` replaced by `_`. Full JSON Schemas:
[{base}/llms-full.txt]({base}/llms-full.txt) and `GET /api/actions`.

{actions}

## Key REST endpoints

- `GET /api/channels`: connected channels
- `POST /api/media` (multipart `files`) or `POST /api/media/from-url`: upload media
- `POST /api/validate`: check a post against each network's rules before scheduling
- `POST /api/posts`: create a post for one or more channels (`when`: queue | prioritize | now | custom | draft)
- `GET /api/posts?status=scheduled`: list the queue
- `PATCH /api/targets/{{id}}`, `POST /api/targets/{{id}}/publish-now`: edit or publish one channel's copy
- `GET /api/insights`, `GET /api/comments`, `POST /api/comments/{{id}}/reply`

## Deep links (UI state lives in the URL hash)

{links}

## Optional

- Same story is never posted twice to a channel: pass a `topic_key` with each post.
- Instagram: at most 50 API posts per channel per rolling 24 hours; PNG/WebP are converted to JPEG.
"""


def llms_full_txt(base: str) -> str:
    import json
    parts = [llms_txt(base), "\n## Action schemas\n"]
    for a in reg.ACTIONS:
        if a.method and a.agent_visible():
            ex = "".join(f"\n  - example: {e}" for e in a.examples)
            parts.append(f"### {a.id}\n{a.title}. `{a.method} {a.path}` · risk `{a.risk}` · scope `{a.scope}` · MCP `{a.tool}`{ex}\n"
                         f"```json\n{json.dumps(a.schema(), indent=1)}\n```\n")
    return "\n".join(parts)


def add_agent_routes(app: FastAPI) -> None:
    @app.get("/llms-full.txt", response_class=PlainTextResponse, include_in_schema=False)
    def llms_full(request: Request):
        return PlainTextResponse(llms_full_txt(_base(request)), media_type="text/plain; charset=utf-8")

    @app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
    def llms(request: Request):
        return PlainTextResponse(llms_txt(_base(request)), media_type="text/plain; charset=utf-8")

    @app.get("/agents", response_class=HTMLResponse, include_in_schema=False)
    def agents(request: Request):
        b = escape(_base(request))
        tools = "".join(f"<li><code>{escape(t)}</code></li>" for t in mcp_tool_names())
        scopes = "".join(f"<li><code>{escape(k)}</code> {escape(v)}</li>" for k, v in SCOPES.items())
        links = "".join(f"<tr><td><code>{b}/{escape(h)}</code></td><td>{escape(d)}</td></tr>" for h, d in DEEP_LINKS)
        acts = "".join(f"<tr data-testid=agents-action><td><code>{escape(a.id)}</code></td><td>{escape(a.title)}</td><td>{escape(a.risk)}</td>"
                       f"<td><code>{escape(a.method)} {escape(a.path)}</code></td></tr>" for a in reg.ACTIONS if a.method and a.agent_visible())
        mcp = escape('{\n  "mcpServers": {\n    "huhqueue": {\n      "command": "huhqueue",\n      "args": ["mcp"],\n'
                     f'      "env": {{ "HUHQ_URL": "{_base(request)}", "HUHQ_API_TOKEN": "hq_..." }}\n    }}\n  }}\n}}')
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HuhQueue for agents</title><link rel="icon" href="/static/favicon.png">
<style>
:root{{--bg:#f8f5ff;--fg:#1c1530;--muted:#776d93;--card:#fff;--line:#e6d9fe;--lav:#c9acfa;--lav2:#6d46c9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d0a16;--fg:#f2edfc;--muted:#9d93ba;--card:#15111f;--line:#2b2342;--lav2:#d4bdfb}}}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.6 Inter,system-ui,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:880px;margin:0 auto;padding:48px 24px 80px}}h1{{font-size:32px;margin:0 0 6px;letter-spacing:-.02em}}
h2{{margin:36px 0 10px;font-size:18px}}p.lead{{color:var(--muted);margin:0 0 24px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
.card{{display:block;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;text-decoration:none;color:inherit;transition:.15s}}
.card:hover{{border-color:var(--lav);transform:translateY(-1px)}}.card b{{display:block}}.card span{{color:var(--muted);font-size:13px}}
code{{background:color-mix(in srgb,var(--lav) 22%,transparent);padding:1px 6px;border-radius:6px;font-size:13px}}
pre{{background:#141020;color:#f3ecff;padding:16px;border-radius:14px;overflow:auto;font-size:13px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td{{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top}}
ul{{padding-left:20px}}.pill{{display:inline-block;background:var(--lav);color:#141020;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600}}
</style></head><body><main>
<span class="pill">For agents</span>
<h1>Drive HuhQueue from an AI agent</h1>
<p class="lead">Everything in the app is available headlessly: REST with an OpenAPI schema, an MCP server, and URL deep links. Tokens are scoped, rate-limited, audited, and can require your approval before anything goes live.</p>
<div class="cards">
<a class="card" href="/api/openapi.json" data-testid="agents-openapi"><b>OpenAPI schema</b><span>/api/openapi.json</span></a>
<a class="card" href="/api/docs" data-testid="agents-swagger"><b>REST docs</b><span>Interactive Swagger UI</span></a>
<a class="card" href="#mcp" data-testid="agents-mcp"><b>MCP server</b><span>huhqueue mcp (stdio)</span></a>
<a class="card" href="/llms.txt" data-testid="agents-llms"><b>llms.txt</b><span>Plain-text summary for LLMs</span></a>
</div>
<h2>1. Create a token</h2>
<p>In HuhQueue open <a href="/#/settings?section=api">Settings &rarr; API &amp; MCP agents</a>, pick scopes and (optionally) the MCP tools it may call, and keep <b>Require approval</b> on if posts should wait for you. The token is shown once.</p>
<h2 id="mcp">2. Add the MCP server to your agent</h2>
<pre>{mcp}</pre>
<p>Tools:</p><ul>{tools}</ul>
<h2>Scopes</h2><ul>{scopes}</ul>
<h2>REST quick start</h2>
<pre>curl -H "Authorization: Bearer hq_..." {b}/api/channels
curl -H "Authorization: Bearer hq_..." -H "Content-Type: application/json" \\
  -d '{{"caption":"hello","channel_ids":["ch_..."],"when":"queue"}}' {b}/api/posts</pre>
<h2>Actions</h2><p>One registry drives REST, MCP, the ⌘K palette, the CLI and this page. Run any of them with
<code>POST {b}/api/actions/&lt;id&gt;:run</code>; full schemas in <a href="/llms-full.txt">llms-full.txt</a>.</p>
<table>{acts}</table>
<h2>Deep links</h2><table>{links}</table>
<p style="color:var(--muted);margin-top:32px">HuhQueue {escape(__version__)} &middot; free and open source (MIT)</p>
</main></body></html>"""
