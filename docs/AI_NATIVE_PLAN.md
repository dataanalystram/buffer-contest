# HuhQueue AI-native plan: every button is an action an agent can call

Goal: anything a person can click, an agent can do through REST, MCP, the ⌘K palette or a natural-language command bar. All four are generated from **one action registry**, under one permission and approval policy, with one audit trail.

Today (code at `f5a144f`):

- The UI already talks only to the REST API (`web/src/api.js`: "The UI uses exactly the same API as agents/CLI").
- FastAPI publishes OpenAPI at `/api/openapi.json`.
- The MCP server (`huhqueue/mcp_server.py`) hand-writes **12 tools**, kept in a list that is duplicated in `security.py` `MCP_TOOLS`.
- The ⌘K palette (`web/src/App.jsx` ~L80–110) is a hand-written list of navigation entries. It runs no actions except theme and logout.
- Approval policy per token: `require_approval`, `publish` scope (`app.py` `approval_actor`).
- Audit: `audit_log` for token requests (`app.py` guard middleware).

The problem is that the three lists (routes, MCP tools, palette) drift apart. The fix is one registry.

---

## 1. Current UI action inventory and coverage

**API** = a REST endpoint does it (the UI calls it). **MCP** = an MCP tool does it today.

| # | Screen | UI action | REST endpoint | API | MCP tool today |
|---|---|---|---|---|---|
| 1 | Login | Set password (first run) | `POST /api/auth/setup` | ✅ | ❌ (not appropriate) |
| 2 | Login | Log in / log out | `POST /api/auth/login`, `/logout` | ✅ | ❌ (n/a) |
| 3 | Settings | Change password | `POST /api/auth/password` | ✅ | ❌ (n/a) |
| 4 | Header | Notifications list / mark read | `GET /api/notifications`, `POST /api/notifications/read` | ✅ | ❌ |
| 5 | Home | Load scorecard, up next, comments | `GET /api/home` | ✅ | ❌ |
| 6 | Home / PostCard | Publish now | `POST /api/targets/{id}/publish-now` | ✅ | ✅ `publish_now` |
| 7 | Composer | Load channel account info (IG collaborators etc.) | `GET /api/channels/{id}/account-info` | ✅ | ❌ |
| 8 | Composer | Upload media (files, zip) | `POST /api/media` | ✅ | ✅ `upload_media` (path/folder/URL) |
| 9 | Composer | Validate per channel (live issues) | `POST /api/validate` | ✅ | ❌ (only indirectly through `get_post` issues) |
| 10 | Composer | Suggest format | `POST /api/suggest-format` | ✅ | ✅ `suggest_format` |
| 11 | Composer | Save draft | `POST /api/posts` when=draft | ✅ | ✅ `create_post` |
| 12 | Composer | Add to queue / prioritize / custom time / now | `POST /api/posts` | ✅ | ✅ `schedule_post` |
| 13 | Composer | Edit existing post | `GET`/`PATCH /api/targets/{id}` | ✅ | ✅ `get_post` / `reschedule` (caption/options) |
| 14 | Composer | Insert template / hashtag group | `GET /api/library/{kind}` (client-side insert) | ✅ read | ❌ |
| 15 | Composer | Kick worker after "now" | `POST /api/worker/tick` | ✅ | ❌ |
| 16 | PostCard | Delete post | `DELETE /api/targets/{id}` | ✅ | ❌ |
| 17 | PostCard | Duplicate (same / other channels) | `POST /api/targets/{id}/duplicate` | ✅ | ❌ |
| 18 | PostCard | Approve / reject | `POST /api/targets/{id}/approve`, `/reject` | ✅ (session only, tokens get 403) | ❌ (by design) |
| 19 | PostCard | Retry failed | `POST /api/targets/{id}/retry` | ✅ | ❌ |
| 20 | PostCard | Mark as posted (notify mode) | `POST /api/targets/{id}/mark-posted` | ✅ | ❌ |
| 21 | PostCard | Refresh insights | `POST /api/targets/{id}/refresh-insights` | ✅ | ❌ |
| 22 | PostCard | Show event timeline | `GET /api/targets/{id}/events` | ✅ | ✅ inside `get_post` |
| 23 | PostCard / Queue | Bulk delete / draft / requeue / tag | `POST /api/bulk` | ✅ | ❌ |
| 24 | Queue | List with filters (status tabs, channel, tag, type, search) | `GET /api/posts` | ✅ | ✅ `list_queue` (status + channel only) |
| 25 | Queue | Drag reorder | `POST /api/queue/{cid}/order` | ✅ | ❌ |
| 26 | Calendar | Load range | `GET /api/posts?start&end&status` | ✅ | ⚠️ `list_queue` has no date range |
| 27 | Calendar | Drag to reschedule | `PATCH /api/targets/{id}` when=custom | ✅ | ✅ `reschedule` |
| 28 | Calendar | Click empty cell → compose at time | (client) → `POST /api/posts` | ✅ | ✅ `schedule_post` |
| 29 | Channels | Start OAuth connect | `POST /api/connect/{provider}` | ✅ (admin) | ❌ |
| 30 | Channels | Connect with pasted token | `POST /api/connect/{provider}/token` | ✅ | ❌ |
| 31 | Channels | Complete OAuth from pasted URL | `POST /api/oauth/complete` | ✅ | ❌ |
| 32 | Channel detail | Edit timezone / daily limit / pause / name | `PATCH /api/channels/{id}` | ✅ | ❌ |
| 33 | Channel detail | Refresh profile | `POST /api/channels/{id}/refresh-profile` | ✅ | ❌ |
| 34 | Channel detail | Refresh token | `POST /api/channels/{id}/refresh-token` | ✅ | ❌ |
| 35 | Channel detail | Disconnect | `DELETE /api/channels/{id}` | ✅ | ❌ |
| 36 | Channel detail | Edit posting slots / apply preset | `PUT /api/channels/{id}/slots`, `POST …/slots/preset` | ✅ | ❌ |
| 37 | Channel tabs | History grid (network posts) | `GET /api/channels/{id}/history` | ✅ | ❌ |
| 38 | Channel tabs | Account insights 7–30d | `GET /api/channels/{id}/insights` | ✅ | ❌ |
| 39 | Channel tabs / Community | Pull comments | `POST /api/comments/sync` | ✅ | ❌ |
| 40 | Community | List comments (status, channel, search) | `GET /api/comments` | ✅ | ✅ `list_comments` |
| 41 | Community | Reply | `POST /api/comments/{id}/reply` | ✅ | ✅ `reply_comment` |
| 42 | Community | Resolve / reopen | `POST /api/comments/{id}/resolve\|reopen` | ✅ | ❌ |
| 43 | Ideas | List / create / edit / move / delete idea | `GET/POST /api/ideas`, `PATCH/DELETE /api/ideas/{id}` | ✅ | ❌ |
| 44 | Ideas | Rename / add columns | `PUT /api/idea-columns` | ✅ | ❌ |
| 45 | Insights | KPIs, chart, top posts (range, channel) | `GET /api/insights` | ✅ | ✅ `get_analytics` (no custom start/end, no channel) |
| 46 | Insights | Export CSV | `GET /api/insights/export.csv` | ✅ | ❌ |
| 47 | Settings | Edit general settings (timezone, hashtag limit, CTA, webhook URL, public URL, theme) | `PUT /api/settings` | ✅ | ❌ |
| 48 | Settings | Library: hashtags, templates, saved replies, tags | `PUT /api/library/{kind}` | ✅ | ❌ |
| 49 | Settings | App keys per network | `GET/PUT /api/app-keys/{provider}` | ✅ **UI-only by design** | ❌ (must stay) |
| 50 | Settings | Mint / edit / delete API tokens | `POST/PATCH/DELETE /api/tokens` | ✅ (admin) | ❌ (keep human-only) |
| 51 | Settings | View audit log | `GET /api/audit` | ✅ | ❌ |
| 52 | Settings | Tunnel start / stop / status | `POST /api/tunnel/start\|stop`, `GET /api/tunnel` | ✅ | ❌ |
| 53 | ⌘K | Navigation entries, theme toggle, logout, "For agents" link | none (client only). `/agents` **does not exist** in `app.py` | ❌ | ❌ |
| 54 | Media (no screen) | Media list / alt text / delete / from-URL | `GET /api/media`, `PATCH/DELETE /api/media/{id}`, `POST /api/media/from-url` | ✅ | ⚠️ only from-URL via `upload_media` |

**Totals.**

- 54 UI actions. 51 have a REST endpoint (the 3 without are client-only: ⌘K navigation, theme, and template insertion done client-side).
- **12 of 54 (about 22%)** have an MCP tool. Another 3 are partly covered (⚠️).
- By design, approve/reject, app keys and token minting should stay human-only.

### Known gaps found while checking

- `/agents` and `/llms.txt` are linked from ⌘K (`App.jsx` L96) but no backend route serves them.
- `get_analytics` cannot pass `start`/`end`/`channel_id`, although `/api/insights` supports all three.
- `list_queue` cannot pass `start`/`end`/`tag`/`q`, although `/api/posts` supports them.
- MCP is stdio only. Remote agents (ChatGPT connectors, hosted Claude) need an HTTP transport.
- No CORS headers. Tokens never expire.

---

## 2. The action registry (one source of truth)

### 2.1 Shape

```python
# huhqueue/actions/registry.py
@action(
  id="post.schedule",                       # dotted, stable, used everywhere
  title="Schedule a post",
  summary="Create a post for one or more channels and queue / time / publish it.",
  input=SchedulePostIn,                     # pydantic model -> JSON Schema
  output=PostOut,
  scope="publish",                          # read | write | publish | comments | admin
  risk="public",                            # safe | write | destructive | public | secret
  confirm=True,                             # command bar must confirm
  approval="policy",                        # never | always | policy (token/user policy decides)
  rest=("POST", "/api/posts"),              # generated route (or alias to existing)
  mcp=True, palette=True,
  palette_hint="schedule {channel} {when}",
  examples=["schedule the Orgo reel for Friday 7pm on Instagram"],
  idempotency_key="client_ref",
)
def schedule_post(ctx: Ctx, inp: SchedulePostIn) -> PostOut:
    return posts_svc.create_post(ctx.core, inp.model_dump(), source=ctx.source, approval_by=ctx.approval_actor())
```

`Ctx` carries the actor (user / token / system), workspace, scopes, policy, a request id, and `dry_run`.

### 2.2 Generators (run at startup, no codegen files to keep in sync)

| Output | How it is generated |
|---|---|
| **REST routes** | For each action with `rest`, add a FastAPI route whose body or query model is `input`. Existing routes are kept and re-pointed to the action function, so the paths stay the same |
| **OpenAPI** | Free from FastAPI. Every action adds `x-huhq-action`, `x-huhq-risk` and `x-huhq-scope` extensions |
| **MCP tools** | `mcp_server.py` stops hand-writing `TOOLS`. It reads `GET /api/actions` (filtered by the token's scopes, tool allowlist and risk) and maps `tools/call` → `POST /api/actions/{id}:run`. The tool name is `id` with `.`→`_` (`post_schedule`). Old names stay as aliases (`schedule_post`, …) |
| **⌘K palette entries** | The UI fetches `GET /api/actions?palette=1`. Each entry shows its title and hint, and opens an auto-generated form from the JSON Schema (prefilled from the current page context: selected post, channel, date). Navigation entries become `nav.*` actions on the client side |
| **CLI** | `huhqueue run <action-id> --json '{…}'` and `huhqueue actions list` |
| **`/llms.txt` + `/agents`** | Generated text and an HTML page listing every action, scope, risk and example, with links to `/api/openapi.json` and MCP setup |

### 2.3 Generic run endpoint

- `POST /api/actions/{id}:run` with body `{input, dry_run?, confirm_token?, idempotency_key?}`. It returns one of:
  - `{status:"done", output}`
  - `{status:"needs_confirmation", preview, confirm_token}`
  - `{status:"pending_approval", approval_id}`
  - `{status:"error", …}`
- `GET /api/actions` lists actions with JSON Schemas (filtered for the caller).

### 2.4 Risk classes

| Risk | Examples | Command bar | Agent token default |
|---|---|---|---|
| `safe` (read) | list posts, analytics, comments | run immediately | allowed with `read` |
| `write` | create draft, edit idea, tag, upload asset | run, with an undo toast | allowed with `write` |
| `destructive` | delete post/asset/channel, bulk delete, disconnect | **confirm** (shows the count and the items) | needs `admin` or explicit tool allow + approval |
| `public` | schedule, publish now, reply to comment, approve | **confirm** with a rendered preview per channel | `require_approval` default ON |
| `secret` | app keys, tokens, password | UI only, never exposed to the LLM | never |

---

## 3. Natural-language command bar

Example: *"schedule the Orgo reel for Friday 7pm on Instagram"*

1. **Input:** the ⌘K box accepts free text. Plain matches go to palette entries first. Otherwise the text is sent to `POST /api/assistant/plan`.
2. **Planning:** the server sends the LLM:
   - the user text,
   - a compact catalog of the actions the caller may run (id, summary, JSON Schema),
   - page context (current route, selection),
   - "resolver" tools: `search.assets`, `search.ideas`, `search.posts`, `channels.list`, `time.parse`.
   
   The model returns **tool calls only** (JSON), never free-form side effects.
3. **Resolution example:**
   - "the Orgo reel" → `search.assets(q="Orgo", kind=video)` + `search.ideas(q="Orgo")` → candidates. If there are several, the bar shows a picker.
   - "Friday 7pm" → `time.parse` in the workspace timezone (Europe/Dublin), giving the next Friday 19:00.
   - "Instagram" → the channel(s) with `provider=instagram`. If there are two IG channels, ask which.
4. **Plan preview:**
   ```
   post.schedule {channel_ids:[ch_ig_huhbyteai], media_ids:[md_orgo], options:{ig_type:"reel"},
                  when:"custom", scheduled_at:"2026-10-23T19:00", timezone:"Europe/Dublin", caption: <from idea or ask>}
   ```
   The bar renders the post preview, runs `/api/validate` issues inline, and shows **[Confirm] [Edit in composer] [Cancel]**.
5. **Execution:** only after Confirm, for `public`/`destructive` risk. It goes through `/api/actions/post.schedule:run` with the `confirm_token`, so the same policy and audit apply as for any other client.
6. **Multi-step:** "move all drafts tagged YC to the queue" becomes `post.list(filter)` → preview of N items → one `post.bulk(action=requeue)` confirm.
7. **Never:** the LLM never sees tokens, app keys or other secrets, and cannot call `secret` actions.

### 3.1 Pluggable LLM providers

```python
class LLMProvider(Protocol):
    def chat(self, messages, tools, *, model, temperature=0, max_tokens=1024) -> ToolCallResponse: ...
```

| Provider | Config | Notes |
|---|---|---|
| OpenAI-compatible (OpenAI, Groq, Together, OpenRouter, LM Studio, vLLM) | `HUHQ_LLM_BASE_URL`, `HUHQ_LLM_API_KEY`, `HUHQ_LLM_MODEL` | Function calling via `tools` |
| Ollama (local, free) | `HUHQ_LLM_PROVIDER=ollama`, `OLLAMA_HOST`, model e.g. `qwen2.5:7b-instruct` | `/api/chat` with tools. The default for $0 self-host |
| NVIDIA NIM | `HUHQ_LLM_PROVIDER=nim`, `NVIDIA_API_KEY`, model e.g. `meta/llama-3.1-70b-instruct` | OpenAI-compatible endpoint `integrate.api.nvidia.com/v1` |
| Anthropic | `HUHQ_LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, model | Messages API `tools` / `tool_use` blocks |

- Keys are stored encrypted, like app keys (`crypto.py`), and managed from Settings → AI, which is UI-only.
- Per-workspace monthly budget and a token counter live in a `llm_usage(workspace_id, day, provider, model, in_tokens, out_tokens, cost_usd)` table.
- A fake provider for tests returns scripted tool calls (same pattern as `connectors/fake.py`).
- The same provider powers the **composer AI** (captions, hooks, repurpose, hashtags, alt text), **AI replies**, **Takeaways** and **sentiment**. Each of these is also a registry action (`ai.caption`, `ai.reply_suggest`, …) so agents can call them too.

---

## 4. Headless mode

- **API-only:** `HUHQ_HEADLESS=1` skips mounting the UI. The API and edge app run as today.
- **CORS:** a per-token `allowed_origins` list plus the global `HUHQ_CORS_ORIGINS`. Preflight is handled by Starlette `CORSMiddleware`, restricted to `/api/*`. Cookies are never sent cross-origin; bearer only.
- **Scoped tokens:** existing scopes + tool allowlist, plus:
  - `expires_at`,
  - `workspace_id`,
  - `user_id` (the token can never exceed its user's role),
  - `channel_ids` allow-list,
  - risk ceiling (e.g. max `write`),
  - IP allow-list (optional).
  
  Tokens get rotate and revoke actions, and last-used is shown (already tracked).
- **Idempotency:** an `Idempotency-Key` header on POST actions, cached for 24h (`idempotency(key, token_id, response, created_at)`). Retries from n8n/Make then can't double-post.
- **Pagination and filtering:** cursor pagination (CMS_PLAN §4.2).

## 5. Webhooks (real ones)

```sql
CREATE TABLE webhooks (id TEXT PRIMARY KEY, workspace_id TEXT, url TEXT, secret_enc TEXT, events JSON, active INTEGER DEFAULT 1,
  created_at TEXT, last_status INTEGER, failures INTEGER DEFAULT 0);
CREATE TABLE webhook_deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, webhook_id TEXT, event TEXT, payload JSON,
  status INTEGER, attempt INTEGER, next_attempt_at TEXT, response_ms INTEGER, created_at TEXT);
```

- **Events:**
  - `post.created`, `post.scheduled`, `post.approval_requested`, `post.approved`, `post.rejected`
  - `post.published`, `post.failed`, `post.needs_action`
  - `comment.received`, `comment.replied`
  - `channel.connected`, `channel.token_expiring`, `channel.disconnected`
  - `insights.updated`
  - `action.executed` (opt-in, everything)
- **Signing:** header `X-HuhQ-Signature: t=<ts>,v1=<hex HMAC-SHA256(secret, ts + "." + body)>`, plus `X-HuhQ-Event` and `X-HuhQ-Delivery`.
- **Retries:** exponential, 6 attempts over about 6h. Auto-disable after 20 failures and notify the owner.
- **UI:** list, test-send, a delivery log with redelivery.
- The existing `notify_webhook_url` (ntfy/Slack) stays as a "simple notification" webhook type.
- These webhooks make Zapier, n8n and Make triggers possible, and the actions give them their actions. A small n8n community node can follow later.

## 6. `/llms.txt` and `/agents`

- `/llms.txt` (public on the UI host, requires no login and reveals no data): what HuhQueue is, the auth model, a link to OpenAPI, MCP setup lines (`huhqueue mcp` with `HUHQ_URL`/`HUHQ_API_TOKEN`), and the action catalog (id, summary, scope, risk).
- `/llms-full.txt`: the same, plus JSON Schemas.
- `/agents`: an HTML version for humans with copy-paste config for Claude Desktop, Cursor and ChatGPT.
- This fixes the broken ⌘K "For agents" link.

## 7. Agent permissions and approval policy

A policy is evaluated in `Ctx.authorize(action)`, in this order:

1. **Hard denies:** `secret` risk is never allowed for tokens, and nothing outside the token's workspace or channels is allowed.
2. **Scope check:** token scopes ∩ the user's role.
3. **Tool allowlist:** the per-token list (exists today as `api_tokens.tools`).
4. **Risk ceiling:** the token's max risk.
5. **Rate and budget:** per-minute (exists) + per-day publish cap per channel (IG has its own 50/24h cap) + LLM budget.
6. **Approval rule:** `require_approval` (exists) generalised to per-risk rules, e.g. `{"public": "approve", "destructive": "deny", "write": "allow"}`, plus quiet hours ("no auto-publish 00:00–07:00 Dublin").

Approvals land in Queue → Approvals (and in the bell + webhook). Only humans can approve. That rule stays (`target_approve` returns 403 for tokens).

## 8. Audit trail

- Extend `audit_log` with:
  - `action_id`, `workspace_id`, `user_id`,
  - `via` (ui | token | mcp | cli | command_bar | system),
  - `input_redacted JSON`, `result` (done | pending_approval | denied | error),
  - `revision_ids JSON`, `llm_prompt_id`.
- Record **every** action run, whether from a UI session or a token. Today UI-session edits are not audited (`app.py` audits only token requests and a few admin actions).
- Command-bar runs keep the user's text and the model's plan (`llm_runs` table, with secrets redacted by the existing `security.redact`).
- UI: Settings → Audit with filters (actor, action, result, date), export CSV, and "show the post at that revision".

## 9. Acceptance tests (summary; details per milestone in ROADMAP.md)

- `test_registry_parity`:
  - every REST route under `/api` maps to an action or is listed in an explicit allow-list (auth, files),
  - every action with `mcp=True` appears in MCP `tools/list` for an all-scope token,
  - every `palette=True` action appears in `/api/actions?palette=1`.
- `test_mcp_matches_rest`: calling a tool and calling the REST route give the same DB state.
- `test_command_bar_schedule` (fake LLM): the text "schedule the Orgo reel for Friday 7pm on Instagram" returns `needs_confirmation` with the right channel, media and time in Europe/Dublin. Nothing is created until confirm. After confirm, there is one target `scheduled_at` = Friday 18:00 UTC (IST is UTC+1 on 23 Oct 2026).
- `test_destructive_requires_confirm`, `test_token_cannot_run_secret`, `test_approval_policy_public`.
- `test_webhook_signature_and_retry`, `test_cors_only_allowed_origins`, `test_idempotency_key`.
- `test_llms_txt_lists_actions` and `/agents` returns 200.
