from __future__ import annotations

import unittest
from datetime import date, timedelta

from foredogs_generator.history import StyleUse
from foredogs_generator.styles import (
    LRU_CANDIDATE_FRACTION,
    choose_style,
    get_art_style_string,
    merge_outfits,
)


def make_pool(size: int) -> list[dict]:
    return [{"style": f"style_{index}", "outfit": None} for index in range(size)]


class ChooseStyleTests(unittest.TestCase):
    def test_picks_only_from_the_least_recently_used_quarter(self) -> None:
        pool = make_pool(52)
        # Everything used, oldest first, one style per day.
        history = [
            StyleUse(style=f"style_{index}", used_on=date(2026, 1, 1) + timedelta(days=index))
            for index in range(52)
        ]

        picked = {choose_style(history, style_pool=pool)["style"] for _ in range(200)}

        eligible = {f"style_{index}" for index in range(13)}
        self.assertTrue(picked.issubset(eligible))
        self.assertEqual(len(eligible), round(52 * LRU_CANDIDATE_FRACTION))

    def test_repeated_runs_on_one_day_do_not_age_the_rotation(self) -> None:
        """The bug this replaces: extra runs aged the block list by position."""
        pool = make_pool(52)
        today = date(2026, 8, 12)
        history = [StyleUse(style="style_0", used_on=date(2026, 8, 11))]
        # Nine throwaway runs on a single day, as happened during birthday tests.
        history += [StyleUse(style=f"style_{index}", used_on=today) for index in range(1, 10)]

        picked = {choose_style(history, style_pool=pool)["style"] for _ in range(200)}

        self.assertNotIn("style_0", picked)

    def test_never_used_styles_rank_ahead_of_dated_ones(self) -> None:
        pool = make_pool(8)
        history = [StyleUse(style=f"style_{index}", used_on=date(2026, 8, 1)) for index in range(4)]

        picked = {choose_style(history, style_pool=pool)["style"] for _ in range(100)}

        self.assertEqual(picked, {"style_4", "style_5", "style_6", "style_7"})

    def test_undated_legacy_entries_rank_ahead_of_dated_ones(self) -> None:
        pool = make_pool(8)
        # style_7 comes from a pre-migration history file, the rest are dated.
        history = [StyleUse(style="style_7", used_on=None)]
        history += [
            StyleUse(style=f"style_{index}", used_on=date(2026, 8, 1) + timedelta(days=index))
            for index in range(7)
        ]

        picked = {choose_style(history, style_pool=pool)["style"] for _ in range(100)}

        self.assertIn("style_7", picked)
        self.assertNotIn("style_6", picked)

    def test_accepts_plain_string_history(self) -> None:
        pool = make_pool(8)

        picked = {choose_style(["style_0", "style_1"], style_pool=pool)["style"] for _ in range(100)}

        self.assertNotIn("style_0", picked)

    def test_holiday_outfit_is_added_to_the_style_costume(self) -> None:
        pool = [{"style": "Silver Age superhero comic style", "outfit": "a classic superhero suit"}]

        entry = choose_style([], style_pool=pool, holiday_outfit="Santa hat")

        self.assertEqual(entry["outfit"], "a classic superhero suit, plus Santa hat")
        self.assertEqual(pool[0]["outfit"], "a classic superhero suit")

    def test_holiday_outfit_stands_alone_when_the_style_has_none(self) -> None:
        pool = [{"style": "watercolor painting with soft edges", "outfit": None}]

        entry = choose_style([], style_pool=pool, holiday_outfit="Santa hat")

        self.assertEqual(entry["outfit"], "Santa hat")

    def test_empty_pool_raises(self) -> None:
        with self.assertRaises(ValueError):
            choose_style([], style_pool=[])


class OutfitTests(unittest.TestCase):
    def test_merge_keeps_both(self) -> None:
        self.assertEqual(merge_outfits("Spartan armor", "Santa hat"), "Spartan armor, plus Santa hat")

    def test_merge_handles_missing_sides(self) -> None:
        self.assertEqual(merge_outfits(None, "Santa hat"), "Santa hat")
        self.assertEqual(merge_outfits("Spartan armor", None), "Spartan armor")
        self.assertIsNone(merge_outfits(None, None))

    def test_merge_does_not_repeat_an_outfit_already_present(self) -> None:
        self.assertEqual(merge_outfits("a red Santa hat and scarf", "Santa hat"), "a red Santa hat and scarf")


class ArtStyleStringTests(unittest.TestCase):
    def test_props_are_staged_rather_than_worn(self) -> None:
        entry = {
            "style": "Mario Kart colorful racing style",
            "outfit": "a racing helmet and driving gloves",
            "props": "a go-kart for the dog to sit in, and a red shell held ready",
        }

        text = get_art_style_string(entry)

        self.assertEqual(
            text,
            "Mario Kart colorful racing style"
            " - dress the dog in a racing helmet and driving gloves"
            " - stage the scene with a go-kart for the dog to sit in, and a red shell held ready",
        )

    def test_plain_style_stays_untouched(self) -> None:
        self.assertEqual(
            get_art_style_string({"style": "watercolor painting with soft edges", "outfit": None}),
            "watercolor painting with soft edges",
        )


if __name__ == "__main__":
    unittest.main()
