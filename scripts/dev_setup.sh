#!/usr/bin/env bash
# Developer setup: Python deps (+ test deps), web UI deps. Safe to re-run.
set -e
cd "$(dirname "$0")/.."
python3 -m pip install -q -r requirements-dev.txt
if command -v npm >/dev/null; then (cd web && npm install --no-audit --no-fund >/dev/null); fi
echo "ok - run: python -m pytest   |   cd web && npm run build"
