#!/usr/bin/env bash
# CI: install apt packages with a time limit and retries (6 Nov 2026: a stuck `apt-get update` held two runners for
# 15-31 minutes, so ui-smoke hit its job timeout and studio-ci's Postgres job waited half an hour before any test ran).
# Each attempt is capped; a slow or dead mirror fails fast and the next attempt starts over.
# Usage: scripts/ci_apt_install.sh ffmpeg [libegl1 libgles2 ...]
set -u
opts=(-o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o DPkg::Lock::Timeout=60)
for attempt in 1 2 3; do
  if timeout 180 sudo apt-get "${opts[@]}" update -qq \
     && timeout 420 sudo DEBIAN_FRONTEND=noninteractive apt-get "${opts[@]}" install -y -qq --no-install-recommends "$@" >/dev/null; then
    exit 0
  fi
  echo "::warning::apt install of $* failed or timed out (attempt $attempt of 3)"
  sleep $((attempt * 10))
done
echo "::error::apt install of $* failed 3 times"
exit 1
