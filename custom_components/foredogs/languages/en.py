"""English.

Most of the base class is already English, so this module is mainly the
tables. The few methods below exist because the base class deliberately keeps
its defaults neutral rather than British or American.
"""

from __future__ import annotations

from datetime import date

from .base import Language


class English(Language):
    code = "en"
    name = "English"

    conditions = {
        "clear-night": "Clear",
        "cloudy": "Cloudy",
        "exceptional": "Extreme",
        "fog": "Fog",
        "hail": "Hail",
        "lightning": "Thunder",
        "lightning-rainy": "Thunderstorms",
        "partlycloudy": "Partly cloudy",
        "pouring": "Heavy rain",
        "rainy": "Rain",
        "snowy": "Snow",
        "snowy-rainy": "Sleet",
        "sunny": "Sunny",
        "windy": "Windy",
        "windy-variant": "Windy",
    }

    conditions_short = {
        "partlycloudy": "Part cloudy",
        "lightning-rainy": "Storms",
        "pouring": "Heavy rain",
        "windy-variant": "Windy",
        "exceptional": "Extreme",
    }

    months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    months_short = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )

    weekdays_long = (
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    )
    weekdays_short = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def waste_when(self, days_until: int) -> str:
        if days_until < 0:
            return "overdue"
        if days_until == 0:
            return "TODAY"
        if days_until == 1:
            return "TOMORROW"
        return f"in {days_until} days"

    def waste_date(self, due: date) -> str:
        return f"{self.weekday_long(due.weekday())} {due.day} {self.month_short(due.month)}"


LANGUAGE = English()
