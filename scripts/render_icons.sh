#!/usr/bin/env bash
#
# Render brand/icon.svg into both apps' static directories as PNGs.
#
#   ./scripts/render_icons.sh
#
# brand/ holds the artwork; everything under midi_memory/*/static/brand/ is
# generated from it and should not be edited by hand. Replace brand/icon.svg
# with a real logo and run this. The output is committed, so neither the Docker
# build nor the Pi install needs these tools; only changing the logo does.
#
# The SVG stays in brand/ and is never copied to where it would be served. It
# is the source the PNGs are cut from, nothing more: browsers rendered it at
# favicon sizes noticeably worse than a PNG resized off the same geometry.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/brand/icon.svg"
SIZES=(512 180 32 16)
TARGETS=(
  "$ROOT/midi_memory/server/static/brand"
  "$ROOT/midi_memory/client/static/brand"
)

# rsvg-convert does the rasterising, not ImageMagick. Homebrew's ImageMagick is
# built without librsvg, so `magick foo.svg` silently falls back to its own
# minimal SVG parser, which ignores gradient fills and drops whole paths. That
# failure is invisible until you open the PNG and find a black square.
if ! command -v rsvg-convert >/dev/null 2>&1; then
  echo "This needs librsvg (brew install librsvg)." >&2
  exit 1
fi
if ! command -v magick >/dev/null 2>&1; then
  echo "This needs ImageMagick (brew install imagemagick)." >&2
  exit 1
fi
[[ -f "$SOURCE" ]] || { echo "No such file: $SOURCE" >&2; exit 1; }

for target in "${TARGETS[@]}"; do
  mkdir -p "$target"
  for size in "${SIZES[@]}"; do
    # -depth 8: rsvg-convert writes 16-bit channels, which roughly doubles the
    # file for a gradient no one can see the extra precision in.
    rsvg-convert -w "$size" -h "$size" -b none "$SOURCE" \
      | magick - -depth 8 -strip -define png:compression-level=9 \
               "$target/icon-${size}.png"
  done
  echo "  wrote $(ls "$target" | wc -l | tr -d ' ') files to ${target#$ROOT/}"
done

echo
echo "Done. Sizes: ${SIZES[*]}"
