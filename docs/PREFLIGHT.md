# Preflight: the rules HuhQueue checks before a post reaches Buffer

"Never Bounced" checks every post, for every channel, **before** `createPost` or `editPost`, on every surface: the web UI,
the REST API (`POST /api/preflight`, `POST /api/preflight/fix`, `GET /api/preflight/rules`) and the MCP server
(`preflight_post`, `autofix_post`, `list_rules`). The same code (`studio/preflight.py`) runs `preview_post`, again right
before `createPost` (so a post that waited in Approvals is re-checked), and before every `editPost` from the Planner.

- **Error** (red): Buffer or the network would reject it. The post is blocked; there is no override.
- **Warning** (amber): it may fail or look wrong. It is blocked until you fix it or send with `override_warnings: true`
  (the "Send anyway" box in the UI).
- **Info**: worth knowing, never blocks.

Every limit below was read on the page it links to on **30 Oct 2026** (rule pack `2026-10-30`). Nothing here is
from memory or a third-party blog. When a rule could not be verified on an official page it is listed at the end and
**not enforced**. This table is generated from the code (`python -m studio.preflight --markdown`) and a test fails if they
drift apart.

## How text is counted (Buffer's rules)

Source: [developers.buffer.com/guides/character-limits.html](https://developers.buffer.com/guides/character-limits.html)

| Network | Counted as |
|---|---|
| Facebook, Google Business, Pinterest, Threads, TikTok, YouTube | UTF-16 code units (emoji and bold/italic Unicode letters count 2) |
| LinkedIn | UTF-16, every URL counts 24 |
| Instagram | UTF-16, every line break counts 2 |
| Mastodon | UTF-16, URLs count 23, the server part of `@user@server` is not counted |
| X | X's weighted count: URLs 23, emoji 2 (twitter-text weights: Latin 1, CJK and other scripts 2) |
| Bluesky | Graphemes (an emoji is 1); a URL is its host plus up to 16 characters |

Golden examples from Buffer's page are unit tests: `𝗛𝗲𝗹𝗹𝗼` = 10 (UTF-16) / 5 (Bluesky) / 10 (X); the family emoji
👨‍👩‍👧 = 8 / 1 / 2; `cafe` + combining accent = 5 / 4 / 4.

## Buffer failures that come back as HTTP 200

Source: [developers.buffer.com/guides/error-handling.html](https://developers.buffer.com/guides/error-handling.html)

A refused `createPost`/`editPost` is a typed `MutationError` in the response body with HTTP 200
(`{"data": {"createPost": {"message": "..."}}}`). HuhQueue treats any mutation without a `post` as a failure, shows
Buffer's own message in the UI, returns it as a 422 from the REST API and as an MCP error, and writes it to the audit log.

## Rules enforced

| Network | Rule | Limit | Source | Checked |
|---|---|---|---|---|
| Facebook | Post text, UTF-16 code units | 5,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Instagram | Post text, UTF-16, each line break counts 2 | 2,196 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| X | Post text, X weighted count (URL 23, emoji 2); paid X tiers 25,000 | 280 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| LinkedIn | Post text, UTF-16, every URL counts 24 | 3,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Pinterest | Post text, UTF-16 code units | 500 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| TikTok | Post text, UTF-16; 2,200 when a video is attached | 4,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Threads | Post text, UTF-16 code units | 500 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Bluesky | Post text, graphemes; a URL is its host + up to 16 characters | 300 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| YouTube | Post text, video description, UTF-16 code units | 5,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Google Business | Post text, UTF-16 code units | 4,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Mastodon | Post text, set by the server (channel maxCharacters, capped at 20,000); URLs 23, @user@server counts @user | 500 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| TikTok | Post text when a video is attached | 2,200 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| X | Post text on X Basic/Premium/Premium+ (opt in with options.x_long_posts) | 25,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Mastodon | Buffer's cap on a server's maxCharacters | 20,000 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| YouTube | Title: required on create, up to 100 | 100 | [developers.buffer.com](https://developers.buffer.com/types/YoutubePostMetadataInput.html) [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| YouTube | categoryId: required on create, one of Buffer's listed IDs | - | [developers.buffer.com](https://developers.buffer.com/types/YoutubePostMetadataInput.html) | 30 Oct 2026 |
| Pinterest | boardServiceId: required on create | - | [developers.buffer.com](https://developers.buffer.com/types/PinterestPostMetadataInput.html) | 30 Oct 2026 |
| Pinterest | Pin title, up to 100 | 100 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Threads | Topic up to 50, no '&' or '.' | 50 | [developers.buffer.com](https://developers.buffer.com/guides/character-limits.html) | 30 Oct 2026 |
| Instagram | Hashtags in a caption | 30 | [developers.facebook.com](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) | 30 Oct 2026 |
| Instagram | @ mentions in a caption | 20 | [developers.facebook.com](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) | 30 Oct 2026 |
| X | Up to 4 photos, or 1 GIF, or 1 video | 4 | [docs.x.com](https://docs.x.com/x-api/media/quickstart/best-practices) | 30 Oct 2026 |
| Bluesky | Up to 4 images | 4 | [docs.bsky.app](https://docs.bsky.app/docs/advanced-guides/posts) | 30 Oct 2026 |
| Instagram | Carousel: up to 20 images/videos | 20 | [developers.facebook.com](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) | 30 Oct 2026 |
| Threads | Carousel: 2 to 20 items | 20 | [developers.facebook.com](https://developers.facebook.com/docs/threads/overview) | 30 Oct 2026 |
| LinkedIn | Multi-image: 2 to 20 images, images only (no video in the set) | 20 | [learn.microsoft.com](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/multiimage-post-api) | 30 Oct 2026 |
| Instagram | JPEG, up to 8 MB, aspect 4:5 to 1.91:1, width 320-1440 (scaled) | 8 MB | [developers.facebook.com](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) | 30 Oct 2026 |
| Threads | JPEG or PNG, up to 8 MB, aspect up to 10:1 | 8 MB | [developers.facebook.com](https://developers.facebook.com/docs/threads/overview) | 30 Oct 2026 |
| X | JPG, PNG, GIF or WEBP; up to 5 MB (animated GIF 15 MB) | 5 MB | [docs.x.com](https://docs.x.com/x-api/media/quickstart/best-practices) | 30 Oct 2026 |
| Bluesky | Up to 1,000,000 bytes per image (the same page's code sample uses 2,000,000, so this is a warning) | 1,000,000 bytes | [docs.bsky.app](https://docs.bsky.app/docs/advanced-guides/posts) | 30 Oct 2026 |
| LinkedIn | JPG, GIF or PNG; fewer than 36,152,320 pixels | 36,152,320 px | [learn.microsoft.com](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/multiimage-post-api) | 30 Oct 2026 |
| TikTok | JPEG or WebP, up to 20 MB, max 1080p | 20 MB | [developers.tiktok.com](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) | 30 Oct 2026 |
| Instagram | Reel: MP4/MOV, H.264/HEVC, 3 s-15 min, 23-60 fps, width up to 1920, video up to 25 Mbps, audio 128 kbps, up to 300 MB | 300 MB | [developers.facebook.com](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) | 30 Oct 2026 |
| Threads | MP4/MOV, H.264/HEVC, up to 5 min, 23-60 fps, width up to 1920, up to 100 Mbps, up to 1 GB | 1024 MB | [developers.facebook.com](https://developers.facebook.com/docs/threads/overview) | 30 Oct 2026 |
| X | 0.5 s-20 min (non-Premium), up to 60 fps, aspect 1:3 to 3:1 | - | [docs.x.com](https://docs.x.com/x-api/media/quickstart/best-practices) | 30 Oct 2026 |
| TikTok | MP4/WebM/MOV, 23-60 fps, 360-4096 px each side, up to 10 min (many accounts 3 min), up to 4 GB | 4096 MB | [developers.tiktok.com](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) | 30 Oct 2026 |
| YouTube | Square or vertical video up to 3 min is a Short; longer is a regular video | - | [support.google.com](https://support.google.com/youtube/answer/15424877) | 30 Oct 2026 |
| All | Media URL must be public, direct, HTTPS and stay live until publish (no share pages, no signed/expiring links) | - | [developers.buffer.com](https://developers.buffer.com/guides/hosting-media.html) | 30 Oct 2026 |

## Fixes ("Fix it")

| Fix | What it does |
|---|---|
| `trim` | Cuts the caption at a sentence or word boundary under the network's count (UTF-16 aware), keeping a trailing link |
| `thread` | X, Bluesky, Threads, Mastodon: splits into numbered thread posts, each under the limit |
| `drop_hashtags` | Instagram: keeps the first 30 hashtags |
| `keep_first:N` | Keeps the first N images/videos the network allows |
| `image:N` | Pillow: centre-crops to the allowed aspect (Instagram 4:5-1.91:1, Threads 10:1), caps width (1440 on Instagram/Threads, 1080p on TikTok, LinkedIn pixel cap), converts to JPEG and recompresses under the byte limit. Saved as new HuhQueue media with a stable public URL |
| `youtube_title` / `youtube_category` | Shortens the title to 100; sets category People & Blogs (22) - you can pick another ID |
| `reencode` | Flag only: the video needs re-encoding (codec, fps, bitrate, width). Run it through Edit media |

## Not verified, so not enforced

| Rule | Why |
|---|---|
| Facebook, Pinterest and Google Business image/video size and aspect limits | not found on an official developer page we could open |
| TikTok number of photos in a photo post | not stated on the Content Posting media guide |
| Bluesky video length and size | not stated on the posts guide |
| Mixing images and a video in one Bluesky or TikTok post | not stated on the pages above |
| Whether Buffer converts PNG to JPEG for Instagram | Buffer's docs don't say; we warn and offer the JPEG fix |
| Whether Buffer exposes an X account's paid tier | not on the Channel type; we default to 280 and let you opt in |
| Instagram/LinkedIn/Facebook first-comment limits (2,200 / 1,250 / 5,000) | verified on Buffer's page but HuhQueue doesn't send first comments yet |
