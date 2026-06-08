"""Web (FastAPI) tests for the export / report / sync-status surfaces (DAV-103).

Skipped unless the optional web test stack — including ``httpx``, which the
FastAPI ``TestClient`` needs — is installed. Notion is always patched so these
tests never touch the network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adso import db
from adso.config import ResolvedConfig
from adso.notion import NotionConfigError

try:  # TestClient requires httpx; the web extra may not be installed.
    import httpx  # noqa: F401
    from fastapi.testclient import TestClient

    _HAS_TESTCLIENT = True
except Exception:  # pragma: no cover - exercised only without the web extra
    _HAS_TESTCLIENT = False

HEADERS = ["Book Id", "Title", "Author", "Exclusive Shelf", "Bookshelves", "My Rating"]


def _seed(conn) -> None:
    run = db.create_import_run(conn, source="goodreads", source_path="x.csv", mode="import", row_count=1)
    db.insert_book_from_goodreads(
        conn,
        {"goodreads_id": "1", "title": "The Clockwork Herbarium", "author": "Mara Ellison",
         "reading_status": "Read", "shelves_json": "[]"},
        import_run_id=run,
    )
    conn.commit()


@unittest.skipUnless(_HAS_TESTCLIENT, "fastapi TestClient (httpx) not installed")
class WebSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        from adso.web.app import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "adso.sqlite")
        conn = db.connect(self.db_path)
        db.initialize(conn)
        _seed(conn)
        conn.close()
        self.config = ResolvedConfig(
            db_path=self.db_path,
            profile="personal",
            notion_api_key="secret",
            notion_database_id="db-1234",
            notion_target="production",
        )
        self.client = TestClient(create_app(self.db_path, config=self.config))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_export_page_shows_download_and_notion_target(self) -> None:
        body = self.client.get("/export").text
        self.assertIn("Download CSV", body)
        self.assertIn("Preview (dry run)", body)
        self.assertIn("production", body)  # the configured Notion target

    def test_csv_download_streams_attachment(self) -> None:
        response = self.client.get("/export/catalogue.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("goodreads_id,title,author", response.text)
        self.assertIn("The Clockwork Herbarium", response.text)

    def test_json_download_streams_attachment(self) -> None:
        response = self.client.get("/export/catalogue.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(response.json()[0]["title"], "The Clockwork Herbarium")

    def test_report_pages_render(self) -> None:
        self.assertIn("Sync summary", self.client.get("/reports/summary").text)
        self.assertIn("Conflict report", self.client.get("/reports/conflicts").text)

    def test_notion_preview_renders_planned_actions(self) -> None:
        fake = {"created": 1, "updated": 0, "errors": 0,
                "actions": [{"action": "create", "title": "The Clockwork Herbarium", "goodreads_id": "1"}]}
        with patch("adso.web.app.export_to_notion", return_value=fake) as mock:
            body = self.client.post("/export/notion", data={"dry_run": "true"}).text
        mock.assert_called_once()
        self.assertEqual(mock.call_args.kwargs["dry_run"], True)
        self.assertIn("Dry run", body)
        self.assertIn("Would create", body)

    def test_notion_not_configured_shows_friendly_error(self) -> None:
        with patch("adso.web.app.export_to_notion", side_effect=NotionConfigError("creds required")):
            body = self.client.post("/export/notion", data={"dry_run": "false"}).text
        self.assertIn("alert-destructive", body)
        self.assertIn("creds required", body)


if __name__ == "__main__":
    unittest.main()
