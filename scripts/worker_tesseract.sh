#!/bin/sh
# Tesseract OCR >= 5.5.4 on the video worker (InstaBox huhqueue-worker), built once from the pinned upstream tag into
# a prefix on the persistent disk. Debian 13's apt package is 5.5.0, below the version HuhQueue requires, so it is not
# used. Idempotent: exits at once when the pinned version is already installed. Runs in the background on the box's next
# job (studio/instabox_worker.py) under `flock -n`, at nice 19; One take OCR fails open until it is there.
#
#   sh scripts/worker_tesseract.sh            # PREFIX=/data/tesseract
#   PREFIX=/tmp/tess sh scripts/worker_tesseract.sh
#
# Licences: Tesseract (Apache-2.0), tessdata_fast eng.traineddata (Apache-2.0), Leptonica (BSD-2-Clause, apt). NOTICE.
set -eu
PREFIX=${PREFIX:-/data/tesseract}
TAG=5.5.4
COMMIT=7e548e6eff172f38f97664afe7f7ce40db36c8d6            # the 5.5.4 tag's commit (git/tags API, 5 Nov 2026)
DATA_COMMIT=87416418657359cb625c412a48b6e1d6d41c29bd       # tessdata_fast main
DATA_SHA256=7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2
if [ -x "$PREFIX/bin/tesseract" ] && "$PREFIX/bin/tesseract" --version 2>&1 | head -1 | grep -qx "tesseract $TAG" \
   && [ -s "$PREFIX/share/tessdata/eng.traineddata" ]; then
  echo "tesseract $TAG already at $PREFIX"; exit 0
fi
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo -n"
$SUDO apt-get update -qq
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  build-essential cmake pkg-config git ca-certificates curl libleptonica-dev >/dev/null
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git -c advice.detachedHead=false clone -q --depth 1 --branch "$TAG" https://github.com/tesseract-ocr/tesseract.git "$WORK/src"
GOT=$(git -C "$WORK/src" rev-parse HEAD)
[ "$GOT" = "$COMMIT" ] || { echo "tesseract $TAG is at $GOT, expected $COMMIT: refusing to build" >&2; exit 1; }
nice -n 19 cmake -S "$WORK/src" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBUILD_SHARED_LIBS=OFF -DBUILD_TRAINING_TOOLS=OFF -DBUILD_TESTS=OFF -DGRAPHICS_DISABLED=ON -DDISABLE_CURL=ON \
  -DDISABLE_ARCHIVE=ON -DOPENMP_BUILD=OFF -DENABLE_NATIVE=OFF >/dev/null
for i in 1 2 3; do nice -n 19 cmake --build "$WORK/build" -j "$(nproc)" >/dev/null && break; [ "$i" = 3 ] && exit 1; done
mkdir -p "$PREFIX"
cmake --install "$WORK/build" >/dev/null
mkdir -p "$PREFIX/share/tessdata"
curl -fsSL -o "$WORK/eng.traineddata" "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$DATA_COMMIT/eng.traineddata"
echo "$DATA_SHA256  $WORK/eng.traineddata" | sha256sum -c - >/dev/null
mv "$WORK/eng.traineddata" "$PREFIX/share/tessdata/eng.traineddata"
"$PREFIX/bin/tesseract" --version 2>&1 | head -1
