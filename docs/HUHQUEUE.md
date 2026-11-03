---
title: "HuhQueue: research, plan and build notes"
subtitle: "Buffer Commit 2026 entry (PR #11, branch feat/house-style)"
date: "30 October 2026"
---

# 1. The pitch

**HuhQueue is a brand-aware AI editor for Buffer.** Give it a topic, a link, or your own photos and videos. One agent finds the story, verifies it on the primary source, structures the facts, renders branded cards, carousels and Reels, writes per-channel captions, checks everything against your brand rules, and hands it to Buffer as a draft, a queued post, or a scheduled post.

When you correct it ("never say breakthrough, logo smaller and top left, no hashtags"), the correction becomes a **Style Ledger** rule. Every future caption and every future render obeys it, and the brand check cites the exact rule when something fails.

Demo loop for judges:

> topic / link / media → live timeline → rendered media → correction → before/after slider → brand check → calendar → Buffer approval or schedule

Deadline: **13 Nov 2026, 11:59 PM PT**. Judging starts 16 Nov 2026.

# 2. Research

## 2.1 What already existed in `buffer-contest`

- Buffer OAuth (PKCE, rotating refresh tokens), GraphQL account / channels / posts / ideas.
- Link-to-caption drafting with Buffer's per-network character rules, threads.
- RSS news radar, plan-limit guard, approvals inbox.
- MCP server + MCP OAuth (dynamic client registration), 10 tools.
- Agent tokens with policies (`review`, `draft`, `trusted`), daily caps, kill switch, audit log.
- Supabase Postgres (free, non-expiring) with keepalive; Render free web service; `/demo` sample workspace.

**Gaps:** no media rendering, no brand kit, no learned rules, no brand check, no multi-step agent run, AI key was server-wide only, discovery was RSS only, nothing verified sources.

## 2.2 Reused from `Huhbyte.ai`

The proven editorial and render pipeline: `hb.py` (Pillow + FFmpeg branded layouts, 4:5 and 9:16, logos, end cards, captions, source credits) and the automation scripts (`news_watch`, `story_bank`, `format_rules`, `pipeline`, `post`). HuhQueue ports these ideas into a multi-tenant, brand-configurable engine.

## 2.3 What HuhQueue is built around

- **Output:** finished, branded images, carousels and Reels, plus one caption per channel.
- **Learning:** every correction becomes a written Style Ledger rule that changes the next render and caption.
- **Facts:** find, then verify on the primary source, corroborate with other outlets and pull out structured facts ("research receipts").
- **Checks:** a brand check runs before anything is sent and cites the exact rule a post breaks.
- **Surface:** a full web app with a Planner for the Buffer queue; MCP and REST are optional ways in, with the same actions.

## 2.4 Using people's AI subscriptions (compliance)

- **ChatGPT:** "Sign in with ChatGPT" can let eligible Plus/Pro users spend plan usage in participating apps. Hosted apps may need OpenAI approval, so it is a roadmap item, not a contest dependency.
- **Claude:** we never route Claude Free/Pro/Max OAuth credentials through HuhQueue. Compliant paths are the MCP connector in Claude.ai, unmodified Claude Code with its own subscription, or the user's own Anthropic API key.
- **Three tiers shipped:** (A) MCP: the user's own assistant does the reasoning, we supply tools; (B) bring-your-own key, encrypted per user (OpenAI, Anthropic, Gemini, Groq, OpenRouter, any OpenAI-compatible); (C) free built-in fallback so judges can run it with nothing.

## 2.5 Free-tier architecture

| Need | Free service |
|---|---|
| Web app | Render free web service (Docker, Frankfurt) |
| Database | Supabase Postgres free |
| Video rendering | GitHub Actions (`render.yml`), one run per Reel, single-use job token |
| News discovery | Bing News RSS, Google News RSS, Hacker News Algolia, user feeds |
| AI | BYO key, MCP, or free starter endpoint |

# 3. What was built (PR #11)

## 3.1 Backend modules

- `studio/brand.py`: brand kit defaults, website brand import (name, colours, logo), palette extraction, correction parser, rule normalisation, visual parameters, mechanical text fixes, `brand_check()` with rule citations plus built-in contrast and frame checks.
- `studio/render.py`: Pillow cards, photo branding, fact cards, end cards; FFmpeg slideshow Reels and branded user videos (logo bug, hook, crop, end card, AAC audio); thumbnails; 4:5, 9:16, 1:1.
- `studio/llm.py`: BYO encrypted AI settings, provider presets, test call, JSON parsing, free fallback.
- `studio/sources.py`: news search, dedupe, relevance and freshness scoring, Google News redirect resolution.
- `studio/house.py`: the agent. Eight visible steps: **Find, Verify, Structure, Format, Render, Caption, Brand check, Ready**. Media-only runs, revisions, before/after, package editing, scheduling, calendar, GitHub render dispatch.
- `studio/render_worker.py` + `.github/workflows/render.yml`: free video worker.
- New tables: `brand_kits`, `style_rules`, `media`, `agent_runs`, `render_jobs`, `ai_settings`.
- Buffer actions now accept multiple media URLs (carousels).

## 3.2 API and MCP

REST: `/api/media`, `/api/brand` (+ preview, from-website), `/api/ledger`, `/api/check`, `/api/ai`, `/api/agent/runs` (+ revise, packages, schedule), `/api/calendar`, worker callbacks, `/m/{token}` media serving with range requests.

MCP grew from 10 to 19 tools: `run_agent`, `edit_media`, `get_run`, `revise`, `remember_rule`, `brand_check`, `get_brand`, `schedule_package`, `list_calendar` and the originals.

## 3.3 Web app

"Make a post" agent screen (topic / link / own media), live timeline, media preview, before/after slider, quick fixes, per-channel packages with brand checks, brand kit editor and website import, Style Ledger, calendar (scheduled, sent, awaiting approval, ready), AI engine settings, mobile layout.

## 3.4 Testing

- Studio unit/integration suite and `tests/studio/test_house.py`: all passing.
- GitHub Actions: **studio-ci passed, ui-smoke passed** on commit `48d90d0` (real Chromium run of the full loop with screenshots, plus the existing browser e2e test).
- Local end-to-end: news run reaches Ready, 3-image carousel, 5 channel packages, correction produced 4 ledger rules, re-render honoured them, scheduling reached Buffer stand-in, calendar showed approval / buffer / ready; own-video Reel rendered in about 47 s and passed brand check.

# 4. Permissions and secrets needed

| What | Where | Permission / scope | Why |
|---|---|---|---|
| GitHub fine-grained token `GH_DISPATCH_TOKEN` | Render env var | Repository access: only `dataanalystram/buffer-contest`. **Actions: Read and write.** Metadata: read (automatic). Nothing else. | Lets the app start one `render.yml` run per Reel |
| GitHub Actions workflow | `render.yml` | `permissions: contents: read` (set in workflow) | Worker only checks out code; it talks to the app with a single-use job token |
| Buffer OAuth app | Render env `BUFFER_CLIENT_ID`, `BUFFER_CLIENT_SECRET` (already set) | `account:read posts:read posts:write ideas:read ideas:write offline_access` | Read channels, create drafts/queue/scheduled posts, ideas, refresh tokens |
| Render API key | Hark vault (used for env setup) | Account-wide (Render cannot scope keys) | Setting env vars; revoke it in Render after setup if you prefer |
| Supabase `DATABASE_URL` | Render env (already set) | Session pooler connection | Data store |
| Optional AI keys | Per user, in-app (encrypted) | Provider API key | BYO AI tier |

Render env names today: `AI_MODEL`, `AI_BASE_URL`, `BUFFER_CLIENT_SECRET`, `BUFFER_CLIENT_ID`, `DATABASE_URL`, `SESSION_SECRET`, `PUBLIC_URL`, `RENDER_WORKER` (= github), `GH_REPO`. Still to add by you: `GH_DISPATCH_TOKEN`.

Security housekeeping: change the Google password that was typed in chat; give the dispatch token a 90-day expiry.

# 5. Remaining plan to 13 Nov

1. You add `GH_DISPATCH_TOKEN` in Render; we run a test Reel through the GitHub worker.
2. Merge PR #11 to `main` (CI green) → Render auto-deploys.
3. Production checks: `/healthz`, `/demo`, agent run, brand preview, calendar, real Buffer draft.
4. Polish pass on calendar and package UI (desktop + mobile screenshots).
5. Demo video (about 2 minutes) following the judge loop above, README hero, submission form.
