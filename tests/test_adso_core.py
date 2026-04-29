from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from builtins import __import__ as real_import
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from adso.catalogue import BookFilters, get_book, list_books, search_books
from adso import db
from adso.cli import main as cli_main
from adso.doctor import doctor_report
from adso.notion import NotionConfigError, _load_existing_pages, export_to_notion
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


class FakeNotionResponse:
    def __init__(
        self,
        data: dict[str, object] | None = None,
        *,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._data = data or {}
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, object]:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text or f"HTTP {self.status_code}")
        return None


class FakeRequestsModule:
    @staticmethod
    def request(*args, **kwargs):
        raise RuntimeError("DNS lookup failed")


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

    def test_synthetic_goodreads_sample_imports_successfully(self) -> None:
        sample_path = Path(__file__).resolve().parent.parent / "examples" / "goodreads_sample.csv"

        summary = import_goodreads_csv(self.conn, sample_path, mode="import")
        rows = self.conn.execute(
            """
            SELECT goodreads_id, title, reading_status, rating, date_read, isbn10, isbn13,
                shelves_json, my_review, private_notes, owned_copies
            FROM books
            ORDER BY goodreads_id
            """
        ).fetchall()

        self.assertEqual(summary.row_count, 3)
        self.assertEqual(summary.created, 3)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(
            [row["reading_status"] for row in rows],
            ["Read", "Currently Reading", "To Read"],
        )
        self.assertEqual(rows[0]["rating"], 5)
        self.assertEqual(rows[0]["date_read"], "2026-01-14")
        self.assertEqual(rows[0]["isbn10"], "1935555012")
        self.assertEqual(rows[0]["isbn13"], "9781935555010")
        self.assertIn("botanical-mystery", rows[0]["shelves_json"])
        self.assertIn("Invented sample row", rows[0]["my_review"])
        self.assertIn("Synthetic demo note", rows[0]["private_notes"])
        self.assertEqual(rows[0]["owned_copies"], 1)

    def test_doctor_reports_brand_new_folder_without_creating_database(self) -> None:
        db_path = self.root / "brand-new.sqlite"
        csv_path = self.root / "goodreads_export.csv"
        write_goodreads_csv(csv_path, [row()])

        output = doctor_report(db_path, root=self.root, env={})

        self.assertFalse(db_path.exists())
        self.assertIn("Database file: no", output)
        self.assertIn("Initialized: no", output)
        self.assertIn("- goodreads_export.csv", output)
        self.assertIn("Credentials configured: no", output)
        self.assertIn("adso init", output)

    def test_doctor_reports_empty_initialized_database(self) -> None:
        output = doctor_report(self.root / "adso.sqlite", root=self.root, env={})

        self.assertIn("Database file: yes", output)
        self.assertIn("Initialized: yes", output)
        self.assertIn("Books: 0", output)
        self.assertIn("Latest import: none yet", output)
        self.assertIn("Pending conflicts: 0", output)
        self.assertIn("adso import goodreads examples/goodreads_sample.csv", output)

    def test_doctor_reports_imported_catalogue_and_notion_config(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")

        output = doctor_report(
            self.root / "adso.sqlite",
            root=self.root,
            env={"NOTION_API_KEY": "secret-value", "NOTION_DB_ID": "database-id"},
        )

        self.assertIn("Books: 1", output)
        self.assertIn("Latest import: import from goodreads", output)
        self.assertIn("Pending conflicts: 0", output)
        self.assertIn("Credentials configured: yes", output)
        self.assertNotIn("secret-value", output)
        self.assertIn("adso export notion", output)

    def test_doctor_prioritizes_pending_conflicts(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")
        self.conn.execute("UPDATE books SET reading_status = ? WHERE goodreads_id = ?", ("Local Status", "1"))
        self.conn.commit()
        changed_path = self.root / "goodreads-changed.csv"
        write_goodreads_csv(changed_path, [row(**{"Exclusive Shelf": "read", "Bookshelves": "read"})])
        import_goodreads_csv(self.conn, changed_path, mode="sync")

        output = doctor_report(self.root / "adso.sqlite", root=self.root, env={})

        self.assertIn("Pending conflicts: 1", output)
        self.assertIn("adso report conflicts", output)

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

    def test_notion_dry_run_reports_create_update_without_writes(self) -> None:
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
                    }
                ),
            ],
        )
        import_goodreads_csv(self.conn, csv_path, mode="import")

        with patch.dict(sys.modules, {"requests": object()}), patch(
            "adso.notion._load_existing_pages", return_value={"2": "page-id"}
        ), patch("adso.notion._request") as request:
            result = export_to_notion(
                self.conn,
                api_key="secret",
                database_id="database",
                dry_run=True,
            )

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(
            [(action["action"], action["goodreads_id"]) for action in result["actions"]],
            [("update", "2"), ("create", "1")],
        )
        request.assert_not_called()

    def test_notion_limit_applies_before_writes(self) -> None:
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
                    }
                ),
            ],
        )
        import_goodreads_csv(self.conn, csv_path, mode="import")

        with patch.dict(sys.modules, {"requests": object()}), patch(
            "adso.notion._load_existing_pages", return_value={"2": "page-id"}
        ), patch("adso.notion._request", return_value=FakeNotionResponse()) as request, patch(
            "adso.notion.time.sleep"
        ):
            result = export_to_notion(
                self.conn,
                api_key="secret",
                database_id="database",
                limit=1,
            )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(
            result["actions"],
            [{"action": "update", "goodreads_id": "2", "title": "The Left Hand of Darkness"}],
        )
        request.assert_called_once()

    def test_notion_existing_page_lookup_uses_goodreads_id(self) -> None:
        response = FakeNotionResponse(
            {
                "results": [
                    {
                        "id": "page-one",
                        "properties": {
                            "Goodreads ID": {"rich_text": [{"plain_text": "1"}]},
                        },
                    },
                    {
                        "id": "page-without-goodreads-id",
                        "properties": {
                            "Goodreads ID": {"rich_text": []},
                        },
                    },
                ],
                "has_more": False,
            }
        )

        with patch("adso.notion._request", return_value=response) as request, patch("adso.notion.time.sleep"):
            existing = _load_existing_pages("secret", "database")

        self.assertEqual(existing, {"1": "page-one"})
        request.assert_called_once_with(
            "secret",
            "post",
            "https://api.notion.com/v1/databases/database/query",
            json={"page_size": 100},
        )

    def test_notion_create_and_update_request_shapes(self) -> None:
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
                    }
                ),
            ],
        )
        import_goodreads_csv(self.conn, csv_path, mode="import")

        with patch.dict(sys.modules, {"requests": object()}), patch(
            "adso.notion._load_existing_pages", return_value={"2": "page-id"}
        ), patch("adso.notion._request", return_value=FakeNotionResponse()) as request, patch(
            "adso.notion.time.sleep"
        ):
            result = export_to_notion(self.conn, api_key="secret", database_id="database")

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(request.call_count, 2)
        update_call, create_call = request.call_args_list
        self.assertEqual(update_call.args[:3], ("secret", "patch", "https://api.notion.com/v1/pages/page-id"))
        self.assertEqual(create_call.args[:3], ("secret", "post", "https://api.notion.com/v1/pages"))
        self.assertEqual(create_call.kwargs["json"]["parent"], {"database_id": "database"})

    def test_notion_reports_missing_credentials_and_optional_dependency(self) -> None:
        def missing_requests_import(name, *args, **kwargs):
            if name == "requests":
                raise ModuleNotFoundError("No module named 'requests'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_requests_import):
            with self.assertRaisesRegex(NotionConfigError, "Install the requests dependency"):
                export_to_notion(self.conn, api_key="secret", database_id="database")

        with patch.dict(sys.modules, {"requests": object()}), patch.dict("adso.notion.os.environ", {}, clear=True):
            with self.assertRaisesRegex(NotionConfigError, "NOTION_API_KEY and NOTION_DB_ID"):
                export_to_notion(self.conn)

    def test_notion_network_failure_reports_clean_error(self) -> None:
        with patch.dict(sys.modules, {"requests": FakeRequestsModule}):
            with self.assertRaisesRegex(NotionConfigError, "Could not reach the Notion API: DNS lookup failed"):
                export_to_notion(self.conn, api_key="secret", database_id="database", dry_run=True)

    def test_notion_lookup_http_error_reports_clean_error(self) -> None:
        response = FakeNotionResponse(status_code=403, text='{"message":"restricted"}')

        with patch("adso.notion._request", return_value=response):
            with self.assertRaisesRegex(NotionConfigError, "Notion database lookup failed: 403"):
                _load_existing_pages("secret", "database")

    def test_notion_write_http_error_counts_error_without_traceback(self) -> None:
        csv_path = self.root / "goodreads.csv"
        write_goodreads_csv(csv_path, [row()])
        import_goodreads_csv(self.conn, csv_path, mode="import")

        with patch.dict(sys.modules, {"requests": object()}), patch(
            "adso.notion._load_existing_pages", return_value={}
        ), patch(
            "adso.notion._request",
            return_value=FakeNotionResponse(status_code=400, text='{"message":"missing property"}'),
        ), patch(
            "adso.notion.time.sleep"
        ):
            result = export_to_notion(self.conn, api_key="secret", database_id="database")

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["errors"], 1)

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
                        "ISBN": '="0441478123"',
                        "ISBN13": '="9780441478125"',
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
        field_searches = {
            "title": search_books(self.conn, "darkness"),
            "author": search_books(self.conn, "guin"),
            "isbn": search_books(self.conn, "9780156001311"),
            "shelves": search_books(self.conn, "science"),
            "local_notes": search_books(self.conn, "lending"),
            "location": search_books(self.conn, "office"),
            "shelf_box": search_books(self.conn, "shelf"),
        }
        book = get_book(self.conn, "2")
        missing = get_book(self.conn, "missing")

        self.assertEqual([result["goodreads_id"] for result in search_results], ["2"])
        self.assertEqual([result["goodreads_id"] for result in filtered_results], ["2"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["title"]], ["2"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["author"]], ["2"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["isbn"]], ["1"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["shelves"]], ["2"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["local_notes"]], ["2"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["location"]], ["2"])
        self.assertEqual([result["goodreads_id"] for result in field_searches["shelf_box"]], ["2"])
        self.assertEqual(book["title"], "The Left Hand of Darkness")
        self.assertEqual(book["shelves"], ["fiction", "science-fiction"])
        self.assertTrue(book["owned"])
        self.assertEqual(book["location"], "Office")
        self.assertIsNone(missing)

    def test_search_uses_fts5_when_available(self) -> None:
        with patch("adso.catalogue._sqlite_supports_fts5", return_value=True), patch(
            "adso.catalogue._search_books_fts", return_value=[{"goodreads_id": "fts"}]
        ) as fts_search:
            results = search_books(self.conn, "winter", BookFilters(owned=True))

        self.assertEqual(results, [{"goodreads_id": "fts"}])
        fts_search.assert_called_once()

    def test_search_finds_review_text_with_fts_query(self) -> None:
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
                        "My Review": "A remarkable novel about winter and society.",
                    }
                ),
            ],
        )
        import_goodreads_csv(self.conn, csv_path, mode="import")

        self.assertEqual(
            [result["goodreads_id"] for result in search_books(self.conn, "winter society")],
            ["2"],
        )

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

    def test_cli_search_outputs_readable_rows_and_filters(self) -> None:
        csv_path = self.root / "goodreads.csv"
        db_path = self.root / "cli-search.sqlite"
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
        _run_cli(["--db", str(db_path), "import", "goodreads", str(csv_path)])
        conn = db.connect(db_path)
        db.update_local_fields(conn, "2", {"owned": 1, "location": "Office", "local_notes": "Lending copy"})
        conn.close()

        output = _run_cli(["--db", str(db_path), "search", "winter", "--owned", "true", "--limit", "1"])
        no_match = _run_cli(["--db", str(db_path), "search", "winter", "--author", "Eco"])

        self.assertIn("Goodreads ID", output)
        self.assertIn("The Left Hand of Darkness", output)
        self.assertIn("Office", output)
        self.assertNotIn("The Name of the Rose", output)
        self.assertEqual(no_match.strip(), "No books found.")

    def test_cli_show_outputs_grouped_detail_and_missing_book_error(self) -> None:
        csv_path = self.root / "goodreads.csv"
        db_path = self.root / "cli-show.sqlite"
        write_goodreads_csv(csv_path, [row(**{"My Rating": "5", "Date Read": "2026/04/28"})])
        _run_cli(["--db", str(db_path), "import", "goodreads", str(csv_path)])
        conn = db.connect(db_path)
        db.update_local_fields(
            conn,
            "1",
            {"owned": 1, "copy_count": 1, "location": "Office", "shelf_box": "A1", "local_notes": "Keeper"},
        )
        conn.close()

        output = _run_cli(["--db", str(db_path), "show", "1"])

        self.assertIn("Goodreads Fields", output)
        self.assertIn("Local Catalogue Fields", output)
        self.assertIn("Title: The Name of the Rose", output)
        self.assertIn("Rating: 5", output)
        self.assertIn("Owned: yes", output)
        self.assertIn("Shelf/Box: A1", output)
        with self.assertRaises(SystemExit) as raised:
            _run_cli(["--db", str(db_path), "show", "missing"])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_doctor_reports_without_initializing_database(self) -> None:
        db_path = self.root / "cli-doctor.sqlite"

        output = _run_cli(["--db", str(db_path), "doctor"])

        self.assertFalse(db_path.exists())
        self.assertIn("Adso Doctor", output)
        self.assertIn("Database file: no", output)

    def test_cli_notion_dry_run_and_limit_output(self) -> None:
        db_path = self.root / "cli-notion.sqlite"

        with patch(
            "adso.cli.export_to_notion",
            return_value={
                "created": 1,
                "updated": 1,
                "errors": 0,
                "actions": [
                    {"action": "create", "goodreads_id": "1", "title": "The Name of the Rose"},
                    {"action": "update", "goodreads_id": "2", "title": "The Left Hand of Darkness"},
                ],
            },
        ) as export:
            output = _run_cli(["--db", str(db_path), "export", "notion", "--dry-run", "--limit", "2"])

        export.assert_called_once()
        self.assertTrue(export.call_args.kwargs["dry_run"])
        self.assertEqual(export.call_args.kwargs["limit"], 2)
        self.assertIn("Notion dry-run complete: 1 would be created, 1 would be updated, 0 errors", output)
        self.assertIn("Would create: The Name of the Rose (Goodreads ID 1)", output)
        self.assertIn("Would update: The Left Hand of Darkness (Goodreads ID 2)", output)


def _run_cli(argv: list[str]) -> str:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = cli_main(argv)
    if exit_code != 0:
        raise AssertionError(f"CLI returned {exit_code}")
    return stdout.getvalue()


if __name__ == "__main__":
    unittest.main()
