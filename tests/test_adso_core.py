from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from adso import db
from adso.exports import export_csv, export_json
from adso.reports import latest_conflicts_markdown, latest_sync_summary_markdown
from adso.sync import import_goodreads_csv


HEADERS = [
    "Book Id",
    "Title",
    "Author",
    "Author l-f",
    "Additional Authors",
    "ISBN",
    "ISBN13",
    "My Rating",
    "Average Rating",
    "Publisher",
    "Binding",
    "Number of Pages",
    "Year Published",
    "Original Publication Year",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Bookshelves with positions",
    "Exclusive Shelf",
    "My Review",
    "Spoiler",
    "Private Notes",
    "Read Count",
    "Owned Copies",
]


def write_goodreads_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in HEADERS})


def row(**overrides: str) -> dict[str, str]:
    data = {
        "Book Id": "1",
        "Title": "The Name of the Rose",
        "Author": "Umberto Eco",
        "ISBN": '="0156001314"',
        "ISBN13": '="9780156001311"',
        "My Rating": "0",
        "Average Rating": "4.14",
        "Publisher": "Harvest Books",
        "Binding": "Paperback",
        "Number of Pages": "536",
        "Year Published": "1994",
        "Original Publication Year": "1980",
        "Date Added": "2026/04/28",
        "Bookshelves": "to-read",
        "Bookshelves with positions": "to-read (#1)",
        "Exclusive Shelf": "to-read",
        "Read Count": "0",
        "Owned Copies": "0",
    }
    data.update(overrides)
    return data


class AdsoCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = sqlite3.connect(self.root / "adso.sqlite")
        self.conn.row_factory = sqlite3.Row
        db.initialize(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_import_preserves_raw_rows_and_is_idempotent(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])

        first = import_goodreads_csv(self.conn, csv_path, mode="import")
        second = import_goodreads_csv(self.conn, csv_path, mode="sync")

        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.unchanged, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_import_rows").fetchone()[0], 2)

    def test_safe_goodreads_activity_update_when_local_value_unchanged(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")

        changed_path = self.root / "goodreads-changed.csv"
        write_goodreads_csv(
            changed_path,
            [row(**{"Exclusive Shelf": "read", "Bookshelves": "read", "Date Read": "2026/04/28"})],
        )
        summary = import_goodreads_csv(self.conn, changed_path, mode="sync")
        book = self.conn.execute("SELECT * FROM books WHERE goodreads_id = '1'").fetchone()

        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.conflicts, 0)
        self.assertEqual(book["reading_status"], "Read")
        self.assertEqual(book["date_read"], "2026-04-28")
        sync_report = latest_sync_summary_markdown(self.conn)
        self.assertIn("What happened", sync_report)
        self.assertIn("safely updated 1 existing books", sync_report)
        self.assertIn("No manual review is required", sync_report)

    def test_local_physical_fields_survive_goodreads_sync(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")
        db.update_local_fields(
            self.conn,
            "1",
            {"owned": 1, "copy_count": 1, "location": "Office", "shelf_box": "A1"},
        )

        changed_path = self.root / "goodreads-changed.csv"
        write_goodreads_csv(changed_path, [row(**{"My Rating": "5"})])
        import_goodreads_csv(self.conn, changed_path, mode="sync")
        book = self.conn.execute("SELECT * FROM books WHERE goodreads_id = '1'").fetchone()

        self.assertEqual(book["owned"], 1)
        self.assertEqual(book["copy_count"], 1)
        self.assertEqual(book["location"], "Office")
        self.assertEqual(book["shelf_box"], "A1")
        self.assertEqual(book["rating"], 5)

    def test_conflict_report_when_local_activity_field_changed(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")
        self.conn.execute("UPDATE books SET reading_status = ? WHERE goodreads_id = ?", ("Local Status", "1"))
        self.conn.commit()

        changed_path = self.root / "goodreads-changed.csv"
        write_goodreads_csv(changed_path, [row(**{"Exclusive Shelf": "read", "Bookshelves": "read"})])
        summary = import_goodreads_csv(self.conn, changed_path, mode="sync")
        report = latest_conflicts_markdown(self.conn)
        book = self.conn.execute("SELECT * FROM books WHERE goodreads_id = '1'").fetchone()

        self.assertEqual(summary.conflicts, 1)
        self.assertEqual(book["reading_status"], "Local Status")
        self.assertIn("The Name of the Rose", report)
        self.assertIn("reading_status", report)
        self.assertIn("current reading state", report)
        sync_report = latest_sync_summary_markdown(self.conn)
        self.assertIn("Local catalogue values were preserved", sync_report)
        self.assertIn("adso report conflicts", sync_report)

    def test_portable_exports(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")

        json_path = export_json(self.conn, self.root / "catalogue.json")
        csv_export_path = export_csv(self.conn, self.root / "catalogue.csv")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["title"], "The Name of the Rose")
        self.assertIn("goodreads_id,title,author", csv_export_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
