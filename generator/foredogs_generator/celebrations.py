"""Special days: public holidays, regional events, and private dates.

Ported from the Home Assistant-side `celebrations.py`, which hung off the dead
Gemini path and so never ran. This version drives the live macOS pipeline and
replaces the flat `HOLIDAYS` dict in `styles.py`.

What it adds over that dict:

  - birthdays that count years ("Alles Gute zum 5. Geburtstag, Rex")
  - Easter-relative days, which is most of the German calendar
  - nth-weekday rules (Muttertag = 2nd Sunday in May)
  - events that repeat every N years, such as a festival held triennially
  - multi-day events (Altstadtfest, Weihnachtsmarkt)
  - a per-event history, so the same birthday does not produce the same cake
    scene every single year

Defaults are built in, so the feature works with no config file at all. A
`celebrations.yaml` next to the other config extends them, and can override or
disable any default by reusing its `id`.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .styles import get_easter_date

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "celebrations_history.json"
CONFIG_FILENAME = "celebrations.yaml"

# How many past years to show the model per event. Enough to steer it away from
# repeats, short enough not to crowd out the rest of the prompt.
HISTORY_LIMIT_PER_EVENT = 5


@dataclass
class CelebrationEvent:
    """A configured occasion.

    The date comes from exactly one of: month+day, a `date` string, or a `rule`.
    """

    id: str
    name: str
    kind: str = "event"  # birthday | anniversary | holiday | local | season | event
    month: int | None = None
    day: int | None = None
    date_text: str | None = None
    start_year: int | None = None
    # rule: nth_weekday | easter_offset | advent
    rule: str | None = None
    weekday: str | int | None = None
    nth: int | None = None
    # Days from Easter Sunday: Karfreitag -2, Christi Himmelfahrt +39.
    offset: int | None = None
    # Repeat every N years, counted from anchor_year.
    every_n_years: int | None = None
    anchor_year: int | None = None
    # Events that run for more than a day (Altstadtfest, Weihnachtsmarkt).
    duration_days: int = 1
    message_template: str | None = None
    prompt: str = ""
    # Feeds style selection, exactly like the old HOLIDAYS tuple's third slot.
    outfit: str | None = None
    priority: int = 50
    enabled: bool = True


@dataclass
class ActiveCelebration:
    """A celebration that applies to the date being generated."""

    id: str
    name: str
    kind: str
    message: str
    prompt: str
    years: int | None
    ordinal: str | None
    outfit: str | None
    priority: int
    day_index: int = 0  # 0 on the first day of a multi-day event
    previous_activities: list[str] = field(default_factory=list)


# --- built-in calendar -------------------------------------------------------
# Parsed through the same path as user YAML, so both behave identically.
#
# Public holidays are those that apply in Niedersachsen. Culturally large days
# that are not legal holidays (Heiligabend, Silvester, Nikolaus, Muttertag) are
# included because they matter far more for a picture than a day off does.
DEFAULT_EVENTS: list[dict] = [
    # --- fixed-date German holidays ---
    {"id": "neujahr", "name": "Neujahr", "kind": "holiday", "month": 1, "day": 1,
     "prompt": "New Year's Day: fireworks remnants, a fresh-start mood, winter light.",
     "outfit": "party hat", "priority": 70},
    {"id": "valentinstag", "name": "Valentinstag", "kind": "holiday", "month": 2, "day": 14,
     "prompt": "Valentine's Day: hearts, flowers, warm affectionate mood.", "priority": 55},
    {"id": "tag_der_arbeit", "name": "Tag der Arbeit", "kind": "holiday", "month": 5, "day": 1,
     "prompt": "May Day in Germany: maypole, spring green, relaxed public-holiday feel.",
     "priority": 60},
    {"id": "tag_deutsche_einheit", "name": "Tag der Deutschen Einheit", "kind": "holiday",
     "month": 10, "day": 3, "prompt": "German Unity Day: flags, civic celebration.",
     "outfit": "German flag bandana", "priority": 60},
    {"id": "halloween", "name": "Halloween", "kind": "holiday", "month": 10, "day": 31,
     "prompt": "Halloween: pumpkins, spooky-but-friendly mood, trick-or-treating.",
     "outfit": "spooky costume", "priority": 75},
    # Same date as Halloween and a public holiday in parts of Germany, but it
    # loses the tie deliberately: it makes the weaker picture.
    {"id": "reformationstag", "name": "Reformationstag", "kind": "holiday", "month": 10, "day": 31,
     "prompt": "Reformation Day: quiet, autumnal, historic church setting.",
     "priority": 40},
    {"id": "nikolaus", "name": "Nikolaus", "kind": "holiday", "month": 12, "day": 6,
     "prompt": "St Nicholas Day: boots by the door filled with treats.",
     "outfit": "red Santa hat", "priority": 65},
    {"id": "heiligabend", "name": "Heiligabend", "kind": "holiday", "month": 12, "day": 24,
     "prompt": "Christmas Eve, the big one in Germany: tree, presents, candlelight, family evening.",
     "outfit": "Christmas sweater and Santa hat", "priority": 90},
    {"id": "weihnachten_1", "name": "1. Weihnachtstag", "kind": "holiday", "month": 12, "day": 25,
     "prompt": "Christmas Day: feast, new toys, cosy indoor warmth.",
     "outfit": "cosy Christmas sweater", "priority": 85},
    {"id": "weihnachten_2", "name": "2. Weihnachtstag", "kind": "holiday", "month": 12, "day": 26,
     "prompt": "Boxing Day: leftovers, family walk, slow winter afternoon.",
     "outfit": "festive winter outfit", "priority": 80},
    {"id": "silvester", "name": "Silvester", "kind": "holiday", "month": 12, "day": 31,
     "prompt": "New Year's Eve: countdown, fireworks over the rooftops.",
     "outfit": "party hat and bow tie", "priority": 75},

    # --- Easter-relative (most of the German calendar hangs off this) ---
    {"id": "rosenmontag", "name": "Rosenmontag", "kind": "holiday",
     "rule": "easter_offset", "offset": -48,
     "prompt": "Carnival Monday: parade, confetti, costumes, thrown sweets.",
     "outfit": "colourful carnival costume with confetti", "priority": 65},
    {"id": "karfreitag", "name": "Karfreitag", "kind": "holiday",
     "rule": "easter_offset", "offset": -2,
     "prompt": "Good Friday: quiet and reflective, a still spring landscape. No party imagery.",
     "priority": 55},
    {"id": "ostersonntag", "name": "Ostersonntag", "kind": "holiday",
     "rule": "easter_offset", "offset": 0,
     "prompt": "Easter Sunday: egg hunt, decorated eggs, spring blossom.",
     "outfit": "bunny ears headband", "priority": 85},
    {"id": "ostermontag", "name": "Ostermontag", "kind": "holiday",
     "rule": "easter_offset", "offset": 1,
     "prompt": "Easter Monday: spring picnic, family walk, leftover eggs.",
     "outfit": "spring flower accessories", "priority": 70},
    # Christi Himmelfahrt and Vatertag are the same day in Germany.
    {"id": "vatertag", "name": "Vatertag / Christi Himmelfahrt", "kind": "holiday",
     "rule": "easter_offset", "offset": 39,
     "prompt": "Father's Day in Germany: a Bollerwagen handcart, a hike through the countryside, "
               "early-summer green.",
     "priority": 70},
    {"id": "pfingstsonntag", "name": "Pfingstsonntag", "kind": "holiday",
     "rule": "easter_offset", "offset": 49,
     "prompt": "Whit Sunday: early summer, long weekend, outdoors.", "priority": 60},
    {"id": "pfingstmontag", "name": "Pfingstmontag", "kind": "holiday",
     "rule": "easter_offset", "offset": 50,
     "prompt": "Whit Monday: excursion weather, relaxed public holiday.", "priority": 60},

    # --- weekday rules ---
    {"id": "muttertag", "name": "Muttertag", "kind": "holiday",
     "rule": "nth_weekday", "month": 5, "weekday": "Sonntag", "nth": 2,
     "prompt": "Mother's Day: flowers, breakfast in bed, affectionate spring scene.",
     "priority": 70},
    {"id": "advent_1", "name": "1. Advent", "kind": "holiday",
     "rule": "advent", "nth": 1,
     "prompt": "First Advent: one candle lit on the wreath, dark cosy December afternoon.",
     "priority": 55},
    {"id": "advent_2", "name": "2. Advent", "kind": "holiday", "rule": "advent", "nth": 2,
     "prompt": "Second Advent: two candles lit, baking biscuits.", "priority": 55},
    {"id": "advent_3", "name": "3. Advent", "kind": "holiday", "rule": "advent", "nth": 3,
     "prompt": "Third Advent: three candles lit, Christmas market mood.", "priority": 55},
    {"id": "advent_4", "name": "4. Advent", "kind": "holiday", "rule": "advent", "nth": 4,
     "prompt": "Fourth Advent: all four candles lit, last preparations before Christmas.",
     "priority": 55},

    # --- dog days, since this is a dog display ---
    {"id": "welthundetag", "name": "Welthundetag", "kind": "event", "month": 10, "day": 10,
     "prompt": "World Dog Day: the dogs are the celebrated guests of honour.", "priority": 50},
    {"id": "internationaler_hundetag", "name": "Internationaler Hundetag", "kind": "event",
     "month": 8, "day": 26,
     "prompt": "International Dog Day: late-summer celebration of dogs.", "priority": 50},
    {"id": "welttierschutztag", "name": "Welttierschutztag", "kind": "event", "month": 10, "day": 4,
     "prompt": "World Animal Day: animals looked after and celebrated.", "priority": 45},

    # --- seasons, which a weather display should mark ---
    {"id": "fruehlingsanfang", "name": "Frühlingsanfang", "kind": "season", "month": 3, "day": 20,
     "prompt": "First day of spring: first blossom, returning light.", "priority": 30},
    {"id": "sommeranfang", "name": "Sommeranfang", "kind": "season", "month": 6, "day": 21,
     "prompt": "Midsummer: the longest day, late golden evening light.", "priority": 30},
    {"id": "herbstanfang", "name": "Herbstanfang", "kind": "season", "month": 9, "day": 22,
     "prompt": "First day of autumn: falling leaves, low warm sun.", "priority": 30},
    {"id": "winteranfang", "name": "Winteranfang", "kind": "season", "month": 12, "day": 21,
     "prompt": "Winter solstice: the shortest day, blue hour, cold clear air.", "priority": 30},

    # --- kept from the old HOLIDAYS dict ---
    {"id": "st_patricks", "name": "St. Patrick's Day", "kind": "event", "month": 3, "day": 17,
     "prompt": "St Patrick's Day: green everywhere, shamrocks.",
     "outfit": "green hat and clover accessories", "priority": 45},
    {"id": "april_fools", "name": "Aprilscherz", "kind": "event", "month": 4, "day": 1,
     "prompt": "April Fools' Day: a harmless prank in progress, mischievous mood.", "priority": 45},
]


# --- public API --------------------------------------------------------------
def active_celebrations(
    events: list[CelebrationEvent],
    today: date,
    data_dir: Path | None = None,
) -> list[ActiveCelebration]:
    """Every configured celebration that falls on `today`, best first.

    Sorted by descending priority, so callers can treat the first entry as the
    one that wins any conflict — which is how Halloween beats Reformationstag on
    31 October.
    """
    history = load_history(data_dir) if data_dir is not None else {}

    active = []
    for event in events:
        celebration = _activate(event, today, history)
        if celebration is not None:
            active.append(celebration)

    return sorted(active, key=lambda c: c.priority, reverse=True)


def load_events(config_dir: Path | None = None) -> list[CelebrationEvent]:
    """Built-in calendar, extended and overridden by an optional YAML file.

    A YAML entry reusing a built-in `id` replaces it outright, so a default can
    be retuned or switched off with `enabled: false` without touching this file.
    """
    events: dict[str, CelebrationEvent] = {}
    for raw in DEFAULT_EVENTS:
        parsed = _parse_event(raw)
        if parsed is not None:
            events[parsed.id] = parsed

    for raw in _load_yaml_entries(config_dir):
        parsed = _parse_event(raw)
        if parsed is not None:
            if parsed.id in events:
                logger.info("celebrations.yaml overrides built-in event %r", parsed.id)
            events[parsed.id] = parsed

    enabled = [event for event in events.values() if event.enabled]
    logger.debug("Loaded %d celebration(s), %d enabled", len(events), len(enabled))
    return enabled


def activity_prompt_block(celebrations: list[ActiveCelebration]) -> str:
    """Text injected into the activity-description prompt."""
    if not celebrations:
        return ""

    lines = [
        "",
        "SPECIAL DAY:",
        "Today is a celebration. Make it central to the activity, but keep the",
        "real weather and season integrated — no generic party backdrop.",
    ]

    for celebration in celebrations:
        # For most holidays the message is just the name, and "- Heiligabend:
        # Heiligabend" reads like a templating bug rather than emphasis.
        if celebration.message and celebration.message != celebration.name:
            lines.append(f"- {celebration.name}: {celebration.message}")
        else:
            lines.append(f"- {celebration.name}")
        if celebration.prompt:
            lines.append(f"  Guidance: {celebration.prompt}")
        if celebration.previous_activities:
            # The whole point of the history file: stop the same birthday
            # producing the same cake scene every single year.
            lines.append("  Previous years used these ideas — invent something different:")
            for activity in celebration.previous_activities[:HISTORY_LIMIT_PER_EVENT]:
                lines.append(f"  - {activity}")

    return "\n".join(lines)


def image_prompt_block(celebrations: list[ActiveCelebration]) -> str:
    """Text injected into the image-generation prompt.

    Seasons and background events only tint the scene. Asking for readable text
    on all of them would put a banner in the picture every day of Advent.
    """
    if not celebrations:
        return ""

    headline = [c for c in celebrations if c.kind != "season"]
    flavour = [c for c in celebrations if c.kind == "season"]

    lines = ["", "CELEBRATION: work today's occasion into the artwork."]

    if headline:
        lines.append(
            "Readable text is welcome — put it on a sign, banner, cake, or "
            "chalkboard that belongs in the scene."
        )
        for celebration in headline:
            lines.append(f'- Include the readable text: "{celebration.message}"')
            if celebration.prompt:
                lines.append(f"  Visual guidance: {celebration.prompt}")

    for celebration in flavour:
        lines.append(f"- Background flavour only, no text: {celebration.prompt or celebration.name}")

    return "\n".join(lines)


def primary_outfit(celebrations: list[ActiveCelebration]) -> str | None:
    """Outfit of the highest-priority celebration that asks for one.

    Feeds style selection, the way the old HOLIDAYS tuple's third slot did.
    """
    for celebration in celebrations:
        if celebration.outfit:
            return celebration.outfit
    return None


def metadata(celebrations: list[ActiveCelebration]) -> list[dict]:
    """Serialisable summary for the status file."""
    return [
        {
            "id": c.id,
            "name": c.name,
            "kind": c.kind,
            "message": c.message,
            "years": c.years,
            "priority": c.priority,
        }
        for c in celebrations
    ]


# --- history -----------------------------------------------------------------
def load_history(data_dir: Path | None) -> dict[str, Any]:
    if data_dir is None:
        return {}
    path = Path(data_dir) / HISTORY_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; starting fresh", path)
        return {}
    return data if isinstance(data, dict) else {}


def record_history(
    data_dir: Path,
    celebrations: list[ActiveCelebration],
    generated_at: datetime,
    activity: str,
    style: str,
) -> None:
    """Append this year's idea so next year can avoid repeating it."""
    if not celebrations:
        return

    path = Path(data_dir) / HISTORY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)

    data = load_history(data_dir)
    events = data.setdefault("events", {})
    day = generated_at.date().isoformat()

    for celebration in celebrations:
        entries = [
            entry
            for entry in events.get(celebration.id, [])
            if isinstance(entry, dict) and entry.get("date") != day
        ]
        entries.append(
            {
                "date": day,
                "year": generated_at.year,
                "message": celebration.message,
                "activity": activity,
                "style": style,
            }
        )
        events[celebration.id] = entries[-HISTORY_LIMIT_PER_EVENT:]

    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except OSError:
        logger.exception("Could not write celebration history to %s", path)


# --- internals ---------------------------------------------------------------
def _load_yaml_entries(config_dir: Path | None) -> list[dict]:
    if config_dir is None:
        return []
    path = Path(config_dir) / CONFIG_FILENAME
    if not path.exists():
        return []

    try:
        import yaml
    except ImportError:
        # Not fatal: the built-in calendar still works, only user additions are
        # lost. Worth a warning because a birthday silently disappearing is the
        # kind of thing nobody notices until the day itself.
        logger.warning("PyYAML not available; ignoring %s", path)
        return []

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except Exception:
        logger.exception("Could not parse %s; using built-in calendar only", path)
        return []

    if not isinstance(raw, dict):
        logger.warning("%s must be a mapping with an 'events' key", path)
        return []

    return [entry for entry in (raw.get("events") or []) if isinstance(entry, dict)]


def _parse_event(entry: dict) -> CelebrationEvent | None:
    try:
        return CelebrationEvent(
            id=str(entry["id"]),
            name=str(entry["name"]),
            kind=str(entry.get("kind", "event")),
            month=entry.get("month"),
            day=entry.get("day"),
            date_text=entry.get("date"),
            start_year=entry.get("start_year"),
            rule=entry.get("rule"),
            weekday=entry.get("weekday"),
            nth=entry.get("nth"),
            offset=entry.get("offset"),
            every_n_years=entry.get("every_n_years"),
            anchor_year=entry.get("anchor_year"),
            duration_days=int(entry.get("duration_days", 1)),
            message_template=entry.get("message_template"),
            prompt=str(entry.get("prompt", "")),
            outfit=entry.get("outfit"),
            priority=int(entry.get("priority", 50)),
            enabled=bool(entry.get("enabled", True)),
        )
    except (KeyError, TypeError, ValueError):
        logger.exception("Skipping malformed celebration entry: %r", entry)
        return None


def _activate(
    event: CelebrationEvent,
    today: date,
    history: dict[str, Any],
) -> ActiveCelebration | None:
    if not event.enabled:
        return None

    try:
        scheduled, parsed_start_year = _scheduled_date(event, today.year)
    except ValueError:
        logger.exception("Bad schedule for celebration %r", event.id)
        return None

    day_index = (today - scheduled).days
    if not 0 <= day_index < max(1, event.duration_days):
        # A multi-day event that started in December can still be running in
        # January, so retry against last year's occurrence before giving up.
        try:
            previous, parsed_start_year = _scheduled_date(event, today.year - 1)
        except ValueError:
            return None
        day_index = (today - previous).days
        if not 0 <= day_index < max(1, event.duration_days):
            return None
        scheduled = previous

    if not _year_matches(event, scheduled.year):
        return None

    start_year = event.start_year if event.start_year is not None else parsed_start_year
    years = today.year - start_year if start_year is not None else None
    ord_text = ordinal(years) if years is not None and years > 0 else None

    return ActiveCelebration(
        id=event.id,
        name=event.name,
        kind=event.kind,
        message=_message(event, today, years, ord_text),
        prompt=event.prompt,
        years=years,
        ordinal=ord_text,
        outfit=event.outfit,
        priority=event.priority,
        day_index=day_index,
        previous_activities=_previous_activities(history, event.id),
    )


def _year_matches(event: CelebrationEvent, year: int) -> bool:
    """Handle events that only happen every N years."""
    if not event.every_n_years:
        return True
    anchor = event.anchor_year if event.anchor_year is not None else year
    return (year - anchor) % int(event.every_n_years) == 0


def _scheduled_date(event: CelebrationEvent, year: int) -> tuple[date, int | None]:
    if event.rule:
        return _rule_date(event, year), event.start_year
    if event.month is not None and event.day is not None:
        return date(year, int(event.month), int(event.day)), event.start_year
    if event.date_text:
        month, day, start_year = _parse_date(event.date_text)
        return date(year, month, day), start_year
    raise ValueError(f"Celebration {event.id!r} needs month+day, date, or rule")


def _rule_date(event: CelebrationEvent, year: int) -> date:
    rule = (event.rule or "").lower().replace("-", "_")

    if rule == "nth_weekday":
        return _nth_weekday_date(event, year)
    if rule == "easter_offset":
        if event.offset is None:
            raise ValueError(f"easter_offset event {event.id!r} needs an offset")
        return date(year, *get_easter_date(year)) + timedelta(days=int(event.offset))
    if rule == "advent":
        return _advent_date(event, year)

    raise ValueError(f"Unsupported rule {event.rule!r}")


def _nth_weekday_date(event: CelebrationEvent, year: int) -> date:
    """Resolve an nth-weekday rule, e.g. Mother's Day = 2nd Sunday in May."""
    if event.month is None or event.weekday is None or event.nth is None:
        raise ValueError(f"nth_weekday event {event.id!r} needs month, weekday and nth")

    weekday = _weekday_index(event.weekday)
    weeks = calendar.monthcalendar(year, int(event.month))
    days = [week[weekday] for week in weeks if week[weekday] != 0]
    nth = int(event.nth)
    # Negative nth counts from the end: -1 is the last such weekday.
    day = days[nth - 1] if nth > 0 else days[nth]
    return date(year, int(event.month), day)


def _advent_date(event: CelebrationEvent, year: int) -> date:
    """Nth Advent Sunday. The fourth is the last Sunday on or before 24 December."""
    nth = int(event.nth or 1)
    if not 1 <= nth <= 4:
        raise ValueError(f"advent event {event.id!r} needs nth between 1 and 4")

    christmas_eve = date(year, 12, 24)
    # weekday(): Monday is 0, Sunday is 6. On a Sunday this yields 0, which is
    # correct — 24 December can itself be the fourth Advent.
    fourth = christmas_eve - timedelta(days=(christmas_eve.weekday() + 1) % 7)
    return fourth - timedelta(weeks=4 - nth)


def _parse_date(value: str) -> tuple[int, int, int | None]:
    """Accept ISO, German, and US date spellings.

    German dates ("15.06.2001") are tried before US ones so 03.04. is read as
    3 April, not 4 March.
    """
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value.strip(), flags=re.I)

    formats: list[tuple[str, bool]] = [
        ("%Y-%m-%d", True),
        ("%d.%m.%Y", True),
        ("%d.%m.", False),
        ("%d.%m", False),
        ("%m-%d", False),
        ("%m/%d/%Y", True),
        ("%m/%d/%y", True),
        ("%B %d, %Y", True),
        ("%b %d, %Y", True),
        ("%B %d", False),
        ("%b %d", False),
    ]

    for fmt, has_year in formats:
        try:
            # Leap-safe filler year for date-only formats.
            parse_text = text if has_year else f"{text} 2000"
            parse_fmt = fmt if has_year else f"{fmt} %Y"
            parsed = datetime.strptime(parse_text, parse_fmt)
        except ValueError:
            continue
        return parsed.month, parsed.day, parsed.year if has_year else None

    raise ValueError(f"Unsupported celebration date: {value!r}")


def _message(
    event: CelebrationEvent,
    today: date,
    years: int | None,
    ord_text: str | None,
) -> str:
    template = event.message_template or _default_template(event, years)

    # A template asking for {years} or {ordinal} on an event with no start_year
    # would render as "Seit  Jahren bei uns" — a visible gap where a number
    # should be. Fall back to the plain name rather than ship that to the image
    # generator as readable text.
    if years is None and re.search(r"\{(years|ordinal)\}", template):
        logger.warning(
            "Celebration %r uses {years}/{ordinal} but has no start_year; "
            "falling back to the plain name",
            event.id,
        )
        return event.name

    try:
        return template.format(
            name=event.name,
            event=event.name,
            year=today.year,
            years="" if years is None else years,
            ordinal="" if ord_text is None else ord_text,
        )
    except (KeyError, IndexError):
        logger.warning("Bad message_template for %r: %r", event.id, template)
        return event.name


def _default_template(event: CelebrationEvent, years: int | None) -> str:
    kind = event.kind.casefold()
    if kind == "birthday":
        return "Alles Gute zum {ordinal} Geburtstag, {name}" if years else "Alles Gute, {name}"
    if kind == "anniversary":
        return "Herzlichen Glückwunsch zum {ordinal} Jubiläum" if years else "{name}"
    return "{name}"


def ordinal(n: int) -> str:
    """German ordinals are simply the number followed by a period."""
    return f"{n}."


def _previous_activities(history: dict[str, Any], event_id: str) -> list[str]:
    entries = history.get("events", {}).get(event_id, [])
    return [
        entry["activity"]
        for entry in reversed(entries)
        if isinstance(entry, dict) and entry.get("activity")
    ]


def _weekday_index(value: str | int) -> int:
    if isinstance(value, int):
        return value

    key = str(value).strip().casefold()
    english = {name.casefold(): i for i, name in enumerate(calendar.day_name)}
    english_abbr = {name.casefold(): i for i, name in enumerate(calendar.day_abbr)}
    german = {
        "montag": 0,
        "dienstag": 1,
        "mittwoch": 2,
        "donnerstag": 3,
        "freitag": 4,
        "samstag": 5,
        "sonnabend": 5,
        "sonntag": 6,
        "mo": 0,
        "di": 1,
        "mi": 2,
        "do": 3,
        "fr": 4,
        "sa": 5,
        "so": 6,
    }

    for table in (german, english, english_abbr):
        if key in table:
            return table[key]

    raise ValueError(f"Unknown weekday: {value!r}")
