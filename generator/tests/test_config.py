from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from foredogs_generator.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_reads_dogs_and_art_styles_from_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir(parents=True)

            (root / "config/dogs.json").write_text(
                json.dumps(
                    {
                        "dogs": [
                            {
                                "name": "Rex",
                                "description": "Frech und lieb.",
                                "image_paths": ["./assets/rex_1.jpg", "./assets/rex_2.jpg"],
                            }
                        ]
                    }
                )
            )
            (root / "config/art_styles.json").write_text(
                json.dumps({"art_styles": [{"style": "custom style", "outfit": None}]})
            )
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "location": "Testville, Testland",
                        "paths": {
                            "dogs_file": "./config/dogs.json",
                            "art_styles_file": "./config/art_styles.json",
                        },
                        "generator": {
                            "output_dir": "./custom-output",
                            "final_image_size": "1024x768",
                        },
                    }
                )
            )

            config = load_config(root / "config.json")

            self.assertEqual(config.location, "Testville, Testland")
            self.assertEqual(config.dog_names, ["Rex"])
            self.assertEqual(config.dog_descriptions, ["Frech und lieb."])
            self.assertEqual(config.input_image_paths, ["./assets/rex_1.jpg", "./assets/rex_2.jpg"])
            self.assertEqual(config.art_style_entries[0]["style"], "custom style")
            self.assertEqual(config.output_dir, "./custom-output")
            self.assertEqual(config.final_image_size, "1024x768")


if __name__ == "__main__":
    unittest.main()
