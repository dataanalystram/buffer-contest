# Buffer Commit submission — HuhQueue

- **App:** https://huhqueue-studio.onrender.com
- **Code (open source, MIT):** https://github.com/dataanalystram/buffer-contest
- **Buffer app client ID:** `BFQSCDAPoD-hJ0cmZtncSW-kLqnDwPfQ511K35AcVmj`
- **Demo video (1:26):** [demo/huhqueue-buffer-commit-demo-16x9.mp4](https://github.com/dataanalystram/buffer-contest/blob/main/demo/huhqueue-buffer-commit-demo-16x9.mp4) ([direct download](https://github.com/dataanalystram/buffer-contest/raw/main/demo/huhqueue-buffer-commit-demo-16x9.mp4)); 9:16 cut on YouTube: https://www.youtube.com/watch?v=iBFgP1GOMlU
- **What was built in the window vs. before it:** [CONTEST.md](CONTEST.md)
- **Build-in-public posts:**
  - LinkedIn (Ram, with the 16:9 demo): https://www.linkedin.com/feed/update/urn:li:activity:7521244269584539648/
  - Instagram Reel (@huhbyte.ai): https://www.instagram.com/reel/DfC3PW3uiL9/
  - YouTube Short (@huhbyte): https://www.youtube.com/watch?v=iBFgP1GOMlU

## Write-up (≈300 words)

**Problem.** People who share news and ideas for a living — creators, founders, small-brand marketers — repeat one chore all day: read a story, then rewrite it as a LinkedIn post, a tighter X post and an Instagram caption that says "link in bio", checking each network's limit before queueing. AI agents can write those posts in seconds, but nobody wants an agent posting to their brand unreviewed.

**What HuhQueue does.** HuhQueue is your brand's editor on top of Buffer. Give it a topic, a link or your own photo or video. An agent finds the story, verifies it on the source page and other outlets, and pulls out the facts, with every step on a visible timeline. It renders the post in your brand kit (colours, font, logo, handle, end card) as a 4:5 carousel, a single card or a 9:16 Reel (Pillow for images, FFmpeg for video, on a free GitHub Actions worker when hosted), and writes one caption per channel, measured with Buffer's own counting rules. Every correction you make ("logo smaller", "no hype") becomes a rule in the **Style Ledger**, and a brand check cites the exact rule before anything goes out. You approve, and the **Planner** shows your Buffer queue as a list, week or month: filter by channel, type and status, see Buffer's posting times, and drag a post or draft to reschedule it through Buffer's `editPost`. Your own Claude or ChatGPT can drive all of it over MCP or REST with a leash: preview → single-use confirm token → submit, an Approvals inbox by default, per-token daily caps, a kill switch and an activity log.

**Audience.** Solo creators and small teams who already schedule with Buffer and are starting to use Claude, ChatGPT or Cursor for content.

**Buffer API surface.** OAuth 2.0 Authorization Code + PKCE with a private client and rotating `offline_access` refresh tokens kept server-side (encrypted in Postgres); GraphQL `account`, `channels` (incl. `postingSchedule`), `posts` (scheduled/draft/sent), `createPost` (draft, addToQueue, shareNext, shareNow, customScheduled; image/video and multi-image assets; per-network metadata and threads), `editPost` and `deletePost` (Planner reschedule, edit and delete draft), `dailyPostingLimits`, organization limits and `createIdea`. We read the `RateLimit` headers on every response, cache channel lists, and back off on 429 + `Retry-After`. No analytics APIs.

**Next steps.** Group multi-channel sends with `createContentItem`, tags, and team approvals.

HuhQueue is an independent open-source project built on the Buffer API; it is not affiliated with Buffer.
