#!/usr/bin/env bash
# Opening/ending frame audit for a rendered Reel: frames at fixed times, a contact sheet of the first 6 s and the last 9 s,
# scene changes and still (freeze) segments in the first 5 s and the last 10 s. Read-only on the video.
# Usage: scripts/reel_opening_audit.sh <video.mp4|https url> <out_dir> [label]
set -euo pipefail
src="$1"; out="$2"; label="${3:-reel}"
mkdir -p "$out"
v="$out/$label.mp4"
if [[ "$src" == http* ]]; then curl -fsSL --max-time 300 -o "$v" "$src"; elif [[ "$(realpath "$src")" != "$(realpath -m "$v")" ]]; then cp "$src" "$v"; fi
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$v")
echo "duration=$dur"
for t in 0 0.25 0.5 1 1.5 2 3; do
  ffmpeg -v error -y -ss "$t" -i "$v" -frames:v 1 -update 1 -vf scale=270:-2 "$out/${label}_t$t.png"
done
ffmpeg -v error -y -i "$v" -t 6 -vf "fps=8,scale=135:-2,tile=8x6" -frames:v 1 -update 1 "$out/${label}_contact_first6s.png"
ffmpeg -v error -y -sseof -9 -i "$v" -vf "fps=2,scale=135:-2,tile=9x2" -frames:v 1 -update 1 "$out/${label}_contact_last9s.png"
{
  echo "# $label  duration=$dur"
  echo "## scene changes, first 5 s (score>0.2)"
  ffmpeg -hide_banner -t 5 -i "$v" -vf "select='gt(scene,0.2)',showinfo" -an -f null - 2>&1 | grep -o "pts_time:[0-9.]*" || true
  echo "## stills (freezedetect n=0.003 d=0.2), first 5 s"
  ffmpeg -hide_banner -t 5 -i "$v" -vf "freezedetect=n=0.003:d=0.2" -an -f null - 2>&1 | grep -o "freeze_[a-z]*: [0-9.]*" || true
  echo "## scene changes, last 10 s (absolute s)"
  st=$(awk -v d="$dur" 'BEGIN{printf "%.3f", (d>10?d-10:0)}')
  ffmpeg -hide_banner -ss "$st" -i "$v" -vf "select='gt(scene,0.08)',showinfo" -an -f null - 2>&1 | grep -o "pts_time:[0-9.]*" \
    | awk -F: -v s="$st" '{printf "%.2f\n", $2+s}' || true
} | tee "$out/${label}_report.txt"
