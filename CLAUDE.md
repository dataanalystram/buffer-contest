# CLAUDE.md: HuhQueue Studio (repo `buffer-contest`)

HuhQueue Studio is an agent-first social media studio built on the Buffer API (Buffer Commit entry), live at
https://huhqueue-studio.onrender.com. MIT licensed. The old self-hosted scheduler (`huhqueue/`, `web/`) was removed
from this repo on 25 Nov 2026; it lives in a private repo.

## Layout
- `studio/`: the app (FastAPI, plain HTML/CSS/JS UI in `studio/static/`, no build step). Entry `studio.app:create_app`.
  Buffer = OAuth PKCE + GraphQL in `studio/buffer_api.py`. MCP at `POST /mcp`.
  Storage: `studio/store.py` (SQLite locally via `STUDIO_DB`, Postgres via `DATABASE_URL`).
- `studio/media/`: video pipeline. Video work never runs on the web dyno; it runs on the InstaBox worker or the
  GitHub Actions fallback (`.github/workflows/render.yml`, `take.yml`).
- `tests/studio/`: the test suite. `tests/live/`: opt-in live checks.
- `Dockerfile.studio` + `render.yaml`: the Render web service. `docs/`: design and operations notes.

## Run and test
```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-studio.txt "numpy>=2.0"
python -m pytest tests/studio -q                                        # SQLite
STUDIO_TEST_PG=postgresql://... python -m pytest tests/studio -q        # Postgres (the live store)
```
`test` (SQLite + Postgres jobs in `studio-ci.yml`) is the required check; `ui-smoke` runs on every PR.

## Rules
- Everything the UI can do is also a REST call and an MCP tool (parity tests enforce it). The operator admin panel
  (`/admin`, `studio/admin.py`, `docs/ADMIN.md`) is browser-session-only on purpose: agent tokens never administer accounts.
- Merging to `main` deploys Render and changes the live video worker at once. `scripts/ci_apt_install.sh` is used by the
  live take/render workflows too, so treat it as production.
- Never add to Buffer scopes in code before enabling the scope on the Buffer app client.
- Destructive UI actions use the in-app confirm dialog, never `window.confirm`/`prompt`.
- Copy third-party code only with credit in `NOTICE`.

## UI design
The UI is `studio/static/` (plain HTML/CSS/JS, no build step). Follow `DESIGN.md` for every UI change: Taste tokens,
one accent that means "needs a person", flat hairline cards, motion only in response to an action, every control with
a `data-testid`.
