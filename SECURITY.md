# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability** (private vulnerability reporting is enabled). Do not open a public issue, pull request or
discussion for a vulnerability.

Include what you found, the steps to reproduce it, and the impact you expect. You will get an acknowledgement within
3 days and a status update within 7 days. Please give us a reasonable time to fix the issue before you disclose it.

## Scope

- The hosted app (`studio/`) at https://huhqueue-studio.onrender.com and its `/demo` sample workspace.
- The video worker workflows (`.github/workflows/render.yml`, `take.yml`) and the InstaBox worker path.
- The self-hosted app (`huhqueue/`).

Out of scope: denial of service by volume, findings that need a compromised device or browser, reports from automated
scanners without a working proof, and the third-party services HuhQueue connects to (report those to their owners).

Please test only against your own account or the `/demo` sample workspace, never other people's data, and stay within
the app's rate limits.

## What the hosted app protects, and how

Written from the code; the privacy page at https://huhqueue-studio.onrender.com/privacy has the full table.

- **Secrets at rest:** Buffer access/refresh tokens and users' own AI keys are sealed with Fernet under `TOKEN_KEY`
  (rotatable with `TOKEN_KEY_PREVIOUS`; rows are re-sealed on boot) and never sent to the browser. Agent tokens, session
  ids, MCP sign-in codes, refresh tokens and device links are stored only as hashes.
- **Media:** Library media lives in Postgres and is served by unguessable links. Deleted items stay in Trash for 7 days,
  then are removed for good.
- **Logs:** activity rows are deleted after 90 days; sign-in IP addresses and browsers are cleared after 30 days. AI logs
  are metadata only (engine, model, task, latency, tokens, cost, status), never prompts, replies or keys.
- **Demo:** `/demo` sample workspaces are view-only (writes answer "In the real app this would…", no Buffer or model
  calls) and are deleted after 1 day.
- **Delete:** Disconnect and delete removes every per-person table. The per-day usage counters are kept on purpose (no
  content) so reconnecting can't reset an allowance.
- **Prompt injection:** outside content (pages, feeds, posts, transcripts, uploads) is fenced in prompts as
  `<untrusted>` data; once a run has read it, sensitive tools (save, send, schedule, delete) are held for a human to
  confirm; MCP clients are told tool results carry untrusted content. This reduces the risk; it does not remove it, which
  is why agent posts wait for approval by default.
- **Third parties:** Buffer, Render, Supabase, AI engines (NVIDIA, Google AI Studio/Gemini, Hugging Face, Anthropic,
  Oracle OCI, or a user's own key), context.dev, Firecrawl, Typesafe/SubQ vision, Product Hunt, InstaBox and GitHub
  Actions (video renders), and an Invidious YouTube fallback that is off unless the operator enables it.

## Supported versions

Only the latest `main` (what is deployed) gets security fixes.

## More

The threat model, the controls and past audit results are in [docs/SECURITY.md](docs/SECURITY.md).
Secrets are never committed: GitHub secret scanning with push protection is on, and `scripts/secret_scan.sh`
(gitleaks over the full history) runs before releases.
