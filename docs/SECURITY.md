# HuhQueue security

This page covers the hosted app in `studio/`, its video worker workflows (`.github/workflows/render.yml`, `take.yml`) and the 31 Oct 2026 security audit. Each of the audit's 7 confirmed findings is fixed in this repository, and `tests/studio/test_security_audit.py` replays each original proof locally to show it now fails. The tests use mock transports, a throwaway database and a self-made signing key, and nothing in them touches the network or production.

Report a vulnerability privately through GitHub's "Report a vulnerability" (Security tab) rather than in a public issue.

## Threat model

**What we protect**
- Each user's Buffer connection (OAuth tokens are encrypted at rest) and the posts it can publish.
- Each user's media, brand kit, Style Ledger rules, takes and drafts.
- Agent tokens and what they are allowed to do: their policy (`review`, `draft`, `trusted`), channels, daily cap and the kill switch.
- The operator's shared resources: the database, GitHub Actions minutes and the free AI quota.
- The internal network the server can reach (loopback, private ranges, cloud metadata).

**Who might attack it**
- An anonymous visitor. `/demo` gives anyone a sample workspace with no sign-in.
- A signed-in user who tries to reach someone else's data.
- An AI agent holding a scoped token that goes beyond its scope, whether through a bug, a bad prompt or text injected from a fetched page.
- A malicious OAuth client posing as a known assistant on the MCP connector consent screen.
- Anyone who can read the repository's Actions run pages and logs.
- A web page we fetch that answers with a redirect or hostile content.

**Trust boundaries**
- Browser to app: a session cookie (HttpOnly, Secure, SameSite=Lax), a custom CSRF header on every write, a strict CSP (`script-src 'self'`, `frame-ancestors 'none'`), `nosniff`, `X-Frame-Options: DENY` and a Referrer-Policy.
- Agent to app: a hashed, scoped and revocable bearer token, or an MCP OAuth token (PKCE S256).
- App to the internet: every user-influenced fetch goes through `studio/netguard.py`.
- App to the worker: a GitHub OIDC identity, traded once for a single-use job token.
- App to Buffer: OAuth 2.0 Authorization Code with PKCE and rotating refresh tokens.

**Out of scope**
- `huhqueue/` (the optional self-host package, which this deploy does not run), `web/src`, `scripts/` and `demo/`.

## The rules every change follows

1. Every outbound fetch goes through one safe-fetch helper. It never follows redirects automatically and re-checks every hop against private, loopback, link-local, 100.64/10 and metadata addresses.
2. Tokens never go in workflow commands or logs. They travel via env or OIDC, are single-use and are invalidated after use.
3. `/demo` and anonymous traffic always have rate and cost caps: no worker runs, and caps on bytes, jobs and requests.
4. The kill switch and daily caps cover every agent action. Only a human can toggle a ledger rule.
5. The OAuth consent screen shows the redirect host and flags unverified clients.
6. Uploads are authenticated first and size-checked before they are decoded or parsed.
7. Every PR gets gitleaks, pip-audit and a security review against this list. Push after every commit.

## The 7 findings and their fixes

| # | Severity | Finding | Fix | Regression test |
|---|---|---|---|---|
| 1 | High | **SSRF via redirect.** The public-host check looked only at the first URL, and every client followed redirects, so a public page answering `302 → http://127.0.0.1/…` let any visitor read internal services | `studio/netguard.py`: `safe_get` / `safe_stream` with no auto-redirects. Each `Location` is resolved and checked again (at most `MAX_HOPS`). An explicit blocklist covers private, loopback, link-local (169.254.169.254), CGNAT 100.64/10, IPv4 mapped into IPv6 and metadata host names. Only http(s), with a byte cap, and no credentials in the URL. Used by the article fetch, feeds, the Google News resolver, `House._fetch_bytes` and `Takes._download` | `test_1_*` |
| 2 | Medium | **Job token replay.** A worker job token could be reused for the whole life of the job to read a user's private media URL and forge a take's transcript | Tokens are scoped and consumed. The spec can be read once, each of the job's own clips can be posted once, and the token dies with the final result. The worker gets its token only by claiming the job with its GitHub OIDC identity (`studio/worker_auth.py`, `POST /api/worker/jobs/{id}/claim`): RS256 against GitHub's keys, plus checks on audience, repository, `workflow_dispatch`, the workflow file and the deploy branch. A job can be claimed once, inside its claim window | `test_2_*` |
| 3 | Medium | **Token in the Actions log.** `echo "::add-mask::${{ inputs.job_token }}"` printed the token before masking took effect | No token anywhere in the workflow or its inputs. The dispatch sends only `job_id` and `app`. Inputs reach commands only through `env`, never by `${{ }}` inside `run:`. `permissions: id-token: write`, and `persist-credentials: false` on checkout | `test_3_*`, `test_2_dispatch_sends_no_token` |
| 4 | Medium | **OAuth client-name spoofing.** The consent page showed only a self-chosen client name, so a fake "Claude" got a `trusted` token with one click | The consent page shows the redirect host prominently. Clients whose redirect host is not a known assistant are labelled unverified and cannot be given `trusted` (the server refuses it even if the form asks). Registration is rate-limited | `test_4_*` |
| 5 | Medium | **Kill switch and cap gaps.** Only 4 post actions were guarded, so a stopped agent could still start runs and takes and switch off the owner's brand rules | The kill switch and daily cap run before every agent action: runs, revisions, takes (including the sample take), clip edits, link drafts, ideas and rule changes, over both MCP and REST. Turning a ledger rule on or off is human-only | `test_5_*` |
| 6 | Medium | **Anonymous `/demo` cost.** Unlimited storage and GitHub Actions dispatches, with no rate limit | A per-IP rate limit for anonymous and sample traffic (`limits.RateLimit`; real accounts are never slowed). Atomic daily caps per sample workspace and per visitor IP for AI, research, uploads (per file and per day) and new workspaces. The sample never dispatches the worker | `test_6_*` |
| 7 | Low | **Unbounded upload body.** The whole JSON body was buffered and base64-decoded before the 40 MB check, before authentication | `limits.BodyCap` caps every POST/PUT/PATCH body by path before anything parses it. A Content-Length over the cap gets 413 at once. A streamed body is counted and cut off at the cap, and the answer is swapped for a 413 (raising there would surface as an ExceptionGroup). Upload and worker endpoints authenticate first, then read through `limits.read_json` with their own cap | `test_7_*` |

## Known limits and open hardening

- **DNS rebinding.** `netguard` checks every hop, but the HTTP client resolves the host again when it connects, so a DNS answer that flips between the check and the connect is not pinned. Redirects, the common case, are covered.
- **BYOK model endpoint.** The host is checked when the endpoint is saved. Re-checking and pinning it on every call is planned.
- **Worker media handling.** The worker runs ffmpeg/ffprobe on user video. Restricting demuxers and protocols (`-protocol_whitelist file`) is planned.
- **Fetched text in prompts.** Page text reaches the model as data. A `trusted` token can still publish without a human step, so `review` stays the default policy.
- **Encryption key.** It is derived from `SESSION_SECRET`. A separate, versioned `TOKEN_KEY` is planned so rotation isn't destructive.
- **HSTS** is not sent yet. Cookies are already `Secure`.

## Checks on every PR

- `gitleaks` over the new commits before each push.
- `pip-audit` on `requirements.txt`, `requirements-studio.txt` and `requirements-worker.txt`. It reported no known vulnerabilities on 31 Oct 2026.
- `studio-ci`: the full `tests/studio` suite on SQLite and Postgres.
- `ui-smoke`: a real browser run of the sample workspace, including the responsive, tour and browser tests.
- A review against the rules above.
