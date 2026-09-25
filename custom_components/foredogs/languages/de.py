"""German. The language the panel was built in, and the default."""

from __future__ import annotations

from datetime import date, datetime

from .base import Language


class German(Language):
    code = "de"
    name = "German"

    conditions = {
        "clear-night": "Klar",
        "cloudy": "Bewölkt",
        "exceptional": "Extrem",
        "fog": "Nebel",
        "hail": "Hagel",
        "lightning": "Gewitter",
        "lightning-rainy": "Gewitterregen",
        "partlycloudy": "Teils wolkig",
        "pouring": "Starkregen",
        "rainy": "Regen",
        "snowy": "Schnee",
        "snowy-rainy": "Schneeregen",
        "sunny": "Sonnig",
        "windy": "Windig",
        "windy-variant": "Windig",
    }

    conditions_short = {
        "partlycloudy": "Teils sonnig",
        "lightning-rainy": "Gewitter",
        "snowy-rainy": "Schneeregen",
        "windy-variant": "Windig",
        "exceptional": "Extrem",
    }

    months = (
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    )

    weekdays_long = (
        "Montag", "Dienstag", "Mittwoch", "Donnerstag",
        "Freitag", "Samstag", "Sonntag",
    )
    weekdays_short = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

    labels = {
        "DAYLIGHT": "TAGESLICHT",
        "RAIN": "REGEN",
        "WIND": "WIND",
        "HUMIDITY": "LUFTFEUCHTIGKEIT",
        "UV": "UV",
        "INSIDE": "DRINNEN",
        "OUTSIDE": "DRAUSSEN",
        "SUNRISE": "SONNENAUFGANG",
        "SUNSET": "SONNENUNTERGANG",
        "HOURS_24": "24 STUNDEN",
        "NO_HOURLY": "keine Stundenvorhersage",
        "PHOTO_NONE": "kein Foto",
        "PHOTO_NOT_FOUND": "kein Foto gefunden",
        "PHOTO_NO_SOURCE": "keine Fotoquelle konfiguriert",
        "PHOTO_IMMICH_UNREACHABLE": "Immich nicht erreichbar",
        "PHOTO_NO_CACHE_DIR": "kein Cache-Verzeichnis",
        "PHOTO_SOURCE_FOLDER": "Ordner",
    }

    def header_date(self, when: datetime | date) -> str:
        return (
            f"{self.weekday_long(when.weekday())}, "
            f"{when.day}. {self.month(when.month)} {when.year}"
        )

    def waste_date(self, due: date) -> str:
        return f"{self.weekday_long(due.weekday())} {due.strftime('%d.%m.')}"

    def waste_when(self, days_until: int) -> str:
        if days_until < 0:
            return "überfällig"
        if days_until == 0:
            return "HEUTE"
        if days_until == 1:
            return "MORGEN"
        return f"in {days_until} Tagen"

    def forecast_label(self, offset: int, when: date) -> str:
        if offset == 1:
            return "Morgen"
        return self.weekday_short(when.weekday())

    def rain_window(self, first_hour: int, last_hour: int) -> str:
        return f"{first_hour:02d}-{last_hour:02d} Uhr"

    def page_marker(self, page: int, page_count: int) -> str:
        return f"Seite {page}/{page_count}"

    def battery_estimate(self, days: float) -> str:
        if days >= 99:
            return "99+ Tage"
        if days < 1:
            return "< 1 Tag"
        rounded = round(days)
        return f"~{rounded} {'Tag' if rounded == 1 else 'Tage'}"

    def years_ago(self, years: int) -> str:
        if years == 1:
            return "vor 1 Jahr"
        return f"vor {years} Jahren"


LANGUAGE = German()
