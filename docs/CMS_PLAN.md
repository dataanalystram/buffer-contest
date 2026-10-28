# HuhQueue CMS plan: one content hub

The goal is an all-in-one content-management hub in the spirit of Laravel Nova and Filament. Every piece of content is a **resource**. Every resource gets the same list, filter, bulk-edit, detail, revision, comment and import/export machinery for free. One backend "resource kernel" drives everything, so adding a content type is a declaration, not a new page.

Starting point (code at `f5a144f`):

- SQLAlchemy models in `huhqueue/models.py`: `channels, slots, media, posts, post_targets, events, ideas, oauth_states, settings(KV), comments, api_tokens, audit_log`.
- Hashtag groups, templates, saved replies and tags are JSON blobs in `settings` (`lib:<kind>` rows, `app.py` `/api/library/{kind}`).
- The schema is created by `Base.metadata.create_all` plus an add-missing-columns pass (`huhqueue/db.py`). That approach cannot rename, backfill or add indexes.

---

## 1. Principles

1. **Resource kernel.** One Python declaration per type sets out fields, filters, sorts, actions, permissions and search columns. From it we generate:
   - the REST routes,
   - the list and detail UI (React, schema-driven),
   - the import/export,
   - the action registry entries (see AI_NATIVE_PLAN.md).
2. **Workspace-scoped.** Every content row has a `workspace_id`. A brand is a workspace setting (logo, colours, voice, CTA). Existing data moves into a default workspace `ws_default`.
3. **Soft delete + revisions.** Nothing is lost: `deleted_at` on every row, plus a `revisions` table holding JSON snapshots.
4. **SQLite-first, Postgres-ready.** Plain SQL migrations, JSON columns, FTS5 for search, with a fallback to `LIKE` when FTS5 is absent.
5. **Same API for UI, CLI, MCP and agents.** No UI-only endpoints except secrets (app keys stay UI-only, as today).

## 2. Content types

| Type | Purpose | Table | Notes |
|---|---|---|---|
| **Post** | A piece of content + per-channel targets (existing) | `posts` + `post_targets` | Adds `campaign_id`, `status_summary`, `workspace_id`, `deleted_at` |
| **Asset** | Media file (existing `media`) | `media` (renamed in the API to "assets") | Adds `folder_id`, `title`, `tags`, `source`, `license`, `usage_count` (view), `deleted_at` |
| **Campaign** | Groups posts, ideas and assets over a date range with a colour | `campaigns` | Shows on the calendar, filters and reports |
| **Idea** | Kanban card (existing `ideas`) | `ideas` | Adds `campaign_id`, `assignee_id`, `due_at`, `custom` |
| **Template** | Caption/post template with variables | `templates` | Moves out of the `lib:templates` KV blob |
| **Hashtag group** | Reusable hashtag set | `hashtag_groups` | Moves out of the KV blob. Adds per-tag stats |
| **Link-in-bio page** | Public page with blocks (links, latest posts) | `bio_pages`, `bio_blocks`, `links`, `link_clicks` | Served by the public edge app at `/@{slug}` |
| **Snippet** | Reusable text: CTA, signature, disclaimer, `{{shortcode}}` | `snippets` | Expanded in the composer and at publish time |
| *(also)* Tag, Saved reply, Saved view, Contact | Supporting resources | see §3 | Tags and saved replies move out of KV |

## 3. Data model (SQLite)

### 3.1 Migration system (replaces add-missing-columns)

- New table `schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)`.
- Migration files live at `huhqueue/migrations/NNNN_name.sql` (plus an optional `.py` for data moves). They run in order at startup inside a transaction, after `PRAGMA foreign_keys=OFF` when rebuilding tables.
- `0001_baseline.sql` is the current schema exactly as `create_all` makes it. Existing databases are marked applied if the `channels` table exists.
- Keep `_add_missing_columns` for one release as a safety net, then remove it.
- Test: a fresh DB and a copy of a `f5a144f`-era DB both migrate to head, and `PRAGMA integrity_check` returns `ok`.

### 3.2 New and changed tables

```sql
-- 0002_workspaces_users.sql
CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,                 -- ws_...
  name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
  brand JSON NOT NULL DEFAULT '{}',    -- logo_media_id, colors, voice, cta_handle, cta_text, timezone
  settings JSON NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',
  password_hash TEXT, totp_secret_enc TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0,
  avatar_media_id TEXT, created_at TEXT NOT NULL, last_login_at TEXT, disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE memberships (
  workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('owner','admin','editor','contributor','viewer')),
  channel_ids JSON,                    -- NULL = all channels; else allow-list
  PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE invites (id TEXT PRIMARY KEY, workspace_id TEXT, email TEXT, role TEXT, token_hash TEXT UNIQUE,
  expires_at TEXT, accepted_at TEXT, created_by TEXT);
CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT, expires_at TEXT, ip TEXT, ua TEXT);
-- data move: create ws_default + user "owner" from the existing app password; all rows get workspace_id='ws_default'
ALTER TABLE channels ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'ws_default';
ALTER TABLE api_tokens ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'ws_default';
ALTER TABLE api_tokens ADD COLUMN user_id TEXT;          -- token acts as this user (role caps scopes)
ALTER TABLE api_tokens ADD COLUMN expires_at TEXT;
ALTER TABLE api_tokens ADD COLUMN allowed_origins JSON;  -- CORS for headless
```

```sql
-- 0003_content.sql
CREATE TABLE campaigns (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL, color TEXT DEFAULT '#C9ACFA',
  starts_on TEXT, ends_on TEXT, goal TEXT DEFAULT '', utm JSON DEFAULT '{}', status TEXT DEFAULT 'active',
  custom JSON DEFAULT '{}', created_at TEXT, updated_at TEXT, deleted_at TEXT);
ALTER TABLE posts ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'ws_default';
ALTER TABLE posts ADD COLUMN campaign_id TEXT REFERENCES campaigns(id);
ALTER TABLE posts ADD COLUMN custom JSON NOT NULL DEFAULT '{}';
ALTER TABLE posts ADD COLUMN created_by TEXT;
ALTER TABLE posts ADD COLUMN deleted_at TEXT;
ALTER TABLE ideas ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'ws_default';
ALTER TABLE ideas ADD COLUMN campaign_id TEXT;  ALTER TABLE ideas ADD COLUMN assignee_id TEXT;
ALTER TABLE ideas ADD COLUMN due_at TEXT;       ALTER TABLE ideas ADD COLUMN custom JSON NOT NULL DEFAULT '{}';
CREATE TABLE templates (id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, body TEXT, variables JSON DEFAULT '[]',
  channel_options JSON DEFAULT '{}', media_ids JSON DEFAULT '[]', tags JSON DEFAULT '[]', created_at TEXT, updated_at TEXT, deleted_at TEXT);
CREATE TABLE hashtag_groups (id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, tags JSON NOT NULL DEFAULT '[]',
  networks JSON DEFAULT '[]', created_at TEXT, updated_at TEXT, deleted_at TEXT);
CREATE TABLE snippets (id TEXT PRIMARY KEY, workspace_id TEXT, key TEXT, body TEXT, kind TEXT DEFAULT 'text',  -- text|cta|signature
  UNIQUE(workspace_id, key));
CREATE TABLE tags (id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, color TEXT, UNIQUE(workspace_id, name));
CREATE TABLE saved_replies (id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, body TEXT, shortcut TEXT, uses INTEGER DEFAULT 0);
-- data move: copy settings rows lib:templates / lib:hashtag_groups / lib:tags / lib:saved_replies into the tables
```

```sql
-- 0004_assets.sql  (media library)
CREATE TABLE folders (id TEXT PRIMARY KEY, workspace_id TEXT, parent_id TEXT REFERENCES folders(id), name TEXT,
  path TEXT, created_at TEXT, UNIQUE(workspace_id, path));
ALTER TABLE media ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'ws_default';
ALTER TABLE media ADD COLUMN folder_id TEXT REFERENCES folders(id);
ALTER TABLE media ADD COLUMN title TEXT DEFAULT '';
ALTER TABLE media ADD COLUMN tags JSON NOT NULL DEFAULT '[]';
ALTER TABLE media ADD COLUMN source TEXT DEFAULT 'upload';    -- upload|url|zip|canva|unsplash|drive|ai
ALTER TABLE media ADD COLUMN source_url TEXT DEFAULT '';
ALTER TABLE media ADD COLUMN license TEXT DEFAULT '';
ALTER TABLE media ADD COLUMN phash TEXT DEFAULT '';           -- perceptual hash for near-dup images
ALTER TABLE media ADD COLUMN deleted_at TEXT;
CREATE INDEX ix_media_ws_folder ON media(workspace_id, folder_id);
-- usage tracker is a VIEW, never a counter that drifts:
CREATE VIEW media_usage AS
  SELECT m.id AS media_id, COUNT(DISTINCT t.id) AS uses, MAX(t.published_at) AS last_used_at,
         SUM(t.status='sent') AS published_uses
  FROM media m JOIN posts p ON EXISTS (SELECT 1 FROM json_each(p.media_ids) j WHERE j.value = m.id)
  JOIN post_targets t ON t.post_id = p.id GROUP BY m.id;
```

```sql
-- 0005_collab.sql  (revisions, comments, mentions, notifications)
CREATE TABLE revisions (id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT, resource_id TEXT, version INTEGER,
  snapshot JSON NOT NULL, diff JSON, actor_id TEXT, actor_kind TEXT,   -- user|token|system
  reason TEXT DEFAULT '', created_at TEXT, UNIQUE(resource, resource_id, version));
CREATE TABLE notes (id TEXT PRIMARY KEY, workspace_id TEXT, resource TEXT, resource_id TEXT, parent_id TEXT,
  author_id TEXT, body TEXT, mentions JSON DEFAULT '[]', resolved_at TEXT, created_at TEXT, edited_at TEXT, deleted_at TEXT);
CREATE INDEX ix_notes_res ON notes(resource, resource_id);
ALTER TABLE events ADD COLUMN user_id TEXT;      -- per-user notification inbox
ALTER TABLE post_targets ADD COLUMN approval_state TEXT DEFAULT 'none';  -- none|requested|approved|changes_requested
ALTER TABLE post_targets ADD COLUMN approver_ids JSON DEFAULT '[]';
-- data move: posts.note (current single note) -> one notes row per post
```

```sql
-- 0006_bio_links.sql
CREATE TABLE bio_pages (id TEXT PRIMARY KEY, workspace_id TEXT, slug TEXT UNIQUE, title TEXT, theme JSON, seo JSON,
  published INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE bio_blocks (id TEXT PRIMARY KEY, page_id TEXT REFERENCES bio_pages(id) ON DELETE CASCADE, position INTEGER,
  kind TEXT,          -- link|header|latest_posts|post_grid|email_capture|social_icons|embed
  data JSON);
CREATE TABLE links (id TEXT PRIMARY KEY, workspace_id TEXT, code TEXT UNIQUE, target_url TEXT, utm JSON DEFAULT '{}',
  post_id TEXT, campaign_id TEXT, created_at TEXT);
CREATE TABLE link_clicks (id INTEGER PRIMARY KEY AUTOINCREMENT, link_id TEXT, at TEXT, referrer TEXT, country TEXT, ua_hash TEXT);
```

```sql
-- 0007_contacts.sql  (see §4.9)
CREATE TABLE contacts (id TEXT PRIMARY KEY, workspace_id TEXT, provider TEXT, external_id TEXT, username TEXT,
  display_name TEXT, avatar_url TEXT, followers INTEGER, profile JSON DEFAULT '{}',
  first_seen_at TEXT, last_seen_at TEXT, comments_count INTEGER DEFAULT 0, replies_count INTEGER DEFAULT 0,
  sentiment REAL, tags JSON DEFAULT '[]', custom JSON DEFAULT '{}', assignee_id TEXT, status TEXT DEFAULT 'active',
  UNIQUE(workspace_id, provider, username));
ALTER TABLE comments ADD COLUMN contact_id TEXT REFERENCES contacts(id);
ALTER TABLE comments ADD COLUMN assignee_id TEXT;
ALTER TABLE comments ADD COLUMN sentiment REAL;
ALTER TABLE comments ADD COLUMN kind TEXT DEFAULT 'comment';   -- comment|mention|dm
CREATE TABLE segments (id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, rule JSON);   -- {"all":[{"field":"comments_count","op":">=","value":3}]}
-- backfill: one contact per (channel provider, comments.author); link comments.contact_id
```

```sql
-- 0008_views_fields_search.sql
CREATE TABLE saved_views (id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT, page TEXT,  -- queue|calendar|sent|community|insights|<resource>
  name TEXT, filters JSON, sort JSON, columns JSON, shared INTEGER DEFAULT 0, position INTEGER);
CREATE TABLE custom_fields (id TEXT PRIMARY KEY, workspace_id TEXT, resource TEXT, key TEXT, label TEXT,
  type TEXT CHECK (type IN ('text','textarea','number','date','select','multiselect','bool','url','user','relation')),
  options JSON DEFAULT '[]', required INTEGER DEFAULT 0, position INTEGER, UNIQUE(workspace_id, resource, key));
CREATE VIRTUAL TABLE search_fts USING fts5(resource, resource_id UNINDEXED, workspace_id UNINDEXED, title, body);
-- triggers keep search_fts in sync for posts, ideas, templates, snippets, media(title, alt_text, filename), comments, contacts
```

Indexes worth adding:

- `post_targets(status, scheduled_at)` (exists in part)
- `post_targets(channel_id, status)`
- `posts(campaign_id)`
- `comments(contact_id, at)`
- `notes(resource, resource_id)`
- `revisions(resource, resource_id, version)`

## 4. The resource kernel

### 4.1 Declaration (Python)

```python
# huhqueue/resources/posts.py
resource(
  name="post", table=Post, label="Posts", search=["caption"],
  fields=[Text("caption"), Tags("tags"), Relation("campaign", "campaign"), MediaList("media_ids"),
          Select("status", source="target"), DateTime("scheduled_at", source="target"), Custom()],
  filters=[Channel(multi=True), Status(multi=True), Tag(multi=True), PostType(["image","video","reel","story","carousel","text"]),
           Campaign(), DateRange("scheduled_at"), Author(), Search()],
  sorts=["scheduled_at", "published_at", "updated_at", "insights.views", "insights.engagement_rate"],
  actions=["post.duplicate", "post.publish_now", "post.move_to_drafts", "post.requeue", "post.add_tag",
           "post.set_campaign", "post.delete", "post.request_approval", "post.approve", "post.reject"],
  policy={"viewer": "read", "contributor": "create_draft", "editor": "write", "admin": "all"},
)
```

Every **action** is a registry entry (AI_NATIVE_PLAN.md §2). Bulk-edit means "run this action on N ids", with one revision per row.

### 4.2 Generic endpoints (generated for every resource `{r}`)

| Method | Path | What |
|---|---|---|
| GET | `/api/r/{r}` | List with `?filter[channel]=a,b&filter[tag]=x&filter[status]=…&q=…&sort=-scheduled_at&page[cursor]=…&page[size]=50&fields=…`. Filtered in SQL, not Python (fixes the post-`LIMIT` filtering bug in `post_list`) |
| GET | `/api/r/{r}/{id}` | Detail incl. `custom`, `revision`, `notes_count` |
| POST | `/api/r/{r}` | Create (validates custom fields) |
| PATCH | `/api/r/{r}/{id}` | Partial update with `If-Match: <version>` for optimistic locking. Writes a revision |
| DELETE | `/api/r/{r}/{id}` | Soft delete. `?hard=1` for admins |
| POST | `/api/r/{r}/{id}/restore` | Undo a soft delete |
| POST | `/api/r/{r}/bulk` | `{action, ids \| filter, params}` → `{ok, count, failed:[{id, error}]}`. Supports `dry_run` |
| GET | `/api/r/{r}/{id}/revisions` | List. `GET …/revisions/{v}`. `POST …/revisions/{v}/restore` |
| GET/POST | `/api/r/{r}/{id}/notes` | Threaded internal comments with `@mentions` |
| GET | `/api/r/{r}/export?format=csv\|json\|ndjson` | Honours the same filters |
| POST | `/api/r/{r}/import` | CSV/JSON with a column map and `dry_run` preview. Returns per-row errors |
| GET | `/api/r/{r}/schema` | Fields, filters, sorts, actions. The UI and agents render from this |
| GET/POST/PATCH/DELETE | `/api/views?page=…` | Saved views |
| GET/POST/PATCH/DELETE | `/api/custom-fields?resource=…` | Custom field definitions |
| GET | `/api/search?q=…&types=post,asset,idea` | FTS5 across resources (powers ⌘K "search everything") |

The existing hand-written routes (`/api/posts`, `/api/targets/*`, `/api/media*`, `/api/ideas*`, `/api/library/*`) stay as **thin aliases** for one major version, so the CLI, the MCP server and Ram's pipelines don't break.

### 4.3 Type-specific endpoints

| Area | Endpoints |
|---|---|
| Assets | `POST /api/assets` (multipart, zip), `POST /api/assets/from-url`, `POST /api/assets/import/{unsplash\|drive\|canva}`, `GET /api/assets/{id}/usage`, `GET /api/assets/duplicates` (exact sha256 + near phash), `POST /api/assets/{id}/move {folder_id}`, `GET/POST/PATCH/DELETE /api/folders` |
| Campaigns | `GET /api/campaigns/{id}/report?start&end` (posts, KPIs, link clicks) |
| Templates | `POST /api/templates/{id}/render {variables}` → caption + options |
| Snippets | `POST /api/snippets/expand {text}` |
| Hashtag groups | `GET /api/hashtag-groups/{id}/stats` (avg reach/ER of posts that used them) |
| Link-in-bio | `GET/PUT /api/bio/{id}` (blocks), `POST /api/bio/{id}/publish`. Public: `GET /@{slug}` on the edge app |
| Links | `POST /api/links` → `{code, short_url}`. Public: `GET /l/{code}` (302 + click row). `GET /api/links/{id}/clicks` |
| Workspaces / users | `GET/POST /api/workspaces`, `POST /api/workspaces/{id}/switch`, `GET/POST/PATCH/DELETE /api/members`, `POST /api/invites`, `POST /api/invites/accept`, `POST /api/me/totp/setup`, `POST /api/me/totp/verify` |
| Contacts | see §4.9 |

### 4.4 Roles and permissions

| Capability | Owner | Admin | Editor | Contributor | Viewer |
|---|---|---|---|---|---|
| View content, calendar, insights | ✅ | ✅ | ✅ | ✅ (own channels) | ✅ |
| Create drafts, ideas, assets, notes | ✅ | ✅ | ✅ | ✅ | notes only |
| Schedule / publish / edit others' posts | ✅ | ✅ | ✅ | ❌ (must request approval) | ❌ |
| Approve / reject | ✅ | ✅ | ✅ if listed as approver | ❌ | ❌ |
| Reply / resolve comments, manage contacts | ✅ | ✅ | ✅ | ✅ if allowed per channel | ❌ |
| Connect channels, app keys, tokens, webhooks, members | ✅ | ✅ (no billing/delete ws) | ❌ | ❌ | ❌ |
| Delete workspace, transfer ownership | ✅ | ❌ | ❌ | ❌ | ❌ |

- `memberships.channel_ids` narrows any role to a list of channels (Buffer Team's "custom access").
- Agent tokens get `min(token scopes, owning user's role)`.

### 4.5 Approval workflow (human and agent)

States: `draft → requested → approved → scheduled → sent`, with `changes_requested → draft`.

- Contributors and `require_approval` tokens can only *request*.
- A request notifies the approvers (in-app bell, plus email or webhook).
- Approve keeps the requested time. If that time has passed, it falls back to the next slot (the existing `posts.approve` logic).
- Reject requires a note.
- Every transition writes a revision and an audit row.

### 4.6 Revisions

- Snapshot the whole resource JSON on each PATCH, with a JSON diff stored for display.
- Compact to at most 50 revisions per row, but keep every revision that was published.
- The UI has a timeline, side-by-side diff, and "Restore this version" (which writes a new revision).

### 4.7 Comments and @mentions

- `notes` rows can sit on any resource: posts, ideas, assets, campaigns, contacts.
- Typing `@` autocompletes members. A mention creates an `events` row with `user_id` + `notify=1`, plus email if configured.
- A note thread can be resolved.
- Notes never leave the server. Never publish them.

### 4.8 Media library details

- **Folders:** nested, using a materialized `path`. Drag assets between folders.
- **Tags:** free tags + AI auto-tags (optional, costs LLM/vision calls).
- **Search:** filename, title, alt text and tags through FTS5. Filters: kind, orientation (w/h), duration, size, source, used/unused, date.
- **Usage tracker:** the `media_usage` view, showing "used in 3 posts, last 12 Oct". Warn before deleting used assets. Highlight "never used".
- **Dedup:** exact SHA-256 already exists (`media.sha256`). Add perceptual hash (pHash via Pillow) for near-duplicates. On upload, offer "use existing".
- **Derivatives:** IG JPEG copy and end-card renders stay as today. Add cropped variants (1:1, 4:5, 9:16) as child assets.

### 4.9 Contacts (commenter CRM)

Data: `contacts`, `segments`, and `comments.contact_id / assignee_id / sentiment / kind` (migration 0007).

- **Build:** `community.sync_channel` upserts a contact per `(provider, author)` on every synced comment. It updates `last_seen_at` and the counts, and fetches the avatar or followers where the API allows it (IG Business Discovery only covers business/creator accounts).
- **Profile card:** avatar, handle, display name, first/last seen, total comments, your replies, average reply time, sentiment trend, tags, custom fields, assignee, notes (§4.7).
- **History:** every comment, mention or DM from this contact across all channels and posts, in date order, with your replies inline. You can reply from the timeline.
- **Segments:** rule JSON over contact fields, e.g. "≥3 comments in 30d" = superfans, "negative sentiment last 7d", "asked a question". Segments become inbox filters, and later export or webhook targets.
- **Assignment:** assign a contact or comment to a member. "Assigned to me" inbox view. Notification on assign.
- **Saved replies:** existing library, now a table with `{{contact.username}}`/`{{first_name}}` variables and usage counts.
- **Endpoints:**
  - `GET /api/r/contact` (filters: segment, tag, assignee, channel, sentiment, last_seen range, q)
  - `GET /api/contacts/{id}/timeline`
  - `PATCH /api/contacts/{id}` (tags, custom, assignee, status=blocked|vip)
  - `POST /api/contacts/merge {ids}`
  - `GET/POST/PATCH/DELETE /api/segments`
  - `POST /api/comments/{id}/assign`
  - `POST /api/comments/bulk {action: resolve|assign|tag}`
- **Privacy:** contacts are deleted when their channel is disconnected or Meta sends data-deletion (extend `services/meta_compliance.py`). Contact export is owner-only and off by default.

### 4.10 Custom fields

- Defined per workspace and resource (`custom_fields`). Values are stored in each row's `custom` JSON and validated by the kernel.
- They show up as list columns, filters (select/bool/date ranges) and in CSV import/export.
- Example: Post → "Source URL" (url), "Topic" (select: launch/YC/fact), "Hook score" (number).

### 4.11 Import / export

- Per-resource CSV and JSON with a column-mapping UI and a dry-run preview (row-level errors).
- **Posts CSV** columns: `caption, channels, when|scheduled_at, timezone, media_urls, first_comment, tags, campaign, ig_type, collaborators, …custom`. This covers Publer/SocialBee-style bulk scheduling (FEATURE_MATRIX #8).
- **Full workspace backup:** `GET /api/workspaces/{id}/export.zip` contains the JSON of every table plus the media files. `POST /api/workspaces/import` restores it.
  - Secrets (`access_token_enc`, app keys) are excluded unless `include_secrets=1` and the caller is the owner.

## 5. UI screens

| Screen | Route | Contents |
|---|---|---|
| Resource list (generic) | `#/r/{resource}` | Filter bar (from schema) with **multi-select chips**, saved-view tabs, column picker, sort, search, density. Checkbox select-all-matching. Bulk action bar. Pagination by cursor |
| Resource detail drawer | `#/r/{resource}/{id}` | Form from schema + custom fields. Tabs: Details · Notes · Revisions · Activity · Usage (assets) |
| Media library | `#/library` | Folder tree, grid/list toggle, upload dropzone (files/zip/URL/Unsplash/Drive), filters, "Unused" and "Duplicates" smart folders, asset drawer (preview, alt text, tags, usage, versions/derivatives) |
| Campaigns | `#/campaigns`, `#/campaigns/{id}` | List with colour and date range. Detail: timeline of posts, KPIs, link clicks, notes |
| Templates · Snippets · Hashtag groups · Saved replies · Tags | `#/r/template` etc. | Generic lists with editors. Templates have a variable preview |
| Link-in-bio builder | `#/bio/{id}` | Block editor with live mobile preview, theme, SEO, publish, clicks chart |
| Contacts | `#/contacts`, `#/contacts/{id}` | Contact list with segments sidebar. Profile + timeline + reply box + notes + assignee |
| Community (upgraded) | `#/community` | Tabs Comments · Mentions · DMs. Filters: channel(s), status, assignee, segment, sentiment, post, date, sort. Bulk resolve/assign. Contact side panel |
| Approvals | `#/queue?tab=approvals` | Requested-by, approver, note thread, approve / request changes |
| Workspace switcher + brand settings | header + `#/settings?section=brand` | Logo, colours, voice, CTA, timezone, members, roles, invites |
| Saved views | everywhere | "Save view" button and view chips on Queue, Calendar, Sent, Community, Insights and every resource list |

All lists use one `<ResourceTable>` component driven by `/api/r/{r}/schema`. This is the Nova/Filament idea: write it once and get the screens for free.

## 6. Order of work

This maps to ROADMAP.md: M1 migration runner → M3 filters/saved views → M4 media library → M7 kernel + campaigns + templates/snippets + custom fields + FTS → M8 contacts → M9 users/roles/2FA/approvals/notes/revisions → M10 link-in-bio + links → M11 CSV import → M14 workspaces/brands + backup.
