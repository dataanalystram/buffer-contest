# HuhQueue information architecture (UX v3, 1 Nov 2026)

Rule: **one job, one place.** Nine screens. On a desktop the rail lists all nine (no "More"); on a phone the bottom bar
holds the four everyday screens and a Menu sheet holds the rest, so nothing is ever listed twice.

| Screen | Route | Sub-tabs | The job |
|---|---|---|---|
| Inbox | `/` (default), `?tab=inbox` | none | Your day: stats, agent posts to approve (cards with media), today's queue, what needs attention, agent activity with app logos, recommended posts |
| Make a post | `?tab=agent` | With the agent · Quick text posts (`&sub=quick`) | Every way to create one post: the agent (topic, link or your media, rendered in your brand) or fast text-only posts from a link |
| One take | `?tab=take` | none | One long video into a week of clips |
| Planner | `?tab=planner&view=…` | Week, Month, Day, List, Board, Grid | When things go out |
| News | `?tab=news` | For you · Story scan (`&sub=scan`) · Sources (`&sub=sources`) | Finding stories: one feed list for both the card feed and the multi-source scan |
| Brand | `?tab=brand` | Kit · Templates (`&sub=templates`) · Style Ledger (`&sub=ledger`) | How posts look: one kit (one source of truth), templates and slots, learned rules |
| AI apps | `?tab=apps` | none | Connecting Claude, ChatGPT, Grok, Cursor, Codex, Claude Code; tokens, last used, per-app activity, the kill switch |
| Activity | `?tab=activity` | All, Sign-ins, AI agents, System & Buffer API | The full audit log |
| Settings | `?tab=settings` | none | Setup (re-open onboarding), connected channels, AI engine (your model key), account |

## Old → new

| Before (31 Oct) | Now | Why |
|---|---|---|
| Inbox | Inbox (dashboard) | Same job, now visual: tiles, cards and lists instead of a column of text |
| Approvals (`?tab=approvals`) | Inbox → "Waiting for your approval" | Approving was in two places (Inbox items and the Approvals list); now one, with media and the agent's logo |
| Make a post | Make a post → With the agent | unchanged job |
| Write from a link (`?tab=studio`) | Make a post → Quick text posts | Same job as Make a post ("a post from a link"), different depth; one screen, two modes. Bookmarklet links still open it |
| News radar (`?tab=radar`) | News → For you | Scannable cards: favicon, headline, one line on why, time, Make post |
| Sources agent (`?tab=sources`) | News → Story scan | Same job as the radar (find stories); its RSS list and the radar's feed list are now one list (News → Sources) |
| Brand studio (`?tab=brandstudio`) | Brand → Kit and Templates | Two kit editors (4 colours, 2 fonts vs 3 colours, 1 font) wrote overlapping fields with no sync. Now one kit form saves everything (`/api/brand-studio`, which writes the base kit too) |
| Brand & ledger (`?tab=brand`) | Brand → Kit (website import, voice, end card) and Style Ledger | Same |
| Connect AI apps (`?tab=connect`) | AI apps | One screen for connecting and for who has access |
| Agents (`?tab=agents`) | AI apps (tokens list, kill switch, custom token) | "Agents" vs "Connect AI apps" vs "Agents and MCP" were one job with three names |
| AI engine (`?tab=ai`) | Settings → AI engine | Your own model key is a setting; connecting a subscription lives in AI apps |
| Account menu: device link, sign out, disconnect | Account menu (plus Settings, Connected channels) and Settings → Account | The menu is a shortcut to the same actions |
| Buffer plan banner "Upgrade your Buffer plan" | Only for real accounts at 100% or with locked channels; never in the sample | It was misleading in the demo |
| `?tab=home`, `calendar`, `queue` | Inbox, Planner, Planner (List) | kept from earlier renames |

Every old `?tab=` value still works (`TAB_ALIAS` in `studio/static/app.js`), and ⌘K finds the old names too
("Approvals", "Story scan", "Style Ledger", "AI engine", "Quick text posts").

## Names
One name per screen, the same in the rail, the heading, ⌘K, the tour and the docs: Inbox, Make a post, One take,
Planner, News, Brand, AI apps, Activity, Settings.
