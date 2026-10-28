#!/usr/bin/env bash
# Run before making the repo public and before every push: full-history + working-tree secret scan.
set -euo pipefail
GL=${GITLEAKS:-gitleaks}
"$GL" detect --source . --log-opts="--all" --redact      # every commit on every branch
"$GL" detect --source . --no-git --redact                # working tree (untracked files too)
if git ls-files | grep -E '(^|/)\.env$|\.pem$|\.key$' ; then echo "Refusing: secret-looking file tracked"; exit 1; fi
echo "secret scan clean"
