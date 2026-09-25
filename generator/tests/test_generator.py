from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fake_provider import FakeProvider

from foredogs_generator.config import AppConfig, DogProfile
from foredogs_generator.generator import run_generation


class GeneratorTests(unittest.TestCase):
    def test_dry_run_writes_status_and_prompt_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "assets/input_images").mkdir(parents=True)
            (root / "imported").mkdir(parents=True)
            (root / "assets/input_images/rex_1.jpg").write_bytes(b"fake")
            (root / "imported/foredogs_prompt_history.txt").write_text("Activity: Old line\n")
            (root / "imported/foredogs_style_history.txt").write_text("pixar 3D animation style\n")

            config = AppConfig(
                location="Testville, Testland",
                dogs=[
                    DogProfile(
                        name="Rex",
                        description="Rex ist ein frecher aber liebherziger Hund.",
                        image_paths=["./assets/input_images/rex_1.jpg"],
                    )
                ],
                art_style_entries=[{"style": "storybook fairytale illustration", "outfit": None}],
                state_dir="./state",
                output_dir="./output",
                seed_prompt_history_path="./imported/foredogs_prompt_history.txt",
                seed_style_history_path="./imported/foredogs_style_history.txt",
            )
            forecast = {
                "datetime": "2026-06-23",
                "condition": "cloudy",
                "temperature": 30,
                "templow": 15,
                "weather_summary_de": "Bewölkt und Ruhig",
            }

            with patch("foredogs_generator.generator.get_provider", return_value=FakeProvider("Activity: Flying a kite, Foreground: Dog running, Background: Canal")):
                result = run_generation(config, root, dry_run=True, forecast_override=forecast)

            status = json.loads(result.status_path.read_text())
            self.assertEqual(status["status"], "dry_run")
            self.assertTrue(result.activity_prompt_path and result.activity_prompt_path.exists())
            self.assertTrue(result.image_prompt_path and result.image_prompt_path.exists())

    def test_dry_run_prompts_include_old_behavior_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "assets/input_images").mkdir(parents=True)
            (root / "assets/input_images/rex_1.jpg").write_bytes(b"fake")

            config = AppConfig(
                location="Testville, Testland",
                dogs=[
                    DogProfile(
                        name="Rex",
                        description="Rex ist ein frecher aber liebherziger Hund.",
                        image_paths=["./assets/input_images/rex_1.jpg"],
                    )
                ],
                art_style_entries=[{"style": "custom rainy noir style", "outfit": None}],
                state_dir="./state",
                output_dir="./output",
            )
            forecast = {
                "datetime": "2026-06-23",
                "condition": "rainy",
                "temperature": 18,
                "templow": 12,
                "weather_summary_de": "Regnerisch und kühl",
            }

            with patch("foredogs_generator.generator.get_provider", return_value=FakeProvider("Activity: Waiting for the bus, Foreground: Dog waiting, Background: Bus stop")):
                result = run_generation(config, root, dry_run=True, forecast_override=forecast)

            activity_prompt = result.activity_prompt_path.read_text()
            image_prompt = result.image_prompt_path.read_text()
            status = json.loads(result.status_path.read_text())

            self.assertIn("Activities should be 50% set in locations in Testville, Testland", activity_prompt)
            self.assertIn("The activity should involve all 1 dogs.", activity_prompt)
            self.assertEqual(status["style"]["style"], "custom rainy noir style")
            self.assertIn("Rex ist ein frecher aber liebherziger Hund.", image_prompt)
            self.assertIn("The art style should be:", image_prompt)


if __name__ == "__main__":
    unittest.main()
