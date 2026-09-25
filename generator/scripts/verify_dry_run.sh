#!/bin/sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

python3 -m foredogs_generator.main --config config.json --forecast-file samples/ha_forecast_sample.json --json --dry-run

test -f "$ROOT_DIR/output/foredogs_generation_status.json"
test -f "$ROOT_DIR/output/latest_activity_prompt.txt"
test -f "$ROOT_DIR/output/latest_image_prompt.txt"

echo "Dry run verification passed."
