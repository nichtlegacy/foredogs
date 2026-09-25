"""Pick which image the dashboard should show today.

The generator and the display are deliberately decoupled: images are produced
elsewhere (currently the macOS worker driving Codex) and dropped into a folder,
and this module decides which of them to draw. That way a day without a fresh
generation still shows a picture rather than a placeholder — the display does not
care whether the Mac was on this morning.

Resolution order, first hit wins:

  1. today's image          dogs/2026-07-27.png
  2. today's, by weather    dogs/rainy/2026-07-27.png  (when a condition is given)
  3. weather pool           any image in dogs/rainy/
  4. most recent dated      the newest YYYY-MM-DD.png anywhere below dogs/
  5. anything at all        a random image from the folder
  6. nothing                caller draws a placeholder

Steps 2 and 3 are what make a stock of images work better than generating a week
ahead: a forecast five days out still moves (this install saw Thursday go
26 °C → 31 °C → 36 °C within an afternoon), so a picture drawn for "hot and
sunny" is often wrong by the time it is shown. Picking from a pool by *actual*
current weather is always right.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import date
from pathlib import Path

logger = logging.getLogger("foredogs")

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# "2026-07-27.png", optionally with a suffix like "2026-07-27_sunny.png".
DATED_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# HA weather condition -> the folder name to look in. Several conditions share a
# pool: there is no point maintaining separate art for "rainy" and "pouring".
CONDITION_FOLDERS = {
    "sunny": "sunny",
    "clear-night": "sunny",
    "partlycloudy": "partlycloudy",
    "cloudy": "cloudy",
    "fog": "cloudy",
    "hazy": "cloudy",
    "rainy": "rainy",
    "pouring": "rainy",
    "snowy-rainy": "rainy",
    "lightning": "lightning",
    "lightning-rainy": "lightning",
    "snowy": "snowy",
    "hail": "snowy",
    "windy": "windy",
    "windy-variant": "windy",
    "exceptional": "cloudy",
}


def _images_in(directory: Path) -> list[Path]:
    """Every usable image directly inside `directory`, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith(".")
    )


def _dated_images(root: Path) -> list[tuple[date, Path]]:
    """All YYYY-MM-DD-named images below `root`, oldest first."""
    found: list[tuple[date, Path]] = []
    if not root.is_dir():
        return found

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        match = DATED_NAME.match(path.stem)
        if match is None:
            continue
        try:
            found.append((date(*(int(g) for g in match.groups())), path))
        except ValueError:
            # e.g. 2026-02-30; a filename typo should not abort the search.
            logger.debug("Ignoring image with impossible date: %s", path.name)

    found.sort(key=lambda pair: pair[0])
    return found


def pick_image(
    directory: Path,
    today: date,
    condition: str = "",
    fallback: Path | None = None,
) -> tuple[Path | None, str]:
    """Choose the image to draw.

    Args:
        directory: folder holding generated images, optionally with per-weather
            subfolders.
        today: date to look for.
        condition: current HA weather condition, used to pick a pool.
        fallback: a single image to use if the folder yields nothing — keeps the
            old single-file behaviour working.

    Returns:
        (path or None, short reason) — the reason is logged and makes it obvious
        from the outside whether today's image was actually found.
    """
    directory = Path(directory)

    # 1. Exactly today, at the top level.
    for suffix in IMAGE_SUFFIXES:
        exact = directory / f"{today.isoformat()}{suffix}"
        if exact.is_file():
            return exact, "today"

    folder = CONDITION_FOLDERS.get(condition)

    # 2. Today's image inside the matching weather folder.
    if folder:
        for suffix in IMAGE_SUFFIXES:
            exact = directory / folder / f"{today.isoformat()}{suffix}"
            if exact.is_file():
                return exact, f"today/{folder}"

    # 3. Any image from the matching weather pool. Seeded by date so the choice
    #    is stable across the day's renders — the picture should not change every
    #    30 minutes, only when the day or the weather does.
    if folder:
        pool = _images_in(directory / folder)
        if pool:
            rng = random.Random(f"{today.isoformat()}:{folder}")
            return rng.choice(pool), f"pool/{folder}"

    # 4. The most recent dated image, wherever it sits.
    dated = _dated_images(directory)
    if dated:
        when, path = dated[-1]
        age = (today - when).days
        return path, f"newest ({age}d old)" if age > 0 else "newest"

    # 5. Anything in the folder at all, again stable per day.
    loose = _images_in(directory)
    if loose:
        rng = random.Random(today.isoformat())
        return rng.choice(loose), "random"

    # 6. The explicitly configured single file.
    if fallback is not None and Path(fallback).is_file():
        return Path(fallback), "configured fallback"

    return None, "nothing found"


def prune_old(directory: Path, today: date, keep_days: int) -> int:
    """Delete dated images older than `keep_days`.

    Without this the folder grows forever. Only touches files whose name is a
    date, so anything hand-placed is left alone.
    """
    if keep_days <= 0:
        return 0

    removed = 0
    for when, path in _dated_images(Path(directory)):
        if (today - when).days > keep_days:
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("Could not delete old image %s", path, exc_info=True)

    if removed:
        logger.info("Pruned %d image(s) older than %d days", removed, keep_days)
    return removed
