"""The language registry and every language's contract."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path

from _component import COMPONENT, languages

from _component import module as _module

base = _module("languages.base")


class RegistryTests(unittest.TestCase):
    def test_every_module_in_the_folder_is_registered(self):
        """A language nobody registered is a language nobody can select.

        This is the check that lets the registry stay an explicit dict: adding
        a file without adding the line fails here rather than silently doing
        nothing.
        """
        on_disk = {
            path.stem
            for path in (COMPONENT / "languages").glob("*.py")
            if path.stem not in {"__init__", "base"}
        }
        registered = set(languages._MODULES)
        self.assertEqual(on_disk, registered)

    def test_default_is_available(self):
        self.assertIn(languages.DEFAULT_LANGUAGE, languages.available_languages())

    def test_codes_match_their_registration(self):
        for code in languages.available_languages():
            self.assertEqual(languages.get_language(code).code, code)

    def test_unknown_code_falls_back_rather_than_raising(self):
        """A typo in an automation should cost a German panel, not a blank one."""
        with self.assertLogs(languages.__name__, level="WARNING"):
            resolved = languages.get_language("klingon")
        self.assertEqual(resolved.code, languages.DEFAULT_LANGUAGE)

    def test_none_and_empty_fall_back(self):
        for value in (None, "", "   "):
            self.assertEqual(
                languages.get_language(value).code, languages.DEFAULT_LANGUAGE
            )

    def test_locale_strings_resolve_to_their_base_language(self):
        """Home Assistant hands out "en-GB" and "de_DE" in different places."""
        self.assertEqual(languages.get_language("en-GB").code, "en")
        self.assertEqual(languages.get_language("de_DE").code, "de")
        self.assertEqual(languages.get_language("EN").code, "en")

    def test_instances_are_cached(self):
        self.assertIs(languages.get_language("de"), languages.get_language("de"))


class ContractTests(unittest.TestCase):
    """Run against every registered language, so a new one cannot ship half
    finished without the suite noticing."""

    def setUp(self):
        self.languages = [languages.get_language(c) for c in languages.available_languages()]

    def test_all_condition_slugs_are_translated(self):
        """Home Assistant's weather conditions, as of 2024.x. A missing slug
        renders as the raw slug, which is ugly but not fatal — still, a shipped
        language should cover them."""
        slugs = {
            "clear-night", "cloudy", "exceptional", "fog", "hail", "lightning",
            "lightning-rainy", "partlycloudy", "pouring", "rainy", "snowy",
            "snowy-rainy", "sunny", "windy", "windy-variant",
        }
        for language in self.languages:
            with self.subTest(language=language.code):
                self.assertEqual(slugs - set(language.conditions), set())

    def test_all_labels_are_defined(self):
        for language in self.languages:
            with self.subTest(language=language.code):
                for key in base.Language.labels:
                    self.assertTrue(language.label(key), f"{key} is empty")

    def test_tables_have_the_right_length(self):
        for language in self.languages:
            with self.subTest(language=language.code):
                self.assertEqual(len(language.months), 12)
                self.assertEqual(len(language.weekdays_long), 7)
                self.assertEqual(len(language.weekdays_short), 7)

    def test_weekday_short_stays_narrow(self):
        """The forecast columns are about 40px wide."""
        for language in self.languages:
            with self.subTest(language=language.code):
                for name in language.weekdays_short:
                    self.assertLessEqual(len(name), 3, name)

    def test_formatting_methods_return_non_empty_strings(self):
        when = datetime(2026, 9, 19, 7, 30)
        for language in self.languages:
            with self.subTest(language=language.code):
                self.assertTrue(language.header_date(when))
                self.assertTrue(language.waste_date(date(2026, 10, 1)))
                self.assertTrue(language.rain_window(14, 17))
                self.assertTrue(language.page_marker(2, 3))
                self.assertTrue(language.years_ago(2))
                for days in (-1, 0, 1, 3, 40):
                    self.assertTrue(language.waste_when(days))
                for left in (0.4, 1.0, 1.4, 23.4, 120.0):
                    self.assertTrue(language.battery_estimate(left))

    def test_battery_estimate_boundaries(self):
        """The three cases the gauge has: capped, singular, and under a day."""
        for language in self.languages:
            with self.subTest(language=language.code):
                self.assertIn("99+", language.battery_estimate(99.0))
                self.assertIn("99+", language.battery_estimate(500.0))
                self.assertIn("1", language.battery_estimate(0.5))
                singular = language.battery_estimate(1.0)
                plural = language.battery_estimate(23.0)
                self.assertNotEqual(singular, plural)

    def test_unknown_condition_slug_passes_through(self):
        for language in self.languages:
            with self.subTest(language=language.code):
                self.assertEqual(language.condition("moon-rain"), "moon-rain")

    def test_unknown_label_key_returns_the_key(self):
        """So a future status string still says something on the panel."""
        for language in self.languages:
            with self.subTest(language=language.code):
                self.assertEqual(language.label("NOT_A_KEY"), "NOT_A_KEY")


class GermanTests(unittest.TestCase):
    """The panel's own language, spelled out. These are the strings the live
    installation draws, so they are asserted exactly."""

    def setUp(self):
        self.de = languages.get_language("de")

    def test_header_date(self):
        self.assertEqual(
            self.de.header_date(datetime(2026, 9, 19, 5, 45)),
            "Samstag, 19. September 2026",
        )

    def test_waste(self):
        self.assertEqual(self.de.waste_date(date(2026, 10, 1)), "Donnerstag 01.10.")
        self.assertEqual(self.de.waste_when(-1), "überfällig")
        self.assertEqual(self.de.waste_when(0), "HEUTE")
        self.assertEqual(self.de.waste_when(1), "MORGEN")
        self.assertEqual(self.de.waste_when(3), "in 3 Tagen")

    def test_forecast_label(self):
        self.assertEqual(self.de.forecast_label(1, date(2026, 9, 20)), "Morgen")
        self.assertEqual(self.de.forecast_label(2, date(2026, 9, 21)), "Mo")

    def test_rain_window_and_page(self):
        self.assertEqual(self.de.rain_window(14, 17), "14-17 Uhr")
        self.assertEqual(self.de.page_marker(2, 3), "Seite 2/3")

    def test_battery(self):
        self.assertEqual(self.de.battery_estimate(23.4), "~23 Tage")
        self.assertEqual(self.de.battery_estimate(1.0), "~1 Tag")
        self.assertEqual(self.de.battery_estimate(0.5), "< 1 Tag")
        self.assertEqual(self.de.battery_estimate(120.0), "99+ Tage")

    def test_years_ago(self):
        self.assertEqual(self.de.years_ago(1), "vor 1 Jahr")
        self.assertEqual(self.de.years_ago(2), "vor 2 Jahren")


class EnglishTests(unittest.TestCase):
    def setUp(self):
        self.en = languages.get_language("en")

    def test_header_date(self):
        self.assertEqual(
            self.en.header_date(datetime(2026, 9, 19, 5, 45)),
            "Saturday, 19 September 2026",
        )

    def test_waste(self):
        self.assertEqual(self.en.waste_date(date(2026, 10, 1)), "Thursday 1 Oct")
        self.assertEqual(self.en.waste_when(0), "TODAY")
        self.assertEqual(self.en.waste_when(1), "TOMORROW")
        self.assertEqual(self.en.waste_when(3), "in 3 days")

    def test_forecast_label(self):
        self.assertEqual(self.en.forecast_label(1, date(2026, 9, 20)), "Tomorrow")
        self.assertEqual(self.en.forecast_label(2, date(2026, 9, 21)), "Mon")

    def test_rain_window_and_page(self):
        self.assertEqual(self.en.rain_window(14, 17), "14:00-17:00")
        self.assertEqual(self.en.page_marker(2, 3), "Page 2/3")

    def test_battery(self):
        self.assertEqual(self.en.battery_estimate(23.4), "~23 days")
        self.assertEqual(self.en.battery_estimate(1.0), "~1 day")
        self.assertEqual(self.en.battery_estimate(0.5), "< 1 day")


if __name__ == "__main__":
    unittest.main()
