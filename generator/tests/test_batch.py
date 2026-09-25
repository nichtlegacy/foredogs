from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fake_provider import FakeProvider

from foredogs_generator.batch import build_scenarios, run_batch
from foredogs_generator.generator import GenerationPlan, GenerationResult, STYLE_HISTORY_LIMIT


class BatchTests(unittest.TestCase):
    def test_build_scenarios_rejects_more_than_20_items(self) -> None:
        with self.assertRaises(ValueError):
            build_scenarios({"datetime": "2026-06-23T10:00:00+00:00"}, 21)

    def test_run_batch_writes_manifest_and_dates_each_style_pick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "assets/input_images").mkdir(parents=True)
            (root / "imported").mkdir(parents=True)
            (root / "config").mkdir(parents=True)
            (root / "assets/input_images/rex_1.jpg").write_bytes(b"fake")
            (root / "imported/foredogs_prompt_history.txt").write_text("Activity: Old line\n")

            seed_styles = {"art_styles": [{"style": f"style_{index}", "outfit": None} for index in range(1, 26)]}
            (root / "config/art_styles.json").write_text(json.dumps(seed_styles))
            (root / "imported/foredogs_style_history.txt").write_text("".join(f"style_{index}\n" for index in range(1, 26)))
            (root / "config/dogs.json").write_text(
                json.dumps(
                    {
                        "dogs": [
                            {
                                "name": "Rex",
                                "description": "Rex ist ein frecher aber liebherziger Hund.",
                                "image_paths": ["./assets/input_images/rex_1.jpg"],
                            }
                        ]
                    }
                )
            )

            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "location": "Testville, Testland",
                        "paths": {
                            "dogs_file": "./config/dogs.json",
                            "art_styles_file": "./config/art_styles.json",
                        },
                        "generator": {
                            "state_dir": "./state",
                            "output_dir": "./output",
                            "seed_prompt_history_path": "./imported/foredogs_prompt_history.txt",
                            "seed_style_history_path": "./imported/foredogs_style_history.txt",
                        },
                    }
                )
            )

            forecast_path = root / "forecast.json"
            forecast_path.write_text(
                json.dumps(
                    {
                        "datetime": "2026-06-23T10:00:00+00:00",
                        "condition": "cloudy",
                        "temperature": 23,
                        "templow": 15,
                        "weather_summary_de": "Bewölkt",
                    }
                )
            )

            with patch(
                "foredogs_generator.generator.get_provider",
                return_value=FakeProvider(
                    [
                        "Activity: One, Foreground: Dog, Background: Park",
                        "Activity: Two, Foreground: Dog, Background: Street",
                    ]
                ),
            ):
                summary = run_batch(
                    config_path=config_path,
                    forecast_path=forecast_path,
                    count=2,
                    parallel=1,
                    dry_run=True,
                    project_dir=root,
                )

            manifest = json.loads(Path(summary["manifest_path"]).read_text())
            self.assertEqual(len(manifest["items"]), 2)

            state_dir = Path(summary["batch_state_dir"])
            style_history = (state_dir / "foredogs_style_history.txt").read_text().splitlines()

            # 25 undated seed entries plus one dated line per batch item.
            self.assertEqual(len(style_history), 27)
            self.assertLessEqual(len(style_history), STYLE_HISTORY_LIMIT)

            # Each scenario illustrates its own day, so each pick is dated with it.
            picks = [line.split("\t") for line in style_history[-2:]]
            self.assertEqual([date for date, _ in picks], ["2026-06-23", "2026-06-24"])
            # The second item must not repeat the first: recording the pick
            # immediately makes it the freshest style in the pool.
            self.assertNotEqual(picks[0][1], picks[1][1])

    def test_run_batch_parallel_renders_in_completion_order_but_manifest_stays_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "assets/input_images").mkdir(parents=True)
            (root / "imported").mkdir(parents=True)
            (root / "config").mkdir(parents=True)
            (root / "assets/input_images/rex_1.jpg").write_bytes(b"fake")
            (root / "imported/foredogs_prompt_history.txt").write_text("Activity: Old line\n")
            (root / "imported/foredogs_style_history.txt").write_text("style_0\n")
            (root / "config/art_styles.json").write_text(json.dumps({"art_styles": [{"style": "style_1", "outfit": None}]}))
            (root / "config/dogs.json").write_text(
                json.dumps(
                    {
                        "dogs": [
                            {
                                "name": "Rex",
                                "description": "Rex ist ein frecher aber liebherziger Hund.",
                                "image_paths": ["./assets/input_images/rex_1.jpg"],
                            }
                        ]
                    }
                )
            )
            config_path = root / "config.json"
            forecast_path = root / "forecast.json"
            config_path.write_text(
                json.dumps(
                    {
                        "location": "Testville, Testland",
                        "paths": {
                            "dogs_file": "./config/dogs.json",
                            "art_styles_file": "./config/art_styles.json",
                        },
                        "generator": {
                            "state_dir": "./state",
                            "output_dir": "./output",
                            "seed_prompt_history_path": "./imported/foredogs_prompt_history.txt",
                            "seed_style_history_path": "./imported/foredogs_style_history.txt",
                        },
                    }
                )
            )
            forecast_path.write_text(json.dumps({"datetime": "2026-06-23T10:00:00+00:00", "condition": "cloudy"}))

            def fake_prepare(config, project_dir, forecast_override):
                index = int(str(config.output_dir).split("/")[-1].split("_", 1)[0])
                output_dir = Path(config.output_dir)
                return GenerationPlan(
                    config=config,
                    reference_images=[],
                    original_path=output_dir / "foredogs_original.png",
                    optimized_path=output_dir / "foredogs_optimized.png",
                    archive_dir=output_dir / "archive",
                    status_path=output_dir / "foredogs_generation_status.json",
                    forecast=forecast_override,
                    style_entry={"style": f"style_{index}", "outfit": None},
                    activity=f"Activity: {index}, Foreground: Dog, Background: Park",
                    image_prompt=f"prompt {index}",
                    activity_prompt_path=output_dir / "latest_activity_prompt.txt",
                    image_prompt_path=output_dir / "latest_image_prompt.txt",
                )

            def fake_render(plan, dry_run=False):
                plan.original_path.parent.mkdir(parents=True, exist_ok=True)
                plan.original_path.write_bytes(b"original")
                plan.optimized_path.write_bytes(b"optimized")
                return GenerationResult(
                    original_path=plan.original_path,
                    optimized_path=plan.optimized_path,
                    status_path=plan.status_path,
                    forecast=plan.forecast,
                    style_entry=plan.style_entry,
                    activity=plan.activity,
                    activity_prompt_path=plan.activity_prompt_path,
                    image_prompt_path=plan.image_prompt_path,
                )

            with (
                patch("foredogs_generator.batch.prepare_generation", side_effect=fake_prepare),
                patch("foredogs_generator.batch.render_generation_plan", side_effect=fake_render),
            ):
                summary = run_batch(
                    config_path=config_path,
                    forecast_path=forecast_path,
                    count=3,
                    parallel=2,
                    dry_run=False,
                    project_dir=root,
                )

            manifest = json.loads(Path(summary["manifest_path"]).read_text())
            self.assertEqual([item["index"] for item in manifest["items"]], [1, 2, 3])
            self.assertEqual(len(manifest["items"]), 3)


if __name__ == "__main__":
    unittest.main()
