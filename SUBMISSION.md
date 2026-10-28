# Buffer Commit submission — HuhQueue Studio

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

**What HuhQueue does.** Paste a link and HuhQueue reads the article and drafts one caption per Buffer channel, measured with Buffer's own counting rules (UTF-16, LinkedIn URLs count 24, Instagram line breaks count 2) and trimmed to fit. You edit and send it to Buffer as a draft, to the queue, to share next or now, or at a set time. Agents get the same powers through an MCP server and REST API, with a leash: every write is preview → single-use confirm token → submit, agent posts wait in an Approvals inbox by default, and there are per-token daily caps, a one-click kill switch and an activity log.

**Audience.** Solo creators and small teams who already schedule with Buffer and are starting to use Claude, ChatGPT or Cursor for content.

**Buffer API surface.** OAuth 2.0 Authorization Code + PKCE with a private client and rotating `offline_access` refresh tokens kept server-side (encrypted in Postgres); GraphQL `account`, `channels`, `posts` (scheduled/draft/sent), `createPost` (draft, addToQueue, shareNext, shareNow, customScheduled; image/video assets; per-network metadata) and `createIdea`. We read the `RateLimit` headers on every response, cache channel lists, and back off on 429 + `Retry-After`. No analytics APIs.

**Next steps.** Group multi-channel sends with `createContentItem`, threads for X/Bluesky/Threads, tags, a browser extension for one-click "draft from this page", and team approvals.

HuhQueue is an independent open-source project built on the Buffer API; it is not affiliated with Buffer.
