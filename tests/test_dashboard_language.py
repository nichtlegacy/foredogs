"""The renderer honours the language it is handed."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from _component import dashboard_pages, dashboard_render, languages

DashboardData = dashboard_render.DashboardData
ForecastDay = dashboard_render.ForecastDay
HourPoint = dashboard_render.HourPoint
PhotoInfo = dashboard_render.PhotoInfo
WasteEntry = dashboard_render.WasteEntry


def _data(language: str = "de", **overrides) -> DashboardData:
    today = date(2026, 9, 19)
    now = datetime(2026, 9, 19, 5, 45)
    data = DashboardData(
        now=now,
        language=languages.get_language(language),
        outside_temp=18.7,
        outside_condition="partlycloudy",
        outside_humidity=87.0,
        inside_temp=24.8,
        inside_humidity=59.7,
        wind_speed=20.2,
        uv_index=4.0,
        battery=43.4,
        battery_days_left=23.4,
        sun_rise="05:42",
        sun_set="21:38",
        temp_high=20.5,
        temp_low=14.5,
        rain_total=1.1,
        daylight_hours="15h 42m",
    )
    data.rain_window = data.language.rain_window(18, 20)
    data.waste = [
        WasteEntry(label="Restabfall", kind="rest", due=today + timedelta(days=3)),
        WasteEntry(label="Papier", kind="papier", due=today + timedelta(days=16)),
    ]
    data.forecast = [
        ForecastDay(
            label="",
            condition=condition,
            temp_high=20.0 + offset,
            temp_low=10.0 + offset,
            when=today + timedelta(days=offset),
            offset=offset,
        )
        for offset, condition in enumerate(
            ["rainy", "sunny", "lightning-rainy", "cloudy", "snowy", "pouring", "sunny"],
            start=1,
        )
    ]
    data.hourly = [
        HourPoint(when=now + timedelta(hours=h), temp=15.0 + h % 8, condition="cloudy")
        for h in range(24)
    ]
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class TextTests(unittest.TestCase):
    def test_condition_label_follows_the_language(self):
        de = languages.get_language("de")
        en = languages.get_language("en")
        self.assertEqual(dashboard_render.condition_label(de, "rainy"), "Regen")
        self.assertEqual(dashboard_render.condition_label(en, "rainy"), "Rain")

    def test_waste_entry_text_follows_the_language(self):
        entry = WasteEntry(label="Papier", kind="papier", due=date.today() + timedelta(days=3))
        de = languages.get_language("de")
        en = languages.get_language("en")
        self.assertEqual(entry.when_text(de), "in 3 Tagen")
        self.assertEqual(entry.when_text(en), "in 3 days")
        self.assertNotEqual(entry.date_text(de), entry.date_text(en))

    def test_forecast_heading_prefers_the_date_over_the_baked_label(self):
        """A collector that formatted a label in one language must not leak it
        into a panel rendered in another."""
        day = ForecastDay(
            label="Morgen",
            condition="sunny",
            temp_high=20.0,
            temp_low=10.0,
            when=date(2026, 9, 20),
            offset=1,
        )
        self.assertEqual(day.heading(languages.get_language("en")), "Tomorrow")
        self.assertEqual(day.heading(languages.get_language("de")), "Morgen")

    def test_forecast_heading_falls_back_to_the_label_without_a_date(self):
        day = ForecastDay(label="Mo", condition="sunny", temp_high=20.0, temp_low=10.0)
        self.assertEqual(day.heading(languages.get_language("en")), "Mo")

    def test_battery_estimate_label_follows_the_language(self):
        self.assertEqual(
            dashboard_render.battery_estimate_label(_data("de")), "~23 Tage"
        )
        self.assertEqual(
            dashboard_render.battery_estimate_label(_data("en")), "~23 days"
        )

    def test_battery_estimate_absent_without_a_figure(self):
        self.assertIsNone(
            dashboard_render.battery_estimate_label(_data("de", battery_days_left=None))
        )

    def test_photo_source_caption_translates_the_kind_but_not_immich(self):
        folder = PhotoInfo(path=Path("/x/y.jpg"), source_kind="folder", source_name="archive")
        immich = PhotoInfo(path=Path("/x/y.jpg"), source_kind="immich", source_name="Holidays")
        de = languages.get_language("de")
        en = languages.get_language("en")
        self.assertEqual(dashboard_pages._source_caption(folder, de), "Ordner · archive")
        self.assertEqual(dashboard_pages._source_caption(folder, en), "Folder · archive")
        self.assertEqual(dashboard_pages._source_caption(immich, de), "Immich · Holidays")
        self.assertEqual(dashboard_pages._source_caption(immich, en), "Immich · Holidays")

    def test_photo_source_caption_empty_without_a_kind(self):
        self.assertEqual(
            dashboard_pages._source_caption(PhotoInfo(path=Path("/x/y.jpg")),
                                            languages.get_language("de")),
            "",
        )


class FingerprintTests(unittest.TestCase):
    def test_language_is_part_of_the_fingerprint(self):
        """Otherwise switching language changes every label on the panel while
        the device happily decides nothing has changed."""
        self.assertNotEqual(
            dashboard_render.fingerprint(_data("de")),
            dashboard_render.fingerprint(_data("en")),
        )

    def test_same_language_same_fingerprint(self):
        self.assertEqual(
            dashboard_render.fingerprint(_data("de")),
            dashboard_render.fingerprint(_data("de")),
        )


class RenderTests(unittest.TestCase):
    """Every page, in every language, actually renders."""

    def test_all_pages_render_in_all_languages(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            for code in languages.available_languages():
                for page in (1, 2):
                    with self.subTest(language=code, page=page):
                        data = _data(code, page=page, page_count=3)
                        out = Path(tmp) / f"{code}-{page}.png"
                        dashboard_render.render_dashboard(data, out, dither_photo=False)
                        self.assertTrue(out.exists())
                        with Image.open(out) as img:
                            self.assertEqual(
                                img.size, (dashboard_render.WIDTH, dashboard_render.HEIGHT)
                            )

    def test_rendered_pages_differ_between_languages(self):
        """A smoke test that the language reaches the canvas at all: the same
        data drawn in two languages cannot produce identical pixels."""
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for code in ("de", "en"):
                out = Path(tmp) / f"{code}.png"
                dashboard_render.render_dashboard(_data(code), out, dither_photo=False)
                paths.append(out.read_bytes())
            self.assertNotEqual(paths[0], paths[1])

    def test_only_six_colours_reach_the_panel(self):
        """The refactor must not have introduced an anti-aliased label: Spectra
        6 has six inks and anything else dithers into speckle."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "en.png"
            dashboard_render.render_dashboard(_data("en"), out, dither_photo=False)
            with Image.open(out) as img:
                used = {c for _, c in img.convert("RGB").getcolors(maxcolors=1 << 24)}
        self.assertTrue(
            used <= set(dashboard_render.SPECTRA6),
            f"unexpected colours: {used - set(dashboard_render.SPECTRA6)}",
        )


if __name__ == "__main__":
    unittest.main()
