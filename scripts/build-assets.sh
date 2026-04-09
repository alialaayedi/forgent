#!/usr/bin/env bash
# Optional asset build step — converts the brand SVGs to PNGs at standard sizes.
#
# Why it's optional: the SVGs ARE the source of truth and render natively on
# GitHub, npm, PyPI, and most modern surfaces. PNGs are only needed for places
# that don't accept SVG (some social cards, some package registries, favicon.ico).
#
# Requires `rsvg-convert` (from librsvg). Install on macOS with:
#   brew install librsvg
#
# Usage:
#   ./scripts/build-assets.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRAND="$REPO_ROOT/assets/brand"
OUT="$BRAND/png"

if ! command -v rsvg-convert >/dev/null 2>&1; then
    echo "rsvg-convert not found. Install with:"
    echo "  brew install librsvg     (macOS)"
    echo "  apt install librsvg2-bin (Debian/Ubuntu)"
    exit 1
fi

mkdir -p "$OUT"

echo "==> Building PNGs into $OUT"

# Banner — multiple widths for different surfaces
rsvg-convert -w 1200 "$BRAND/banner.svg"          -o "$OUT/banner-1200.png"
rsvg-convert -w 2400 "$BRAND/banner.svg"          -o "$OUT/banner-2400.png"   # @2x

# Social preview — exactly 1280x640 (GitHub's required size)
rsvg-convert -w 1280 -h 640 "$BRAND/social-preview.svg" -o "$OUT/social-preview-1280x640.png"

# Icon (with wordmark) — npm, PyPI thumbnails
rsvg-convert -w 512  "$BRAND/icon.svg"            -o "$OUT/icon-512.png"
rsvg-convert -w 256  "$BRAND/icon.svg"            -o "$OUT/icon-256.png"
rsvg-convert -w 128  "$BRAND/icon.svg"            -o "$OUT/icon-128.png"

# Mark only (no wordmark) — favicons, avatars, tiny sizes
rsvg-convert -w 1024 "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-1024.png"
rsvg-convert -w 512  "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-512.png"
rsvg-convert -w 256  "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-256.png"
rsvg-convert -w 128  "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-128.png"
rsvg-convert -w 64   "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-64.png"
rsvg-convert -w 32   "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-32.png"
rsvg-convert -w 16   "$BRAND/icon-mark.svg"       -o "$OUT/icon-mark-16.png"

# Horizontal lockup — for navigation bars
rsvg-convert -w 800  "$BRAND/logo-horizontal.svg" -o "$OUT/logo-horizontal-800.png"
rsvg-convert -w 1600 "$BRAND/logo-horizontal.svg" -o "$OUT/logo-horizontal-1600.png"

echo "==> Done. Generated:"
ls -lh "$OUT" | tail -n +2 | awk '{print "  ", $9, "(" $5 ")"}'

cat <<'EOF'

Where to use these:
  social-preview-1280x640.png   GitHub Settings -> Social Preview
  icon-mark-512.png             GitHub org/user avatar
  icon-mark-32.png              browser tab favicon
  banner-2400.png               README hero (high-DPI)
  icon-512.png                  npm/PyPI package icon
EOF
