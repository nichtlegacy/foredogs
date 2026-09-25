"""Home Assistant glue for the kitchen dashboard.

Reads live HA state, hands it to the renderer, and writes the composite PNG the
device fetches. Deliberately separate from `foredogs.py`: that module talks to
Gemini and costs money per call, this one is free and runs every 30 minutes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .image_source import pick_image, prune_old
from .dashboard_render import (
    FORECAST_DAYS_LONG,
    HOURLY_POINTS,
    DashboardData,
    ForecastDay,
    HourPoint,
    PhotoInfo,
    WasteEntry,
    fingerprint,
    parse_waste_sensor,
    render_dashboard,
)
from .languages import Language, get_language
from .photo_source import pick_photo

_LOGGER = logging.getLogger(__name__)


OUTPUT_NAME = "dashboard.png"
FINGERPRINT_NAME = "dashboard_fingerprint.txt"

# Below 0.2 mm/h nothing perceptible falls, so it should not claim "next rain".
RAIN_THRESHOLD_MM = 0.2
RAIN_LOOKAHEAD_H = 36

# How long page 3 keeps the same photo. Every wake would mean a download and a
# forced panel refresh; three hours means roughly five photos a day, each of
# which gets long enough on screen to actually be looked at.
PHOTO_ROTATE_HOURS = 3


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _state_float(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    return _as_float(state.state)


# Last known values for the display's own sensors, so a render that happens
# while the device is in deep sleep does not draw dashes. The device is
# unreachable for all but a minute of each day, and after an HA restart its
# entities read "unavailable" until it next wakes — but yesterday morning's
# battery reading is still far more useful than "--.-".
#
# Entries carry a timestamp and expire. Without one the cache happily served a
# value for as long as Home Assistant stayed up: the battery sensor stopped
# reporting on 31.07 and the panel kept drawing a confident 28% for three days,
# which is worse than an honest dash because nobody goes looking for a fault
# they cannot see.
_LAST_KNOWN: dict[str, tuple[float, datetime]] = {}

# The device reports once a day when it wakes at 05:45, and a render can happen
# shortly before that — so a legitimately fresh value is already ~23 h old.
# 30 h accepts a normal daily cycle with slack, while a sensor that has been
# silent for two days falls through to None and the gauge shows "—%".
_MAX_STALE = timedelta(hours=30)


async def _state_float_sticky(hass: HomeAssistant, entity_id: str) -> float | None:
    """Like _state_float, but falls back to the last real reading.

    Checked in order: live state, this process's memory, then the recorder. The
    recorder lookup matters after an HA restart, when the in-memory cache is
    empty and the sleeping device has not reported in yet.
    """
    now = dt_util.utcnow()

    value = _state_float(hass, entity_id)
    if value is not None:
        _LAST_KNOWN[entity_id] = (value, now)
        return value

    cached = _LAST_KNOWN.get(entity_id)
    if cached is not None:
        remembered, seen_at = cached
        age = now - seen_at
        if age <= _MAX_STALE:
            _LOGGER.debug(
                "%s unavailable; using cached %.1f (%.1f h old)",
                entity_id, remembered, age.total_seconds() / 3600,
            )
            return remembered
        # Drop it rather than keep serving it: a value this old means the sensor
        # itself has stopped, not that the device happens to be asleep.
        _LOGGER.warning(
            "%s has not reported for %.1f h; dropping stale value %.1f",
            entity_id, age.total_seconds() / 3600, remembered,
        )
        _LAST_KNOWN.pop(entity_id, None)

    historic = await _recorder_last_value(hass, entity_id)
    if historic is not None:
        _LAST_KNOWN[entity_id] = (historic, now)
        _LOGGER.debug("%s unavailable; using recorder value %.1f", entity_id, historic)
    else:
        _LOGGER.warning("%s has no usable value; drawing it as unknown", entity_id)
    return historic


async def _recorder_last_value(hass: HomeAssistant, entity_id: str) -> float | None:
    """Most recent numeric state from the recorder, within _MAX_STALE.

    The window used to be 12 h, which is shorter than the device's once-a-day
    reporting interval — so after a Home Assistant restart a perfectly good
    reading from that morning was thrown away as too old.
    """
    try:
        from homeassistant.components.recorder import get_instance, history
    except ImportError:
        return None

    start = dt_util.utcnow() - _MAX_STALE

    def _query() -> float | None:
        states = history.state_changes_during_period(
            hass,
            start,
            dt_util.utcnow(),
            entity_id,
            include_start_time_state=True,
            no_attributes=True,
        ).get(entity_id, [])
        for state in reversed(states):
            value = _as_float(state.state)
            if value is not None:
                return value
        return None

    try:
        return await get_instance(hass).async_add_executor_job(_query)
    except Exception:  # noqa: BLE001 - no recorder, or it is busy; not fatal
        _LOGGER.debug("Recorder lookup failed for %s", entity_id, exc_info=True)
        return None


# --- battery runtime estimate ------------------------------------------------
#
# The panel reports once a day, so a forecast needs weeks of history — longer
# than the recorder keeps states (10 days by default). Long-term statistics are
# hourly, survive purging, and go back as far as the sensor has existed, which
# is the only source that can carry this.

# The panel reports once a day, so a run is measured in wakes, not hours. Two
# days apart with a 2 % fall means roughly 7 % of real drop at the observed
# 3.5 %/day, against ADC noise of about +/-1 % on each end: a rate error near
# 14 %, or +/-2 days on a two-week estimate. That is a "~" number's worth of
# accuracy.
#
# One day is not enough. The same noise on a single 3.5 % step is a 29 % rate
# error, which turns sixteen days into eleven or twenty-two while looking just
# as confident.
_ESTIMATE_MIN_SPAN_DAYS = 2.0
_ESTIMATE_MIN_DROP = 2.0
# Beyond this the number stops being information and starts being decoration.
_ESTIMATE_MAX_DAYS = 99.0
# A rise larger than this means the cell was charged, not that the ADC wobbled.
_ESTIMATE_CHARGE_TOLERANCE = 1.5
# Older readings describe a different regime: other firmware, other refresh
# cadence, a different season.
_ESTIMATE_WINDOW = timedelta(days=30)


def estimate_days_left(
    points: list[tuple[datetime, float]],
    level: float | None,
) -> float | None:
    """Days until empty at the rate of the current discharge run.

    Returns None rather than a guess whenever the data cannot support one. That
    is the whole point: a confident wrong number on this panel is what let a
    draining battery go unnoticed for nine days, and a forecast is exactly the
    kind of figure nobody double-checks.

    `points` must be oldest-first and already filtered to plausible percentages.
    """
    if level is None or level <= 0 or len(points) < 2:
        return None

    # Only the stretch since the last charge describes the current cycle.
    run_start = 0
    for index in range(1, len(points)):
        if points[index][1] > points[index - 1][1] + _ESTIMATE_CHARGE_TOLERANCE:
            run_start = index
    run = points[run_start:]
    if len(run) < 2:
        return None

    span_days = (run[-1][0] - run[0][0]).total_seconds() / 86400.0
    drop = run[0][1] - run[-1][1]
    if span_days < _ESTIMATE_MIN_SPAN_DAYS or drop < _ESTIMATE_MIN_DROP:
        return None

    per_day = drop / span_days
    if per_day <= 0:
        return None
    return min(level / per_day, _ESTIMATE_MAX_DAYS)


async def _battery_points(hass: HomeAssistant, entity_id: str) -> list[tuple[datetime, float]]:
    """Hourly means for the battery sensor, from long-term statistics."""
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
    except ImportError:
        return []

    start = dt_util.utcnow() - _ESTIMATE_WINDOW

    def _query() -> list[tuple[datetime, float]]:
        rows = statistics_during_period(
            hass,
            start,
            None,
            {entity_id},
            "hour",
            None,
            {"mean"},
        ).get(entity_id, [])

        points: list[tuple[datetime, float]] = []
        for row in rows:
            mean = row.get("mean")
            if mean is None:
                continue
            # While charging over USB the divider reads nonsense; -371% has been
            # seen. Anything outside a real percentage is not a measurement.
            if not 0.0 < float(mean) <= 100.0:
                continue
            when = row.get("start")
            if isinstance(when, (int, float)):
                when = datetime.fromtimestamp(when, timezone.utc)
            if not isinstance(when, datetime):
                continue
            points.append((when, float(mean)))

        points.sort(key=lambda pair: pair[0])
        return points

    try:
        return await get_instance(hass).async_add_executor_job(_query)
    except Exception:  # noqa: BLE001 - no recorder, or busy; the gauge still draws
        _LOGGER.debug("Battery statistics lookup failed for %s", entity_id, exc_info=True)
        return []


async def battery_days_left(
    hass: HomeAssistant,
    entity_id: str | None,
    level: float | None,
) -> float | None:
    """Runtime estimate for the panel battery, or None when unknowable."""
    if not entity_id or level is None:
        return None
    points = await _battery_points(hass, entity_id)
    estimate = estimate_days_left(points, level)
    if estimate is None:
        _LOGGER.debug(
            "No battery estimate yet: %s usable points in the last %s days",
            len(points),
            _ESTIMATE_WINDOW.days,
        )
    return estimate


async def collect_dashboard_data(
    hass: HomeAssistant,
    weather_entity: str,
    inside_temp_entity: str | None,
    inside_humidity_entity: str | None,
    battery_entity: str | None,
    waste_sensors: list[dict],
    waste_calendars: list[dict],
    image_path: Path | None,
    page: int = 1,
    page_count: int = 1,
    photo: dict | None = None,
    cache_dir: Path | None = None,
    language: str | None = None,
) -> DashboardData:
    """Assemble everything the renderer needs from current HA state.

    Only what the requested page actually draws is fetched: page 1 never needs
    the hourly forecast, and page 3 must not download a photo on every render of
    the other two.
    """
    # Local time, so the header clock matches the wall clock rather than UTC.
    data = DashboardData(
        now=dt_util.now(),
        page=page,
        page_count=page_count,
        language=get_language(language),
    )

    weather = hass.states.get(weather_entity)
    if weather is not None:
        attrs = weather.attributes
        data.outside_condition = weather.state
        data.outside_temp = _as_float(attrs.get("temperature"))
        data.outside_humidity = _as_float(attrs.get("humidity"))
        data.wind_speed = _as_float(attrs.get("wind_speed"))
        data.uv_index = _as_float(attrs.get("uv_index"))
        data.feels_like = _as_float(attrs.get("apparent_temperature"))

    # Sticky: the display sleeps for most of every cycle, so its own sensors are
    # usually "unavailable" at render time. A slightly stale reading beats "--".
    if inside_temp_entity:
        data.inside_temp = await _state_float_sticky(hass, inside_temp_entity)
    if inside_humidity_entity:
        data.inside_humidity = await _state_float_sticky(hass, inside_humidity_entity)
    if battery_entity:
        data.battery = await _state_float_sticky(hass, battery_entity)
        data.battery_days_left = await battery_days_left(hass, battery_entity, data.battery)

    # --- waste from sensors ---
    for spec in waste_sensors:
        entity_id = spec["entity_id"]
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Waste sensor %s unavailable, skipping", entity_id)
            continue
        entry = parse_waste_sensor(
            state.state,
            dict(state.attributes),
            spec.get("label", entity_id),
            spec.get("kind", "rest"),
        )
        if entry is not None:
            data.waste.append(entry)

    # --- waste from calendars ---
    # Optional alternative to the sensors, for bins that only exist as calendar
    # events. Group by calendar so one fetch serves every bin configured
    # against it.
    calendars: dict[str, list[dict]] = {}
    for spec in waste_calendars:
        calendars.setdefault(spec["entity_id"], []).append(spec)

    for entity_id, specs in calendars.items():
        data.waste.extend(await _calendar_pickups(hass, entity_id, specs))

    # Nearest pickup first; the sidebar shows only as many as fit. A bin
    # configured through both a sensor and a calendar keeps the earlier date.
    by_kind: dict[str, WasteEntry] = {}
    for entry in data.waste:
        existing = by_kind.get(entry.kind)
        if existing is None or entry.due < existing.due:
            by_kind[entry.kind] = entry
    data.waste = sorted(by_kind.values(), key=lambda entry: entry.due)

    data.forecast = await _collect_forecast(hass, weather_entity, data.language)

    # Today's own high/low, from the daily forecast. The header shows these
    # rather than the current reading: rendered once in the morning, "what will
    # today be" is the useful number and "what is it right now" is not.
    daily = await _daily_forecast(hass, weather_entity)
    if daily:
        today = dt_util.now().date()
        for entry in daily:
            try:
                when = datetime.fromisoformat(
                    str(entry.get("datetime", "")).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if dt_util.as_local(when).date() != today:
                continue
            data.temp_high = _as_float(entry.get("temperature"))
            data.temp_low = _as_float(entry.get("templow"))
            # The daily entry carries the day's condition, which is a better
            # summary than whatever it happens to be doing at 05:30.
            if entry.get("condition"):
                data.outside_condition = str(entry["condition"])
            break

    # One hourly fetch feeds both the header's rain summary and page 2's curve.
    hours = await _hourly_forecast(hass, weather_entity)
    _collect_today(hours, data)
    if page == 2:
        data.hourly = _collect_hourly(hours)
    _collect_sun_times(hass, data)
    data.image_path = image_path

    if page == 3:
        data.photo, data.photo_error = await hass.async_add_executor_job(
            _pick_photo_sync,
            photo or {},
            cache_dir,
            data.now,
        )

    return data


def _pick_photo_sync(
    photo: dict,
    cache_dir: Path | None,
    now: datetime,
) -> tuple[PhotoInfo | None, str]:
    """Executor wrapper — the Immich fetch and file write are blocking."""
    if cache_dir is None:
        return None, "PHOTO_NO_CACHE_DIR"
    # Rotation slot. Photos change every PHOTO_ROTATE_HOURS rather than every
    # render, so a wake that lands in the same slot finds the same photo and the
    # fingerprint check can still skip the panel refresh.
    hours = max(1, int(photo.get("rotate_hours", PHOTO_ROTATE_HOURS)))
    slot = f"{now.strftime('%Y-%m-%d')}-{now.hour // hours}"
    return pick_photo(photo, cache_dir, slot=slot)


def _collect_sun_times(hass: HomeAssistant, data: DashboardData) -> None:
    """Sunrise, sunset and length of day, as local HH:MM.

    The sun integration exposes only the *next* event, so after sunrise
    sensor.sun_next_rising already points at tomorrow. Rendering happens before
    dawn, so today's pair is normally what those sensors hold — but the length of
    day is computed from the raw timestamps rather than the formatted strings, so
    a render later in the day still produces a sane figure.
    """
    times: dict[str, datetime] = {}

    for entity_id, target in (
        ("sensor.sun_next_rising", "sun_rise"),
        ("sensor.sun_next_setting", "sun_set"),
    ):
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            continue
        try:
            parsed = datetime.fromisoformat(state.state.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        local = dt_util.as_local(parsed)
        times[target] = local
        setattr(data, target, local.strftime("%H:%M"))

    rise, dusk = times.get("sun_rise"), times.get("sun_set")
    if rise and dusk:
        # Same-day difference: if sunset is listed before sunrise (i.e. we are
        # between them), the sunset belongs to today and the sunrise to tomorrow.
        span = dusk - rise
        if span.total_seconds() < 0:
            span += timedelta(days=1)
        hours, minutes = divmod(int(span.total_seconds() // 60), 60)
        if 0 < hours < 24:
            data.daylight_hours = f"{hours}h {minutes:02d}m"


async def _forecast_entries(
    hass: HomeAssistant,
    weather_entity: str,
    kind: str,
) -> list[dict]:
    """Raw forecast entries of the given kind, or [] if the entity has none.

    One implementation for both "daily" and "hourly": the service call is the
    expensive part, and the unwrapping is identical either way.
    """
    try:
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_entity, "type": kind},
            blocking=True,
            return_response=True,
        )
    except Exception:  # noqa: BLE001 - a missing forecast is not fatal
        _LOGGER.debug("No %s forecast for %s", kind, weather_entity, exc_info=True)
        return []

    if not isinstance(response, dict):
        return []

    for value in response.values():
        if isinstance(value, dict) and "forecast" in value:
            return list(value["forecast"])
    return []


async def _hourly_forecast(hass: HomeAssistant, weather_entity: str) -> list[dict]:
    """Hourly entries, shared by the header's rain summary and page 2's curve."""
    return await _forecast_entries(hass, weather_entity, "hourly")


async def _daily_forecast(hass: HomeAssistant, weather_entity: str) -> list[dict]:
    """Daily entries, for today's high/low and the forecast bands."""
    return await _forecast_entries(hass, weather_entity, "daily")


def _collect_hourly(hours: list[dict]) -> list[HourPoint]:
    """The next HOURLY_POINTS hours, for the page 2 curve."""
    now = dt_util.now()
    points: list[HourPoint] = []

    for entry in hours:
        try:
            when = datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        when = dt_util.as_local(when)
        # Keep the hour that is currently running: dropping it would start the
        # curve up to 59 minutes in the future.
        if when < now - timedelta(hours=1):
            continue
        points.append(
            HourPoint(
                when=when,
                temp=_as_float(entry.get("temperature")),
                precipitation=_as_float(entry.get("precipitation")),
                condition=str(entry.get("condition", "")),
            )
        )
        if len(points) >= HOURLY_POINTS:
            break

    return points


def _collect_today(hours: list[dict], data: DashboardData) -> None:
    """Summarise today's rain from the hourly forecast.

    Replaces the old "next rain in 1h" countdown, which was only meaningful on a
    panel refreshed every half hour. Rendered once in the morning, what matters
    is how much rain the day holds and roughly when — both still true at 20:00.
    """
    today = dt_util.now().date()
    wet: list[tuple[int, float]] = []

    for entry in hours:
        mm = _as_float(entry.get("precipitation")) or 0.0
        try:
            when = datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        when = dt_util.as_local(when)
        if when.date() != today:
            continue
        if mm >= RAIN_THRESHOLD_MM:
            wet.append((when.hour, mm))

    if not wet:
        return

    data.rain_total = sum(mm for _, mm in wet)

    # A contiguous-ish window reads better than a list of hours. Showing just
    # first-to-last is a deliberate simplification: two separate showers become
    # one wider window, which is the right answer to "when do I need a coat".
    first, last = wet[0][0], wet[-1][0]
    data.rain_window = data.language.rain_window(first, last + 1)


async def _calendar_pickups(
    hass: HomeAssistant,
    entity_id: str,
    specs: list[dict],
) -> list[WasteEntry]:
    """Return the next pickup for each configured bin in one calendar.

    One fetch serves every bin: a single waste calendar typically carries all
    of them (Restabfall, Papier, Wertstoff, Bio), distinguished only by the
    event summary.
    """
    today = date.today()
    try:
        response = await hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": entity_id,
                "start_date_time": datetime.combine(today, datetime.min.time()).isoformat(),
                "end_date_time": (
                    datetime.combine(today + timedelta(days=60), datetime.min.time())
                ).isoformat(),
            },
            blocking=True,
            return_response=True,
        )
    except Exception:  # noqa: BLE001 - a broken calendar must not kill the render
        _LOGGER.warning("Could not read calendar %s", entity_id, exc_info=True)
        return []

    if not isinstance(response, dict):
        return []

    events: list[dict] = []
    for value in response.values():
        if isinstance(value, dict):
            events.extend(value.get("events", []))

    soonest: dict[str, WasteEntry] = {}
    for event in events:
        summary = str(event.get("summary", "")).casefold()
        raw = str(event.get("start", ""))[:10]
        try:
            due = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if due < today:
            continue

        for spec in specs:
            needle = str(spec.get("match", "")).casefold()
            if needle and needle not in summary:
                continue
            kind = spec.get("kind", "rest")
            # Events are not guaranteed sorted, so keep the earliest per bin.
            if kind not in soonest or due < soonest[kind].due:
                soonest[kind] = WasteEntry(
                    label=spec.get("label", kind.title()),
                    kind=kind,
                    due=due,
                )
            break

    for entry in soonest.values():
        _LOGGER.debug("Waste %s: %s", entry.label, entry.due)

    return list(soonest.values())


async def _collect_forecast(
    hass: HomeAssistant, weather_entity: str, language: Language
) -> list[ForecastDay]:
    """Pull the daily forecast and keep the next FORECAST_DAYS_LONG days.

    One list serves both pages: page 1's band slices the first four days off the
    front, page 2 uses the whole week. Fetching the longer list unconditionally
    costs nothing — the weather integration has already computed it.

    This is the reason the dashboard is rendered here instead of on the device:
    ESPHome can import scalar HA states and attributes, but not the forecast
    *list*, so the band is only reachable from this side.
    """
    try:
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_entity, "type": "daily"},
            blocking=True,
            return_response=True,
        )
    except Exception:  # noqa: BLE001 - render a dashboard without the band rather than none
        _LOGGER.warning("Could not read forecast for %s", weather_entity, exc_info=True)
        return []

    if not isinstance(response, dict):
        return []

    raw_days: list[dict] = []
    for value in response.values():
        if isinstance(value, dict) and "forecast" in value:
            raw_days = value["forecast"]
            break

    today = date.today()
    days: list[ForecastDay] = []
    for entry in raw_days:
        try:
            when = datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        offset = (when.date() - today).days
        if offset < 1:
            continue  # today is already in the header
        days.append(
            ForecastDay(
                # Kept for callers that render without a language, but the
                # renderer prefers `when`/`offset` below.
                label=language.forecast_label(offset, when.date()),
                when=when.date(),
                offset=offset,
                condition=str(entry.get("condition", "")),
                temp_high=_as_float(entry.get("temperature")),
                temp_low=_as_float(entry.get("templow")),
                precipitation=_as_float(entry.get("precipitation")),
            )
        )
        if len(days) >= FORECAST_DAYS_LONG:
            break

    return days


async def render_kitchen_dashboard(
    hass: HomeAssistant,
    weather_entity: str,
    inside_temp_entity: str | None,
    inside_humidity_entity: str | None,
    battery_entity: str | None,
    waste_sensors: list[dict],
    waste_calendars: list[dict],
    source_image: str | None,
    dither_photo: bool = True,
    page: int = 1,
    page_count: int = 1,
    photo: dict | None = None,
    image_dir: str | None = None,
    keep_days: int = 0,
    language: str | None = None,
) -> tuple[Path, str, bool]:
    """Render the requested dashboard page and report whether it changed.

    Returns:
        (output_path, fingerprint, changed)

        `changed` is what makes the 30-minute cadence affordable: the device
        asks before spending a 20-second panel refresh, and most wakeups are
        genuinely identical to the last one. A page change always counts as a
        change, because the page number is part of the fingerprint.
    """
    config_path = Path(hass.config.path())
    static_dir = config_path / "www" / "daily_foredogs"
    static_dir.mkdir(parents=True, exist_ok=True)

    # Which picture to draw. `image_dir` is the normal case: a folder the
    # generator drops images into, with a fallback chain so a day without a
    # fresh generation still shows something. `source_image` remains supported
    # as the single-file case and as the last link in that chain.
    single: Path | None = None
    if source_image:
        candidate = Path(source_image)
        if not candidate.is_absolute():
            candidate = static_dir / candidate
        single = candidate
        if not candidate.exists():
            _LOGGER.debug("Configured source image %s does not exist", candidate)

    image_path: Path | None = single if (single and single.exists()) else None

    # Read the condition before picking, so a stock of images can be filtered by
    # what the weather is actually doing rather than what was forecast when the
    # picture was generated.
    weather_state = hass.states.get(weather_entity)
    weather_condition = weather_state.state if weather_state is not None else ""

    if image_dir:
        directory = Path(image_dir)
        if not directory.is_absolute():
            directory = static_dir / directory

        chosen, reason = await hass.async_add_executor_job(
            pick_image,
            directory,
            dt_util.now().date(),
            weather_condition,
            single,
        )
        if chosen is not None:
            image_path = chosen
            _LOGGER.info("Image: %s (%s)", chosen.name, reason)
        else:
            _LOGGER.warning("No image found in %s; rendering placeholder", directory)

        if keep_days:
            await hass.async_add_executor_job(
                prune_old,
                directory,
                dt_util.now().date(),
                keep_days,
            )
    elif image_path is None:
        _LOGGER.warning("Source image %s not found; rendering placeholder", single)

    data = await collect_dashboard_data(
        hass,
        weather_entity=weather_entity,
        inside_temp_entity=inside_temp_entity,
        inside_humidity_entity=inside_humidity_entity,
        battery_entity=battery_entity,
        waste_sensors=waste_sensors,
        waste_calendars=waste_calendars,
        image_path=image_path,
        page=page,
        page_count=page_count,
        photo=photo,
        cache_dir=static_dir,
        language=language,
    )

    current = fingerprint(data)
    fingerprint_path = static_dir / FINGERPRINT_NAME
    previous = ""
    if fingerprint_path.exists():
        previous = fingerprint_path.read_text().strip()

    output_path = static_dir / OUTPUT_NAME
    # Always re-render: the file must exist and stay current even when the
    # fingerprint matches, because the device may be fetching it for the first
    # time. Only the *refresh decision* depends on the fingerprint.
    await hass.async_add_executor_job(
        _render_sync,
        data,
        output_path,
        dither_photo,
    )
    # Written via a temp file and rename: the device fetches the PNG and the
    # fingerprint as two separate requests, so a half-written file would make it
    # either miss a real change or repaint for nothing.
    tmp_fingerprint = fingerprint_path.with_suffix(".tmp")
    tmp_fingerprint.write_text(current)
    tmp_fingerprint.replace(fingerprint_path)

    changed = current != previous
    _LOGGER.info(
        "Dashboard rendered (changed=%s) fingerprint=%s",
        changed,
        current,
    )
    return output_path, current, changed


def _render_sync(data: DashboardData, output_path: Path, dither_photo: bool) -> None:
    """Executor wrapper — Pillow and the dither loop are blocking."""
    render_dashboard(data, output_path, dither_photo=dither_photo)
