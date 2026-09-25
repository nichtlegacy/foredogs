from __future__ import annotations

import urllib.parse
import unittest
from unittest.mock import patch

from foredogs_generator.config import WeatherConfig
from foredogs_generator.weather import fetch_daily_forecast, geocode_location


class WeatherTests(unittest.TestCase):
    def test_geocode_location_tries_fallback_queries(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str) -> dict:
            calls.append(url)
            query_value = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("name", [""])[0]
            if query_value == "Testville, Testland":
                return {}
            return {
                "results": [
                    {
                        "name": "Testville",
                        "latitude": 52.52143,
                        "longitude": 7.31845,
                        "country": "Testland",
                        "admin1": "Test Region",
                    }
                ]
            }

        with patch("foredogs_generator.weather._fetch_json", side_effect=fake_fetch):
            result = geocode_location("Testville, Testland", WeatherConfig())

        self.assertEqual(result, (52.52143, 7.31845, "Testville, Test Region, Testland"))
        self.assertGreaterEqual(len(calls), 2)

    def test_fetch_daily_forecast_normalizes_payload(self) -> None:
        def fake_fetch(url: str) -> dict:
            if "geocoding-api" in url:
                return {
                    "results": [
                        {
                            "name": "Testville",
                            "latitude": 52.52143,
                            "longitude": 7.31845,
                            "country": "Testland",
                            "admin1": "Test Region",
                        }
                    ]
                }
            return {
                "daily": {
                    "time": ["2026-06-23"],
                    "weather_code": [3],
                    "temperature_2m_max": [30.1],
                    "temperature_2m_min": [15.4],
                    "precipitation_sum": [0.0],
                    "precipitation_probability_max": [2],
                    "wind_speed_10m_max": [11.9],
                }
            }

        with patch("foredogs_generator.weather._fetch_json", side_effect=fake_fetch):
            forecast = fetch_daily_forecast("Testville, Testland", WeatherConfig())

        self.assertEqual(forecast["condition"], "cloudy")
        self.assertEqual(forecast["temperature"], 30)
        self.assertEqual(forecast["templow"], 15)
        self.assertEqual(forecast["weather_summary_de"], "Bewölkt und Ruhig")


if __name__ == "__main__":
    unittest.main()
