#!/usr/bin/env python3
"""Measure the display's real battery drain from Home Assistant history.

Answers the only question that matters for the refresh cadence: how much does
one wake cycle cost, and how much does the device burn just sitting in deep
sleep. Both are needed, because the two scale differently — cycles scale with
how often you refresh, sleep scales with wall-clock time — and on this board the
sleep current is large enough to dominate.

Charging periods are excluded automatically: the ADC reads nonsense while USB is
attached (values like -371%), and any rising stretch is a charge, not a drain.

    export HA_URL=http://homeassistant.local:8123
    export HA_TOKEN=...
    python3 tools/battery_report.py            # last 24 h
    python3 tools/battery_report.py --hours 72
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BATTERY_ENTITY = "sensor.e_ink_frame_battery_level"
REFRESH_ENTITY = "sensor.e1002_test_display_refreshes"

# 2000 mAh cell in the reTerminal E1002.
CELL_MAH = 2000.0


def fetch_history(url: str, token: str, entity: str, hours: int) -> list[dict]:
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/history/period/{start}"
        f"?filter_entity_id={entity}&minimal_response",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        print(f"could not read history for {entity}: {err}", file=sys.stderr)
        return []

    return payload[0] if payload else []


def numeric_points(states: list[dict]) -> list[tuple[datetime, float]]:
    """Timestamped readings, dropping anything not a plausible percentage."""
    points: list[tuple[datetime, float]] = []
    for state in states:
        raw = state.get("state")
        if raw in (None, "unknown", "unavailable"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        # While charging over USB the divider reads nonsense (seen: -371%).
        if not 0.0 < value <= 100.0:
            continue
        try:
            when = datetime.fromisoformat(state["last_changed"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        points.append((when, value))

    points.sort(key=lambda pair: pair[0])
    return points


def discharge_segments(
    points: list[tuple[datetime, float]],
    min_minutes: int = 20,
) -> list[tuple[datetime, datetime, float]]:
    """Split the series into stretches where the level only fell.

    A rise means the cell was charging, which ends the segment. Short segments
    are dropped: over a few minutes the ADC noise (±1%) swamps the real drain.
    """
    segments: list[tuple[datetime, datetime, float]] = []
    seg_start, seg_start_value = points[0]
    previous = points[0][1]

    for when, value in points[1:]:
        # +1.5% tolerates ADC jitter without treating it as a charge.
        if value > previous + 1.5:
            drop = seg_start_value - previous
            span = (when - seg_start).total_seconds() / 60
            if drop > 0 and span >= min_minutes:
                segments.append((seg_start, when, drop))
            seg_start, seg_start_value = when, value
        previous = value

    drop = seg_start_value - points[-1][1]
    span = (points[-1][0] - seg_start).total_seconds() / 60
    if drop > 0 and span >= min_minutes:
        segments.append((seg_start, points[-1][0], drop))

    return segments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="how far back to look")
    args = parser.parse_args()

    url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")
    if not (url and token):
        print("set HA_URL and HA_TOKEN", file=sys.stderr)
        return 1

    battery = numeric_points(fetch_history(url, token, BATTERY_ENTITY, args.hours))
    if len(battery) < 2:
        print("not enough battery history yet", file=sys.stderr)
        return 1

    refreshes = numeric_points(fetch_history(url, token, REFRESH_ENTITY, args.hours))

    print(f"=== battery report, last {args.hours} h ===")
    print(f"readings: {len(battery)}")
    print(f"from {battery[0][0].astimezone():%Y-%m-%d %H:%M}  {battery[0][1]:.1f}%")
    print(f"to   {battery[-1][0].astimezone():%Y-%m-%d %H:%M}  {battery[-1][1]:.1f}%")
    print()

    segments = discharge_segments(battery)
    if not segments:
        print("no clean discharge stretch found (still charging?)")
        return 0

    print("discharge stretches:")
    total_drop = 0.0
    total_hours = 0.0
    for start, end, drop in segments:
        hours = (end - start).total_seconds() / 3600
        total_drop += drop
        total_hours += hours
        print(
            f"  {start.astimezone():%d.%m %H:%M} -> {end.astimezone():%d.%m %H:%M}"
            f"  {hours:5.1f} h  -{drop:4.1f} %  ({drop / hours:.2f} %/h)"
        )

    rate = total_drop / total_hours
    mah_per_h = CELL_MAH * rate / 100
    print()
    print(f"average:      {rate:.2f} %/h  =  {mah_per_h:.1f} mAh/h")
    print(f"projected:    {100 / rate / 24:.1f} days from full")

    if refreshes:
        cycles = refreshes[-1][1] - refreshes[0][1]
        span_h = (refreshes[-1][0] - refreshes[0][0]).total_seconds() / 3600
        if cycles > 0 and span_h > 0:
            print()
            print(f"refresh cycles: {cycles:.0f} over {span_h:.1f} h "
                  f"({cycles / span_h:.1f}/h)")
            # Split the total between the cycles and the idle floor. A cycle is
            # roughly 35 s awake; everything else is sleep.
            cycle_h = cycles * 35 / 3600
            sleep_h = total_hours - cycle_h
            if sleep_h > 0:
                print(f"  time awake for refreshes: {cycle_h * 60:.0f} min "
                      f"of {total_hours:.1f} h")
                print("  -> most of the drain is the sleep floor, not the "
                      "refreshes" if cycle_h / total_hours < 0.05 else
                      "  -> refreshes are a meaningful share of the drain")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
