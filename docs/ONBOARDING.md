# Onboarding after Connect Buffer (UX v3)

What you post → your channels → your brand → your feed. Skippable at every step (Esc or "Skip setup"), re-openable from
Settings → Setup. Never opens over the sample's guided tour. Frontend: `studio/static/onboarding.js`.

## Backend contract (owned by the agent pipeline; the UI degrades gracefully until it ships)

`GET /api/onboarding` → 200
```json
{"done": false, "skipped": false, "step": 0, "content_types": ["ai_news", "launches"], "channels": ["ch_1"], "website": "huhbyte.com"}
```
`PUT /api/onboarding` with any subset of those fields → 200 with the full object. Session-authenticated (CSRF on PUT),
per user, no agent tokens. `content_types` ids: `ai_news, launches, tutorials, recaps, opinion, behind, company, events,
offers, clips`. `channels` are Buffer channel ids; they become the default selection in Make a post, Quick text posts and
One take (One take keeps only channels that take video).

`GET /api/recommendations` → 200
```json
{"items": [{"id": "r1", "title": "…", "why": "one line", "source": "openai.com", "url": "https://…", "image": "https://…",
            "format": "carousel", "run_id": null}]}
```
`run_id` set means a rendered post is ready: the card shows Review (opens that agent run) instead of Make post.

Until these routes exist the UI keeps onboarding state in `localStorage` (`hq_onb`) and fills "Recommended for you" from
the newest followed-feed stories, labelled "From your feeds". Brand import uses the existing
`POST /api/brand/from-website` (context.dev on the backend) and logo upload uses `POST /api/media` + `POST /api/brand-studio`.
