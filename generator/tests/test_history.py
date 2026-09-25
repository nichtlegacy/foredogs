from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from foredogs_generator.history import (
    StyleUse,
    format_style_history,
    parse_style_history,
    sanitize_prompt_history,
    sanitize_style_history,
    seed_history_if_missing,
)


class HistoryTests(unittest.TestCase):
    def test_sanitize_prompt_history_keeps_only_activity_lines(self) -> None:
        lines = [
            "This version of Antigravity is no longer supported.",
            "",
            "Activity: Walking through fog, Foreground: Dog walking, Background: Trees",
        ]
        self.assertEqual(
            sanitize_prompt_history(lines),
            ["Activity: Walking through fog, Foreground: Dog walking, Background: Trees"],
        )

    def test_seed_history_if_missing_uses_sanitizer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "source.txt"
            target = temp_path / "target.txt"
            source.write_text("junk\nActivity: Valid line\n")

            seed_history_if_missing(target, source, sanitize_prompt_history)

            self.assertEqual(target.read_text(), "Activity: Valid line\n")

    def test_sanitize_style_history_drops_empty_lines(self) -> None:
        self.assertEqual(sanitize_style_history(["", "pixar", "  ", "anime"]), ["pixar", "anime"])

    def test_parse_style_history_reads_dated_and_legacy_lines(self) -> None:
        uses = parse_style_history(["pixar 3D animation style", "", "2026-08-12\tAmong Us cartoon space style"])

        self.assertEqual(
            uses,
            [
                StyleUse(style="pixar 3D animation style", used_on=None),
                StyleUse(style="Among Us cartoon space style", used_on=date(2026, 8, 12)),
            ],
        )

    def test_parse_style_history_keeps_unparsable_prefix_as_part_of_the_style(self) -> None:
        uses = parse_style_history(["not-a-date\tsome style"])

        self.assertEqual(uses, [StyleUse(style="not-a-date\tsome style", used_on=None)])

    def test_format_style_history_round_trips(self) -> None:
        uses = [
            StyleUse(style="pixar 3D animation style", used_on=None),
            StyleUse(style="watercolor painting with soft edges", used_on=date(2026, 8, 12)),
        ]

        lines = format_style_history(uses)

        self.assertEqual(lines, ["pixar 3D animation style", "2026-08-12\twatercolor painting with soft edges"])
        self.assertEqual(parse_style_history(lines), uses)


if __name__ == "__main__":
    unittest.main()
