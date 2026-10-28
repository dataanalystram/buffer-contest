# HuhQueue roadmap: milestone by milestone

Ordered so Ram can start coding in the morning. **Instagram-first items** and features that Buffer and others charge for, which HuhQueue gives **free to everyone**, come first. Each milestone is shippable on its own, has acceptance tests (pytest for the backend, Playwright `data-testid` checks for the UI, following the existing `tests/` style), and touches as few files as possible.

The basis for this order is FEATURE_MATRIX.md (statuses checked against code at `f5a144f`), with CMS_PLAN.md and AI_NATIVE_PLAN.md for the designs.

**Pricing angle.** Everything below is free and unlimited when self-hosted (the repo is MIT-licensed; no feature gates). A possible cheap hosted tier (e.g. €3–5/month) would charge only for what costs money to run:

- **X API**: pay-per-use, $0.015 per post or $0.20 per post with a link. Always bring your own key.
- **Hosted LLM calls**: metered credits. Ollama stays free.
- **Storage and bandwidth**, and **outbound email**.
- **Our own reviewed Meta/TikTok/Google apps**, so hosted users skip developer-app setup.

The milestones that add a running cost are marked 💸.

---

## M1: Fix what is broken + groundwork (½–1 day)

Small fixes found during the audit, plus the migration system that later milestones need.

1. **Insights CSV export:**
   - export every post in the range, not `top_posts[:20]` (`app.py` `insights_csv`),
   - honour `start`/`end` and `channel_id` (`Insights.jsx` sends `days=30` for custom).
2. **Filtering in SQL:** move `tag`, `q` and `post_type` filtering in `GET /api/posts` from Python into SQL. Today it runs after `LIMIT`, which silently drops rows. Same fix for `q` in `GET /api/comments`.
3. **`/llms.txt` + `/agents`:** add both routes (static text for now, generated in M5). This fixes the ⌘K "For agents" link (`App.jsx` L96).
4. **MCP parameters:** `get_analytics` accepts `start`/`end`/`channel_id`. `list_queue` accepts `start`/`end`/`tag`/`q`.
5. **Post notes:** `PATCH /api/targets/{id}` accepts `note`. Show and edit the note in the composer.
6. **Channel-page Community:** add saved replies, resolve/reopen and the By-post toggle, so it matches the global Community page.
7. **Migrations:** add a `schema_migrations` table and `huhqueue/migrations/0001_baseline.sql` (CMS_PLAN §3.1).

**Acceptance**
- `test_insights_csv_all_rows_and_custom_range`: 25 sent posts in the range → the CSV has 25 data rows. `start`/`end` limits the rows.
- `test_posts_filter_sql`: with 400 posts and the tag on the 350th, `GET /api/posts?tag=x` returns it.
- `test_llms_txt` and `/agents` return 200 without auth and leak no data.
- `test_mcp_get_analytics_custom_range`.
- `test_target_patch_note`.
- `test_migrations_fresh_and_upgrade`: a fresh DB and an `f5a144f` DB both migrate to head, and `PRAGMA integrity_check` returns `ok`.
- Playwright: the channel Community tab has `comment-saved-reply` and `comment-resolve`.

## M2: Instagram Pro features, free (2–3 days)

These are features Buffer locks or lacks for Instagram.

1. **Mentions tab:** sync `/{ig-user-id}/tags` (media you are tagged in) and mentioned comments, using the same `comments` table with a new `kind` column. Add Community tabs Comments · Mentions.
2. **Bulk resolve / bulk reply-with-saved-reply** on Community (`POST /api/comments/bulk`).
3. **Turn comment into post:** "Start a post" on a comment opens the composer prefilled with a quote and the author handle.
4. **Emoji picker** in reply boxes. Pre-fill `@author` in the reply box.
5. **Planned grid preview:** queued IG posts plus the live history in one 3-column grid (`ChannelTabs.jsx`), with drag to reorder the queue.
6. **Best time to post from own data:** compute the best weekday×hour from `post_targets.insights` (reach/ER), plus IG `online_followers` when there are 100+ followers. Show it as a heat-map and offer "Use as posting slots" (`scheduling.set_slots`).
7. **Per-post insights:** store IG watch time and avg watch time for Reels (`ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`).
8. **Hashtag group stats:** average reach and ER of posts that used each group.

**Acceptance**
- `test_ig_mentions_sync` (mocked Graph `tags` edge) → `kind='mention'` rows. Playwright `comments-tab-mentions`.
- `test_comments_bulk_resolve`.
- Playwright: "Start a post" opens the composer with the comment text.
- `test_best_time_heatmap`: synthetic insights → the top slot equals the planted best hour. "Use as slots" writes the slots.
- `test_ig_reel_watch_time_saved` (mocked).
- `test_hashtag_group_stats`.

## M3: Filter and view parity + saved views + timezone (2 days)

This covers every control in FEATURE_MATRIX §3.

1. **Shared `<FilterBar>`** with multi-select **channels, status, tags, post type** (image / video / reel / story / carousel / text, read from `options.ig_type` and the media), **campaign** (once M7 lands), **search** and **date range**. Used on Queue, Calendar, Sent, the channel-page tabs, Community and Insights.
2. **Calendar:**
   - status filter,
   - tag filter,
   - an **undated drafts** tray,
   - "show posting slots" overlay,
   - post-type filter.
3. **Display timezone control:** a user preference, overridable per view. All times are rendered in it, and drag/drop writes in it (the calendar currently uses the browser timezone, `Calendar.jsx` L54/95).
4. **Saved views:** the `saved_views` table (CMS_PLAN 0008), "Save view" button and view chips on every page.
5. **Sort controls:** lists (scheduled, published, updated, views, ER), Community (newest, oldest, most liked), Insights table (any metric column). Also add a post-type column to the Insights table.
6. **Queue niceties:**
   - day headers on every tab,
   - a "see more" expander,
   - an inline "Add tags" picker on PostCard,
   - an empty-slot "+ New" placeholder at its slot time.
7. **Insights:**
   - tag and post-type filters,
   - **period-over-period** comparison (This period / Previous / Both),
   - a custom range on the channel Insights tab.
8. **Community filters:** by post, date, network, and "has a question" (`?` heuristic).

**Acceptance**
- API: `GET /api/posts?channel_id=a,b&status=scheduled,draft&tag=x,y&post_type=reel,story&start&end&sort=-published_at` returns the correct set (table-driven test).
- `test_saved_views_crud` + Playwright: save a view on Queue, reload, click the chip → the same URL filters.
- Playwright: the calendar status filter hides Sent. The undated drafts tray shows a dateless draft. The timezone switch moves a 19:00 Dublin post to 14:00 New York.
- `test_insights_period_compare`: the totals and the previous-period totals are both right.
- Every Buffer control in FEATURE_MATRIX §3 has a matching `data-testid`. A Playwright smoke test asserts each one exists.

## M4: Media library (2 days)

1. **Migration 0004:** folders, tags, title, source, phash, soft delete, plus the `media_usage` view.
2. **`#/library` screen:** folder tree, grid/list, dropzone (files, zip, URL), filters, smart folders "Unused" and "Duplicates", asset drawer (alt text, tags, usage, derivatives).
3. **Usage tracker:** warn before deleting an asset that is in use.
4. **Near-duplicate detection:** pHash via Pillow. On upload, offer "use existing".
5. **Unsplash import** (free API key, BYO) and Google Drive import (OAuth, BYO). These are free.
6. **Composer picker:** "From library" opens the library in pick mode.

**Acceptance**
- `test_media_usage_view`: an asset used in 2 posts → `uses=2`.
- `test_delete_used_asset_requires_force`.
- `test_phash_near_duplicate`: the same image resized is flagged.
- `test_folders_move_and_filter`.
- Playwright: upload into a folder, tag it, search by tag, pick it in the composer.

## M5: One action registry → REST, OpenAPI, MCP, ⌘K, CLI, llms.txt (3 days)

Design: AI_NATIVE_PLAN §2.

1. **Registry:** the `@action` decorator plus `GET /api/actions` and `POST /api/actions/{id}:run`, with `dry_run`, confirm tokens and idempotency keys.
2. **Wrap the existing actions:** all 51 API-backed UI actions (inventory §1) are registered, and the existing routes are re-pointed to the action functions without changing their paths.
3. **MCP from the registry:**
   - `mcp_server.py` builds `tools/list` from `/api/actions`, filtered by scopes, tool allowlist and risk,
   - the old tool names stay as aliases,
   - add HTTP (streamable) transport next to stdio.
4. **⌘K runs actions:** a JSON-Schema form, prefilled from page context.
5. **CLI:** `huhqueue run <action>`.
6. **Generated docs:** `/llms.txt`, `/llms-full.txt` and `/agents` are generated from the registry.
7. **Audit everything:** every action run is audited, including UI-session runs (`audit_log` columns from AI_NATIVE_PLAN §8).

**Acceptance**
- `test_registry_parity`: every `/api` route is either an action or on the allow-list. Every `mcp=True` action is in `tools/list`. Every `palette=True` action is in the palette feed.
- `test_mcp_matches_rest` for schedule, reschedule, delete, reply, resolve and bulk.
- `test_ui_session_actions_audited`.
- MCP coverage ≥ 90% of non-secret UI actions (up from about 22% today).

## M6: AI assistant + natural-language command bar 💸 (3–4 days)

Design: AI_NATIVE_PLAN §3. It costs money only when a hosted LLM is used.

1. **Provider layer:** OpenAI-compatible, Ollama, NVIDIA NIM and Anthropic, plus a fake provider for tests. Settings → AI (UI-only keys, encrypted). `llm_usage` budget table.
2. **Composer AI** (each one is also an action):
   - caption from media or idea,
   - 5 hooks,
   - rewrite per network,
   - shorten,
   - hashtags (respecting `hashtag_limit`),
   - alt text (vision when the model supports it),
   - repurpose (Reel → carousel text, thread).
3. **Command bar:** free text → plan → preview with validation → **Confirm** for public and destructive actions.
   - Resolvers: assets, ideas, posts, channels, time in the workspace timezone.
4. **AI replies:**
   - suggestions in Community in "your voice" (few-shot from your past replies),
   - **Takeaways** card on Insights (weekly),
   - optional **sentiment** on comments.

**Acceptance**
- `test_command_bar_schedule` (fake LLM): "schedule the Orgo reel for Friday 7pm on Instagram" → `needs_confirmation`. After confirm there is one target at 2026-10-23 18:00 UTC with `ig_type=reel` and the Orgo media.
- `test_command_bar_never_runs_secret_actions`.
- `test_destructive_requires_confirm` (bulk delete).
- `test_llm_budget_blocks_when_exceeded`.
- `test_providers_payload_shape`: one test per provider, mocked HTTP.
- Playwright: Composer "Write caption" fills the caption (fake provider).

## M7: Resource kernel + campaigns + templates/snippets/hashtags as tables (3 days)

Design: CMS_PLAN §3–5.

1. **Migration 0003:** move the library KV blobs into tables (templates, hashtag_groups, snippets, tags, saved_replies). Add campaigns. Add `custom` and `deleted_at` columns.
2. **Kernel:** the generic `/api/r/{resource}` list/detail/create/patch/delete/restore/bulk/export/import/schema endpoints, plus the `<ResourceTable>` UI.
3. **Campaigns:** colour on the calendar, campaign filter everywhere (completes M3), campaign report.
4. **Templates with variables:** a fill-in form. **Snippets:** `{{key}}` expanded in the composer and at publish.
5. **Custom fields** (CMS_PLAN 0008).
6. **FTS5 search:** "search everything" in ⌘K.

**Acceptance**
- `test_kv_library_migrated`: old `lib:*` rows → tables, with the same ids.
- `test_resource_generic_crud_and_bulk` for every resource.
- `test_campaign_filter_and_report`.
- `test_template_render_variables`.
- `test_snippet_expand_on_publish`.
- `test_custom_field_validation`.
- `test_fts_search_across_types`.

## M8: Contacts (commenter CRM) (2 days)

Design: CMS_PLAN §4.9.

1. **Migration 0007:** contacts, segments, and `comments.contact_id / assignee_id / sentiment / kind`. Backfill from existing comments.
2. **Upsert on sync:** `community.sync_channel` upserts contacts. Profile enrichment where the API allows it.
3. **Screens:** `#/contacts` list with segments sidebar. Contact profile with timeline, reply, notes, tags, custom fields.
4. **Segments:** rule builder (superfans, new, negative, asked a question) usable as Community filters.
5. **Assignment:** assign a comment or contact (needs users from M9; until then, assign to "me").
6. **Privacy:** contacts are removed on channel disconnect and on Meta data deletion.

**Acceptance**
- `test_contacts_backfill`: N distinct authors → N contacts, with comments linked.
- `test_contact_timeline_cross_channel`.
- `test_segment_rule_eval`.
- `test_contacts_deleted_on_meta_data_deletion` (extends `tests/test_meta_compliance.py`).
- Playwright: open a contact from a comment, add a note, reply from the timeline.

## M9: Users, roles, 2FA, human approvals, notes, revisions (3–4 days)

**Status (26 Oct 2026): done** except the collision hint (6). Roles also include Client (view + comment); invites are links with optional email. Tests: `tests/test_teams.py`, `tests/e2e/test_ui_teams.py`.

Design: CMS_PLAN 0002, 0005, §4.4–4.7.

1. **Users, sessions, invites:** the current app password becomes the Owner user. **TOTP 2FA** (pyotp) and recovery codes.
2. **Roles:** Owner / Admin / Editor / Contributor / Viewer, plus a per-channel allow-list. Tokens are capped by their user's role.
3. **Human approval workflow:** Contributors request approval. Approvers approve or request changes, and a note is required on reject. Notifications go to the bell, email (optional) and webhook.
4. **Notes and @mentions** on posts, ideas, assets, campaigns and contacts. Mentions notify.
5. **Revisions:** a timeline with diff and restore for posts, templates and ideas.
6. **Collision hint:** "Ana is editing" via a short-lived presence row.

**Acceptance**
- `test_migrate_password_to_owner`: the existing login still works.
- `test_totp_enrol_and_login`.
- `test_role_matrix`: the table-driven CMS_PLAN §4.4 matrix holds for every role × action.
- `test_contributor_cannot_schedule_only_request`.
- `test_reject_requires_note`.
- `test_mention_creates_notification`.
- `test_revision_restore`.
- `test_token_capped_by_user_role`.

## M10: Link-in-bio + link shortener + UTM builder (2 days)

All free. No third-party shortener needed.

1. **Migration 0006:** `bio_pages`, `bio_blocks`, `links`, `link_clicks`.
2. **Public page** `/@{slug}` on the edge app. Blocks: links, latest IG posts grid (each post links to its URL), email capture (stored locally), social icons. Theming from the workspace brand.
3. **Shortener** `/l/{code}`: 302 redirect plus a click row (no third-party cookies; ua hashed).
4. **UTM builder** in the composer: per-campaign defaults, auto-applied to links when "shorten links" is on.
5. **Analytics:** clicks per link, block, post and campaign.

**Acceptance**
- `test_bio_page_public_render` (no auth, no data leaks).
- `test_short_link_redirect_and_click_logged`.
- `test_utm_applied_on_publish`.
- `test_link_clicks_in_campaign_report`.
- Playwright: build a page with 3 links, publish it, open `/@huhbyte`.

## M11: Automation (2–3 days)

1. **Evergreen recycling:** a recycle pool per channel, with min gap, max uses and spintax variants. The worker refills empty slots from it.
2. **Recurring posts:** every N days, weeks or months until a date.
3. **RSS auto-post:** a feed → draft or queue, optional AI caption 💸, dedup by GUID and the existing topic-key duplicate guard.
4. **Bulk CSV import of posts:** CMS_PLAN §4.11 columns, with a dry-run preview.
5. **Channel groups:** saved channel sets in the composer and filters.
6. **Threads:** N-part posts for X 💸 (per-post cost), and Bluesky, Threads and Mastodon once those connectors land.

**Acceptance**
- `test_evergreen_fills_empty_slot_respecting_gap`.
- `test_recurring_generates_next_occurrence`.
- `test_rss_new_items_once` (fixture feed, run twice → no duplicates).
- `test_csv_import_dry_run_errors_then_commit`.
- `test_channel_group_expands`.
- `test_x_thread_chain` (mocked: replies chained by `in_reply_to`).

## M12: Reports (1–2 days)

1. **Reports page:** a range with period comparison, filters (channel, tag, type, campaign), and KPI, chart and table sections.
2. **Export** to CSV (fixed in M1), **markdown**, and **PDF** (HTML → PDF with WeasyPrint, which is free). **Branded cover:** workspace logo and colours.
3. **Scheduled report:** weekly or monthly to a webhook or email 💸 (own SMTP is free).

**Acceptance**
- `test_report_markdown_matches_kpis`.
- `test_pdf_report_has_logo_and_pages`.
- `test_scheduled_report_delivered` (webhook mock).

## M13: Real webhooks + headless mode (1–2 days)

Design: AI_NATIVE_PLAN §4–5.

1. **Webhooks:** `webhooks` and `webhook_deliveries` tables, event subscriptions, HMAC signature, retries, delivery log with redeliver, test-send.
2. **Headless:** `HUHQ_HEADLESS=1`, CORS per token and global, token `expires_at`, channel allow-list, risk ceiling, IP allow-list, idempotency keys.
3. **n8n community node** (thin wrapper over the actions and webhooks). Zapier and Make use the generic webhooks.

**Acceptance**
- `test_webhook_hmac_valid`.
- `test_webhook_retry_backoff_and_disable`.
- `test_cors_preflight_allowed_origin_only`.
- `test_expired_token_401`.
- `test_idempotent_post_once`.

## M14: Workspaces / brands + white-label + backup (2 days)

1. **Migration 0002 workspace parts:** `ws_default` plus a switcher. Every query is scoped by workspace. Brand settings: logo, colours, voice, CTA, timezone.
2. **White-label:** replace the hard-coded HuhByte logo and name (`huhqueue/static/logo.png`) with the workspace brand. Custom domain for bio pages and short links.
3. **Full export/import** as a workspace zip (secrets only on explicit owner opt-in).

**Acceptance**
- `test_workspace_isolation`: a token from workspace A can't see B's posts, assets or contacts.
- `test_white_label_logo_served`.
- `test_backup_roundtrip`: export → fresh install → import → identical counts and checksums.

## M15: Mobile PWA + browser extension (2 days)

1. **PWA:** manifest, service worker (offline shell), install prompt, web push for "Your turn" / notify-mode posts and new comments. This is free; VAPID keys are self-generated.
2. **Browser extension (MV3):** "Add to HuhQueue" for the current page or a selected image → `upload_media` + draft. It authenticates with a scoped token.

**Acceptance**
- Lighthouse PWA installable.
- `test_web_push_subscription_and_send` (mock).
- Extension e2e: a context-menu image → asset + draft created.

## M16: More connectors (rolling)

This follows `docs/CONNECTORS_RESEARCH.md` §5:

- Facebook Pages (same Meta app),
- Bluesky and Mastodon (easy, free),
- Threads,
- LinkedIn.

Each one gets the existing connector-test pattern (`tests/test_x.py` style, mocked HTTP).

## M17: Optional cheap hosted tier 💸

- **Multi-tenant:** a workspace per customer (from M14).
- **Metering:** `llm_usage`, storage GB, emails.
- Stripe or Paddle checkout. No feature gates; only usage caps.
- Shared reviewed Meta/Google/TikTok apps. X is always BYO-key.

**Acceptance:** tenant isolation tests (M14) pass with 2 tenants. Usage caps stop LLM calls at budget, not publishing.

---

## Morning start checklist (M1)

1. `git checkout -b feat/m1-fixes origin/main` (after the UI-pass branch merges).
2. `app.py` `insights_csv`: rebuild the rows from the query instead of `top_posts`, and accept `channel_id`. In `Insights.jsx`, build the export href from the same params as the page.
3. `app.py` `post_list`: push `tag` (`json_each(posts.tags)`), `q` (`LIKE`) and `post_type` into SQL before `.limit()`.
4. Add `/llms.txt` and `/agents` to `create_app` (public, static for now). Add them to `PUBLIC_API`-style exemptions in the guard.
5. `mcp_server.py` `get_analytics` / `list_queue`: pass the extra params through.
6. `posts.update_target`: handle `note`. Add the field to `Composer.jsx`.
7. `ChannelTabs.jsx` CommunityTab: reuse the Community components.
8. Add `huhqueue/migrations/` + runner in `db.py`.
9. Run `pytest -q` and the Playwright smoke test, then open a PR.
