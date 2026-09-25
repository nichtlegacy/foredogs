"""Weather lookup via Open-Meteo."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from .config import WeatherConfig


WEATHER_CODE_TO_CONDITION = {
    0: "sunny",
    1: "partlycloudy",
    2: "partlycloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "rainy",
    53: "rainy",
    55: "pouring",
    56: "snowy-rainy",
    57: "snowy-rainy",
    61: "rainy",
    63: "rainy",
    65: "pouring",
    66: "snowy-rainy",
    67: "snowy-rainy",
    71: "snowy",
    73: "snowy",
    75: "snowy",
    77: "snowy",
    80: "rainy",
    81: "rainy",
    82: "pouring",
    85: "snowy",
    86: "snowy",
    95: "lightning-rainy",
    96: "lightning",
    99: "lightning",
}

WEATHER_CODE_TO_GERMAN = {
    0: "Sonnig und Mild",
    1: "Leicht Bewölkt",
    2: "Wolkig und Mild",
    3: "Bewölkt und Ruhig",
    45: "Neblig und Still",
    48: "Neblig und Kühl",
    51: "Nieselig und Kühl",
    53: "Feucht und Trüb",
    55: "Regnerisch und Kühl",
    56: "Eisig und Nass",
    57: "Winterlich und Nass",
    61: "Leichter Regen",
    63: "Regen und Grau",
    65: "Starker Regen",
    66: "Kalt und Glatt",
    67: "Nass und Frostig",
    71: "Leichter Schnee",
    73: "Schnee und Kalt",
    75: "Viel Schnee",
    77: "Flockig und Kalt",
    80: "Schauer und Frisch",
    81: "Schauer und Wind",
    82: "Sturm und Regen",
    85: "Schneeschauer Kalt",
    86: "Viel Schneefall",
    95: "Gewitter und Regen",
    96: "Gewitter und Hagel",
    99: "Schweres Gewitter",
}


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def geocode_location(location: str, weather: WeatherConfig) -> tuple[float, float, str]:
    """Resolve location to latitude, longitude, and display name."""
    queries = [
        location,
        re.sub(r"\s*\([^)]*\)", "", location).strip(),
        location.replace(",", " "),
        re.sub(r"[^A-Za-zÄÖÜäöüß\s-]", " ", location).strip(),
        location.split(",")[0].strip(),
        re.sub(r"\s*\([^)]*\)", "", location.split(",")[0]).strip(),
    ]

    results = []
    for candidate in dict.fromkeys(filter(None, queries)):
        params = urllib.parse.urlencode(
            {
                "name": candidate,
                "count": 1,
                "language": weather.geocoding_language,
                "format": "json",
            }
        )
        payload = _fetch_json(f"{weather.geocoding_url}?{params}")
        results = payload.get("results") or []
        if results:
            break

    if not results:
        raise RuntimeError(f"Could not geocode location: {location}")

    first = results[0]
    display_name = ", ".join(part for part in [first.get("name"), first.get("admin1"), first.get("country")] if part)
    return float(first["latitude"]), float(first["longitude"]), display_name


def fetch_daily_forecast(location: str, weather: WeatherConfig) -> dict:
    """Fetch next daily forecast in HA-like shape."""
    if weather.provider != "open-meteo":
        raise ValueError(f"Unsupported weather provider: {weather.provider}")

    latitude, longitude, resolved_name = geocode_location(location, weather)
    params = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": weather.timezone,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
            "forecast_days": weather.forecast_days,
        }
    )
    payload = _fetch_json(f"{weather.forecast_url}?{params}")
    daily = payload["daily"]
    weather_code = int(daily["weather_code"][0])

    return {
        "location_resolved": resolved_name,
        "latitude": latitude,
        "longitude": longitude,
        "datetime": daily["time"][0],
        "condition": WEATHER_CODE_TO_CONDITION.get(weather_code, "cloudy"),
        "temperature": round(float(daily["temperature_2m_max"][0])),
        "templow": round(float(daily["temperature_2m_min"][0])),
        "weather_code": weather_code,
        "weather_summary_de": WEATHER_CODE_TO_GERMAN.get(weather_code, "Wetter und Wolken"),
        "precipitation_sum": float(daily["precipitation_sum"][0]),
        "precipitation_probability_max": float(daily["precipitation_probability_max"][0] or 0),
        "wind_speed_10m_max": float(daily["wind_speed_10m_max"][0]),
    }
