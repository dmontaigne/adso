"""Tests for the activity (import history) service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adso import activity as activity_service
from adso import db


class ActivityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "adso.sqlite")
        db.initialize(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _run(self, *, mode: str, source_path: str) -> int:
        run_id = db.create_import_run(
            self.conn, source="goodreads", source_path=source_path, mode=mode, row_count=10
        )
        db.update_import_run_counts(
            self.conn, run_id, created=2, updated=3, unchanged=4, conflicts=1
        )
        self.conn.commit()
        return run_id

    def test_empty_history(self) -> None:
        self.assertEqual(activity_service.list_activity(self.conn), [])

    def test_lists_runs_newest_first_with_basename_and_counts(self) -> None:
        self._run(mode="import", source_path="/home/me/first.csv")
        second = self._run(mode="sync", source_path="/home/me/second.csv")

        runs = activity_service.list_activity(self.conn)
        self.assertEqual(len(runs), 2)
        # Newest first.
        self.assertEqual(runs[0]["id"], second)
        self.assertEqual(runs[0]["mode"], "sync")
        self.assertEqual(runs[0]["source_file"], "second.csv")
        self.assertEqual(runs[0]["row_count"], 10)
        self.assertEqual(runs[0]["created"], 2)
        self.assertEqual(runs[0]["updated"], 3)
        self.assertEqual(runs[0]["unchanged"], 4)
        self.assertEqual(runs[0]["conflicts"], 1)

    def test_limit(self) -> None:
        self._run(mode="import", source_path="a.csv")
        self._run(mode="sync", source_path="b.csv")
        self._run(mode="sync", source_path="c.csv")
        self.assertEqual(len(activity_service.list_activity(self.conn, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
