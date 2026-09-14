#!/usr/bin/env bash
#
# Render the icon into both apps' static directories.
#
#   ./scripts/render_icons.sh
#
# The source is the server's own icon.svg, so there is no separate copy of the
# artwork to keep in step: replace that file with a real logo and run this, and
# the PNGs and the client's copy follow. The output is committed, so neither the
# Docker build nor the Pi install needs ImageMagick; only changing the logo does.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/midi_memory/server/static/brand/icon.svg"
SIZES=(512 180 32 16)
TARGETS=(
  "$ROOT/midi_memory/server/static/brand"
  "$ROOT/midi_memory/client/static/brand"
)

if ! command -v magick >/dev/null 2>&1; then
  echo "This needs ImageMagick (brew install imagemagick)." >&2
  exit 1
fi
[[ -f "$SOURCE" ]] || { echo "No such file: $SOURCE" >&2; exit 1; }

for target in "${TARGETS[@]}"; do
  mkdir -p "$target"
  # Skipped for the server, whose copy is the source.
  [[ "$target/icon.svg" -ef "$SOURCE" ]] || cp "$SOURCE" "$target/icon.svg"
  for size in "${SIZES[@]}"; do
    # -depth 8: the default is 16-bit, which makes a flat two-colour icon about
    # six times larger than it has any need to be.
    magick -background none "$SOURCE" -resize "${size}x${size}" \
           -depth 8 -strip -define png:compression-level=9 \
           "$target/icon-${size}.png"
  done
  echo "  wrote $(ls "$target" | wc -l | tr -d ' ') files to ${target#$ROOT/}"
done

echo
echo "Done. Sizes: ${SIZES[*]}"
