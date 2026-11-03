# Buffer API audit (3 Nov 2026)

Checked every Buffer GraphQL call in this repo against Buffer's live schema (introspection on api.buffer.com with the
owner's key, 3 Nov 2026) and developers.buffer.com (Rate Limits guide, reference). Branch `fix/reel-cover-buffer-audit`.

**Where the calls are:** all Buffer traffic goes through `studio/buffer_api.py` (`BufferClient.gql`), and every post is
shaped by one function, `studio/actions.py::build_post_input` (used by the web UI, REST API, MCP and the agent). The
`huhqueue/` package (self-hosted scheduler) and the render workers (`studio/media/worker.py`, `studio/render_worker.py`)
make **no** Buffer calls. `automation/post.py` is not in this repo (it lives in the Huhbyte.ai content repo) and was not
audited here.

**Channels connected (channels query, 3 Nov 2026):** Instagram `huhbyte.ai` (business), YouTube `Huhbyte`, Facebook Page
`HuhByte.AI`, LinkedIn profile. No X/Twitter, Threads or TikTok channel is connected; their shapes were checked against
the schema only.

## Schema facts that drive the fixes

- `VideoAssetInput.thumbnailUrl`: *"Do not use: social networks do not accept custom video thumbnail images, and the API
  rejects video assets that set this field. To choose the video thumbnail, set `metadata.thumbnailOffset` (Instagram,
  TikTok, Pinterest only)."* So a Reel cover **cannot** be sent as an image; it must be a frame in the video, selected by
  `thumbnailOffset` (ms). YouTube custom thumbnails are not settable through Buffer at all.
- `InstagramPostMetadataInput`: `type` (PostType, required), `shouldShareToFeed` (required), `firstComment`, `geolocation
  {id,text}`, `link`, `isAiGenerated`, `stickerFields`. No collaborators field. User tags are `ImageMetadataInput.userTags`.
- `FacebookPostMetadataInput.type`: `PostTypeFacebook` = post | reel | story (required); `firstComment`; `linkAttachment`.
- `YoutubePostMetadataInput`: `title`, `categoryId` (*required on create*), `privacy` (public/unlisted/private),
  `madeForKids` (default false), `notifySubscribers`, `embeddable`, `license`, `isAiGenerated`.
- `LinkedInPostMetadataInput`: `firstComment`, `annotations`, `linkAttachment`. `TwitterPostMetadataInput.thread`,
  `ThreadsPostMetadataInput.thread/topic/type/location*`, `TikTokPostMetadataInput.title`.
- `ImageMetadataInput.altText` (+ `userTags`, `animatedThumbnail`).
- `CreatePostInput` has **no idempotency key** (`source` is tracking only). `editPost` (EditPostInput) can change `text`,
  `assets`, `metadata`, `dueAt`/`mode`, `saveToDraft`, and `approvalChange`; omitted fields are kept.
- Rate limits (Rate Limits guide): app client 15 min 100 + 10/user, 24 h 500 + 25/user, 30 days 15,000 + 100/user;
  API keys/MCP by plan (Free 150 / 600 / 1,000 per channel). 429 carries `Retry-After` and costs no quota.

## Audit table

| Call | Channel | What we sent (before) | Missing / wrong | Fix (this branch) |
|---|---|---|---|---|
| `createPost` assets | Instagram, TikTok, Pinterest (video) | `video.url` + `metadata.thumbnailOffset` (300 ms into a 0.6 s baked cover) | Offset correct as a mechanism; the cover it pointed at was followed by a second baked title still (see Reel opening bug) | Cover frame 0.2 s, offset 100 ms; no title card after it. Never send `thumbnailUrl` (test) |
| `createPost` assets | YouTube, LinkedIn, Facebook (video) | `video.url` only | Correct: Buffer ignores `thumbnailOffset` there and rejects `thumbnailUrl`. Our 16:9 YouTube cover (`pk.thumbnail`) cannot be sent via Buffer | Documented; YouTube thumbnail stays a manual upload |
| `createPost` metadata | Instagram (video, no `ig_type`) | `type: "post"` | A video is published as a Reel by Instagram; Buffer converted it silently (Click draft reads back `reel`) but we said `post` | Video with no `ig_type` → `type: "reel"`; agent Reel packages set `ig_type: reel` explicitly |
| `createPost` metadata | Instagram | `type`, `shouldShareToFeed: true` | No `firstComment`, no alt text, no geolocation, no user tags | `first_comment` option → `firstComment`; `alt_text` option → `ImageMetadataInput.altText` per image. Geolocation / user tags: not added (no product need yet) |
| `createPost` metadata | Instagram story | `type: story` when `ig_type=story` | OK | — |
| `createPost` metadata | Facebook (video) | `type: "post"` always | A 9:16 Reel went to the Page as a feed video | `fb_type` option (post/reel/story; reel only with a video). Agent Reel packages and One-take clips send `fb_type: reel`; a bare video with no option stays `post` (shape unknown) |
| `createPost` metadata | Facebook / LinkedIn | — | No `firstComment` | `first_comment` option |
| `createPost` metadata | YouTube | `title` (≤100), `categoryId` only when given | `categoryId` is required on create — guarded by preflight (error + autofix "22"), agent packages send "28", clips "22". `madeForKids` implicit | `madeForKids: false` always sent explicitly. Privacy left to Buffer's default (public) |
| `createPost` metadata | X / Threads / Bluesky / Mastodon | `thread` from `---` parts, first part = `text`, assets on part 1 | OK per schema; X 280 (25,000 with `x_long_posts`) enforced by preflight | — |
| `createPost` metadata | Pinterest / Threads topic / Google Business | board, title, topic, `whats_new` | OK | — |
| `createPost` mode | all | draft → `saveToDraft` (+`customScheduled`/`dueAt` when timed); queue/next/now/schedule → `addToQueue`/`shareNext`/`shareNow`/`customScheduled` | OK. `needsApproval: false`, `schedulingType: automatic` always | — |
| Caption limits | all | `drafter.LIMITS` + preflight (UTF-16, IG line breaks, LinkedIn URLs, X weighted) | OK | — |
| `createPost` retry / idempotency | all | No retry on transport errors; 401 refresh-and-retry once; 429 short `Retry-After` (≤5 s) retried once | A timeout or 5xx **after** Buffer made the post left the package unsent, so the next "Save to Buffer" made a second draft. Confirm tokens and `pk.sent` only cover replays we know about | On timeout/5xx, `create_post` reads the channel's posts once and returns the one with the same text created in the last 15 min instead of failing (test) |
| `gql` rate limits | all | `RateLimit` / `RateLimit-Policy` read on every response, matched by window `w`; 429 blocks only that account; cached reads when ≤10% left | OK, matches the guide. Docstring numbers match the app-client table | — |
| `gql` errors | all | HTTP 200 + `MutationError.message` surfaced; GraphQL `errors[0].extensions.code` surfaced | Non-JSON 5xx had no status, so callers could not tell a server failure from a refusal | 5xx keeps its status on `BufferError` |
| `editPost` (update/reschedule) | all | `text` and/or `dueAt`+`customScheduled` (+`saveToDraft` for drafts) | OK — this is the "change schedule" API for MCP (`update_post`, `reschedule_post`) | — |
| `editPost` (swap media) | drafts | `replace_draft`: `text`, `assets`, `metadata`, `saveToDraft` on a draft only | OK — this is the "swap media" API (MCP `update_package_draft`). Scheduled posts deliberately never touched | — |
| `deletePost` | drafts only | `delete_draft` refuses scheduled/sent | OK | — |
| Media URLs Buffer fetches (`/m/<token>`) | all | GET (+Range 206), HEAD 200 | Multi-range / malformed `Range` answered 416 | 416 only for a single range past the end; anything else gets the whole file (200) |

## "Change schedule" and "swap media" for MCP

Both exist in Buffer's API and in HuhQueue already:

- **Reschedule:** `editPost(input: {id, dueAt, mode: customScheduled})` (+ `saveToDraft: true` to keep a draft a draft).
  HuhQueue: `update_post` / `reschedule_post` (MCP tools, Planner drag). `review`/`draft` agent tokens may only move drafts.
- **Swap media:** `editPost(input: {id, assets: [...], metadata, saveToDraft: true})`. HuhQueue: `replace_draft`
  (`update_package_draft` over MCP) — drafts only, so a re-render can never change what publishes. Changing a scheduled
  post's media is possible in Buffer's API but intentionally not exposed.

## Not done (needs a decision)

- Instagram collaborators: not in Buffer's public schema.
- Geolocation (`geolocation {id,text}`) and Instagram user tags (`ImageMetadataInput.userTags`): fields exist; no UI for
  them yet.
- YouTube `privacy` choice (public/unlisted/private) and `notifySubscribers`: Buffer defaults (public, notify) are used.
