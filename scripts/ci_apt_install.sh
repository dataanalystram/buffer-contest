#!/usr/bin/env bash
# CI: install apt packages, skipping ones already on the runner, with apt's network timeouts and one retry.
# 6 Nov 2026: the runners' Ubuntu mirror was very slow (ffmpeg took 15-31 min to install). A tight per-attempt cap
# turned "slow" into "failed", so the caps here are generous (update 5 min, install 25 min): a dead mirror still
# ends instead of hanging until the job timeout, and a slow one still finishes.
# Usage: scripts/ci_apt_install.sh ffmpeg [libegl1 libgles2 ...]
set -u
need=()
for p in "$@"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || need+=("$p"); done
if [ ${#need[@]} -eq 0 ]; then echo "already installed: $*"; exit 0; fi
opts=(-o Acquire::Retries=5 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 -o DPkg::Lock::Timeout=120)
for attempt in 1 2; do
  start=$SECONDS
  if timeout 300 sudo apt-get "${opts[@]}" update -qq \
     && timeout 1500 sudo DEBIAN_FRONTEND=noninteractive apt-get "${opts[@]}" install -y -qq --no-install-recommends "${need[@]}" >/dev/null; then
    echo "installed ${need[*]} in $((SECONDS - start)) s"
    exit 0
  fi
  echo "::warning::apt install of ${need[*]} failed or timed out after $((SECONDS - start)) s (attempt $attempt of 2)"
  sleep 15
done
echo "::error::apt install of ${need[*]} failed twice"
exit 1
