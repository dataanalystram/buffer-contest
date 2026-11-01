#!/bin/sh
# Dev setup for studio/media (docs/MEDIA_PIPELINE.md): a venv with the media extras and the test clip.
#   sh scripts/media_dev_setup.sh [venv_dir]      (default /tmp/venv-media)
# Needs ffmpeg on PATH (apt-get install ffmpeg) or the imageio-ffmpeg wheel (installed below as a fallback).
set -e
V=${1:-/tmp/venv-media}
[ -x "$V/bin/python" ] || python3 -m venv "$V"
"$V/bin/pip" install -q --no-cache-dir --disable-pip-version-check -r requirements-studio.txt -r requirements-dev.txt
"$V/bin/pip" install -q --no-cache-dir --disable-pip-version-check -r requirements-media.txt
echo "venv ready: $V"
