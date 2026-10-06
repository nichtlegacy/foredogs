"""Catching up on days Immich missed."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from foredogs_generator import immich
from foredogs_generator.immich import ImmichError, ImmichTarget, archive_backlog


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = Path(self._tmp.name)
        self.target = ImmichTarget(url="https://immich.example", api_key="k", album_id="album")
        for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
            path = self.archive / f"{day}.png"
            path.write_bytes(day.encode())
            stamp = datetime.fromisoformat(f"{day}T04:33:00").timestamp()
            os.utime(path, (stamp, stamp))
        (self.archive / "notes.png").write_bytes(b"hand-placed")

    def tearDown(self):
        self._tmp.cleanup()

    def _check_reply(self, present: set[str]):
        def api(target, method, path, payload=None):
            if path == "/api/assets/bulk-upload-check":
                return {
                    "results": [
                        {"id": a["id"], "action": "reject", "reason": "duplicate", "assetId": "x"}
                        if a["id"] in present
                        else {"id": a["id"], "action": "accept"}
                        for a in payload["assets"]
                    ]
                }
            if path.startswith("/api/albums/"):
                return [{"success": True}]
            raise AssertionError(path)

        return api

    def test_uploads_only_the_missing_days(self):
        uploads = []
        with mock.patch.object(immich, "_api", side_effect=self._check_reply({"2026-09-19.png"})), \
             mock.patch.object(immich, "upload", side_effect=lambda t, image, taken: uploads.append((image.name, taken)) or "id"):
            sent = archive_backlog(self.target, self.archive)

        self.assertEqual(sent, ["2026-09-20.png", "2026-09-21.png"])
        self.assertEqual(uploads[0], ("2026-09-20.png", datetime(2026, 9, 20, 4, 33)))

    def test_touched_file_still_lands_on_its_day(self):
        path = self.archive / "2026-09-20.png"
        later = datetime(2026, 10, 6, 12, 0).timestamp()
        os.utime(path, (later, later))
        uploads = []
        present = {"2026-09-19.png", "2026-09-21.png"}
        with mock.patch.object(immich, "_api", side_effect=self._check_reply(present)), \
             mock.patch.object(immich, "upload", side_effect=lambda t, image, taken: uploads.append(taken) or "id"):
            archive_backlog(self.target, self.archive)

        self.assertEqual(uploads, [datetime(2026, 9, 20, 5, 0)])

    def test_nothing_missing_sends_nothing(self):
        present = {"2026-09-19.png", "2026-09-20.png", "2026-09-21.png"}
        with mock.patch.object(immich, "_api", side_effect=self._check_reply(present)), \
             mock.patch.object(immich, "upload") as upload:
            self.assertEqual(archive_backlog(self.target, self.archive), [])
        upload.assert_not_called()

    def test_failure_stops_the_catch_up(self):
        with mock.patch.object(immich, "_api", side_effect=self._check_reply(set())), \
             mock.patch.object(immich, "upload", side_effect=ImmichError("down")) as upload:
            with self.assertRaises(ImmichError):
                archive_backlog(self.target, self.archive)
        self.assertEqual(upload.call_count, 1)

    def test_missing_archive_dir_is_not_an_error(self):
        self.assertEqual(archive_backlog(self.target, self.archive / "nope"), [])


if __name__ == "__main__":
    unittest.main()
