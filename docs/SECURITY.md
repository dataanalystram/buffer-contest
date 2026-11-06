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
4. The kill switch covers every agent action. The daily cap (default 50, max 500 a token) counts and blocks Buffer writes
   only (posts, drafts, draft edits and deletes, ideas); renders, previews and image edits are free of it (6 Nov 2026).
   Only a human can toggle a ledger rule.
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
| 5 | Medium | **Kill switch and cap gaps.** Only 4 post actions were guarded, so a stopped agent could still start runs and takes and switch off the owner's brand rules | The kill switch runs before every agent action: runs, revisions, takes (including the sample take), clip edits, link drafts, ideas and rule changes, over both MCP and REST. Since 6 Nov 2026 the daily cap counts Buffer writes only (default 50, max 500). Turning a ledger rule on or off is human-only | `test_5_*` |
| 6 | Medium | **Anonymous `/demo` cost.** Unlimited storage and GitHub Actions dispatches, with no rate limit | A per-IP rate limit for anonymous and sample traffic (`limits.RateLimit`; real accounts are never slowed). Atomic daily caps per sample workspace and per visitor IP for AI, research, uploads (per file and per day) and new workspaces. The sample never dispatches the worker | `test_6_*` |
| 7 | Low | **Unbounded upload body.** The whole JSON body was buffered and base64-decoded before the 40 MB check, before authentication | `limits.BodyCap` caps every POST/PUT/PATCH body by path before anything parses it. A Content-Length over the cap gets 413 at once. A streamed body is counted and cut off at the cap, and the answer is swapped for a 413 (raising there would surface as an ExceptionGroup). Upload and worker endpoints authenticate first, then read through `limits.read_json` with their own cap | `test_7_*` |

## Fixed in the hardening pass (31 Oct 2026)

Every item that was open after the audit is closed. Each has a regression test in `tests/studio/test_security_hardening.py`.

| # | Item | Fix | Regression test |
|---|---|---|---|
| H1 | **HSTS** | Every HTTPS response carries `Strict-Transport-Security: max-age=31536000; includeSubDomains`. Render terminates TLS and uvicorn trusts the forwarded scheme (`--proxy-headers`), so the app sees `https`. | `test_hsts_on_https_only` |
| H2 | **DNS rebinding** | `netguard` resolves each hop once, vets every address and connects to that vetted IP. The URL's host goes in the `Host` header and as the TLS SNI, which is also the name the certificate is checked against. This applies on every redirect hop and to IPv6 and non-default ports. A name that resolves to a private address at connect time is refused before any socket opens. (Requests sent through an operator's `HTTP(S)_PROXY` are not pinned, because the proxy does the lookup.) | `test_rebinding_host_is_pinned_to_the_vetted_ip`, `test_a_private_answer_at_connect_time_is_refused`, `test_every_redirect_hop_is_pinned`, `test_port_and_ipv6_are_kept_when_pinning` |
| H3 | **AI model endpoint** | `llm.check_engine` runs on every call, not only when the key is saved. Each named provider (OpenAI, Anthropic, Gemini, Groq, OpenRouter) may only call its own API host. The free starter may only call the operator's `AI_BASE_URL` host. A custom endpoint must be https and public, and the operator can restrict it with `HQ_AI_CUSTOM_HOSTS`. Calls go through `netguard.safe_post`: pinned, no redirects, body capped. Saving a preset with a foreign base URL is refused. | `test_a_named_provider_only_talks_to_its_own_host`, `test_custom_endpoint_is_rechecked_each_call`, `test_operator_can_restrict_custom_hosts_and_redirects_are_not_followed`, `test_saving_a_preset_with_a_foreign_base_url_is_refused` |
| H4 | **FFmpeg / FFprobe** | Every call runs through `studio/mediasafe.py`. Before each input it adds `-protocol_whitelist file,pipe` and a `-format_whitelist` that allows plain media demuxers only, so hls, dash, concat/ffconcat and tee are refused. Our own `-f lavfi` and `-f concat` inputs get exactly that one demuxer. Calls also run with `-nostdin`, a wall-clock timeout and an input size cap, and URL-style inputs are refused. Before any decoder opens an uploaded take (including PySceneDetect and MediaPipe), `check_source` confirms it is a plain MP4/MOV/MKV/WebM. OpenCV is limited to `protocol_whitelist;file`. | `test_harden_puts_whitelists_before_every_input`, `test_crafted_playlists_cannot_read_local_files_or_urls`, `test_take_analyze_refuses_a_non_video_before_any_decoder`, `test_no_raw_subprocess_ffmpeg_calls_outside_mediasafe` |
| H5 | **Token encryption key** | A keyring separate from `SESSION_SECRET`. New seals use `TOKEN_KEY`. `TOKEN_KEY_PREVIOUS` holds older keys, which are only read during a rotation. The old key derived from `SESSION_SECRET` is read-only. On every boot, `Store.rotate_keys()` re-seals Buffer tokens, BYOK AI keys and pending OAuth state under the current key. It logs counts only, and leaves a row that no key can open untouched. `TOKEN_KEY` is set on the Render service. | `test_token_key_is_separate_from_session_secret_and_migrates`, `test_rotation_reads_the_previous_key_and_writes_the_new_one`, `test_app_boot_migrates_and_logs_counts_only` |

**Rotating TOKEN_KEY.** Generate a new key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Set it as `TOKEN_KEY`, move the old value into `TOKEN_KEY_PREVIOUS`, and deploy. The boot log line `token keys: dedicated TOKEN_KEY; {'resealed': N, ...}` confirms the move. Remove `TOKEN_KEY_PREVIOUS` on the next deploy. If `TOKEN_KEY` is lost, sealed tokens can't be recovered and users have to reconnect Buffer.

### Sweep (same pass)

| Area | Finding | Fix | Regression test |
|---|---|---|---|
| Auth on every route | Every route was checked | A test lists every route and calls it anonymously. It must answer 401/403, or appear in a `PUBLIC` table with a reason. A new route with neither fails CI. | `test_every_route_requires_auth_or_is_explicitly_public`, `test_oauth_authorize_grant_needs_a_signed_in_human` |
| Error pages | An unhandled error fell through to Starlette's default page | A JSON 500 with a fixed message. The log keeps only the exception class and the path. | `test_a_crash_never_leaks_a_trace` |
| Rate limits on sign-in | `/login`, `/callback` and `/device/{token}` were outside the per-IP limiter | A separate per-IP bucket (`HQ_AUTH_PER_MIN`, default 30/min) for `/login`, `/callback`, `/device/`, `/oauth/authorize`, `/oauth/token` and `/oauth/register`. Signed-in real accounts are never slowed. | `test_sign_in_endpoints_are_rate_limited_per_ip` |
| Open redirects | `/login?next=` only accepts our own consent page. This was already right and is now pinned by a test. | — | `test_login_next_cannot_redirect_off_site` |
| CSP gaps | No `object-src` or `base-uri`, and fonts couldn't load from `'self'` | `object-src 'none'; base-uri 'none'`, `font-src 'self' https://fonts.gstatic.com`. Also `Cross-Origin-Opener-Policy: same-origin` and a `Permissions-Policy` denying camera, microphone, geolocation, payment and usb. `form-action` is left out on purpose: the MCP consent form redirects to the client's registered redirect URI. | `test_csp_and_isolation_headers` |
| Caching | API answers (which carry the CSRF token) had no cache directive | `Cache-Control: no-store` on `/api/*` unless a route sets its own | `test_session_cookie_flags` |
| Cookie flags | `hq_sid` is `HttpOnly; Secure; SameSite=Lax; Path=/`. This is now pinned by a test. | — | `test_session_cookie_flags` |
| Dependency pins | The floors allowed versions with published advisories | `starlette>=0.49.1`, `fastapi>=0.115`, `cryptography>=44.0.1`, `pillow>=11.3.0`, `fonttools>=4.60.2`, `python-multipart>=0.0.18`, `uvicorn>=0.30`, `psycopg>=3.2`. pip-audit found no known vulnerabilities in any of the three requirement files on 31 Oct 2026. | `test_studio_requirements_pin_floors_past_known_advisories` |
| security.txt | There wasn't one | `/.well-known/security.txt` (and `/security.txt`): the GitHub private advisory as Contact, Expires 2027-10-31, Canonical, Policy | `test_security_txt` |

## Known limits

- **Fetched text in prompts.** Page text reaches the model as data. A `trusted` token can still publish without a human step, so `review` stays the default policy.
- **Hosting.** The demo runs on Render's free plan, which spins an idle service down after 15 minutes. A Supabase `pg_cron` job hits `/healthz` every 10 minutes to keep it warm. A cold start after a deploy or an outage takes about 30-60 s.

## Checks on every PR

- `gitleaks` over the new commits before each push.
- `pip-audit` on `requirements.txt`, `requirements-studio.txt` and `requirements-worker.txt`. It reported no known vulnerabilities on 31 Oct 2026.
- `studio-ci`: the full `tests/studio` suite on SQLite and Postgres.
- `ui-smoke`: a real browser run of the sample workspace, including the responsive, tour and browser tests.
- A review against the rules above.
