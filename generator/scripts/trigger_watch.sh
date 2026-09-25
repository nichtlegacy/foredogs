#!/bin/sh
# Watch Home Assistant for a manual generation request and serve it.
#
# The generator host polls rather than Home Assistant pushing: Home Assistant
# has no SSH key for this machine, and a desktop firewall usually drops inbound
# connections, so an outbound poll is the path that needs no new credentials and
# no open ports. It also survives the machine being asleep — a press made while
# it was off is picked up on the next run instead of being lost.
#
# The input_button holds the timestamp of the last press as its state.
# Remembering the last one we acted on turns that into an idempotent trigger:
# pressing once runs once, and a restart does not re-run the last press.
#
# Both entity ids can be overridden, so the helpers can be named anything:
#   FOREDOGS_TRIGGER_ENTITY, FOREDOGS_STATUS_ENTITY
#
# Install with the scheduler unit for your platform:
#   macOS  scripts/macos/com.foredogs.trigger.plist
#   Linux  scripts/linux/foredogs-trigger.service + .timer
#
# Watch it work:
#   tail -f logs/trigger_watch.log
#
# POSIX sh on purpose, so the same script runs under bash, dash and zsh.

set -u

ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$ROOT" || exit 1

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/trigger_watch.log"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/foredogs"
mkdir -p "$STATE_DIR"
SEEN_FILE="$STATE_DIR/last_trigger"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"
}

HA_URL="${HA_URL:-http://homeassistant.local:8123}"
TOKEN_FILE="$HOME/.config/foredogs/ha_token"
HA_TOKEN="${FOREDOGS_HA_TOKEN:-}"
if [ -z "$HA_TOKEN" ] && [ -r "$TOKEN_FILE" ]; then
  HA_TOKEN=$(cat "$TOKEN_FILE")
fi

if [ -z "$HA_TOKEN" ]; then
  log "ERROR no HA token"
  exit 1
fi

BUTTON="${FOREDOGS_TRIGGER_ENTITY:-input_button.foredogs_generation_trigger}"
STATUS_ENTITY="${FOREDOGS_STATUS_ENTITY:-input_text.foredogs_generation_status}"

PYTHON=$(command -v python3 2>/dev/null || echo /usr/bin/python3)

PRESSED=$(curl -s -m 15 -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/states/$BUTTON" 2>/dev/null | "$PYTHON" -c '
import json, sys
try:
    state = json.load(sys.stdin).get("state", "")
except Exception:
    state = ""
# "unknown" means the button has never been pressed.
print("" if state in ("unknown", "unavailable") else state)
' 2>/dev/null)

if [ -z "$PRESSED" ]; then
  # Home Assistant unreachable, or the button was never pressed. Not worth
  # logging every two minutes.
  exit 0
fi

SEEN=""
if [ -r "$SEEN_FILE" ]; then
  SEEN=$(cat "$SEEN_FILE")
fi

if [ "$PRESSED" = "$SEEN" ]; then
  exit 0
fi

# First run on a fresh machine: adopt the current value rather than generating,
# so installing the watcher does not immediately spend an image.
if [ -z "$SEEN" ]; then
  printf '%s\n' "$PRESSED" >"$SEEN_FILE"
  log "first run, adopting current press $PRESSED without generating"
  exit 0
fi

log "=== trigger $PRESSED (was $SEEN) ==="

# Record it before starting: a crash mid-generation should not cause an endless
# retry loop on every poll.
printf '%s\n' "$PRESSED" >"$SEEN_FILE"

# Tell Home Assistant we picked it up, so the dashboard can show progress. The
# wording is German because that is what the kitchen panel displays.
notify() {
  curl -s -m 15 -X POST \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\":\"$STATUS_ENTITY\",\"value\":\"$1\"}" \
    "$HA_URL/api/services/input_text/set_value" >/dev/null 2>&1 || true
}

notify "running since $(date '+%H:%M')"

if "$ROOT/scripts/daily_run.sh" >>"$LOG" 2>&1; then
  log "generation + publish complete"
  notify "done $(date '+%H:%M')"
else
  rc=$?
  log "FAILED (exit $rc)"
  notify "failed $(date '+%H:%M')"
fi

# Always exit 0. A non-zero exit makes a scheduler throttle and eventually stop
# the unit, which would silently disable the button until it was reloaded by
# hand. The outcome is already in the log and in the Home Assistant status
# field, so the poll itself has no reason to report failure.
exit 0
