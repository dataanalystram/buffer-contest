# Media pipeline (`studio/media`)

The media agent turns "post about this in all my channels <link>, make it a short punchy explainer" into real,
branded video for every channel: it finds the **real launch video** for the link, edits it in a style, and falls back
to generated images and original music when there is no usable media.

```
link / story ─▶ find (cascade) ─▶ fetch (worker) ─▶ transcribe (worker) ─▶ render style × ratios (worker) ─▶ package
                    │                                                        ▲
                    └── no video ─▶ generate images (worker) ────────────────┘   music: synthesised, always original
```

| Module | Job |
|---|---|
| `find.py` | `find_launch_video(query)`: the source cascade below, ranked candidates with credit lines |
| `download.py` | safe fetch: direct files via `netguard.safe_stream`, platform posts via yt-dlp with one extractor |
| `transcribe.py` | faster-whisper word timestamps (audio decoded by our FFmpeg, handed over as numpy) |
| `captions.py` | ASS caption styles (opus / news / minimal / hype), measured so lines never clip or split words |
| `textfit.py` | Pillow text measuring + auto-fit used by every card and caption; `inside` / `overlaps` checks |
| `compose.py` | `render_style(...)`: layout, cards, segments, concat, ducked music mix; returns every layout box |
| `styles.py` | the four style presets, aliases (`news` = `huhbyte_news`), intent → style, channel → ratio |
| `music.py` | original royalty-free music in numpy: `cinematic` (owner's score) and `pulse` (120 BPM) |
| `imagegen.py` | no-media fallback: Pollinations (key) → Hugging Face FLUX.1-schnell (token) → offline brand card |
| `tools.py` | the agent tool interface (`SPECS`, `call`) used by agent-core's brain, REST and MCP |
| `ff.py` | FFmpeg/ffprobe wrapper (system ffmpeg, else the imageio-ffmpeg binary), silence detection |

Install for the worker: `pip install -r requirements-media.txt` (dev: `sh scripts/media_dev_setup.sh`). The web app
imports `studio.media` with only httpx and Pillow; FFmpeg, yt-dlp, numpy and faster-whisper load inside the functions
that need them.

## 1. Source cascade

`find_launch_video` tries, in order, and ranks what it finds by how the video was found (`find.CONF`):

1. **The link is a video post** (X, YouTube, Vimeo, Loom, TikTok, Instagram, Wistia): that post. Title and author from
   the platform's public oEmbed; duration/size from yt-dlp metadata only (no download). Confidence 0.97.
2. **Launch YC page**: the launch's JSON (same URL, `Accept: application/json`): video links in the body (0.93), the
   share image, then the company's own site with the company name as the expected uploader.
3. **Hacker News item** → HN Algolia API item → its URL (one hop, then 1/2/4). **GitHub repo** → README video links
   (REST API) and video assets on the latest release.
4. **Any page** (company blog, press kit, Product Hunt, LinkedIn post): `og:video(:secure_url)`, `twitter:player`,
   JSON-LD `VideoObject`, `<video>/<source>`, embedded YouTube/Vimeo/Loom/Wistia iframes, links to single posts.
5. **Optional search** when nothing was found: YouTube Data API `search.list` with the operator's key, or (opt-in,
   `HQ_MEDIA_YTSEARCH=1`) yt-dlp `ytsearch`, which scrapes YouTube and is a ToS risk, so it is off by default.
6. **Images** (`og:image`, YC share image) so the caller can make a slideshow, or `media_generate_images`.

A candidate is `official` when its uploader or host matches the company (then no rights question is needed). Every
page fetch goes through `netguard.safe_get` (no auto-redirects, every hop re-checked against private / loopback /
link-local / 100.64/10 / metadata ranges, byte cap; SECURITY_RULES 1). yt-dlp only ever sees an allow-listed
platform host with **only that platform's extractor** enabled, never the generic extractor, so a user URL can't make
it fetch an arbitrary page. Downloads are size- and height-capped and then vetted by `mediasafe.check_source`.

### Licence / ToS per source

| Source | Access | Key | Limits | Terms we follow |
|---|---|---|---|---|
| Company's own file (og:video, `<video>`, press kit, GitHub release asset) | direct HTTPS via netguard | none | byte cap 300 MB | published by the company for viewing; still credit it; `official` when the host matches |
| X post | oEmbed `publish.x.com/oembed` (text, author) | none | public | X's Terms forbid scraping without consent: we read the post via oEmbed only; **media needs the person's own X API key** (agent-core `read_url`), or the person uploads the file |
| YouTube | oEmbed (metadata); Data API `search.list` (optional) | operator's API key for search | Data API default 10,000 units/day, `search.list` = 100 units | YouTube's Terms allow downloading only where the service offers it or with permission: prefer the company's own MP4; download only with `rights_confirmed` |
| Vimeo / Loom / Wistia | oEmbed + yt-dlp (single extractor) | none | public | download only when the owner enabled it or the person confirms rights |
| TikTok / Instagram / LinkedIn | oEmbed where public; yt-dlp single extractor | none | public | same rights gate; these often refuse datacenter IPs |
| Launch YC | the launch page's JSON | none | public, unofficial | read-only metadata; video links lead to 1/4 |
| Hacker News | HN Algolia API `hn.algolia.com/api/v1/items/<id>` | none | public | read-only |
| GitHub | REST API (README, releases) | optional token | 60 req/h unauthenticated | read-only; repo licence does not cover the video, so credit + rights gate apply |
| Generated images | Pollinations (`sk_` key) / HF Inference FLUX.1-schnell (Apache-2.0 weights) / Pillow brand card | the person's or operator's key | HF free accounts get no monthly credits | labelled "AI-generated image" in the credit |
| Music | synthesised in numpy (`music.py`) | none | none | original, royalty-free; no third-party tracks bundled |

## 2. Tool choices

| Need | Choice | Why | Licence |
|---|---|---|---|
| Video edit | **FFmpeg** (libx264, libass) as a subprocess | one dependency for cut / scale / overlay / captions / mix / loudnorm; segment renders + `concat` keep memory flat | LGPL/GPL program, not linked |
| Platform fetch | **yt-dlp** with `allowed_extractors` | covers X, YouTube, Vimeo, Loom, TikTok, IG, Wistia; one extractor per host closes the SSRF door | Unlicense |
| Transcript | **faster-whisper** (CTranslate2 int8 CPU) + Silero VAD | word timestamps on CPU; `tiny` by default (`HQ_WHISPER_MODEL` to change it; see the memory section on `base`) | MIT (model weights MIT) |
| Captions | **ASS via libass**, measured with Pillow on the same font files | no clipping by construction; WrapStyle 2 so libass never re-wraps | own code |
| Text cards | **Pillow** + `textfit` | auto-fit inside the platform safe area; boxes returned for tests | HPND |
| Music | **numpy** synthesis | no licence questions, ever | BSD-3 |
| FFmpeg fallback | **imageio-ffmpeg** wheel | static ffmpeg when the box has none | BSD-2 (binary GPLv3, run as a program) |

Rejected: MoviePy (holds frames in Python memory, too heavy for small workers), PyAV decoding of untrusted containers
(we decode with our hardened FFmpeg instead; PyAV 19 also broke faster-whisper 1.2.1's decoder on 1 Nov 2026),
library music (each track needs its own licence record).

## 3. Styles

Every style renders **9:16 (1080×1920), 4:5 (1080×1350) and 1:1 (1080×1080)**, puts the hook on screen from the first
second, keeps every text box inside the safe area (9:16: 230 px top, 420 px bottom, 150 px right rail, from the
owner's `hb.py`), credits third-party video on screen, and ends on the 3 s Follow end card. Real audio is always kept;
silent demos get the score; music is ducked ~20 dB under speech and the mix is normalised to −14 LUFS.

| id (alias) | Look | Clip | Captions | Music | Best for |
|---|---|---|---|---|---|
| `opus` | full-bleed reframe, hook title on top | auto: best 20–45 s moment from the transcript (One take's scorer), ends on a sentence | 1–3 huge words, current word pops in the accent | pulse | talking-head launches, founder demos, short punchy explainers |
| `huhbyte_news` (`news`) | cover card (0.9 s) → logo + hook over the **full** video in a panel → facts card (3.5 s) | whole video (≤180 s) | sentence chunk under the panel, word highlight | cinematic | launch announcements, silent demos, AI news |
| `minimal` | blurred-video backdrop, framed panel, small left title, key-points card | whole video | boxed sentence captions | cinematic | tutorials, B2B, LinkedIn |
| `hype` | white flash in, full-bleed, giant one-word captions punching in with a tilt | auto: 12–30 s | one word at a time, alternating colour | pulse | TikTok, big funding / launch news |

Captions switch off automatically in the panel styles when the source already has burned-in subtitles
(`detect_burned_subs`, tuned on real launch videos), so they never stack. On the Aarush Yadav / Subvocal launch video
(77 s) the picker chooses **28.2–67.2 s** for `opus` (the "radar signals through your face" sentence run) and
28.2–55.2 s for `hype`.

`styles.pick(words)` maps the person's request to a style ("short punchy" → opus, "hype" → hype, "tutorial" /
"linkedin" → minimal, otherwise news); `styles.CHANNEL_RATIO` maps channels to ratios (Reels / TikTok / Shorts 9:16;
Instagram feed / Facebook / LinkedIn / Threads 4:5; X / Bluesky / Mastodon 1:1).

## 4. Agent tool interface (`studio.media.tools`)

`tools.SPECS` is a list of JSON tool definitions (`name`, `description`, `input_schema`, `worker`) that agent-core
hands to its model as-is; `tools.call(name, args)` dispatches. Light tools run anywhere; `worker: true` tools need
FFmpeg and are queued as a render job on the video worker.

| Tool | Worker | Input (required **bold**) | Returns |
|---|---|---|---|
| `media_plan` | no | **prompt**, channels | style, ratios per channel, target seconds, links, and (with a link) `media`, `needs_rights_confirmation`, `next` |
| `media_find` | no | **query**, max_results | `best_video`, `best_downloadable`, `candidates`, `images`, `note` (e.g. "the launch video is on YouTube but this server can't fetch it") |
| `media_styles` | no | – | the four styles with what each looks like and is best for |
| `media_fetch` | yes | **candidate**, run_dir | `{path, ...}` of the vetted download |
| `media_transcribe` | yes | **path**, names, model | `words_path`, word count, language, text preview |
| `media_render` | yes | **style** (`opus`/`news`/`minimal`/`hype`), **hook**, source_path or images, ratios, facts, credit, words_path, names, clip, music, brand, official, rights_confirmed | `renders: [{path, ratio, width, height, duration, clip, captions, burned_subs, music, caption_problems}]` |
| `media_moments` | no | captions or words_path, prompt, n, duration | `moments: [{id, rank, start, end, duration, score, reason, hook}]` |
| `media_split` | yes | **source_path**, url, captions, prompt, n, ratios, credit, official, rights_confirmed, brand | transcript source, burned subs, `clips` with renders (section 8) |
| `media_recap_plan` | no | prompt, n, days | cover, `segments` (headline, url, media + credit), `needs_confirmation` + `question` (section 9) |
| `media_recap` | yes | **plan**, rights_confirmed (true / false / story numbers), brand | carousel slides (4:5) and one Reel (9:16) |
| `media_generate_images` | yes | **prompt**, headline, n, ratio, brand | `images: [{path, provider, credit}]` (never empty: the brand card is the last resort) |
| `media_music` | yes | **seconds**, mood | `{path, mood, seconds, licence}` |

**Rights gate.** `media_render` with a `source_path` that is not `official` returns
`{"needs_confirmation": true, "question": ...}` unless `rights_confirmed` is true, and refuses without a `credit`
line. The agent asks the person once ("I'll use Aarush Yadav's launch video from X with credit. OK?") and passes
`rights_confirmed: true` with the credit kept on screen and in the caption.

Agent-core integration: `find_media` calls `media_find` and stores the best fetchable video; `render_reel` with a
style queues a `media_render` job on the render worker (GitHub Actions `render.yml`), which fetches, transcribes
(styles with captions), renders with `media_render` and posts the MP4 back; `generate_images` covers stories with no
media. See `studio/agent_tools.py`.

## 5. Memory and where video runs

Measured on the Aarush launch video (1920×1080, 77 s, H.264) with `memrun.py`, which samples the summed RSS of the
whole process tree (Python + every FFmpeg child) every 0.1 s, i.e. what a container limit sees. 2 vCPU, libx264
`veryfast`, CRF 21.

| Render | Output | Peak RSS, whole tree | Largest single process | Wall |
|---|---|---|---|---|
| opus 9:16, 1080×1920 (clip 28.2–67.2 s) | 42 s | **890 MB** | 841 MB | 98 s |
| hype 9:16, 1080×1920 (clip 28.2–55.2 s, with the first-beat hook layer) | 30 s | **1,040 MB** | 985 MB | 62–98 s |
| hype 9:16, 1080×1920, before the hook layer | 30 s | 828 MB | 782 MB | 80 s |
| news 9:16, 1080×1920 (full video in a panel) | 84 s | **621 MB** | 547 MB | 206 s |
| news 4:5, 1080×1350 | 84 s | 493 MB | 426 MB | 119 s |
| news 1:1, 1080×1080 | 84 s | 418–423 MB | 352–357 MB | 111 s |
| minimal 9:16, 1080×1920 (blurred backdrop) | 84 s | **612 MB** | 566 MB | 296 s |
| minimal 4:5, 1080×1350 | 84 s | 473 MB | 430 MB | 137 s |
| minimal 1:1, 1080×1080 | 84 s | 406 MB | 364 MB | 146 s |
| **opus 9:16, 720×1280** | 42 s | **484 MB** | 441 MB | 66 s |
| **hype 9:16, 720×1280** | 30 s | **546 MB** | 499 MB | 33 s |
| **news 9:16, 720×1280** | 84 s | **354 MB** | 316 MB | 85 s |

Transcription is a separate step on the same video: faster-whisper `tiny` int8 peaked at **337 MB** (6 s, 201 words);
`base` peaked at **571 MB** (19 s) but returned only **77 words** for the same 77 s of speech, so the worker stays on
`tiny` (the `HQ_WHISPER_MODEL` default) until `base`'s dropped segments are understood (likely VAD / no-speech
thresholds; open item).

**Decision: video renders on the GitHub Actions worker (`render.yml`, 7 GB runner), never on the Render free web
instance (512 MB).** 1080×1920 measured 610–1,040 MB depending on the style (full-bleed opus/hype decode and
scale the 1080p source to a 1080×1920 canvas with libass and x264 lookahead live; panel styles are lighter), so every
1080p Reel would be OOM-killed on a 512 MB instance. 720×1280 is lighter but **not safe either**: the panel style fits
(354 MB) while full-bleed opus (484 MB) and hype (546 MB) sit at or over 512 MB before the web app's own ~150 MB is
counted. 4:5 and 1:1 at 1080 wide (406–493 MB) are also too close. So 720p is not a way to render on the free web
instance; it only helps on a 1 GB box. The web app only runs the
light tools (`media_plan`, `media_find`, `media_styles`) and queues the rest. A paid Render instance with ≥1 GB, or a
self-hosted worker (`JOB_TOKEN=... python -m studio.render_worker`), can run the same job.

## 6. Risks

- **YouTube blocks datacenter yt-dlp.** From cloud IPs YouTube answers "Sign in to confirm you're not a bot" for most
  videos; `download.py` turns that into a clear message. Prefer the company's own MP4 (og:video, press kit, GitHub
  release, YC launch assets); `media_find` ranks a fetchable official file as `best_downloadable` and notes the
  YouTube link for the caption. The person can always upload the file.
- **X Terms.** X's terms forbid scraping without consent. Post text and author come from the public oEmbed; the video
  itself should come from the person's own X API key (agent-core `read_url`) or their upload. The yt-dlp X extractor is
  only a fallback, used behind the rights gate; `HQ_MEDIA_YTDLP=0` turns off yt-dlp metadata lookups in `find`.
- **Rights and credit are required.** Any video not marked `official` needs `rights_confirmed` from the person and a
  `credit` line, which is burned on screen and goes in the caption. Commentary/news use is the person's call; the
  agent never assumes it.
- **Generated images** are disclosed ("AI-generated image") and never presented as product photos.
- **Burned-in subtitle detection** is a heuristic; a miss means double captions, a false hit means none.
  `captions_mode="on"/"off"` overrides it.
- **Small Whisper models mishear names**: pass `names` (product, company, founder) and `captions.fix_names` repairs
  them. The worker uses `tiny`; `base` dropped most words on the test video (see section 5).
- **Cost and abuse**: every worker tool runs inside agent-core's kill switch, daily caps and budgets
  (SECURITY_RULES 3–4); downloads are byte-capped and vetted before decoding (SECURITY_RULES 6).

## 7. Tests

| File | Covers |
|---|---|
| `tests/studio/test_media_find.py` | cascade on recorded pages; opt-in live run (`HQ_MEDIA_LIVE=1`) on 5 real launch URLs |
| `tests/studio/test_media_captions.py` | caption styles, no split words, fit checker |
| `tests/studio/test_media_textfit.py` | auto-fit, non-monotonic wraps, truncation flag |
| `tests/studio/test_media_render.py` | every style and ratio end to end on synthetic clips: length, audio, safe area, no overlap, end card (needs FFmpeg) |
| `tests/studio/test_media_tools.py` | tool specs, dispatch, style aliases, rights + credit gate, ratios, image and music fallbacks |
| `tests/studio/test_media_subtitles.py` | SRT/VTT parsing, rolling-caption stitching, track choice, the reuse cascade |
| `tests/studio/test_media_longform.py` | split requests, moment picking, hook titles, reframing, a real split end to end |
| `tests/studio/test_media_recap.py` | recap prompts, story clustering and ranking, rights question, layout interface, carousel + Reel render |
| `tests/studio/test_agent_recap.py` | recap routing in Make a post, inline rights answers, `media_recap` worker job, carousel + Reel packages, credits |
| `tests/studio/test_media_lowerthird.py` | lower-third detection, band position per ratio/reframe, captions above it (1:1, 9:16) |

Run one file at a time: `python -m pytest -q tests/studio/test_media_render.py`.

## 8. Long-video splitting and transcript reuse

### Splitting (`studio/media/longform.py`, tools `media_moments` + `media_split`)

"Split this into 5 shorts", "find the best moments", "3 clips of 30-45 s each": `longform.parse_request` reads the
count (digits or words, "a few" = 3) and the length range (20-60 s by default, clamped to 10 clips). With no count,
`default_count` scales with the video's length.

1. **Transcript** from `subtitles.resolve_transcript` (below): Whisper only runs when no captions exist.
2. **Sentences** are rebuilt from the words (pauses ≥ 0.6 s, sentence punctuation, ≤ 34 words).
3. **Moments** (`pick_moments`): runs of whole sentences inside the length range, scored 0-100 by One take's
   deterministic scorer (strong opening line, question or number up front, clean ending, steady pace, little filler,
   scene and silence boundaries) so both features agree on what a good moment is. Non-overlapping, best first. An
   optional model (`chat_json`) can rank the candidates; every span it returns is validated against the transcript.
   Each moment carries a plain `reason` and a **hook title**: the strongest line actually said near its start, cut to
   ≤ 9 words with throat-clearing dropped (never invented).
4. **Reframing** (`reframe_plan`): a MediaPipe face track on the worker drives `take.crop_plan` per clip and per ratio
   (dead zone, smoothing, hard cut on scene changes). No face model on the box keeps a centre crop rather than guessing;
   no face on screen (slides, screen recordings) shows the whole frame over a blurred copy of itself.
5. **Render** (`render_moment`): opus style per ratio: the hook as an opener (the brand's card, popping in at frame 0
   and fading out by `compose.HOOK_OPENER_SECS` = 3 s, with the credit chip), word captions, the Follow end card (with the
   credit line), music under silent stretches. Layout & captions > Hook "Keep on screen" (`hook_mode: "keep"`) keeps
   both for the whole clip. With burned-in subtitles the bottom 16% is cropped before reframing and our
   captions are skipped, so captions never stack. Each render gets a contact sheet for a quick visual check.

**One take pause & filler trim + loudness** (`studio/media/cleancut.py`, on by default for One take, `trim_pauses: false`
to keep every pause; off for the Opus-style render and the sample). Before a clip renders, the worker plans cuts from the
transcript's own word timestamps: a gap between words longer than 0.6 s shrinks to 80 ms of padding either side (only
where FFmpeg `silencedetect` agrees there is silence), and filler words go (um / uh / erm / hmm; "like" only when it
stands between pauses or commas, never "like a ..."). A cut never starts or ends inside a kept word. A plan that would
remove half the span or more is dropped (music, silent screen work), and any failure renders the span untrimmed (fail
open). The kept stretches are joined with FFmpeg `trim`/`atrim` + `concat` (10 ms fades at each join), and the captions,
face samples and scene cuts are remapped onto the trimmed timeline; punch-in emphasis (a word after a pause) is found on the untrimmed words and remapped, so a shortened pause still counts. Audio is then normalised two-pass: `loudnorm`
measures the span first, and the render applies the linear filter for -14 LUFS integrated / -1 dBTP (one pass when the
measurement fails). Each clip reports `trimmed_s` (shown as "trimmed X s" on the clip) and `meta.trim`. FFmpeg only: no
new dependency, no model, no paid API.

**One take on-screen text (Tesseract OCR)** (`studio/media/ocr.py`, worker only). The `tesseract` program
(Apache-2.0, **5.5.4 or newer required**; an older or missing binary turns OCR off) runs as a subprocess with fixed
arguments, a timeout per frame, a minimal environment and no shell; no pytesseract. Frames are sampled with FFmpeg at
most one per 2 s, at most 60 per take and at most 30 s of OCR per take (one budget shared by every clip of a render
job), scaled to 960 px wide, and read as TSV word boxes (confidence ≥ 60). Uses:
- *Caption placement*: when on-screen text sits in a segment's default caption band, the caption line moves to the
  nearest clear band inside the platform-safe area (track and fit layouts, where the source's vertical position is
  known; split and screen keep their default). Fail open: any problem keeps the default band.
- *Watermark warning*: a platform watermark (TikTok, CapCut, Likee, Kwai, Snapchat, …) or an @handle other than the
  brand's own adds a `media.watermark` **warning** to the clip's preflight before it's placed. Never an error, never a block.
- *Alt text and Library search*: each clip gets `screen_text` and an `alt_text` ("Video clip: “hook”. On-screen text:
  …") in `GET /api/takes/{id}`, and the Library's `q` search matches a clip's on-screen text.
Each clip's `meta.ocr` records the version, frames read, time spent and why OCR was skipped. The worker builds the
pinned release itself (`scripts/worker_tesseract.sh`, see docs/INSTABOX_WORKER.md); GitHub Actions renders run
without OCR (Debian's apt package is 5.5.0).

**One take per-scene layouts** (`studio/media/reframe/`, One take style). YuNet faces (`cv2.FaceDetectorYN`, the MIT
model vendored and sha256-checked at load, OpenCV < 5, no contrib) on each clip span pick a layout per scene: `track`
(9:16 crop following the face), `fit` (whole frame over a blur or the brand colour), `split` (two people stacked, only
with `HQ_LAYOUT_SPLIT=1`) or `screen` (screen + webcam face, PiP or stacked). A layout changes only at a cut or a
sentence boundary, holds at least 2 s, follows a 1-second majority vote and changes at most 3 times in a clip of up to
60 s; the person's or their agent's choice (`set_clip_layout`, owner-scoped, 404 for someone else's take) always wins.
The plan is written as a layout sidecar that the ASS karaoke captions (4 presets, OFL fonts, ≤ 3 words or 18 characters
a line) use to find a free band. Speed target ≤ 1.2× realtime on 2 vCPU (`scripts/bench_layouts.py`, run in CI).

**Free tiers only.** Transcripts: Groq `whisper-large-v3-turbo` (free plan; 20 requests/min, 7,200 audio-s/hour,
28,800/day enforced in code with a 10% margin), then local faster-whisper `base.en`. Smart layout (beta, off unless the
take's owner turns it on; `HQ_LAYOUT_VISION=off|shadow|on`, shipped as `shadow`): ≤ 12 keyframes at ≤ 1024 px, re-encoded
without metadata, to NVIDIA NIM `nvidia/nemotron-3.5-super-vl-120b-a12b`, then OpenRouter `:free` models only, then the
rules; 20 s timeout, any error falls back. Each worker run is a fresh machine, so every free request is reserved first on
the app's shared ledger (`studio/free_quota.py`, `POST /api/worker/jobs/{id}/quota` with the job's token); a refusal
or any error means the free call is skipped, never a paid one.

**The keys stay on the app (5 Nov 2026).** Production runs jobs on the InstaBox box, whose environment carries only the
job id, the app URL and the one-job token, and the repo has no Actions secrets, so no worker holds a Groq, NVIDIA or
OpenRouter key. The worker (`free_quota.PROXY`, set by `render_worker`) hands the free calls to the app with its job
token, the same pattern as the Jev second opinion: `POST /api/worker/jobs/{id}/asr` (one 16 kHz mono FLAC chunk ≤ 25 MB,
≤ 610 s; the app reserves the larger of the FLAC header's duration and `x-audio-seconds` on the ledger, then calls Groq
once; a 429 pauses Groq for every job until its retry-after) and `POST /api/worker/jobs/{id}/vision` (≤ 12 base64
JPEGs, each ≤ 400 KB and ≤ 1024 px; the app re-checks the owner's Smart layout opt-in before decoding, re-encodes every
frame without metadata, then runs the picker reserving each request). Both answer `{"ok": false, "why": ...}` on a
refusal, so the worker transcribes locally / keeps the rules. Server side: `studio/free_proxy.py`. The box runs
`HQ_LAYOUT_VISION` from the app's env (default `shadow`, like `take.yml`).

`split_video` writes `manifest.json` (transcript source, burned subs, reframe detector, every clip's score, reason, hook
and renders). Third-party video needs `rights_confirmed` and a credit line, exactly like `media_render`.

**Routing.** In Make a post (and `POST /api/agent/runs`), a split request ("split this into 5 shorts", "find the best
moments") on a video link or an uploaded video becomes a One take run in the opus style (`agent_brain._split_request`
→ `takes.start`), so the person picks from scored clips. Someone else's video gets the rights question inline under
the composer first (a 409 starting `Rights:`), then the same request goes again with `rights_confirmed`. Any other
prompt with a video stays on the normal Make a post path. Over MCP/REST the agent can call `media_moments` first to
let the person pick, then `media_split`.

### Transcript reuse (`studio/media/subtitles.py`)

Captions the person or the platform already has are faster, free and spell names right, so the order is:

| # | Source | How |
|---|---|---|
| 1 | Uploaded `.srt` / `.vtt` next to the video | `parse_captions` (size-capped at 8 MB before parsing) |
| 2 | The platform's own track (YouTube, Vimeo, X, ...) | yt-dlp metadata only, that platform's extractor only, no video download; manual captions before automatic ones |
| 3 | A caption stream in the file (MP4 `mov_text`, MKV SRT/ASS) | our FFmpeg |
| 4 | Whisper | `studio.media.transcribe`, the only branch that costs CPU minutes |

All four return the same `[{"w", "s", "e"}]` word list. Cue-level captions get each cue's words spread over the cue by
length; YouTube's rolling captions (each cue repeats the previous line) are stitched by overlap so no word appears
twice; `[Music]`/`(applause)` cues are dropped. The result says where it came from (`source`, `detail`) and the UI
can say which captions it used.

### YouTube fetch findings (Oct-Nov 2026)

A temporary probe workflow ran the splitter on real inputs on a GitHub runner (the render worker's environment) and
was removed before merge. Findings:

- **yt-dlp is bot-blocked from datacenter IPs and from GitHub-hosted runners** ("Sign in to confirm you're not a bot")
  for every yt-dlp player client, for both the video and the caption track.
- **Piped and Invidious** public instances are blocked or rate-limited for YouTube media too, so they are not a route.
- What does work from a server: official MP4s on company pages (og:video), X post video, Vimeo/Loom public files,
  and anything the person uploads.

So for a YouTube link the app **asks for an upload** (the video, plus its `.srt`/`.vtt` if they have one) instead of
pretending to fetch it, and `media_find` says why in its `note`. We do not use cookies, residential proxies or
account sign-ins to get around YouTube's bot check.

### Captions vs lower-thirds

A source with a lower-third graphic (name strap, ticker, product bar) would have our word captions printed over it.
`compose.detect_lower_third` samples the clip and looks for rows in the lower frame dense with text edges, at the same
height in most samples and far denser than the top of the picture (so busy footage doesn't count). `band_in_output`
maps the band into the output frame for the crop in use (cover crop, face-track, padded fit, burned-subtitle crop),
and the opus/hype caption line is lifted to end above it, in 9:16, 4:5 and 1:1. Burned-in subtitles keep their own
handling (cropped off in full-bleed styles, our captions skipped in panel styles). `lower_third_mode="off"` disables it.

## 9. Weekly recap (`studio/media/recap.py`, tools `media_recap_plan` + `media_recap`)

"Make a 7 days what happened in AI recap with videos", "weekly recap", "AI round-up of the week": `recap.wants_recap`
reads the window (default 7 days, max 14) and an optional story count (3-7, default 5).

1. **Gather** (`gather_week`, light): AI headlines from the free news sources (Bing News, Google News, Hacker News,
   followed feeds), clustered against each group's first headline measured on the longer title (no chaining, so two
   different Anthropic stories don't merge), ranked by how many outlets carried it, Hacker News points and freshness,
   at most two stories per company, publisher links rather than Google News redirects.
2. **Plan** (`plan`): each story's official video or image through `media_find`; X posts through the free X reader
   (oEmbed → react-tweet syndication → FxTwitter). Media from the company's own site or CDN counts as official. Anything
   else is listed in `needs_confirmation` with **one** question for the person; stories they decline get a brand
   plate naming who reported it.
3. **Render** (`compose`, worker job `media_recap`): the HuhByte weekly-recap format: cover "DO YOU REALIZE WHAT JUST
   HAPPENED IN AI IN THE LAST 7 DAYS?", one branded segment per story with its credit on screen, the Follow card only
   once at the very end (story slides never get their own end card). Output: a **4:5 carousel** of video slides
   (each ≤ 60 s) plus **one 9:16 Reel** (~7 s per story). Fetched media is validated (an error page is not an image)
   and every download goes through netguard with a byte cap.

**In Make a post and the agent.** A recap prompt with no link or upload ("make a 7 days what happened in AI recap with
videos", "weekly recap", "AI round-up of the week") starts an agent run that calls `recap_plan` (→ `media_recap_plan`).
If some media isn't the company's own, the one rights question is posted in the chat thread with one-tap answers ("Use
them with credit" / "Title cards instead"); a typed reply also works ("only 1 and 3"). Then `render_recap` queues a
`media_recap` job (local FFmpeg off the hosted app, else the GitHub render worker `render.yml`, which posts each file
once with its single-use job token; the app checks each is the PNG/MP4 its id says). Instagram and Threads get the
4:5 carousel with the Reel beside it as its own post; every other channel posts the 9:16 Reel. Captions end with the
credit lines (dropped only where a network's limit can't fit them; they stay on screen). `rights_confirmed` can be
passed up front over REST/MCP to skip the question. Story headlines pass the headline guard (`studio/headline.py`)
at render time.

The look sits behind `RecapLayout` (`cover`, `story_overlay`, `plate`, `follow`). `register_layout("huhbyte_weekly_recap",
...)` swaps in the hq-themes template when it lands; until then the minimal layout renders the same structure, with
the story headline and source pill filling the bottom of the 4:5 slide.

## 10. Face-safe overlays (`studio/media/facesafe.py`, `studio/face_qa.py`)

Owner bug, 6 Nov 2026: on a One take Opus-style clip the hook box covered the speaker's eyes and the captions sat on his
chin (fixed layout positions; the faces only steered the crop). Now no overlay is placed on a face:

- **Opus style** (`longform.render_moment`): YuNet samples the clip span (2 fps) plus the take's Jev-verified boxes,
  mapped through the reframe crop into output pixels. The keep-out of the first 3 s (`zones_for(opener_s=...)`) guides
  the opener's hook box and credit chip (the clip-wide one when the hook is kept on screen; the credit never lands on the
  hook box, and an opener's credit with no free spot shows on the end card only), a
  per-segment keep-out (every scene cut, at most 4 s) guides the captions: under the chin, else above the head, else the
  safest band (flagged). The hook shrinks, shortens, and is dropped when no spot clear of the face exists.
- **Main One take path** (`reframe/take_clip.py`): each tracked segment's caption line is placed clear of the head
  *after* the reframe crop and the punch-in zoom (the peak, up to 1.12), and clear of on-screen text. A segment with no
  clear line keeps the nearest safe line and the clip is flagged.
- **OCR boxes** count for caption placement only when they are on screen (inside the tracked crop) and look like text
  (confidence >= 75, two or more letters or digits, a word-like size), so a shirt pattern can't push captions onto a face.
- **Render QA (worker)**: finished frames are diffed against the clean reframed frames and YuNet measures how much of
  each face our pixels cover (limit 2 %). Over the limit the Opus path re-places once and re-renders; still over, the
  clip is `needs_review` with the reason.
- **Final look (server)**: the two worst QA frames (540 px JPEGs, rebuilt from raw pixels, never stored) go to Jev
  (`jev-latest`, one Noul question), with SubQ (`subq-snap-1.1`) as the tie-break when Jev is unsettled or errors,
  within the account's daily Jev allowance (`HQ_JEV_DAILY_CAP`, reserved then refunded) and cached by frame hash. Only
  for takes that allow Jev. With no model answer the worker's YuNet measure stands, logged. A clear "covered" adds the
  flag; the models can never clear a flag the worker measured.
- A `needs_review` clip shows **Needs review** with the reason on the take page, is never spread to Buffer, and
  preflight never flips it back to ready. A re-render clears it when the new render passes.

## Credits

Layouts, safe-area numbers and the cinematic score are adapted from the owner's own HuhByte pipeline
(`dataanalystram/Huhbyte.ai`: `code/hb.py`, `code/music/make_cinematic.py`); open-source licences are listed in NOTICE.

### 10.1 One render per clip, and where a link take spends its time (6 Nov 2026, take #91)

Take #91 rendered every clip twice with the same face cover (the re-place moved nothing), sampled YuNet again for the 4:5
render, and rendered the clips one at a time: ~19 min from the pasted link to ready. Now:

* **Caption level before rendering.** `compose._face_safe_layout` places the overlays at each level in `facesafe.LEVELS`
  (`full`, then `compact`: one line at 62 % size, ~70 px at 4:5, under the chin) and predicts the QA's number from the
  face track (`facesafe.predict_cover`: the union of hook box, credit chip and caption block over each face box + 6 %).
  It renders `full` when that covers <= 2 % of a face, else the level with the lowest prediction.
* **One encode, one re-place at most.** `longform._face_safe_render` encodes once and runs the QA. On a failed QA it
  re-places once with the faces the QA saw, one level up, into a side file, and keeps whichever covers less. When the
  re-place lands on the same spots (`facesafe.placement_sig`), `render_style(skip_sig=...)` returns `skipped` without
  encoding, and the clip is `needs_review` with that reason. Jev/SubQ (and a flagged 4:5 version) can add
  `needs_review`; nothing clears it except a new render.
* **YuNet samples once per clip.** `facesafe.cached_sample` keeps the samples per (file, span, size, fps), deep-copied,
  so the 4:5 render reuses the 9:16 samples.
* **Clips in parallel.** `render_worker.render_parallel`: link takes render one clip per 2 vCPUs (`HQ_FFMPEG_THREADS=2`
  per encode) while ~1.5 GB stays free per clip, at most 3 (`HQ_RENDER_PARALLEL` overrides). One take's own render
  stays one at a time.
* **Analysis overlap.** The face track and scene cuts run beside the silences + transcript, and the fetched link is
  kept in storage while the analysis reads it.
* **Audio.** Every finished video encodes AAC at `ff.AAC_KBPS` (124k, under Instagram's 128 kbps Reels spec; 160k measured
  142-153 kbps and every Instagram chip warned; a 128k encode can measure 128.1-129). A video already over it (an import,
  an older render) is not handed back to the person or the agent: preflight's finding carries fix `audio:<i>`, and
  preview / submit / approve (`actions._fix_audio`) re-encode only its sound on the video worker (job `audio_fix`,
  `media.worker.fix_audio`: picture stream-copied, about a second), then check again. `autofix_post(fixes=['audio'])` does
  the same on request. The web dyno never runs it (`house.fix_audio` uses the worker there; `HQ_AUDIO_FIX_WAIT`, 150 s).
* **Timings.** `GET /api/takes/{id}` → `timings`: `download`, `store_source`, `silences`, `transcribe`, `scenes`,
  `faces`, `faces_jev`, `total` (analysis wall clock), `moments`, `clips.{id or id@ratio}` (`face_sampling`, `render`,
  `qa`, `renders`, `captions`, `encode`, `cards`, `audio_mux`, `cover`, `clip_total`, `source_download`,
  `upload_storage`), `preflight`, `render_job` (queued to done) and `end_to_end` (created to ready). The worker prints
  the same as `timing:` / `face-safe <ratio>:` lines in its job log.
* 4:5 is rendered only when a selected channel posts 4:5 (`takes.TAKE_RATIO`: LinkedIn, Threads).
* **Layout & captions on link takes.** Clips from a link get the dialog too: one framing for the whole clip (Auto,
  Follow face, Full frame) and the Caption look (Bold pop → opus, Clean lower third → minimal, Two-tone keyword → slam,
  Typewriter → mono). Split and Screen + face stay One take only (400 with the reason).
