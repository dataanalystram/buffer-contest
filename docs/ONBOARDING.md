# Onboarding after Connect Buffer (UX v3)

Welcome → what you post → your channels → your brand → your AI → your feed. The welcome shows on a first visit only
("Change" on News and Settings → Setup open straight at the interests). Skippable at every step (Esc or "Skip setup"), re-openable from
Settings → Setup. Never opens over the sample's guided tour. Frontend: `studio/static/onboarding.js`.

## Backend contract (`studio/onboarding.py`, shipped with the agent pipeline in #27; the UI reads these routes)

`GET /api/onboarding` → `{"profile": {"content_types": [...], "channel_ids": [...], "website": "", "tone": "plain", "x_watchlist": [], "done": false},
"options": {"content_types": [{"id": "ai_news", "label": "AI news"}, ...], "tones": [...], "channels": [...]}, "brand": {...}, "x_key": false}`.
The UI shows `options.content_types` as the chips (its built-in copy of the same ten ids is the fallback).

`PUT /api/onboarding` with any of `content_types`, `channel_ids`, `website` (builds the brand kit through context.dev, else the
website reader; the response carries `brand`), `tone`, `x_watchlist`, `done` → the GET shape. `channel_ids` become the default
selection in Make a post, Quick text posts and One take (One take keeps the channels that take video).

`GET /api/recommendations` → `{"feed": [{"id", "topic", "story": {"headline", "line"}, "items": [{"url", "title", "source"}], "why"}],
"recommended": [{"cluster_id", "headline", "source_url", "cover", "captions": [...], "make_post": {...}}], "sample": bool, "cached": bool}`.
"Recommended for you" on the Inbox shows `recommended` first (cover rendered in the brand), then `feed`. The last setup step
shows the top four For you stories (`GET /api/radar`, instant, no scan or render) and the feeds followed for the person
(`GET /api/onboarding` → `following: [{"url", "label"}]`).

## News is never empty (`studio/starter_feeds.py`, 9 Nov 2026)

- **Skipped setup / no interests:** the first open of For you (`GET /api/radar` by a person, never an agent) follows five
  current tech-news feeds (Hacker News, The Verge, TechCrunch, MIT Technology Review AI, Ars Technica).
- **Interests picked:** `PUT /api/onboarding` with `content_types` (or `done: true`) follows the feeds mapped to those
  interests (`BY_TYPE`, each interest's best feed first, then Hacker News, up to 12) and unfollows starter feeds that no
  longer match. Stories from a feed picked for an interest count for it in the For you ranking ("Picked for ...").
- Feeds the person followed by hand are never removed; a starter feed they unfollow is never added back
  (`user_profiles.data.starter_feeds` / `starter_dropped`). A starter feed that's down is left out of the error lines
  while other stories show. The sample keeps `demo.SEED_FEEDS`. `HQ_STARTER_FEEDS=0` turns it all off (tests default to off).
- Feeds are read side by side (`radar.stories`, 6 at a time), so a new account's first open isn't twelve fetches in a row.

Skipping is remembered in the browser (`hq_onb.skipped`): the server only knows done or not done, so a skipped setup stays
closed on this browser and Settings → Setup opens it again.

If the routes fail (an old self-hosted build without them) the UI keeps the whole state in `localStorage` (`hq_onb`) and fills "Recommended for you"
from the newest followed-feed stories, labelled "From your feeds". Logo upload uses `POST /api/media` + `POST /api/brand-studio`.
