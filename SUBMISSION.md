# Buffer Commit submission — HuhQueue

- **App:** https://huhqueue-studio.onrender.com
- **Code (open source, MIT):** https://github.com/dataanalystram/buffer-contest
- **Buffer app client ID:** `BFQSCDAPoD-hJ0cmZtncSW-kLqnDwPfQ511K35AcVmj`
- **Demo video (2:00):** https://youtu.be/1DwcEoPNsrI (same video in the repo: [demo/huhqueue-buffer-commit-demo-2min.mp4](https://github.com/dataanalystram/buffer-contest/blob/main/demo/huhqueue-buffer-commit-demo-2min.mp4), [transcript](demo/huhqueue-buffer-commit-demo-2min.transcript.md))
- **What was built in the window vs. before it:** [CONTEST.md](CONTEST.md)
- **What is the entry:** Studio (`studio/`). It signs users in with **Buffer OAuth only** (Authorization Code + PKCE S256, `prompt=consent`, rotating `offline_access` refresh tokens) and never asks for a Buffer API key. The core `huhqueue/` app's paste-a-token options are for connecting LinkedIn and Instagram directly, not Buffer.
- **Build-in-public posts (9 Nov, the 2-minute demo):**
  - X (@HUHBYTE): https://x.com/HUHBYTE/status/2119897935676612635
  - YouTube (@huhbyte): https://youtu.be/1DwcEoPNsrI
  - LinkedIn (Ram): https://lnkd.in/p/eFecc3xz
  - Instagram Reel (@huhbyte.ai): https://www.instagram.com/reel/DfiQgZku-Yp/
  - Facebook: https://www.facebook.com/share/v/19n81wvYeX/
- **Earlier build-in-public posts (the 1:26 demo):**
  - LinkedIn (Ram, with the 16:9 demo): https://www.linkedin.com/feed/update/urn:li:activity:7521244269584539648/
  - Instagram Reel (@huhbyte.ai): https://www.instagram.com/reel/DfC3PW3uiL9/
  - YouTube Short (@huhbyte): https://www.youtube.com/watch?v=iBFgP1GOMlU

## Write-up (≈300 words)

**Problem.** People who share news and ideas for a living — creators, founders, small-brand marketers — repeat one chore all day: read a story, then rewrite it as a LinkedIn post, a tighter X post and an Instagram caption that says "link in bio", checking each network's limit before queueing. AI agents can write those posts in seconds, but nobody wants an agent posting to their brand unreviewed.

**What HuhQueue does.** HuhQueue is your brand's editor on top of Buffer. Give it a topic, a link or your own photo or video. An agent finds the story, verifies it on the source page and other outlets, and pulls out the facts, with every step on a visible timeline. It renders the post in your brand kit (colours, font, logo, handle, end card) as a 4:5 carousel, a single card or a 9:16 Reel (Pillow for images, FFmpeg for video, on our video worker when hosted: an InstaBox machine, with GitHub Actions as the fallback), and writes one caption per channel, measured with Buffer's own counting rules. Every correction you make ("logo smaller", "no hype") becomes a rule in the **Style Ledger**, and a brand check cites the exact rule before anything goes out. You approve, and the **Planner** shows your Buffer queue as a list, week or month: filter by channel, type and status, see Buffer's posting times, and drag a post or draft to reschedule it through Buffer's `editPost`. Your own Claude or ChatGPT can drive all of it over MCP or REST with a leash: preview → single-use confirm token → submit, an Approvals inbox by default, per-token daily caps, a kill switch and an activity log.

**Audience.** Solo creators and small teams who already schedule with Buffer and are starting to use Claude, ChatGPT or Cursor for content.

**Buffer API surface.** OAuth 2.0 Authorization Code + PKCE with a private client and rotating `offline_access` refresh tokens kept server-side (encrypted in Postgres); GraphQL `account`, `channels` (incl. `postingSchedule`), `posts` (scheduled/draft/sent), `createPost` (draft, addToQueue, shareNext, shareNow, customScheduled; image/video and multi-image assets; per-network metadata and threads), `editPost` and `deletePost` (Planner reschedule, edit and delete draft), `post` by id (two-way sync: a post's text and status follow Buffer, so a caption edited or a post deleted in Buffer shows that way in HuhQueue; a change only to spacing or line breaks, or media changed in Buffer, is not pulled back), `instagramAudio` (a best-effort check that a Reel's music can play before it is sent; if Buffer can't answer, or the check is switched off, the post still goes and Buffer may refuse the track), `dailyPostingLimits`, organization limits and `createIdea`. We read the `RateLimit` headers on every response, cache channel lists, and back off on 429 + `Retry-After`. No analytics APIs.

**Next steps.** Group multi-channel sends with `createContentItem`, tags, and team approvals.

HuhQueue is an independent open-source project built on the Buffer API; it is not affiliated with Buffer.

*About the demo: the Raven YC launch in the "Bring your own AI" part is only an example link we gave the agent to show the flow. HuhQueue credits the source video on the post, and Raven is not connected with this project.*
