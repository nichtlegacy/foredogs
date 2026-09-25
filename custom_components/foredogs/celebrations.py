"""Private celebrations: birthdays, anniversaries, and recurring special days.

Extends what `foredogs.get_holiday_info()` already does for public holidays with
the things a household actually cares about — a birthday, a wedding
anniversary, the day the dog came home. Three things the plain HOLIDAYS dict
cannot express:

  * **Priority.** Several occasions can land on the same date; the highest
    priority one decides which people appear in the scene.
  * **Counting.** "Happy 5th Birthday, Rex" needs the start year, so the
    template gets `{ordinal}`, `{years}`, `{name}` and `{year}`.
  * **Memory.** Without history, the same birthday produces roughly the same
    scene every year. Past activities are recorded and fed back into the prompt
    as things to avoid.

Adapted from the approach in https://github.com/kylekampy/petcasts, reworked for
this project's request model (person_names / person_descriptions /
person_image_paths as parallel lists) and German holidays.

Events are configured in `foredogs_data/celebrations.yaml`; see
`config_examples/celebrations.yaml` for the format. A missing file simply means
no private celebrations, which is a valid state.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("foredogs")

HISTORY_FILENAME = "celebrations_history.json"
CONFIG_FILENAME = "celebrations.yaml"

# How many past occurrences to remember per event. Five years of "don't do this
# again" is plenty of steering without bloating the prompt.
HISTORY_LIMIT_PER_EVENT = 5


@dataclass
class CelebrationEvent:
    """A configured occasion. Either month+day, or a date, or an nth-weekday rule."""

    id: str
    name: str
    kind: str = "event"  # birthday | anniversary | holiday | event
    month: int | None = None
    day: int | None = None
    date_text: str | None = None
    start_year: int | None = None
    # nth_weekday rule, e.g. second Sunday in May (Mother's Day)
    rule: str | None = None
    weekday: str | int | None = None
    nth: int | None = None
    message_template: str | None = None
    prompt: str = ""
    # Which configured people to put in the scene: "all", "none", or names.
    people: str | list[str] = "default"
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
    people: str | list[str]
    priority: int
    previous_activities: list[str] = field(default_factory=list)


# --- public API --------------------------------------------------------------
def active_celebrations(
    events: list[CelebrationEvent],
    today: date,
    data_dir: Path | None = None,
) -> list[ActiveCelebration]:
    """Return every configured celebration that falls on `today`.

    Sorted by descending priority, so callers can treat the first entry as the
    one that wins any conflict.
    """
    history = load_history(data_dir) if data_dir is not None else {}

    active = []
    for event in events:
        celebration = _activate(event, today, history)
        if celebration is not None:
            active.append(celebration)

    return sorted(active, key=lambda c: c.priority, reverse=True)


def load_events(data_dir: Path) -> list[CelebrationEvent]:
    """Read celebrations.yaml. Absent or malformed config yields no events."""
    path = Path(data_dir) / CONFIG_FILENAME
    if not path.exists():
        logger.debug("No celebrations config at %s", path)
        return []

    try:
        import yaml

        raw = yaml.safe_load(path.read_text()) or {}
    except Exception:
        logger.exception("Could not parse %s; ignoring private celebrations", path)
        return []

    if not isinstance(raw, dict):
        logger.warning("%s must be a mapping with an 'events' key", path)
        return []

    events = []
    for entry in raw.get("events", []) or []:
        if not isinstance(entry, dict):
            continue
        try:
            events.append(
                CelebrationEvent(
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
                    message_template=entry.get("message_template"),
                    prompt=str(entry.get("prompt", "")),
                    people=entry.get("people", "default"),
                    priority=int(entry.get("priority", 50)),
                    enabled=bool(entry.get("enabled", True)),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.exception("Skipping malformed celebration entry: %r", entry)

    logger.info("Loaded %d private celebration(s) from %s", len(events), path)
    return events


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
        lines.append(f"- {celebration.name}: {celebration.message}")
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
    """Text injected into the image-generation prompt."""
    if not celebrations:
        return ""

    lines = [
        "",
        "CELEBRATION: Work today's occasion into the artwork. Readable text is",
        "welcome — put it on a sign, banner, cake, or chalkboard that belongs in",
        "the scene, ideally the same object that carries the weather info.",
    ]
    for celebration in celebrations:
        lines.append(f'- Include the readable text: "{celebration.message}"')
        if celebration.prompt:
            lines.append(f"  Visual guidance: {celebration.prompt}")
    return "\n".join(lines)


def resolve_people(
    celebrations: list[ActiveCelebration],
    people_pool: list[dict],
) -> list[dict] | None:
    """Pick the people the highest-priority celebration asks for.

    Returns None when no celebration expresses a preference, which leaves the
    normal random person selection in charge.

    A birthday should show the person whose birthday it is — that is the single
    most valuable thing this module does for a kitchen display.
    """
    by_name = {person["name"].casefold(): person for person in people_pool}

    for celebration in celebrations:
        spec = celebration.people

        if isinstance(spec, list):
            resolved = [by_name[n.casefold()] for n in spec if n.casefold() in by_name]
            missing = [n for n in spec if n.casefold() not in by_name]
            if missing:
                logger.warning(
                    "Celebration %r names unknown people: %s",
                    celebration.id,
                    ", ".join(missing),
                )
            if resolved:
                return resolved
            continue

        text = str(spec).strip().casefold()
        if text in ("", "default", "selected"):
            continue  # no opinion; fall through to the next celebration
        if text == "all":
            return list(people_pool)
        if text in ("none", "no_one", "keine"):
            return []
        if text in by_name:
            return [by_name[text]]

        logger.warning("Celebration %r names unknown person %r", celebration.id, spec)

    return None


def metadata(celebrations: list[ActiveCelebration]) -> list[dict]:
    """Serialisable summary, useful for logging and for the dashboard later."""
    return [
        {
            "id": c.id,
            "name": c.name,
            "kind": c.kind,
            "message": c.message,
            "years": c.years,
            "ordinal": c.ordinal,
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

    if scheduled != today:
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
        people=event.people,
        priority=event.priority,
        previous_activities=_previous_activities(history, event.id),
    )


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
    """Resolve an nth-weekday rule, e.g. Mother's Day = 2nd Sunday in May."""
    rule = (event.rule or "").lower().replace("-", "_")
    if rule != "nth_weekday":
        raise ValueError(f"Unsupported rule {event.rule!r}")
    if event.month is None or event.weekday is None or event.nth is None:
        raise ValueError(f"nth_weekday event {event.id!r} needs month, weekday and nth")

    weekday = _weekday_index(event.weekday)
    weeks = calendar.monthcalendar(year, int(event.month))
    days = [week[weekday] for week in weeks if week[weekday] != 0]
    nth = int(event.nth)
    # Negative nth counts from the end: -1 is the last such weekday.
    day = days[nth - 1] if nth > 0 else days[nth]
    return date(year, int(event.month), day)


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
