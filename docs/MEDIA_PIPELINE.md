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

Run one file at a time: `python -m pytest -q tests/studio/test_media_render.py`.

## Credits

Layouts, safe-area numbers and the cinematic score are adapted from the owner's own HuhByte pipeline
(`dataanalystram/Huhbyte.ai`: `code/hb.py`, `code/music/make_cinematic.py`); open-source licences are listed in NOTICE.
