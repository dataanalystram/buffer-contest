# Onboarding after Connect Buffer (UX v3)

What you post → your channels → your brand → your feed. Skippable at every step (Esc or "Skip setup"), re-openable from
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
"Recommended for you" on the Inbox and the last onboarding step show `recommended` first (cover rendered in the brand), then `feed`.

Skipping is remembered in the browser (`hq_onb.skipped`): the server only knows done or not done, so a skipped setup stays
closed on this browser and Settings → Setup opens it again.

If the routes fail (an old self-hosted build without them) the UI keeps the whole state in `localStorage` (`hq_onb`) and fills "Recommended for you"
from the newest followed-feed stories, labelled "From your feeds". Logo upload uses `POST /api/media` + `POST /api/brand-studio`.
