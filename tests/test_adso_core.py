from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from adso.catalogue import BookFilters, get_book, list_books, search_books
from adso import db
from adso.cli import main as cli_main
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

    def test_catalogue_query_service_lists_filters_and_limits_books(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(
            csv_path,
            [
                row(),
                row(
                    **{
                        "Book Id": "2",
                        "Title": "The Left Hand of Darkness",
                        "Author": "Ursula K. Le Guin",
                        "Exclusive Shelf": "read",
                        "Bookshelves": "read, fiction",
                    }
                ),
                row(
                    **{
                        "Book Id": "3",
                        "Title": "A Room of One's Own",
                        "Author": "Virginia Woolf",
                        "Exclusive Shelf": "currently-reading",
                        "Bookshelves": "currently-reading, essays",
                    }
                ),
            ],
        )
        import_goodreads_csv(self.conn, csv_path, mode="import")
        db.update_local_fields(self.conn, "2", {"owned": 1, "location": "Office"})

        all_books = list_books(self.conn)
        read_books = list_books(self.conn, BookFilters(status="Read"))
        owned_books = list_books(self.conn, BookFilters(owned=True))
        office_books = list_books(self.conn, BookFilters(location="off"))
        limited_books = list_books(self.conn, BookFilters(limit=2))

        self.assertEqual([book["title"] for book in all_books], [
            "A Room of One's Own",
            "The Left Hand of Darkness",
            "The Name of the Rose",
        ])
        self.assertEqual([book["goodreads_id"] for book in read_books], ["2"])
        self.assertEqual([book["goodreads_id"] for book in owned_books], ["2"])
        self.assertEqual([book["goodreads_id"] for book in office_books], ["2"])
        self.assertEqual(len(limited_books), 2)
        self.assertIs(limited_books[0]["owned"], False)

    def test_catalogue_query_service_searches_and_gets_books(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(
            csv_path,
            [
                row(),
                row(
                    **{
                        "Book Id": "2",
                        "Title": "The Left Hand of Darkness",
                        "Author": "Ursula K. Le Guin",
                        "Bookshelves": "fiction, science-fiction",
                        "My Review": "A remarkable novel about winter and society.",
                    }
                ),
            ],
        )
        import_goodreads_csv(self.conn, csv_path, mode="import")
        db.update_local_fields(
            self.conn,
            "2",
            {"owned": 1, "location": "Office", "shelf_box": "Shelf B", "local_notes": "Lending copy"},
        )

        search_results = search_books(self.conn, "winter")
        filtered_results = search_books(self.conn, "novel", BookFilters(owned=True))
        book = get_book(self.conn, "2")
        missing = get_book(self.conn, "missing")

        self.assertEqual([result["goodreads_id"] for result in search_results], ["2"])
        self.assertEqual([result["goodreads_id"] for result in filtered_results], ["2"])
        self.assertEqual(book["title"], "The Left Hand of Darkness")
        self.assertEqual(book["shelves"], ["fiction", "science-fiction"])
        self.assertTrue(book["owned"])
        self.assertEqual(book["location"], "Office")
        self.assertIsNone(missing)

    def test_cli_list_outputs_readable_rows_and_filters(self) -> None:
        csv_path = self.root / "goodreads.csv"
        db_path = self.root / "cli.sqlite"
        write_goodreads_csv(
            csv_path,
            [
                row(),
                row(
                    **{
                        "Book Id": "2",
                        "Title": "The Left Hand of Darkness",
                        "Author": "Ursula K. Le Guin",
                        "Exclusive Shelf": "read",
                        "Bookshelves": "read, fiction",
                    }
                ),
            ],
        )
        _run_cli(["--db", str(db_path), "import", "goodreads", str(csv_path)])
        conn = db.connect(db_path)
        db.update_local_fields(conn, "2", {"owned": 1, "location": "Office"})
        conn.close()

        output = _run_cli(["--db", str(db_path), "list"])
        filtered = _run_cli(["--db", str(db_path), "list", "--owned", "true", "--location", "office"])
        no_match = _run_cli(["--db", str(db_path), "list", "--status", "Currently Reading"])

        self.assertIn("Goodreads ID", output)
        self.assertIn("The Left Hand of Darkness", output)
        self.assertIn("The Name of the Rose", output)
        self.assertIn("Office", filtered)
        self.assertIn("yes", filtered)
        self.assertNotIn("The Name of the Rose", filtered)
        self.assertEqual(no_match.strip(), "No books found.")

def _run_cli(argv: list[str]) -> str:
    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = cli_main(argv)
    if exit_code != 0:
        raise AssertionError(f"CLI returned {exit_code}")
    return stdout.getvalue()


if __name__ == "__main__":
    unittest.main()
