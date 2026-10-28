# Buffer paid features vs HuhQueue: real gaps (27 Oct 2026)

Follow-up to [BUFFER_PRO_FEATURES.md](BUFFER_PRO_FEATURES.md) (26 Oct). Since then `main` gained teams, growth, channel
pages, AI replies, mentions and Buffer CSV import, so most of that doc's ranked list is done. This pass re-checked
Buffer's current plans and changelog against the code (`main` 0ec9a29 + PR #2 bulk) with `rg`, not memory.

Sources: https://buffer.com/pricing (plan table), https://buffer.com/changelog (Apr to Oct 2026 entries), Ram's signed-in
Team trial (Home: scorecard, up next, comments, templates).

## Already matched (no work)
Queue, calendar, drafts, ideas board, tags, templates, AI assistant, first comment, hashtag manager, custom video covers,
link shortening + UTM, Start Page (bio pages v2 on PR #2), duplicate, reminders (Notify me), comment inbox, comment score,
AI replies (unlimited), saved replies, turn comment into post, mentions (Instagram), insights with ranges and previous
period, per-channel takeaways, reports + branded PDF, notes with @mentions, approvals, per-channel permissions, 2FA,
saved views, post-type filters, drafts in the calendar, MCP + API (no request caps), bulk actions (PR #2).

## Gaps, ranked (value for everyone, Instagram first, then effort)

| # | Gap | Buffer | HuhQueue today (checked) | Effort |
|---|---|---|---|---|
| 1 | Instagram via Facebook Login: **music on Reels**, real collaborators, location | Changelog 7 Oct 2026 | Instagram Login only; plan in FBLOGIN_PLAN.md (milestone M5) | L |
| 2 | **Threaded posts** (X; later Threads/Bluesky/Mastodon) | All plans (Free 1) | `connectors/x.py` has no thread support | M |
| 3 | **Channel groups** | Essentials | no model or UI (`rg ChannelGroup` = 0) | S |
| 4 | **AI ideas, AI alt text, comment insights** (themes from comments) | Free / changelog 17 Jun | LLM provider exists (`services/assistant.py`); none of the three exist | S each |
| 5 | **Image editor**: crop, rotate, flip, zoom, alt text | Changelog 15 Sep | no editor in the composer | M |
| 6 | **Insights extras**: audience demographics, story insights saved before they expire, multi-channel takeaways | Essentials / changelog 17 Sep | `analytics.audience` is follower growth only; takeaways only per channel | S each |
| 7 | **Integrations**: outgoing webhooks for Zapier/Make/n8n/IFTTT, media pickers (Unsplash, Google Drive, Dropbox) | All plans | `Webhook` model exists with no routes; no pickers | M |
| 8 | **Browser push notifications** (web push, no app needed) | Changelog 22 Sep (mobile push) | webhook push only (ntfy/Slack) | M |
| 9 | **More networks**: Facebook Pages, Threads, Bluesky, Mastodon, Pinterest, Google Business Profile, Substack Notes | All plans | listed as planned only | M each |
| 10 | Shop grid page, browser extension, real-time notes | All plans / changelog 14 Oct | missing; notes refresh on reload | S-M each |

Not gaps worth copying: Bitly (own short links already), Buffer's API request caps and per-channel pricing (HuhQueue has none).

## Suggested order with the milestone plan
M2 agent safety UI and M3 reliable publishing first (they protect real posts), then 3 + 4 + 6 (small, high value),
then 2, then M5 (1), then 5, 7, 8, 9.
