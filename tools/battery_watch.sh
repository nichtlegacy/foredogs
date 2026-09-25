#!/bin/zsh
# Append a battery reading to a log, once per run.
#
# Runs from launchd so the measurement survives closing the terminal — a
# Claude Code cron job only lives as long as that session does, which is no use
# for a multi-day discharge measurement.
#
# Install:
#   cp tools/com.foredogs.batterywatch.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.foredogs.batterywatch.plist
#
# Read:
#   tail -20 ~/.local/state/foredogs/battery.tsv
#   python3 tools/battery_report.py          # the analysis

set -uo pipefail

STATE_DIR="$HOME/.local/state/foredogs"
LOG="$STATE_DIR/battery.tsv"
mkdir -p "$STATE_DIR"

HA_URL="${HA_URL:-http://homeassistant.local:8123}"
# The panel's address, for the reachability probe below. Override per install.
DEVICE_HOST="${FOREDOGS_DEVICE_HOST:-e1002-playground.local}"
TOKEN_FILE="$HOME/.config/foredogs/ha_token"
HA_TOKEN="${FOREDOGS_HA_TOKEN:-$( [[ -r "$TOKEN_FILE" ]] && cat "$TOKEN_FILE" )}"

if [[ -z "$HA_TOKEN" ]]; then
  print -r -- "$(date '+%Y-%m-%dT%H:%M:%S')\tERROR\tno token" >>"$LOG"
  exit 1
fi

fetch() {
  curl -s -m 20 -H "Authorization: Bearer $HA_TOKEN" \
    "$HA_URL/api/states/$1" 2>/dev/null
}

read_state() {
  fetch "$1" | /usr/bin/python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("state", ""))
except Exception:
    print("")
' 2>/dev/null
}

BATTERY=$(read_state "sensor.e_ink_frame_battery_level")
REFRESHES=$(read_state "sensor.e1002_test_display_refreshes")
AWAKE=$(read_state "input_boolean.e1002_stay_awake")

# Reachability says whether the device is mid-cycle or in deep sleep, which is
# the difference between "measuring as designed" and "the sleep bug is back".
if /sbin/ping -c 1 -t 2 "$DEVICE_HOST" >/dev/null 2>&1; then
  REACHABLE="awake"
else
  REACHABLE="asleep"
fi

# Write a header once so the file is readable on its own.
if [[ ! -s "$LOG" ]]; then
  print -r -- "timestamp\tbattery_pct\trefreshes\tstay_awake\treachable" >"$LOG"
fi

print -r -- "$(date '+%Y-%m-%dT%H:%M:%S')\t${BATTERY:-?}\t${REFRESHES:-?}\t${AWAKE:-?}\t$REACHABLE" >>"$LOG"
