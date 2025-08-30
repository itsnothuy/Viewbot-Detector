#!/usr/bin/env bash
# Generate a tiny HLS VOD from an input (requires ffmpeg).
# Usage: ./tools/make_hls.sh input.mp4
set -euo pipefail
IN=${1:-/usr/share/sounds/alsa/Front_Center.wav}
OUT=player/sample
mkdir -p "$OUT"
ffmpeg -y -re -i "$IN" -c:v libx264 -pix_fmt yuv420p -preset veryfast -g 60 -sc_threshold 0 \
  -c:a aac -b:a 128k -hls_time 6 -hls_playlist_type vod \
  -hls_segment_filename "$OUT/seg%03d.ts" "$OUT/stream.m3u8"
echo "Wrote HLS to $OUT"
