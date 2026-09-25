#!/bin/sh
# Pull the reference photos and generation history out of a Home Assistant
# instance that still runs the custom component, so the macOS worker can take
# over without starting from an empty history.
#
# Only needed when migrating from the Home Assistant integration. A fresh
# install just puts its own photos into assets/input_images/ instead.
#
#   HA_SSH_HOST=homeassistant ./scripts/import_from_home_assistant.sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
IMPORT_DIR="$ROOT_DIR/imported"
ASSET_DIR="$ROOT_DIR/assets/input_images"

# SSH alias or user@host of the Home Assistant machine.
HA_SSH_HOST="${HA_SSH_HOST:-homeassistant}"
# Where the custom component keeps its data and published images.
HA_DATA_DIR="${HA_DATA_DIR:-/config/foredogs_data}"
HA_WWW_DIR="${HA_WWW_DIR:-/config/www/daily_foredogs}"

mkdir -p "$IMPORT_DIR" "$ASSET_DIR" "$ROOT_DIR/state" "$ROOT_DIR/output"

# Every reference photo, whatever the dogs are called.
scp "$HA_SSH_HOST:$HA_DATA_DIR/input_images/*" "$IMPORT_DIR/"

scp "$HA_SSH_HOST:$HA_WWW_DIR/foredogs_original.png" "$IMPORT_DIR/"
scp "$HA_SSH_HOST:$HA_WWW_DIR/foredogs_optimized.png" "$IMPORT_DIR/"
scp "$HA_SSH_HOST:$HA_DATA_DIR/foredogs_prompt_history.txt" "$IMPORT_DIR/"
scp "$HA_SSH_HOST:$HA_DATA_DIR/foredogs_style_history.txt" "$IMPORT_DIR/"

# The photos are the only part the generator reads on every run.
for image in "$IMPORT_DIR"/*.jpg "$IMPORT_DIR"/*.jpeg "$IMPORT_DIR"/*.png; do
  [[ -e "$image" ]] || continue
  case "$(basename -- "$image")" in
    foredogs_original.png|foredogs_optimized.png) continue ;;
  esac
  cp "$image" "$ASSET_DIR/"
done

echo "Imported Home Assistant foredogs assets into:"
echo "  $IMPORT_DIR"
echo "Copied reference images into:"
echo "  $ASSET_DIR"
