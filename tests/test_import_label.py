"""Tests for the import source_label (used by web uploads)."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from adso import activity as activity_service
from adso import db
from adso.sync import import_goodreads_csv

HEADERS = ["Book Id", "Title", "Author", "Exclusive Shelf", "Bookshelves", "My Rating"]


def _write_csv(path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerow({"Book Id": "1", "Title": "A Book", "Author": "An Author",
                    "Exclusive Shelf": "to-read", "Bookshelves": "", "My Rating": "0"})


class ImportLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "adso.sqlite")
        db.initialize(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_source_label_overrides_recorded_path(self) -> None:
        # Simulate a web upload: read from a temp path, but record the original name.
        temp_upload = self.root / "tmpXXXX.csv"
        _write_csv(temp_upload)

        summary = import_goodreads_csv(
            self.conn, temp_upload, mode="import", source_label="goodreads_library_export.csv"
        )
        self.assertEqual(summary.created, 1)

        runs = activity_service.list_activity(self.conn)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["source_file"], "goodreads_library_export.csv")

    def test_without_label_falls_back_to_path(self) -> None:
        csv_path = self.root / "export.csv"
        _write_csv(csv_path)
        import_goodreads_csv(self.conn, csv_path, mode="import")
        runs = activity_service.list_activity(self.conn)
        self.assertEqual(runs[0]["source_file"], "export.csv")


if __name__ == "__main__":
    unittest.main()
