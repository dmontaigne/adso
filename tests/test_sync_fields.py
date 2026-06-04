"""Tests for informational (non-tracked) sync fields like average_rating."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from adso import db
from adso.sync import import_goodreads_csv

HEADERS = [
    "Book Id", "Title", "Author", "Additional Authors", "ISBN", "ISBN13",
    "Exclusive Shelf", "Bookshelves", "My Rating", "Average Rating",
    "Number of Pages", "Original Publication Year", "Publisher",
]


def _base_row(**overrides) -> dict:
    row = {
        "Book Id": "1", "Title": "A Book", "Author": "An Author",
        "Additional Authors": "Helper One", "ISBN": "1234567890", "ISBN13": "9781234567890",
        "Exclusive Shelf": "to-read", "Bookshelves": "", "My Rating": "0",
        "Average Rating": "4.23", "Number of Pages": "100",
        "Original Publication Year": "2000", "Publisher": "Text Publishing",
    }
    row.update(overrides)
    return row


def _write(path: Path, row: dict) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerow(row)


class InformationalFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "adso.sqlite")
        db.initialize(self.conn)
        first = self.root / "first.csv"
        _write(first, _base_row(**{"Average Rating": "4.23"}))
        import_goodreads_csv(self.conn, first, mode="import")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _avg(self) -> str:
        return self.conn.execute(
            "SELECT average_rating FROM books WHERE goodreads_id='1'"
        ).fetchone()[0]

    def test_average_rating_drift_is_not_an_update(self) -> None:
        changed = self.root / "changed.csv"
        _write(changed, _base_row(**{"Average Rating": "4.50"}))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.conflicts, 0)
        # ...but the value is still refreshed for display.
        self.assertEqual(self._avg(), "4.50")

    def test_average_rating_blanked_is_not_an_update(self) -> None:
        changed = self.root / "blank.csv"
        _write(changed, _base_row(**{"Average Rating": ""}))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(self._avg(), "")

    def test_publisher_relabel_is_not_an_update(self) -> None:
        changed = self.root / "pub.csv"
        _write(changed, _base_row(**{"Publisher": "TEXT EBOOK"}))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.conflicts, 0)
        publisher = self.conn.execute(
            "SELECT publisher FROM books WHERE goodreads_id='1'"
        ).fetchone()[0]
        self.assertEqual(publisher, "TEXT EBOOK")  # still refreshed for display

    def test_metadata_drift_is_not_an_update(self) -> None:
        # Page counts, ISBNs, edition year, extra authors all drift on Goodreads.
        changed = self.root / "meta.csv"
        _write(changed, _base_row(**{
            "Number of Pages": "222",
            "ISBN": "0987654321",
            "ISBN13": "9780987654321",
            "Original Publication Year": "1999",
            "Additional Authors": "Helper Two",
        }))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.conflicts, 0)
        # ...but the values are still refreshed for display.
        pages = self.conn.execute(
            "SELECT number_of_pages FROM books WHERE goodreads_id='1'"
        ).fetchone()[0]
        self.assertEqual(pages, 222)

    def test_case_only_title_change_is_not_an_update(self) -> None:
        changed = self.root / "case.csv"
        _write(changed, _base_row(**{"Title": "a  BOOK"}))  # only case/whitespace differ
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.conflicts, 0)

    def test_edition_tag_change_is_not_an_update(self) -> None:
        changed = self.root / "edition.csv"
        _write(changed, _base_row(**{"Title": "A Book (Vintage International)"}))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.conflicts, 0)

    def test_subtitle_change_still_counts(self) -> None:
        changed = self.root / "subtitle.csv"
        _write(changed, _base_row(**{"Title": "A Book: A Health Resort Horror Story"}))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.conflicts, 0)

    def test_title_change_still_counts(self) -> None:
        # Identity fields stay tracked so a bad-data change surfaces.
        changed = self.root / "title.csv"
        _write(changed, _base_row(**{"Title": "A Completely Different Book"}))
        summary = import_goodreads_csv(self.conn, changed, mode="sync")
        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.conflicts, 0)
        title = self.conn.execute(
            "SELECT title FROM books WHERE goodreads_id='1'"
        ).fetchone()[0]
        self.assertEqual(title, "A Completely Different Book")


if __name__ == "__main__":
    unittest.main()
