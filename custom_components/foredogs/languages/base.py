"""The contract every dashboard language implements.

A language is one class with lookup tables and a handful of formatting
methods. It is deliberately not gettext: there are about seventy strings, all
of them drawn onto a fixed 800x480 layout, and most of the work is not
translation but formatting — "24.09." against "24 Sep", "in 3 Tagen" against
"in 3 days", the plural of "Tag".

Adding a language means subclassing this, overriding the tables and whichever
methods format differently, and registering it in __init__.py. Everything left
unset falls back to what is defined here, so a new language is usable long
before it is complete.
"""

from __future__ import annotations

from datetime import date, datetime


class Language:
    """Base implementation. English-ish defaults, so an incomplete subclass
    still renders something readable rather than raising mid-render."""

    #: Two-letter code, as passed to the render service.
    code = "xx"
    #: Human-readable name, in English, for logs and error messages.
    name = "Unnamed"

    # --- tables --------------------------------------------------------------
    #: Home Assistant condition slug -> label for the header and forecast band.
    conditions: dict[str, str] = {}

    #: Shorter alternatives for the narrow forecast columns. A slug missing
    #: here simply falls back to the long label, which is then truncated.
    conditions_short: dict[str, str] = {}

    #: Twelve entries, January first.
    months: tuple[str, ...] = ()
    #: Twelve entries, abbreviated. Only used by languages that write dates
    #: with a month name rather than a number.
    months_short: tuple[str, ...] = ()
    #: Seven entries, Monday first, matching date.weekday().
    weekdays_long: tuple[str, ...] = ()
    #: Seven entries, Monday first. These sit in ~40px forecast columns, so
    #: two or three characters.
    weekdays_short: tuple[str, ...] = ()

    #: Fixed captions. Drawn in small capitals, so keep them short:
    #: HUMIDITY shares a row with three other metrics.
    labels: dict[str, str] = {
        "DAYLIGHT": "DAYLIGHT",
        "RAIN": "RAIN",
        "WIND": "WIND",
        "HUMIDITY": "HUMIDITY",
        "UV": "UV",
        "INSIDE": "INSIDE",
        "OUTSIDE": "OUTSIDE",
        "SUNRISE": "SUNRISE",
        "SUNSET": "SUNSET",
        "HOURS_24": "24 HOURS",
        # Shown instead of the curve when the weather integration publishes no
        # hourly forecast. Sentence case: it is a sentence, not a caption.
        "NO_HOURLY": "no hourly forecast",
        # Page 3 status. A blank screen looks like a broken device; naming the
        # reason tells whoever walks past what to check.
        "PHOTO_NONE": "no photo",
        "PHOTO_NOT_FOUND": "no photo found",
        "PHOTO_NO_SOURCE": "no photo source configured",
        "PHOTO_IMMICH_UNREACHABLE": "Immich unreachable",
        "PHOTO_NO_CACHE_DIR": "no cache directory",
        # Prefix for a photo taken from a local folder. "Immich" is a product
        # name and stays as it is.
        "PHOTO_SOURCE_FOLDER": "Folder",
    }

    # --- table access --------------------------------------------------------
    def label(self, key: str) -> str:
        """A fixed caption. Falls back to the base table, then to the key
        itself — a missing label shows up on the panel as SUNRISE rather than
        as a traceback."""
        return self.labels.get(key) or Language.labels.get(key, key)

    def condition(self, slug: str) -> str:
        """Long condition label. An unknown slug is passed through, which is
        how a new Home Assistant condition degrades: ugly, but truthful."""
        return self.conditions.get(slug) or slug or "—"

    def condition_short(self, slug: str) -> str | None:
        return self.conditions_short.get(slug)

    def weekday_long(self, index: int) -> str:
        return self.weekdays_long[index]

    def weekday_short(self, index: int) -> str:
        return self.weekdays_short[index]

    def month(self, number: int) -> str:
        return self.months[number - 1]

    def month_short(self, number: int) -> str:
        if self.months_short:
            return self.months_short[number - 1]
        return self.month(number)[:3]

    # --- formatting ----------------------------------------------------------
    def header_date(self, when: datetime | date) -> str:
        """The date line in the header, and the caption under a photo.

        Default: "Saturday, 19 September 2026".
        """
        return (
            f"{self.weekday_long(when.weekday())}, "
            f"{when.day} {self.month(when.month)} {when.year}"
        )

    def waste_date(self, due: date) -> str:
        """The date under a bin label. The sidebar has room for a full weekday
        name, which is read without the beat of decoding that "Thu" costs."""
        return f"{self.weekday_long(due.weekday())} {due.day} {self.month_short(due.month)}"

    def waste_when(self, days_until: int) -> str:
        """The countdown beside a bin label."""
        if days_until < 0:
            return "overdue"
        if days_until == 0:
            return "TODAY"
        if days_until == 1:
            return "TOMORROW"
        return f"in {days_until} days"

    def forecast_label(self, offset: int, when: date) -> str:
        """The column heading in the forecast band. `offset` is days from
        today, so 1 is tomorrow."""
        if offset == 1:
            return "Tomorrow"
        return self.weekday_short(when.weekday())

    def rain_window(self, first_hour: int, last_hour: int) -> str:
        """The window rain falls in. `last_hour` is exclusive."""
        return f"{first_hour:02d}:00-{last_hour:02d}:00"

    def page_marker(self, page: int, page_count: int) -> str:
        return f"Page {page}/{page_count}"

    def battery_estimate(self, days: float) -> str:
        """Days of battery left, in small type under the gauge."""
        if days >= 99:
            return "99+ days"
        if days < 1:
            return "< 1 day"
        rounded = round(days)
        return f"~{rounded} {'day' if rounded == 1 else 'days'}"

    def years_ago(self, years: int) -> str:
        """The part of a photo caption that makes an old picture land."""
        if years == 1:
            return "1 year ago"
        return f"{years} years ago"
