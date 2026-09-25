#!/usr/bin/env bash
# Assemble the landing page into _site/ for GitHub Pages.
#
# Everything the page serves already lives under site/ in its final form: the
# panel frames and pictures come from tools/build_screens.py, the social
# preview and the icons from tools/build_og.py. This step only copies and fills
# in the version, so the Pages runner needs no image tooling at all.
#
#   tools/build_site.sh            # build _site/
#   tools/build_site.sh --serve    # build, then serve on 0.0.0.0:8000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/_site"

# The integration's manifest is the one version the project publishes.
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
  "$ROOT/custom_components/foredogs/manifest.json")"
[ -n "$VERSION" ] || { echo "no version in manifest.json" >&2; exit 1; }

# Full W3C datetime: Google reads <lastmod> for scheduling, date-only is coarser.
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

# Empty the folder rather than replacing it, so a --serve already running from
# it keeps serving and picks up the new files.
mkdir -p "$OUT"
find "$OUT" -mindepth 1 -delete

cp "$ROOT"/site/*.css "$ROOT"/site/*.js "$ROOT"/site/*.svg "$ROOT"/site/*.png \
   "$ROOT"/site/*.jpg "$ROOT"/site/*.ico "$ROOT/site/site.webmanifest" "$OUT/"
cp "$ROOT/site/CNAME" "$ROOT/site/robots.txt" "$OUT/"
cp -R "$ROOT/site/fonts" "$ROOT/site/screens" "$ROOT/site/pictures" "$ROOT/site/brands" "$OUT/"

# index.html carries the placeholders: the version in the navbar and in the
# structured data, and the build time as the page's dateModified.
# The stylesheet and script are fetched by a URL that changes with their
# content. Browsers and GitHub Pages cache them (Pages for ten minutes); a
# fresh page with a cached old script is exactly the mismatch that breaks the
# demo, so a new build must never share a URL with an old one.
hash8() { shasum -a 256 "$1" | cut -c1-8; }
CSS_V="$(hash8 "$ROOT/site/app.css")"
JS_V="$(hash8 "$ROOT/site/app.js")"
# Link previews are cached the same way, by the platforms, for days.
OG_V="$(hash8 "$ROOT/site/og.jpg")"
sed -e "s/__VERSION__/$VERSION/g" -e "s|__BUILD_DATE__|$BUILD_DATE|g" -e "s|__OG_V__|$OG_V|g" \
    -e "s|href=\"app.css\"|href=\"app.css?v=$CSS_V\"|" -e "s|src=\"app.js\"|src=\"app.js?v=$JS_V\"|" \
    "$ROOT/site/index.html" > "$OUT/index.html"
sed "s|href=\"app.css\"|href=\"app.css?v=$CSS_V\"|" "$ROOT/site/404.html" > "$OUT/404.html"
sed "s|__BUILD_DATE__|$BUILD_DATE|g" "$ROOT/site/sitemap.xml" > "$OUT/sitemap.xml"

# Any placeholder left behind would ship to production, so fail loudly instead.
if grep -rq "__VERSION__\|__BUILD_DATE__\|__OG_V__" "$OUT"; then
  echo "unsubstituted placeholder left in _site" >&2
  exit 1
fi

# A file referenced but never copied would 404 in production, where nobody
# looks. The panel's frames are named in app.js, so check those too.
missing=0
while read -r asset; do
  [ -z "$asset" ] && continue
  [ -f "$OUT/$asset" ] || { echo "referenced but missing: $asset" >&2; missing=1; }
done < <(grep -ho '\(src\|href\)="[^":#]*"' "$OUT/index.html" "$OUT/404.html" \
         | sed 's/.*="//; s/"$//; s|^/||; s/?.*$//' | grep -v '^\./$' | sort -u)
for season in winter spring summer autumn; do
  for file in "pictures/$season.webp" "pictures/$season-card.webp" "pictures/$season-panel.png" \
              screens/"$season"-{en,de}-p{1,2}.png; do
    [ -f "$OUT/$file" ] || { echo "frame missing: $file" >&2; missing=1; }
  done
done
for style in watercolour comic risograph woodcut childrens stainedglass collage poster chalk voxel botanical enamel; do
  [ -f "$OUT/pictures/styles/$style.webp" ] || { echo "style print missing: $style" >&2; missing=1; }
done
[ "$missing" -eq 0 ] || exit 1

echo "built _site for version $VERSION ($(du -sh "$OUT" | cut -f1))"

if [ "${1:-}" = "--serve" ]; then
  port="${2:-8000}"
  echo "serving on http://0.0.0.0:$port"
  cd "$OUT"
  # no-cache: the browser may keep files but must ask before reusing them,
  # so a rebuild shows up on a plain reload.
  exec python3 - "$port" <<'PY'
import http.server, sys

class Handler(http.server.SimpleHTTPRequestHandler):
    # Keep-alive: a page load reuses a few connections instead of opening one
    # per frame and picture.
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass

class Server(http.server.ThreadingHTTPServer):
    # socketserver's default backlog is 5. A browser opens more than that at
    # once, and macOS resets the rest: images and scripts then fail to load
    # at random.
    request_queue_size = 128
    daemon_threads = True

Server(("0.0.0.0", int(sys.argv[1])), Handler).serve_forever()
PY
fi
