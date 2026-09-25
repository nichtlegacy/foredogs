#!/usr/bin/env python3
"""Render any kitchen dashboard page locally against live Home Assistant state.

Lets the layout be iterated without touching the device or Home Assistant.
Set HA_URL and HA_TOKEN to pull real data; without them it falls back to
representative mock values.

    python3 tools/preview_dashboard.py --out /tmp/dashboard.png
    python3 tools/preview_dashboard.py --mock --fast      # instant, no dithering
    python3 tools/preview_dashboard.py --page 2 --pages 3 # weather detail
    python3 tools/preview_dashboard.py --all --fast       # all three at once

Page 3 needs a photo source. Either point it at Immich:

    export IMMICH_URL=https://immich.example.com
    export IMMICH_API_KEY=...            # needs album.read + asset.read
    export IMMICH_ALBUM_ID=...           # or IMMICH_SHARE_KEY for a shared link
    python3 tools/preview_dashboard.py --page 3 --pages 3

or at a local folder with --photo-folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# The render modules import each other relatively (`from .dashboard_render
# import ...`) because inside Home Assistant they are a package. Importing the
# repo directly would run its `__init__.py`, which pulls in `homeassistant` and
# fails on a dev machine — so register an empty stand-in package pointing at the
# repo instead. The render code then runs byte-identically to the device path.
import importlib  # noqa: E402
import types  # noqa: E402

# The render modules live in the Home Assistant component directory.
_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "foredogs"
_PKG = "_foredogs_preview"

_shim = types.ModuleType(_PKG)
_shim.__path__ = [str(_COMPONENT)]  # type: ignore[attr-defined]
sys.modules[_PKG] = _shim

_render = importlib.import_module(f"{_PKG}.dashboard_render")
_languages = importlib.import_module(f"{_PKG}.languages")
_photos = importlib.import_module(f"{_PKG}.photo_source")

FORECAST_DAYS = _render.FORECAST_DAYS
FORECAST_DAYS_LONG = _render.FORECAST_DAYS_LONG
HOURLY_POINTS = _render.HOURLY_POINTS
DashboardData = _render.DashboardData
ForecastDay = _render.ForecastDay
HourPoint = _render.HourPoint
WasteEntry = _render.WasteEntry
fingerprint = _render.fingerprint
parse_waste_sensor = _render.parse_waste_sensor
render_dashboard = _render.render_dashboard
pick_photo = _photos.pick_photo
get_language = _languages.get_language
available_languages = _languages.available_languages


# Below 0.2mm/h nothing perceptible falls, so it should not claim "next rain".
RAIN_THRESHOLD_MM = 0.2
RAIN_LOOKAHEAD_H = 36

# Entity mapping for this specific installation.
ENTITY_WEATHER = "weather.forecast_home"
ENTITY_INSIDE_TEMP = "sensor.e1002_playground_temperature"
ENTITY_INSIDE_HUM = "sensor.e1002_playground_humidity"
# Home Assistant kept the entity ID from when the device was still called
# "e-ink-frame" and only updated the display name, so the battery percentage
# does NOT follow the e1002_* naming the other sensors use.
ENTITY_BATTERY = "sensor.e_ink_frame_battery_level"
ENTITY_BATTERY_VOLTAGE = "sensor.e1002_test_battery_voltage"
ENTITY_SUN_RISE = "sensor.sun_next_rising"
ENTITY_SUN_SET = "sensor.sun_next_setting"

# Waste comes from the calendar, not the sensors. If the
# waste_collection_schedule integration has more than one district configured,
# its sensors can end up bound to the wrong one and report dates that do not
# match the actual collection days. Reading the calendar sidesteps that, and it
# also picks up bin types that have no sensor at all.
#
# Replace this with your own calendar entity.
WASTE_CALENDAR = "calendar.waste_collection"

# Calendar summary -> (display label, colour key). Matched case-insensitively
# as a substring, so "Restabfallbehaelter" hits "restabfall".
WASTE_KINDS = [
    ("restabfall", "Restabfall", "rest"),
    ("papier", "Papier", "papier"),
    ("wertstoff", "Gelber Sack", "gelb"),
    ("bioabfall", "Bio", "bio"),
]

# Which bins to show, in the order they should be considered. Bio is tracked
# but left out of the display by default.
WASTE_SHOW = ("rest", "papier", "gelb")


class HAClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def _get(self, path: str) -> object | None:
        req = urllib.request.Request(
            f"{self.url}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            print(f"  ! {path}: {err}", file=sys.stderr)
            return None

    def _post(self, path: str, payload: dict) -> object | None:
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            print(f"  ! {path}: {err}", file=sys.stderr)
            return None

    def state(self, entity_id: str) -> dict | None:
        result = self._get(f"/api/states/{entity_id}")
        return result if isinstance(result, dict) else None

    def forecast(self, entity_id: str, kind: str = "daily") -> list[dict]:
        result = self._post(
            "/api/services/weather/get_forecasts?return_response",
            {"entity_id": entity_id, "type": kind},
        )
        if not isinstance(result, dict):
            return []
        response = result.get("service_response", result)
        if not isinstance(response, dict):
            return []
        for value in response.values():
            if isinstance(value, dict) and "forecast" in value:
                return value["forecast"]
        return []

    def calendar(self, entity_id: str, days: int = 30) -> list[dict]:
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=days)).isoformat()
        result = self._get(
            f"/api/calendars/{entity_id}?start={start}T00:00:00Z&end={end}T00:00:00Z"
        )
        return result if isinstance(result, list) else []


def _iso_to_local_hhmm(value: str) -> str:
    """'2026-07-27T03:42:54+00:00' -> '05:42' in local time."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    return parsed.astimezone().strftime("%H:%M")


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def collect_live(client: HAClient, language) -> DashboardData:
    print("Fetching Home Assistant state...")
    data = DashboardData(now=datetime.now(), language=language)

    weather = client.state(ENTITY_WEATHER)
    if weather:
        attrs = weather.get("attributes", {})
        data.outside_condition = str(weather.get("state", ""))
        data.outside_temp = _as_float(attrs.get("temperature"))
        data.outside_humidity = _as_float(attrs.get("humidity"))
        data.wind_speed = _as_float(attrs.get("wind_speed"))
        data.uv_index = _as_float(attrs.get("uv_index"))
        data.feels_like = _as_float(attrs.get("apparent_temperature"))
        print(f"  weather: {data.outside_condition} {data.outside_temp}")

    # HA publishes these as UTC ISO timestamps; the header wants local HH:MM.
    for entity, target in ((ENTITY_SUN_RISE, "sun_rise"), (ENTITY_SUN_SET, "sun_set")):
        state = client.state(entity)
        if state:
            setattr(data, target, _iso_to_local_hhmm(str(state.get("state", ""))))

    for entity, target in (
        (ENTITY_INSIDE_TEMP, "inside_temp"),
        (ENTITY_INSIDE_HUM, "inside_humidity"),
        (ENTITY_BATTERY, "battery"),
        (ENTITY_BATTERY_VOLTAGE, "battery_voltage"),
    ):
        state = client.state(entity)
        if state:
            setattr(data, target, _as_float(state.get("state")))

    print(f"  inside: {data.inside_temp} / {data.inside_humidity}%  battery {data.battery}")

    # --- waste, from the calendar ---
    today = date.today()
    soonest: dict[str, WasteEntry] = {}

    for event in client.calendar(WASTE_CALENDAR, days=60):
        summary = str(event.get("summary", "")).casefold()
        start = event.get("start", {})
        raw = str(start.get("date") or start.get("dateTime", ""))[:10]
        try:
            due = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if due < today:
            continue

        for needle, label, kind in WASTE_KINDS:
            if needle not in summary or kind not in WASTE_SHOW:
                continue
            # Events are not guaranteed sorted, so keep the earliest per bin.
            if kind not in soonest or due < soonest[kind].due:
                soonest[kind] = WasteEntry(label=label, kind=kind, due=due)
            break

    data.waste = sorted(soonest.values(), key=lambda entry: entry.due)
    for entry in data.waste:
        print(f"  waste {entry.label}: {entry.date_text(language)} ({entry.when_text(language)})")

    # --- forecast ---
    # The long list serves both pages: page 1 slices the first FORECAST_DAYS off
    # the front, page 2 draws the whole week.
    days = client.forecast(ENTITY_WEATHER)
    today = date.today()
    # Take two extra: today's entry is dropped below, so a bare slice would
    # leave the week one day short.
    for entry in days[: FORECAST_DAYS_LONG + 2]:
        try:
            when = datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        offset = (when.date() - today).days
        if offset < 1:
            continue  # today already sits in the header
        label = language.forecast_label(offset, when.date())
        data.forecast.append(
            ForecastDay(
                label=label,
                when=when.date(),
                offset=offset,
                condition=str(entry.get("condition", "")),
                temp_high=_as_float(entry.get("temperature")),
                temp_low=_as_float(entry.get("templow")),
                precipitation=_as_float(entry.get("precipitation")),
            )
        )
        if len(data.forecast) >= FORECAST_DAYS_LONG:
            break

    print(f"  forecast: {len(data.forecast)} days")

    # --- next rain, from the hourly forecast ---
    # met.no does not publish precipitation_probability, but it does give
    # per-hour precipitation, so "when does it next rain" is answerable even
    # though "how likely is rain" is not.
    hourly = client.forecast(ENTITY_WEATHER, "hourly")
    now = datetime.now().astimezone()

    # --- page 2's curve, from the same fetch ---
    for entry in hourly:
        try:
            when = datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        when = when.astimezone()
        # Keep the hour currently running; dropping it would start the curve up
        # to 59 minutes in the future.
        if when < now - timedelta(hours=1):
            continue
        data.hourly.append(
            HourPoint(
                when=when,
                temp=_as_float(entry.get("temperature")),
                precipitation=_as_float(entry.get("precipitation")),
                condition=str(entry.get("condition", "")),
            )
        )
        if len(data.hourly) >= HOURLY_POINTS:
            break

    print(f"  hourly: {len(data.hourly)} points")

    for entry in hourly:
        mm = _as_float(entry.get("precipitation")) or 0.0
        if mm < RAIN_THRESHOLD_MM:
            continue
        try:
            when = datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        when = when.astimezone()
        if when < now:
            continue
        delta_h = (when - now).total_seconds() / 3600
        if delta_h > RAIN_LOOKAHEAD_H:
            break
        # Anything within the next 12 hours reads better as a bare time or a
        # relative hour count: at 22:22, "Mo 00:00" is a confusing way to say
        # "in under two hours".
        pass

    return data


def _mock_forecast(today: date) -> list[ForecastDay]:
    """Seven days hung off the real date, so the weekday headings are correct
    whatever language the preview is rendered in.

    The conditions are fixed rather than random: they exercise the column
    fitting ("lightning-rainy" is the longest label), the blue low-temperature
    colour and the range bar's extremes, and a fixed set keeps two runs
    comparable.
    """
    shape = [
        ("rainy", 21.5, 13.6, 1.6),
        ("sunny", 26.6, 13.4, None),
        ("sunny", 31.3, 16.8, None),
        ("lightning-rainy", 24.0, 15.0, 8.4),
        ("cloudy", 19.0, 11.2, None),
        ("pouring", 16.4, 4.8, 12.7),
        ("partlycloudy", 22.0, 12.0, None),
    ]
    days = []
    for offset, (condition, high, low, mm) in enumerate(shape, start=1):
        when = today + timedelta(days=offset)
        days.append(
            ForecastDay(
                label="",
                condition=condition,
                temp_high=high,
                temp_low=low,
                precipitation=mm,
                when=when,
                offset=offset,
            )
        )
    return days


def collect_mock(language) -> DashboardData:
    today = date.today()
    now = datetime.now()

    # A day that dips overnight, warms into the afternoon and gets a burst of
    # rain around hour 8 — enough shape to show whether the curve, the bars and
    # the min/max callouts all land where they should.
    hourly: list[HourPoint] = []
    shape = [
        17.2, 16.4, 15.8, 15.1, 14.6, 14.2, 14.9, 16.8,
        19.4, 21.7, 23.6, 25.1, 26.2, 26.8, 26.4, 25.3,
        23.8, 22.1, 20.6, 19.4, 18.5, 17.9, 17.4, 17.0,
    ]
    rain = {6: 0.4, 7: 1.9, 8: 3.6, 9: 1.2, 10: 0.3, 19: 0.8}
    for offset, temp in enumerate(shape):
        hourly.append(
            HourPoint(
                when=now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset),
                temp=temp,
                precipitation=rain.get(offset),
                condition="rainy" if offset in rain else "partlycloudy",
            )
        )

    return DashboardData(
        now=now,
        language=language,
        outside_temp=18.7,
        outside_condition="partlycloudy",
        inside_temp=24.8,
        inside_humidity=59.7,
        wind_speed=20.2,
        battery=43.4,
        # Enough history for the runtime estimate to be worth printing.
        battery_days_left=23.4,
        outside_humidity=87.0,
        uv_index=0.2,
        battery_voltage=3.71,
        sun_rise="05:42",
        sun_set="21:38",
        temp_high=20.5,
        temp_low=14.5,
        rain_total=1.1,
        rain_window=language.rain_window(18, 20),
        daylight_hours="15h 42m",
        waste=[
            WasteEntry(label="Restabfall", kind="rest", due=today + timedelta(days=3)),
            WasteEntry(label="Papier", kind="papier", due=today + timedelta(days=16)),
            WasteEntry(label="Gelber Sack", kind="gelb", due=today + timedelta(days=25)),
        ],
        hourly=hourly,
        forecast=_mock_forecast(now.date()),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/dashboard.png", help="output PNG path")
    parser.add_argument("--image", help="path to the Foredogs image to composite")
    parser.add_argument("--mock", action="store_true", help="use mock data, skip HA")
    parser.add_argument(
        "--language",
        default="de",
        help=f"dashboard language ({', '.join(available_languages())})",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip Atkinson dithering (much faster, for layout work)",
    )
    parser.add_argument("--page", type=int, default=1, help="page to render (1-3)")
    parser.add_argument("--pages", type=int, default=1, help="total page count")
    parser.add_argument(
        "--all",
        action="store_true",
        help="render all three pages, one file per page (--out gets a -pN suffix)",
    )
    parser.add_argument(
        "--photo-folder",
        help="folder of images for page 3, instead of Immich",
    )
    parser.add_argument(
        "--urgent",
        action="store_true",
        help="force the first bin to tomorrow, to check the urgent styling",
    )
    args = parser.parse_args()

    url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")

    language = get_language(args.language)

    if args.mock or not (url and token):
        if not args.mock:
            print("HA_URL/HA_TOKEN unset — using mock data")
        data = collect_mock(language)
    else:
        data = collect_live(HAClient(url, token), language)

    if args.urgent and data.waste:
        data.waste[0].due = date.today() + timedelta(days=1)

    if args.image:
        data.image_path = Path(args.image)

    pages = [1, 2, 3] if args.all else [args.page]
    data.page_count = args.pages if not args.all else max(3, args.pages)

    out_base = Path(args.out)
    for page in pages:
        data.page = page
        if page == 3:
            _attach_photo(data, args.photo_folder)

        target = out_base
        if len(pages) > 1:
            target = out_base.with_name(f"{out_base.stem}-p{page}{out_base.suffix}")

        print(f"\npage {page} fingerprint: {fingerprint(data)}")
        written = render_dashboard(data, target, dither_photo=not args.fast)
        size = written.stat().st_size
        print(f"wrote {written} ({size / 1024:.1f} KB)")
        if size > 200_000:
            print("  ! large for ESPHome online_image; raise buffer_size accordingly")

    return 0


def _attach_photo(data: DashboardData, folder: str | None) -> None:
    """Resolve page 3's photo from --photo-folder or the IMMICH_* environment."""
    config: dict[str, object] = {}
    if folder:
        config["fallback_folder"] = folder
    else:
        config = {
            "url": os.environ.get("IMMICH_URL", ""),
            "api_key": os.environ.get("IMMICH_API_KEY", ""),
            "share_key": os.environ.get("IMMICH_SHARE_KEY", ""),
            "album_id": os.environ.get("IMMICH_ALBUM_ID", ""),
            "album_name": os.environ.get("IMMICH_ALBUM_NAME", ""),
            "fallback_folder": os.environ.get("IMMICH_FALLBACK_FOLDER", ""),
        }

    cache = Path("/tmp/foredogs_preview_cache")
    slot = data.now.strftime("%Y-%m-%d-") + str(data.now.hour // 3)
    data.photo, data.photo_error = pick_photo(config, cache, slot=slot)

    if data.photo is not None:
        print(f"  photo: {data.photo.path.name} ({data.photo.source_kind} {data.photo.source_name})")
    else:
        print(f"  photo: none — {data.photo_error}")


if __name__ == "__main__":
    raise SystemExit(main())
