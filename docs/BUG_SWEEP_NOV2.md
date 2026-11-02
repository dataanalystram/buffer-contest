# Bug sweep, 2 Nov 2026 ("make sure no bugs")

Branch `fix/nov2-bugs`, PR #39. One regression test (or more) per bug in `tests/studio/test_owner_bugs_nov2.py`.
(The working plan file was lost with a sandbox reset; this is the plan rewritten from the commits and the owner's list.)

| # | Bug | Root cause | Fix | Commit |
|---|-----|------------|-----|--------|
| 1 | Link -> split of a YouTube video crashed on the worker (Actions run 41363638009) | `requirements-worker*.txt` lacked `cryptography`, which `studio.store` imports | Added it; `scripts/worker_imports.py` walks every module the worker can reach and a CI job imports them all in a worker-only venv | c43f7b0 |
| 2 | URL import failed in production | Render can't reach its own public hostname (no hairpin), so every `/m/`, `/sample/`, `/static/` import failed; the error hid why | Own-host URLs are read locally (path-confined); failures say why (404/403/size/private/unreachable) and log host + status | eb29cca |
| 3 | `edit_video` double-captioned a clip that already had captions burned in | No check for existing captions | Detects centred, changing text in the lower half; adds none by default with a note; `captions="replace"` crops the old ones off; `"on"` forces | d0e9504 |
| - | `GET /api/recommendations` 500 when one source's answer couldn't be parsed; Activity tab showed the previous tab's rows (flaky ui-smoke) | Unguarded parse; out-of-order fetches | Per-source error, never a 500; the Activity tab ignores a slower answer for a tab already left | d1e00c6 |
| 4 | The ernd carousel cover used the 4x-upscaled logo and wrote "Ernd" | Cover always cropped the hero to full bleed; `research_link` put the page image first even when it was a logo; Title Case capitalised an all-lowercase brand | Cover never upscales past 1.5x (a small picture is a rounded inset on the brand ground, `fit` reports it); `research_link` ranks og/share images and photos ahead of logo/icon URLs (returned separately as `logos`); Title Case keeps brand casing (known names, a name the copy starts a sentence with, `render_carousel.keep_case`) | 7d90ae3 |
| 5 | The live "no video -> no reel" check failed on the demo | It required a YouTube channel, which the demo doesn't have | `tests/live/ep_live_check.py` (LIVE=1 only) checks video-only channels only when one exists; `scripts/mcp_live.py` adds a $0 MCP smoke with a ledger check | bb1ee07 |

## Verification

- Tests run one file at a time locally (the media/theme files that OOM the sandbox run in CI).
- `pip-audit` on every requirements file.
- Live after deploy: link -> split of a YouTube link (splits, or says "Upload the file instead"), URL import of a `/sample/`
  file, `scripts/mcp_live.py` at $0, and a Playwright sweep at 1440 and 390, light and dark (console errors, 4xx/5xx,
  broken images, horizontal overflow).

Results are in the PR description and the final report.
