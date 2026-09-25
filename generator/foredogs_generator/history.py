"""Prompt and style history helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


STYLE_HISTORY_SEPARATOR = "\t"


@dataclass(frozen=True)
class StyleUse:
    """One recorded use of an art style.

    ``used_on`` is None for entries written before the date was tracked. Those
    rank as the oldest possible uses, so a freshly migrated history behaves the
    same as one that has never seen a given style.
    """

    style: str
    used_on: date | None = None


def sanitize_prompt_history(lines: list[str]) -> list[str]:
    """Keep only valid prior prompt entries."""
    return [line.strip() for line in lines if line.strip().startswith("Activity: ")]


def sanitize_style_history(lines: list[str]) -> list[str]:
    """Keep only non-empty style lines."""
    return [line.strip() for line in lines if line.strip()]


def parse_style_history(lines: list[str]) -> list[StyleUse]:
    """Read style history lines into dated records.

    Current entries look like "2026-08-12<TAB>Minecraft blocky voxel art style".
    Older files hold the bare style name; a style name containing a tab would be
    misread as a date, which is why the prefix has to parse as one to count.
    """
    uses: list[StyleUse] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue

        prefix, separator, remainder = text.partition(STYLE_HISTORY_SEPARATOR)
        style = remainder.strip()
        used_on = _parse_iso_date(prefix) if separator and style else None
        uses.append(StyleUse(style=style, used_on=used_on) if used_on else StyleUse(style=text))

    return uses


def format_style_history(uses: list[StyleUse]) -> list[str]:
    """Render style records back into history lines."""
    return [
        f"{use.used_on.isoformat()}{STYLE_HISTORY_SEPARATOR}{use.style}" if use.used_on else use.style
        for use in uses
    ]


def _parse_iso_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def load_history(filepath: Path) -> list[str]:
    """Load simple newline history file."""
    if not filepath.exists():
        return []
    return filepath.read_text().splitlines()


def save_history(filepath: Path, entries: list[str]) -> None:
    """Save newline history file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text("\n".join(entries) + ("\n" if entries else ""))


def seed_history_if_missing(target_path: Path, source_path: Path | None, sanitizer) -> None:
    """Seed a history file from an imported source when target is missing."""
    if target_path.exists() or source_path is None or not source_path.exists():
        return

    seeded = sanitizer(source_path.read_text().splitlines())
    save_history(target_path, seeded)

