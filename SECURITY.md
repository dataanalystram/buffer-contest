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

## Supported versions

Only the latest `main` (what is deployed) gets security fixes.

## More

The threat model, the controls and past audit results are in [docs/SECURITY.md](docs/SECURITY.md).
Secrets are never committed: GitHub secret scanning with push protection is on, and `scripts/secret_scan.sh`
(gitleaks over the full history) runs before releases.
