from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from foredogs_generator.styles import get_art_style_string

CATALOGUE = Path(__file__).resolve().parent.parent / "config" / "art_styles.json"
ALLOWED_KEYS = {"style", "outfit", "props", "identity", "universe", "render_as"}
IDENTITY_MODES = {"simplified"}
# Anything worn goes in `outfit`; anything merely present goes in `props`.
NOT_CLOTHING = re.compile(r"\b(sitting|seated|standing|floating|following|walking|riding|nearby)\b", re.I)


class CatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.styles = json.loads(CATALOGUE.read_text())["art_styles"]

    def test_names_are_unique(self) -> None:
        """The style name is the history key: a duplicate would share a slot."""
        names = [style["style"] for style in self.styles]
        self.assertEqual(len(names), len(set(names)))

    def test_every_style_has_a_render_as(self) -> None:
        for style in self.styles:
            with self.subTest(style=style["style"]):
                self.assertTrue(style.get("render_as", "").strip())

    def test_no_unknown_keys(self) -> None:
        for style in self.styles:
            with self.subTest(style=style["style"]):
                self.assertLessEqual(set(style), ALLOWED_KEYS)

    def test_identity_modes_are_known(self) -> None:
        """A typo here would silently fall back to the default identity wording."""
        for style in self.styles:
            identity = style.get("identity")
            if identity is not None:
                with self.subTest(style=style["style"]):
                    self.assertIn(identity, IDENTITY_MODES)

    def test_outfits_describe_something_worn(self) -> None:
        """Guards the bug that produced "dress the dog in sitting in a go-kart"."""
        for style in self.styles:
            outfit = style.get("outfit")
            if outfit:
                with self.subTest(style=style["style"]):
                    self.assertIsNone(NOT_CLOTHING.search(outfit), outfit)

    def test_style_names_stay_short_enough_to_be_stable_keys(self) -> None:
        """Renaming a style loses its last-used date, so names should not drift."""
        for style in self.styles:
            with self.subTest(style=style["style"]):
                self.assertLessEqual(len(style["style"].split()), 25)

    def test_art_style_string_builds_for_every_entry(self) -> None:
        for style in self.styles:
            with self.subTest(style=style["style"]):
                self.assertTrue(get_art_style_string(style).startswith(style["style"]))


if __name__ == "__main__":
    unittest.main()
