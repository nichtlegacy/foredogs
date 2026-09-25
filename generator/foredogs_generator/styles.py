"""Weather moods, art-style selection, and the Easter computus.

The holiday calendar moved to celebrations.py; only get_easter_date stays here,
because that is what the Easter-relative rules are built on.
"""

from __future__ import annotations

import random
from datetime import date
from math import ceil

from .history import StyleUse


# Share of the pool eligible on any given day. A quarter keeps the choice
# genuinely random while pushing the minimum gap between repeats to 39 days at
# the current pool size of 52.
LRU_CANDIDATE_FRACTION = 0.25
MIN_LRU_CANDIDATES = 4

WEATHER_MOODS = {
    "clear-night": ("peaceful, serene, starlit", "night sky with stars"),
    "cloudy": ("calm, muted, contemplative", "soft diffused lighting"),
    "exceptional": ("dramatic, intense, striking", "unusual atmospheric conditions"),
    "fog": ("mysterious, atmospheric, ethereal", "misty and dreamlike environment"),
    "hail": ("dramatic, intense, sheltered", "icy conditions"),
    "lightning": ("dramatic, electrifying, powerful", "stormy skies with lightning"),
    "lightning-rainy": ("dramatic, moody, cozy indoors", "thunder and rain outside"),
    "partlycloudy": ("pleasant, balanced, cheerful", "mix of sun and clouds"),
    "pouring": ("cozy, sheltered, rainy-day-vibes", "heavy rain outside"),
    "rainy": ("cozy, reflective, peaceful", "gentle rain"),
    "snowy": ("magical, cozy, winter-wonderland", "snow-covered landscape"),
    "snowy-rainy": ("chilly, cozy, wintry", "sleet and wintry mix"),
    "sunny": ("bright, cheerful, vibrant, happy", "warm sunlight"),
    "windy": ("dynamic, energetic, breezy", "wind-swept environment"),
    "windy-variant": ("lively, fresh, movement", "gusty conditions"),
}

def get_easter_date(year: int) -> tuple[int, int]:
    """Calculate Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return month, day


def get_art_style_string(style_entry: dict) -> str:
    """Combine style, outfit and props into one string.

    Props are kept apart from the outfit because the outfit is phrased as
    "dress the dog in ...", which produced lines like "dress the dog in sitting
    in a go-kart" while a kart, a floating mask or a trailing companion belong
    in the scene rather than on the dog.
    """
    parts = [style_entry["style"]]

    outfit = style_entry.get("outfit")
    if outfit:
        parts.append(f"dress the dog in {outfit}")

    props = style_entry.get("props")
    if props:
        parts.append(f"stage the scene with {props}")

    return " - ".join(parts)


def merge_outfits(base: str | None, addition: str | None) -> str | None:
    """Combine a style's own outfit with a holiday one.

    Replacing lost the costume the style was built around: on a holiday a
    superhero style kept the comic look but dropped the suit, leaving a dog in
    nothing but a Santa hat.
    """
    if not base:
        return addition
    if not addition:
        return base
    if addition.lower() in base.lower():
        return base
    return f"{base}, plus {addition}"


def choose_style(
    style_history: list[StyleUse] | list[str],
    style_pool: list[dict],
    holiday_outfit: str | None = None,
) -> dict:
    """Pick at random from the least recently used quarter of the pool.

    The previous rule blocked whatever appeared in the last N history entries,
    which counted runs rather than days: five test runs in one afternoon aged
    the list by five slots, and a style could come back around two weeks later.
    Ranking by the date a style was last used makes the spacing independent of
    how often the generator runs, and reserving the pick for the stalest
    quarter guarantees a gap of roughly three quarters of the pool.
    """
    if not style_pool:
        raise ValueError("No art styles configured.")

    uses = [use if isinstance(use, StyleUse) else StyleUse(style=use) for use in style_history]

    # Position breaks ties so that same-day picks still rotate: a style chosen
    # a moment ago sorts as fresher than one chosen earlier the same morning.
    last_used: dict[str, tuple[date, int]] = {}
    for index, use in enumerate(uses):
        last_used[use.style] = (use.used_on or date.min, index)

    # sorted() is stable, so styles never used keep their configured order.
    ranked = sorted(style_pool, key=lambda entry: last_used.get(entry["style"], (date.min, -1)))
    candidate_count = max(MIN_LRU_CANDIDATES, ceil(len(ranked) * LRU_CANDIDATE_FRACTION))
    style_entry = random.choice(ranked[:candidate_count])

    if holiday_outfit:
        style_entry = {**style_entry, "outfit": merge_outfits(style_entry.get("outfit"), holiday_outfit)}

    return style_entry
