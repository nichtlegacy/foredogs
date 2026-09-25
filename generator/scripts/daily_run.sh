#!/bin/sh
# Generate today's image and publish it to Home Assistant.
#
# Driven by a scheduler: launchd on macOS (scripts/macos/) or a systemd timer on
# Linux (scripts/linux/). It stays a shell script rather than being called
# directly from the scheduler so the whole sequence — generate, publish, nudge —
# can be run by hand with one command.
#
#   scripts/daily_run.sh                 generate and publish
#   scripts/daily_run.sh --dry-run       prompts only, no image, no upload
#   scripts/daily_run.sh --publish-only  re-upload the last image
#
# Exit codes: 0 published, 1 generation failed, 2 upload failed.
#
# POSIX sh on purpose, so the same script runs under bash, dash and zsh.

set -u

ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$ROOT_DIR" || exit 1

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily_run.log"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

DRY_RUN=0
PUBLISH_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --publish-only) PUBLISH_ONLY=1 ;;
    *) log "unknown argument: $arg"; exit 1 ;;
  esac
done

# A scheduler starts with a minimal PATH that lacks Homebrew and /usr/local, so
# codex would not be found. Append rather than prepend: prepending Homebrew
# shadowed the python3 that actually has Pillow installed (the python.org
# framework build) with Homebrew's, which does not.
PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

# Pick an interpreter that can actually import Pillow, rather than whichever
# python3 happens to come first. The macOS framework build is tried first
# because that is where Pillow usually lands there; on Linux the PATH entry
# resolves first in practice.
PYTHON=""
for candidate in \
  "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
  "$(command -v python3 2>/dev/null)" \
  "/opt/homebrew/bin/python3" \
  "/usr/local/bin/python3" \
  "/usr/bin/python3"
do
  [ -n "$candidate" ] || continue
  [ -x "$candidate" ] || continue
  if "$candidate" -c "import PIL" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  log "no python3 with Pillow found; run: python3 -m pip install -r requirements.txt"
  exit 1
fi
log "python: $PYTHON"

command -v codex >/dev/null 2>&1 || { log "codex CLI not found in PATH"; exit 1; }

if [ "$PUBLISH_ONLY" -eq 0 ]; then
  log "=== generation start ==="
  if [ "$DRY_RUN" -eq 1 ]; then
    "$PYTHON" -m foredogs_generator.main --config config.json --json --dry-run >>"$LOG" 2>&1
  else
    "$PYTHON" -m foredogs_generator.main --config config.json --json >>"$LOG" 2>&1
  fi
  rc=$?
  if [ "$rc" -ne 0 ]; then
    log "generation FAILED (exit $rc)"
    exit 1
  fi
  log "generation ok"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "dry run: skipping publish"
  exit 0
fi

# Read the Immich key here as well, so a manual run archives too rather than
# silently skipping it.
if [ -z "${FOREDOGS_IMMICH_KEY:-}" ] && [ -r "$HOME/.config/foredogs/immich_key" ]; then
  FOREDOGS_IMMICH_KEY=$(cat "$HOME/.config/foredogs/immich_key")
  export FOREDOGS_IMMICH_KEY
fi

# Manual runs do not pass through the scheduler unit, so load the Home Assistant
# token here as well. This keeps --publish-only from uploading successfully but
# skipping the dashboard refresh.
if [ -z "${FOREDOGS_HA_TOKEN:-}" ] && [ -r "$HOME/.config/foredogs/ha_token" ]; then
  FOREDOGS_HA_TOKEN=$(cat "$HOME/.config/foredogs/ha_token")
  export FOREDOGS_HA_TOKEN
fi

log "=== publish start ==="
"$PYTHON" scripts/publish_to_ha.py >>"$LOG" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  log "publish FAILED (exit $rc)"
  exit 2
fi

log "published"
exit 0
