"""Dated local archive of generated originals."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from foredogs_generator.generator import archive_original


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.original = self.root / "foredogs_original.png"
        self.original.write_bytes(b"picture bytes")
        self.archive = self.root / "archive"

    def tearDown(self):
        self._tmp.cleanup()

    def test_copies_under_the_date(self):
        result = archive_original(self.original, self.archive, when=date(2026, 9, 19))
        self.assertEqual(result, self.archive / "2026-09-19.png")
        self.assertEqual(result.read_bytes(), b"picture bytes")
        # The working file stays where the publisher expects it.
        self.assertTrue(self.original.exists())

    def test_missing_original_is_reported_not_raised(self):
        self.original.unlink()
        self.assertIsNone(archive_original(self.original, self.archive))

    def test_keep_days_zero_keeps_everything(self):
        for offset in range(400, 0, -100):
            day = date(2026, 9, 19) - timedelta(days=offset)
            archive_original(self.original, self.archive, keep_days=0, when=day)
        archive_original(self.original, self.archive, keep_days=0, when=date(2026, 9, 19))
        self.assertEqual(len(list(self.archive.glob("*.png"))), 5)

    def test_keep_days_prunes_older_entries(self):
        today = date(2026, 9, 19)
        for offset in (40, 20, 5, 1):
            archive_original(self.original, self.archive, keep_days=0, when=today - timedelta(days=offset))
        archive_original(self.original, self.archive, keep_days=30, when=today)

        kept = sorted(path.stem for path in self.archive.glob("*.png"))
        self.assertNotIn("2026-08-10", kept)  # 40 days back
        self.assertIn("2026-08-30", kept)  # 20 days back
        self.assertIn("2026-09-19", kept)

    def test_hand_placed_files_are_left_alone(self):
        self.archive.mkdir(parents=True)
        stray = self.archive / "keep-me.png"
        stray.write_bytes(b"not mine")
        archive_original(self.original, self.archive, keep_days=1, when=date(2026, 9, 19))
        self.assertTrue(stray.exists())

    def test_rerunning_the_same_day_overwrites_rather_than_duplicates(self):
        archive_original(self.original, self.archive, when=date(2026, 9, 19))
        self.original.write_bytes(b"second attempt")
        archive_original(self.original, self.archive, when=date(2026, 9, 19))
        files = list(self.archive.glob("*.png"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b"second attempt")


if __name__ == "__main__":
    unittest.main()
