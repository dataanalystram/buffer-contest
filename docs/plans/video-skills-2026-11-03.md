# Video "skills" for HuhQueue: research and plan (3 Nov 2026)

Status: **plan only, no code.** Branch `plan/video-skills`, draft PR. Nothing here touches `main`.
Deadline it is planned against: Buffer Commit contest, **13 Nov 2026 23:59 PT** (≈ 8 working days from today).

## 0. TL;DR

- Creators call them "skills", but each one is two separate things: **a look** (palette, type, motion grammar, caption
  grammar) and **a renderer** (FFmpeg, headless Chrome seeking an HTML timeline, React/Remotion, or a paid generative
  model). Most of the GitHub skills are the look, written as a long `SKILL.md` for Claude Code with Opus. They expect
  a coding agent with a big context window and 30–60 minutes per film. Our in-app brain has little context and has
  to answer in seconds, so **we copy looks and renderer contracts, never the prompt-driven workflow.**
- We already have most of the floor: 4 base styles plus 17 FFmpeg themes in `studio/media/styles.py` (16 adapted from
  lemo-opuscar, MIT, credited in NOTICE), ASS/libass word captions, One take (Whisper, scenes, faces), brand kits,
  and an InstaBox worker that renders a branded 58 s Reel in **21.1 s** (measured 2 Nov). **The missing pieces are a
  registry that makes all of this one catalogue, an HTML→MP4 overlay renderer, and a picker.**
- **Use HyperFrames' idea, not Remotion.** HyperFrames is Apache-2.0: an HTML timeline seeked frame by frame in
  headless Chrome, then piped to FFmpeg. Remotion needs a paid Company License for organisations of 4 or more people
  ($100/month minimum for automation), and that cost would land on every self-hoster at a company. **Avoid GSAP too:**
  its free licence bans tools that let users build animations without code, which is close to what a style picker
  does.
- **Before 13 Nov, ship 5 things** (section 9): the style registry and its MCP tools, Kinetic captions, Editorial
  motion (an HTML overlay renderer with 3 templates), a premium Style picker with brand-kit previews and undo, and
  Talking-head clean-up (trim pauses and false starts). Ship B-roll collage, Screen-demo zooms and generative B-roll
  after the contest.

---

## 1. The references, checked one by one

Licences and stars are from the GitHub API on 3 Nov 2026 (`repo_get`), and the licence files were read where GitHub
reports `NOASSERTION`. Maturity: **A** = large, active and production-used; **B** = useful, one maintainer, months
old; **C** = demo-grade, days or weeks old, few commits.

| What the owner heard | Exact repo | Licence | Stars / last push | What it is and how it works | Maturity | Can we reuse it? |
|---|---|---|---|---|---|---|
| **HyperFrames** (HTML→video) | [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) | **Apache-2.0** | 78k / 3 Nov 2026 | A video is an `index.html` with `data-start` / `data-duration` / `data-track-index` attributes and a paused, seekable timeline (`window.__timelines`). The producer seeks each frame in headless Chrome (Puppeteer) and encodes with FFmpeg, so the same input gives the same frames. Ships a CLI (`npx hyperframes render`), a catalogue of about 450 blocks, 8 published agent skills (`/hyperframes`, `/hyperframes-cli`, `/hyperframes-registry`, `/media-use`, ...), Docker deterministic renders and AWS Lambda. Needs Node 22+ and FFmpeg. Some parts are HeyGen services: "HyperFrames Pro" (`/reverse-any-video`) and plan usage reported by `usage --json`. | **A** | **Yes.** Apache-2.0 with a NOTICE entry. Phase 1 uses only its contract (seek-based timeline, data attributes); Phase 2 can take `@hyperframes/producer` as an npm dependency. Watch out: its examples load GSAP from a CDN, which means network access during a render and the GSAP licence below. |
| **Remotion** (React→video) | [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | Source-available Remotion License (`NOASSERTION`) | 70k / 3 Nov 2026 | React components rendered by headless Chrome and FFmpeg; mature, with Lambda support. **Free only for individuals, for-profit organisations of up to 3 people, non-profits, and evaluation.** At 4 or more people (total headcount, not users) a Company License is required: *Automators* is **$0.01 per render with a $100/month minimum**, *Creators* is $25 per seat per month ([LICENSE.md](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md), [pricing](https://www.remotion.dev/pricing), [terms v5.0](https://www.remotion.dev/docs/terms)). | **A** | **Ideas only.** HuhQueue is MIT and self-hosted, so any company of 4+ running it would owe Remotion fees. Bundling Remotion would make our "free, MIT" promise false. |
| **"one take"** continuous video | [feitangyuan/onetake](https://github.com/feitangyuan/onetake) | **PolyForm Noncommercial 1.0.0** ("free for noncommercial use only") | 3.7k / 21 Oct 2026 | A Claude skill for launch films shot as one continuous camera move: each beat grows out of the previous one. It is a single HTML composition where every value is a pure function of time, built from measured springs, carries and camera moves. A Python "continuity oracle" (`probe.py`) rejects drafts that read like slideshows. | **B** | **Ideas only; do not copy code or assets.** **Name clash:** HuhQueue's existing *One take* (long video → clips) is a different feature. Do not market this as "onetake". |
| **B-roll collage** | [pyang5166/gbro-collage-broll](https://github.com/pyang5166/gbro-collage-broll) (+ EN fork [MegaTroll222/VOX-COLLAGE-BROLL](https://github.com/MegaTroll222/VOX-COLLAGE-BROLL), [higgsfield-collage-broll](https://github.com/GiannisCon/higgsfield-collage-broll)) | **MIT** | 1.6k / 15 Jul 2026 | One 5 s voiceover line → a visual metaphor (gate 1) → an AI still (gate 2) → **paid image-to-video** (Gemini Omni / MaxFusion / Higgsfield) animates halftone paper cut-outs assembling. Vox paper-collage look. | **B** | **Look and the 3-gate approval pattern only.** The motion comes from a paid generative model and is not deterministic. We can rebuild the look deterministically: halftone cut-outs with stop-motion assembly at 12 fps in HTML or Pillow. |
| **Claude animation** / "Claude Motion" | Anthropic **Claude Motion** ([help article](https://support.claude.com/en/articles/17454997-get-started-with-claude-motion)) | Proprietary product feature | n/a | A claude.ai artifact template (beta, **Team/Enterprise only**) that writes code animating your text, charts and shapes, exported as MP4. **No API, no library.** Many "Claude Motion" sites and skill packs are unaffiliated, and several are paid (e.g. claude-motion.com sells skills at $4.90). | n/a | **Nothing to integrate.** It is useful only as proof that "code animation, not a video model" is the style creators want. Treat the third-party "Claude Motion" sites as **hype/marketing**. |
| **lemo-opuscar** (the one we use) | [lemomo-ai/lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar) | **MIT** (code); films CC BY 4.0 | 11.4k / 31 Oct 2026 | A Claude Code plugin: 66 film styles (98 in the gallery), each a style prompt plus a demo film rendered from canvas/WebGL pages frame by frame, with scores from free sample libraries and TTS. **Tuned for Opus 5.5; 30–60 min and many tokens per film.** | **B+** | **Already reused** (16 of our FFmpeg themes, credited in NOTICE). Keep taking palettes and caption grammar. Do not run its skill at runtime: it is far too token-heavy for our brain. |
| **ghost editor** | [kurbaitaev/ghost-editor](https://github.com/kurbaitaev/ghost-editor) | **MIT** | 182 / 30 Oct 2026 | Raw talking-head take → 30 s reel. Pipeline: scale to 1080×1920 and level to −16 LUFS → Whisper word timestamps → **keep the best take of each sentence and trim pauses** → OpenCV **YuNet** face tracking → the agent writes `reel.json` (takes, style, beats) → `build.mjs` turns it into a **HyperFrames** composition → lint and snapshot every beat → render → QA of loudness, true peak, SFX against voice, and caption positions. 7 named styles. Mac-first; a Gemini key is needed for B-roll checks. | **C+** (young, one maintainer, but well engineered) | **Yes for the approach, and code where it helps** (MIT, add a NOTICE entry). Its `reel.json` is the right shape for our agent: **the agent writes data, scripts do everything else.** |
| **"editorial motion graphics skills by the highfield"** = **Higgsfield** | [higgsfield.ai/skills/editorial-motion-graphics](https://higgsfield.ai/skills/editorial-motion-graphics); skills repo [higgsfield-ai/skills](https://github.com/higgsfield-ai/skills) | Skills repo **MIT**; the work runs on Higgsfield's **paid hosted MCP** (`mcp.higgsfield.ai`) | 1.9k / 27 Oct 2026 | A faceless "Vox mixed-media" explainer from a topic: script, scenes, voiceover and video, all generated on Higgsfield credits. The skill is a thin prompt layer over their paid models. | **A** as a product, but **a vendor funnel** | **Do not build on it.** A possible later BYO-key provider ("Generative B-roll") behind a confirm token. The look (paper collage, editorial type) is reproducible deterministically. |
| **Claude shorts** | [AgriciDaniel/claude-shorts](https://github.com/AgriciDaniel/claude-shorts) | **MIT** | 291 / 11 Jul 2026 | Long video → shorts. 10 steps: faster-whisper → Claude scores segments on 5 dimensions → user picks → **audio-aware boundary snapping** (never cuts mid-word) → **Remotion** captions (Hormozi / MrBeast / Clean) → FFmpeg export. | **B** | **Ideas and MIT scoring/snapping code are fine.** Its renderer is Remotion, so **do not use that part**. We already have One take's scorer and `media_moments`; borrow the 5-dimension rubric and boundary snapping if ours are weaker. |
| **OpenMontage** | [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) | **AGPL-3.0** (Studio app under separate ToS) | 75.6k / 28 Oct 2026 | "Agentic video production system": 12 pipelines, 50–100+ tools, 500–700 skill and knowledge files. Research → script → assets (multi-provider: Veo, Kling, FLUX, stock and archive corpora with CLIP search) → timeline → FFmpeg/Remotion. A commercial Windows "Studio" is in private alpha. | **B** (huge surface, star-driven) | **Ideas only.** AGPL code in our MIT repo would force AGPL on HuhQueue, and the network clause matters because we are a hosted service. Its size is the opposite of what a small-context brain can drive. Useful ideas: retrieval-first B-roll from free archives with credits, and reference-video analysis. |

Also worth knowing (not asked for, checked):
- [Barty-Bart/motion-graphics](https://github.com/Barty-Bart/motion-graphics) (MIT, 1k★): `motion-broll`, motion
  graphics timed to the transcript. It is the closest public match to our "B-roll timed to words".
- [Melvynx/video-agent-skills](https://github.com/Melvynx/video-agent-skills) (MIT): its own small Node + Chrome +
  ffmpeg + whisper.cpp short-form engine. This shows the "own a tiny HTML renderer" route is realistic.
- [charlie947/motion-graphics-skills](https://github.com/charlie947/motion-graphics-skills) (MIT, 15 skills): rules
  written from rejected drafts. Good taste checklists for golden tests.
- [ItsssssJack/claude-video-2](https://github.com/ItsssssJack/claude-video-2) (MIT, **1 commit**): a 21-look gallery
  plus 5 questions. Demo-grade.
- [zhuyansen/awesome-claude-video-skills](https://github.com/zhuyansen/awesome-claude-video-skills): an index of 180
  repos with a security grade each. Use it to find more; do not trust stars there.

**GSAP licence (affects nearly every HTML skill above).** GSAP is free under Webflow's "Standard No-Charge License",
which prohibits use "in tools that allow users to build visual animations without code" that compete with Webflow's
animation builder ([text](https://gsap.com/community/standard-license/)). A picker that styles animated videos is in
that grey zone. **Decision: no GSAP.** Use CSS animations and WAAPI driven by our own `seek(t)`, or anime.js (MIT) if
easing helpers are needed.

**Honesty notes.** Star counts in this space are inflated by trend sites, and several projects (OpenMontage,
claude-motion sites) are funnels into paid apps. Most showcase films were made by Opus in 30–60 minute agent sessions
and then hand-picked. **None of the "one prompt → viral reel" claims hold for a small-context runtime agent.** What
does transfer is **fixed templates with typed slots**, which is how ghost-editor's `reel.json` and HyperFrames'
registry blocks actually work.

---

## 2. Product decision: five styles, not 66

The user sees a **Style** row with five cards. Everything else (the 17 FFmpeg themes) stays under "More looks".

| Card | What the creator gets | Renderer | Built from | Phase |
|---|---|---|---|---|
| **Kinetic captions** | Their own video, full-bleed, with 1–3 huge word-by-word captions in brand colours, placed clear of the face | FFmpeg + libass (exists) | `opus` / `hype` presets + brand kit + face boxes from One take | **P1** |
| **Editorial motion** | Title card, stat/quote cards and lower thirds animated over the footage (or over a brand background when there is no video) | **HTML overlay → alpha frames → FFmpeg composite** (new) | HyperFrames contract; looks from lemo-opuscar/Higgsfield editorial (ideas) | **P1** |
| **Talking head (clean)** | Best take of each sentence, pauses and false starts removed, loudness levelled, captions | FFmpeg (exists) + word-timestamp cut list (new) | ghost-editor pipeline (MIT), our Whisper words | **P1** |
| **Screen-demo zooms** | Screen recordings auto-zoomed to where things change, with eased pans | FFmpeg `crop`/`scale` keyframes from frame-difference hotspots | `screencast` theme + new hotspot finder | P2 |
| **B-roll collage** | Halftone paper cut-outs of found images assembling on a flat brand colour, timed to the transcript | HTML (same renderer as Editorial) | gbro-collage look (ideas), Barty-Bart motion-broll timing (ideas) | P2 |

**One take** (long video → clips) stays an *entry point*, not a style: it produces clips, and each clip gets a style.
That keeps the two "one take" meanings apart in the UI.

**Defaults-to-best-guess rule** (deterministic, no LLM call; extends `styles.pick` / `template_pick`):
1. The person or agent named a style → use it.
2. Source has a face on screen for more than 60% of frames and speech → **Talking head (clean)**, captions on.
3. Source is a screen recording (static UI, cursor, little camera motion; the existing `screencast` detection) →
   **Screen-demo zooms** (P2; until then **Editorial motion**).
4. Speech but no face → **Kinetic captions**.
5. No video (a link, an article or images only) → **Editorial motion** on brand background.
6. Otherwise → the brand kit's saved default (the kit gains `default_video_style`).

The reason for each pick ("Picked Talking head: your face is on screen and you speak") is shown under the card and
returned to agents.

---

## 3. Architecture: a video-skill registry

### 3.1 Manifest (one file per skill, versioned, in the repo)

`studio/video_skills/<id>/skill.json`, loaded at start-up into `VIDEO_SKILLS`. The **existing STYLE_PRESETS and
THEMES are generated into the same registry** (renderer `ffmpeg`), so there is one catalogue and no second list to
drift.

```json
{
  "id": "editorial_motion",
  "version": "1.0.0",
  "label": "Editorial motion",
  "one_line": "Animated title, stat and quote cards plus lower thirds over your video, in your brand.",
  "best_for": ["news", "explainers", "link to reel"],
  "renderer": "html_overlay",           // ffmpeg | html_overlay | html_full (P2) | generative (P3, BYO key)
  "template": "templates/editorial_v1.html",
  "inputs": {                            // the ONLY thing the agent sees (compact JSON Schema)
    "hook":   {"type": "string", "maxLength": 70, "required": true},
    "cards":  {"type": "array", "maxItems": 4, "items": {"kind": "stat|quote|point", "text": "string<=90", "at": "seconds?"}},
    "lower_third": {"type": "string", "maxLength": 40}
  },
  "needs": {"video": "optional", "transcript": "optional", "faces": "optional"},
  "ratios": ["9:16", "4:5", "1:1"],
  "duration": {"min": 6, "max": 90},
  "preview": {"sample": "samples/editorial_news.json", "seconds": 3, "size": [360, 640], "fps": 15},
  "cost": {"provider_usd": 0, "est_render_s_per_output_s": 1.5, "confirm_over_s": 120},
  "determinism": "frame-exact",          // frame-exact | best-effort (generative)
  "licence_notes": "Own templates. Contract idea: HyperFrames (Apache-2.0). No GSAP."
}
```

Rules:
- **Version pinning.** A render records `skill_id@version` plus `renderer_version` (Chromium build, ffmpeg build).
  Re-rendering an old post uses the same version unless the person picks "update look". Old template versions stay
  in the repo until no stored render references them.
- **Agent-facing inputs are content-only** (text, timings, which cards). Colours, fonts and logo come from the
  active brand kit, never from the agent, so the brand cannot be broken by a prompt.
- **Validation** with the schema before queueing. Strings are clipped to `maxLength`, and `textfit` measures fit on
  the real fonts (as captions do today), so overflow is rejected before render time.

### 3.2 HTML overlay renderer (new; the core of P1)

The proven, risky part of HTML video is decoding real footage inside Chrome (seek accuracy, codecs, A/V drift).
**We avoid it. Chrome draws only the graphics layer, and FFmpeg does the footage, audio and final encode.**

```
inputs (validated JSON) + brand kit tokens
   └─► template.html (ours, vetted)  ── data injected as JSON, written with textContent only
         └─► headless Chromium (Playwright, already a dev dependency) — network blocked
               for f in 0..N: page.evaluate("__hq.seek(f/fps)") → screenshot(omit_background) → PNG (alpha)
         └─► PNG stream ─► ffmpeg (-f image2pipe) ─► qtrle/ProRes 4444 alpha .mov  (or webm vp9 alpha)
   └─► existing compose.py: footage + [overlay] + captions(libass) + music duck + loudnorm −14 LUFS → MP4
```

- **Seek contract** (same idea as HyperFrames, our own ~150 lines of JS): `window.__hq = {duration, seek(t)}`.
  Animations are CSS/WAAPI with `animation-play-state: paused` and `currentTime` set from `t`, or pure functions of
  `t`. **No `requestAnimationFrame`, no timers, no `Date`, no `Math.random`** (a seeded PRNG if needed). A lint step
  rejects templates that use them.
- **Fonts**: the same TTFs as `studio/fonts` via `@font-face` with `file:` URLs, and `document.fonts.ready` awaited
  before frame 0. Without this the first frames are not deterministic.
- **Faster capture (measure first).** Plain `page.screenshot` per frame is simple and slow. Measure it on Day 1
  against Chrome DevTools Protocol `HeadlessExperimental.beginFrame` (chrome-headless-shell), which HyperFrames-style
  engines use for exact frames. Overlays that are mostly empty can render at 30 fps and only during the overlay's
  active time windows; idle stretches become a transparent hold, not captured frames.
- **Phase 2 option:** adopt `@hyperframes/producer` (Apache-2.0) for `html_full` compositions (B-roll collage, the
  continuous-camera look) if it beats our capture loop. Add a NOTICE entry then.

### 3.3 Sandbox for HTML from untrusted text

Untrusted inputs are the hook, cards, captions, transcript text and image URLs found by `media_find`.
1. **Templates are ours and reviewed. The agent never writes HTML/CSS/JS in P1.** It only fills the schema. (Freeform
   agent-authored HTML is on the do-not-build list until after the contest.)
2. Data enters as `<script type="application/json" id="hq-data">` (JSON-encoded, with `<`→`\u003c`) and is rendered
   with `textContent` / `setAttribute` only. A lint rule rejects `innerHTML`, `insertAdjacentHTML`, `document.write`,
   `eval` and `new Function` in templates.
3. **No network in the render.** Playwright `route("**/*")` aborts everything except `file://` under the job
   directory. Chromium flags: `--host-resolver-rules="MAP * ~NOTFOUND"`, `--disable-background-networking`,
   `--no-first-run`, and no `--no-sandbox` unless the worker cannot run a sandbox (then run as an unprivileged user).
   CSP meta: `default-src 'none'; img-src file: data:; font-src file:; style-src 'unsafe-inline' file:; script-src file:`.
4. **Images** are downloaded by the existing hardened fetcher (`netguard`, `mediasafe`) *before* the render, converted
   to PNG by Pillow (which strips SVG/script risks), and referenced by local path.
5. **Limits**: per-job temp dir, wall-clock timeout (2× the estimate, at least 60 s), max frames (90 s × 30 fps),
   Chromium memory cap (`--js-flags=--max-old-space-size=512`, renderer-process-limit 1), and killing the process tree
   on timeout. Job tokens stay in stdin, as the InstaBox worker already does.

### 3.4 Jobs, progress, caching

- `render_with_style` queues a job on the existing worker path (InstaBox first, GitHub Actions fallback). The worker
  posts progress events (`stage`: fetch, transcribe, overlay, compose, encode; `pct`; `eta_s`) to
  `POST /api/worker/jobs/{id}`, the same channel that already posts failures with `log_tail`.
- **Cache key** = `sha256(skill_id@version, renderer_version, canonical_json(inputs), brand_kit_hash, source_media_sha256, ratio, duration)`.
  A hit returns the stored MP4 instantly with no worker wake. Intermediate caches: transcript by source hash (exists),
  overlay `.mov` by (template, inputs, kit, ratio), so switching ratio or music reuses the overlay.
- **Previews never queue behind long renders** (the worker holds a one-job lock). See 6.2: HTML styles preview in
  the browser itself, and FFmpeg styles use pre-rendered per-kit samples.

---

## 4. Agent context budget

The brain must never load a SKILL.md or long docs. Budget for video styles: **≤ 300 tokens resident, ≤ 500 tokens per
pick.**

1. **Resident catalogue** (in the system prompt, generated from the registry): one line per card, ≤ 90 characters,
   5 lines plus "more looks: call list_video_styles(all=true)".
   `editorial_motion: animated title/stat/quote cards + lower thirds; link or news → reel`
2. **`pick_style(id)`** returns only that skill's compact input schema (the `inputs` block above), the best-guess
   reason, the cost estimate and whether a confirm will be needed. Nothing else.
3. **The agent writes data, not media**: one JSON object per render (the ghost-editor `reel.json` idea), validated
   server-side. Errors come back as a field path plus a fix ("cards[1].text: 112 chars, max 90").
4. **Long renders are jobs.** The tool returns `{job_id, eta_s}` immediately. The brain does not poll in a loop; the
   run's step updates from worker events, and the UI shows progress.
5. **Thread compaction keeps choices.** The run's structured state (`res["brain"]["video"]`) stores
   `{style_id, version, inputs_hash, inputs, render_ids[], active_render_id}`. Compaction summarises the chat but
   always carries this block verbatim, so "make the hook bigger" after compaction edits the same inputs instead of
   re-picking.
6. **Brand corrections flow through the ledger** that already exists ("captions lower", "no yellow"). The registry
   reads ledger rules as overrides, so the agent never has to remember them.

---

## 5. MCP and REST surface

Following the codebase rule that every action is REST + MCP + ⌘K + CLI from one registry, with parity tests in CI.
**4 new tools**; `media_styles` stays as a deprecated alias of `list_video_styles` so existing clients keep working.

| Tool | Read-only | Input | Returns |
|---|---|---|---|
| `list_video_styles` | yes | `all?` (bool), `for?` (`{has_video, has_face, is_screen, channel}`) | `[{id, label, one_line, best_for, recommended, reason}]`, ≤ 1 line each |
| `preview_video_style` | yes | **`style_id`**, `brand_kit_id?`, `inputs?` | `{preview_url, poster_url, seconds: 3, cached}`; a cached sample per kit, never a worker job when cached |
| `render_with_style` | no | **`style_id`**, **`inputs`**, `source_media_id?`, `ratios?`, `post_id?`, `confirm_token?` | `{job_id, eta_s}` or `{needs_confirmation, confirm_token, preview, estimate}` |
| `get_render_job` | yes | **`job_id`** | `{status, stage, pct, eta_s, renders[], error?}` |
| (existing) revision tools | – | – | switching the active render or undoing uses the post `Revision` model |

**Confirm tokens** reuse `ledger._confirm` (single use, 15 min, payload-bound, human callers skip it). Agents need a
token when **any** of these hold: the estimate is over `confirm_over_s` (default 120 s of worker time), a
paid/BYO-key provider is involved (P3 generative), rights are not yet confirmed (the existing rights gate folds in
here), or the render would replace a post's active render that is already approved. Within the free limits a
render needs no confirm, so the common path stays one call.

---

## 6. UX: the Style picker

### 6.1 Layout (designed at 390 px first)

- In Make a post and on each One take clip: a **Style** row of 5 cards in a horizontal scroll-snap strip
  (card ≈ 148 × 263 px at 390 px width, so 2.4 cards show and the strip invites a swipe). Each card shows a 3-second
  loop in the **active brand kit**, the label, and a one-line "best for". The recommended card is first, with a
  small "Best for this video" badge and the reason on tap.
- **One tap switches.** The new render starts in the background; the current video keeps playing until the new one
  is ready, then crossfades. No modal.
- **Versions strip** under the player: thumbnails of earlier renders for this post (style + time), with **Undo**
  (back to the previous one) and pin. The previous version is never deleted by a switch. Storage cap: the last 5 per
  post; pinned and approved ones are kept.
- "More looks" opens a sheet with the 17 themes as a grid, using the same card component.
- `prefers-reduced-motion`: posters only, and tap to play.

### 6.2 Previews that are live but cheap

- **HTML styles** (Editorial motion, B-roll collage): the card is the real template in a sandboxed `<iframe
  sandbox="allow-scripts">` (no same-origin, no network, the same CSP) driven by `__hq.seek` on
  `requestAnimationFrame`, fed with brand tokens and a fixed sample. It is the same code that renders the MP4, so
  preview and output match, and it changes the moment the kit changes. **Only the card in view plays**
  (IntersectionObserver); the others show a poster, which keeps low-end phones smooth.
- **FFmpeg styles** (Kinetic captions, Talking head, Screen zooms): a 3 s, 360 × 640, 15 fps MP4 (≈ 150–300 KB)
  rendered from a fixed sample clip **per brand kit**, in one batch job when a kit is created or activated, and cached
  by `(skill@version, kit_hash)`. Until it exists the card shows a poster tinted with the kit and "preview updating".
- **Deterministic previews**: same sample, same template version and same kit give the same frames. The golden tests
  in section 8 run on these samples.

### 6.3 Copy

Plain words, no engine names: "Editorial motion: animated cards over your video", never "HTML renderer". Cost or time
shows only when it matters: "About 40 s" on long renders, and nothing for normal ones.

---

## 7. Performance and cost

Measured (InstaBox 4 vCPU / 8 GiB, 2 Nov 2026, `docs/INSTABOX_WORKER.md`): resume **1 s**; branded 58 s Reel
**21.1 s**; One take analysis of 194 s source **22.6 s**, 19.4 min source **106 s**. Box cost on paid plans ≈
**$0.034/hour**, and the free sandbox costs $0.

**Not measured yet (Day 1 spike, numbers to beat):**

| Job | Budget | Notes |
|---|---|---|
| Editorial overlay, 30 s @ 30 fps, 1080×1920, active ≈ 40% of the time | ≤ 30 s | only active windows are captured; a transparent hold elsewhere |
| Full Editorial reel (overlay + compose) for a 30 s source | ≤ 50 s | compose is roughly the current 21 s path |
| Kinetic captions, 30 s | ≤ 20 s | existing libass path |
| Per-kit preview batch (3 FFmpeg styles × 3 s @ 360p) | ≤ 15 s | runs once per kit activation |
| Chromium cold start on the worker | ≤ 2 s | keep one browser per job; install `chromium` from apt into the persistent root (like ffmpeg) |

If plain screenshots miss the budget: (1) beginFrame capture, (2) render overlays at 540 × 960 and upscale (text is
vector, so render at full size and drop only the frame rate on static holds), (3) adopt `@hyperframes/producer`.

**HTML route versus Remotion, in cost:** our route costs $0 in licences at any company size. Remotion for Automators
would cost at least $1,200/year per organisation of 4+ people, plus $0.01 per render above 10k a month, and that bill
would land on our self-hosting users. Render speed is similar, since both drive headless Chrome; Remotion's
advantages (Lambda, Player) are not needed at our scale.

---

## 8. Test plan

- **Golden frames** (`tests/video_skills/test_golden.py`, on the worker image in CI): for each skill × ratio, render
  the fixed sample and extract frames at t = 0.5, 1.5, 2.5 s and the last frame. Compare to committed PNGs with
  **SSIM ≥ 0.98** (not byte equality: Chromium and x264 builds vary across machines). Masks around the logo are
  allowed. Update goldens with `--update-goldens`, and review them in the PR.
- **Template lint**: no `innerHTML`/`eval`/timers/`Date`/`Math.random`/remote URLs/GSAP; schema maxLengths present;
  every text slot measured with `textfit` against the safe area (230 px top / 420 px bottom / 150 px right rail at
  9:16).
- **Sandbox tests**: an input of `"<img src=x onerror=fetch('http://evil')>"` renders as literal text; a template
  edited to fetch a remote URL fails the render (the network block is proven, not assumed); timeout kills the process
  tree.
- **A/V sync**: a synthetic source with a beep and a white flash at known times goes through each style. Assert
  `|audio_onset − video_flash| ≤ 1 frame (33 ms)` after compose, and that the overlay card appears at its `at` time
  within ±1 frame. Run it on cut lists too (Talking head), where drift builds up across many cuts.
- **Loudness**: −14 LUFS ±1 integrated, true peak ≤ −1 dBTP (ffmpeg `ebur128`).
- **Determinism**: render the same job twice and the frame hashes are equal on one machine; same cache key → cache
  hit with no worker call.
- **Agent budget tests**: the resident catalogue is ≤ 300 tokens; `pick_style` output is ≤ 500 tokens; after a forced
  compaction the run still has `video.style_id`, and "make the hook bigger" edits the same render inputs.
- **MCP parity**: the 4 tools exist in REST/MCP/⌘K/CLI (existing parity test); confirm-token flow tests (wrong, used
  or expired → 409).
- **UI at 390 px** (Playwright, already used for `ui_smoke.py`): the Style strip scrolls and snaps, the recommended
  card is first, only the visible card's iframe is playing, switch → progress → crossfade, Undo restores the
  previous render, reduced-motion shows posters. Screenshot tests at 390 × 844 and 1280 × 800.

---

## 9. Roadmap

Effort: **S** ≤ 1 day, **M** 2–3 days, **L** 4+ days (one developer plus agents).

### Phase 1: ship before 13 Nov 23:59 PT (≈ 8 days)

| # | Item | Why | Effort |
|---|---|---|---|
| 1 | **Style registry + `list_video_styles` / `pick_style` / `render_with_style` (+confirm) / `get_render_job` + progress events + cache keys**; existing styles and themes generated into it | One catalogue for the UI, the brain and MCP. This is the "skills, agent-first" story judges can see in MCP, and it does not add 66 tools. | **M** |
| 2 | **Kinetic captions** style: brand-kit colours and fonts on the existing `opus`/`hype` ASS captions, face-safe placement from One take face boxes, an emphasis word in the accent | The #1 thing Instagram creators ask for; nearly all of it is already built, so value is high and risk is low | **S** |
| 3 | **Editorial motion** via the **HTML overlay renderer** (Playwright seek-capture → alpha → ffmpeg composite), 3 templates (title card, stat/quote card, lower third), 9:16 first, sandboxed | The differentiator: "HTML skills" without Remotion fees or Chrome decoding footage. It also covers link-only posts (no video). | **M–L** (Day 1 spike decides capture method) |
| 4 | **Style picker UI**: 5-card strip, best-guess default and reason, in-browser iframe previews for HTML styles, per-kit sample MP4s for FFmpeg styles, one-tap switch, versions and Undo | Makes it simple: one tap, see it in your brand, never lose the last version | **M** |
| 5 | **Talking head (clean)**: cut list from Whisper words (pauses > 0.35 s, repeated sentence starts → keep the last complete take), boundary snapping, −16 LUFS prep, then Kinetic captions | ghost-editor's best idea on our existing analysis; it turns a raw selfie take into a postable reel | **M** |

Plus: golden-frame, A/V-sync, sandbox and 390 px tests for these (included in the estimates); a NOTICE entry for
ghost-editor if any of its code is adapted, and an "ideas from" note for HyperFrames, onetake, gbro-collage,
claude-shorts and OpenMontage (ideas only).

Suggested order: **D1** capture spike + registry skeleton → **D2** registry, MCP tools and cache → **D3** Kinetic
captions + overlay renderer core → **D4** Editorial templates + sandbox tests → **D5–6** picker UI + previews →
**D6–7** Talking-head clean-up → **D7** golden/A-V tests at 390 px → **D8** demo video and buffer. If behind by D5,
cut Talking head to "pause trim only" (S) rather than cutting the picker.

### Phase 2: after the contest (Nov–Dec)

| Item | Effort |
|---|---|
| **Screen-demo zooms**: frame-difference hotspots plus cursor detection → eased crop keyframes; reuse `screencast` theme | M |
| **B-roll collage** (deterministic): images from `media_find` → Pillow halftone + torn-paper masks → HTML stop-motion assembly at 12 fps on brand colour, timed to transcript beats | M |
| `html_full` renderer: evaluate `@hyperframes/producer` (Apache-2.0) against our loop; adopt if faster or more robust | M |
| Continuous-camera "One shot" look (own implementation of the *idea* of continuity between beats; no onetake code) | L |
| More templates per style (each a versioned manifest), template gallery in docs | S each |
| Style learning from edits: the ledger records which style a person switches to after the best guess, and the kit default updates (a playbook from edits, which the rival lacks) | S |

### Phase 3: later, opt-in

| Item | Effort |
|---|---|
| **Generative B-roll** providers (Higgsfield / Gemini / fal) as **BYO-key**, `determinism: best-effort`, always behind a confirm token with a cost estimate, labelled "AI-generated" | M |
| Agent-authored HTML ("Custom motion") only inside the sandbox, with the lint + golden QA loop and a human approve step | L |
| Reference-video style copy ("make it look like this reel"): analyse pacing, cut rhythm and caption grammar, then map to our parameters | L |

---

## 10. Risks and what NOT to build

**Risks**
- *Chromium capture speed on 4 vCPU.* Not measured yet. Mitigation: Day 1 spike with a fallback ladder (7); if all
  of it fails, Editorial motion ships as FFmpeg-drawn cards (`theme_cards.py`) with simpler motion, and the registry
  and picker still ship.
- *Fonts and frame-0 determinism*: fixed by preloading fonts and the golden tests.
- *Worker lock contention*: previews are not worker jobs (6.2), and batches run on kit change only.
- *Licence drift*: HyperFrames is Apache today; we depend only on its idea in P1. GSAP is avoided. Remotion, AGPL
  and PolyForm-NC code are never copied; a CI check greps for `gsap`, `remotion` and `@remotion/` in dependency
  files.
- *Rights*: the existing rights gate and credits apply unchanged to every style.
- *Scope creep against the deadline*: the five-card cap is the guard.

**Do NOT build (before 13 Nov, and some never)**
- No Remotion, no GSAP, no OpenMontage, onetake or other copyleft/noncommercial code.
- No freeform agent-written HTML/JS in renders (P3 at the earliest, sandboxed and reviewed).
- No paid generative video in the contest build; nothing that spends money without a confirm token.
- No timeline or keyframe editor in the UI. It is a big build, it is a GSAP-licence trap, and it is the opposite of
  "simple".
- No 66-style gallery and no style marketplace. Five curated cards plus "More looks".
- No per-keystroke worker previews; previews are in-browser or pre-rendered.
- Do not run SKILL.md-driven Claude Code workflows at runtime (token-heavy, slow, built for Opus): **port them into
  templates and schemas.**
- Do not decode the source footage in Chrome; FFmpeg owns footage and audio.

---

## 11. Sources

HyperFrames repo and README: https://github.com/heygen-com/hyperframes · docs: https://hyperframes.heygen.com/introduction
Remotion licence: https://github.com/remotion-dev/remotion/blob/main/LICENSE.md · pricing: https://www.remotion.dev/pricing · terms: https://www.remotion.dev/docs/terms · FAQ: https://www.remotion.dev/docs/license/faq
onetake: https://github.com/feitangyuan/onetake (LICENSE: PolyForm Noncommercial 1.0.0)
gbro-collage-broll: https://github.com/pyang5166/gbro-collage-broll · VOX-COLLAGE-BROLL: https://github.com/MegaTroll222/VOX-COLLAGE-BROLL
Claude Motion: https://support.claude.com/en/articles/17454997-get-started-with-claude-motion
lemo-opuscar: https://github.com/lemomo-ai/lemo-opuscar
ghost-editor: https://github.com/kurbaitaev/ghost-editor · how it works: https://github.com/kurbaitaev/ghost-editor/blob/main/docs/HOW-IT-WORKS.md
Higgsfield editorial motion graphics: https://higgsfield.ai/skills/editorial-motion-graphics · skills repo: https://github.com/higgsfield-ai/skills
claude-shorts: https://github.com/AgriciDaniel/claude-shorts
OpenMontage: https://github.com/calesthio/OpenMontage (AGPL-3.0)
GSAP Standard No-Charge License: https://gsap.com/community/standard-license/
Others: https://github.com/Barty-Bart/motion-graphics · https://github.com/Melvynx/video-agent-skills ·
https://github.com/charlie947/motion-graphics-skills · https://github.com/ItsssssJack/claude-video-2 ·
https://github.com/zhuyansen/awesome-claude-video-skills
In-repo: `studio/media/styles.py`, `docs/MEDIA_PIPELINE.md`, `docs/INSTABOX_WORKER.md`, `docs/THEMES.md`,
`studio/ledger.py` (`_confirm`), `studio/mcp.py`, `NOTICE`.
