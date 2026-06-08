"""Web (FastAPI) tests for the conflict decision UI.

Skipped unless the optional ``[web]`` test dependency stack — including ``httpx``,
which the FastAPI ``TestClient`` needs — is installed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adso import db

try:  # TestClient requires httpx; the web extra may not be installed.
    import httpx  # noqa: F401
    from fastapi.testclient import TestClient

    _HAS_TESTCLIENT = True
except Exception:  # pragma: no cover - exercised only without the web extra
    _HAS_TESTCLIENT = False


@unittest.skipUnless(_HAS_TESTCLIENT, "fastapi TestClient (httpx) not installed")
class ConflictWebTests(unittest.TestCase):
    def setUp(self) -> None:
        from adso.web.app import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "adso.sqlite")
        conn = db.connect(self.db_path)
        db.initialize(conn)
        run = db.create_import_run(
            conn, source="goodreads", source_path="x.csv", mode="sync", row_count=1
        )
        book_id = db.insert_book_from_goodreads(
            conn,
            {"goodreads_id": "1", "title": "Test Book", "reading_status": "To Read", "shelves_json": "[]"},
            import_run_id=run,
        )
        conn.execute("UPDATE books SET reading_status = 'Currently Reading' WHERE id = ?", (book_id,))
        db.add_conflict(
            conn,
            import_run_id=run,
            book_id=book_id,
            source="goodreads",
            field_name="reading_status",
            old_source_value="To Read",
            local_value="Currently Reading",
            incoming_value="Read",
        )
        conn.commit()
        self.conflict_id = conn.execute("SELECT id FROM sync_conflicts").fetchone()[0]
        conn.close()
        self.client = TestClient(create_app(self.db_path))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _resolve(self, choice: str):
        return self.client.post(f"/conflicts/{self.conflict_id}/resolve", data={"choice": choice})

    def test_conflicts_page_lists_open_conflict(self) -> None:
        response = self.client.get("/conflicts")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Reading status", response.text)
        self.assertIn("Review later", response.text)

    def test_decision_lifecycle_through_http(self) -> None:
        deferred = self._resolve("later")
        self.assertEqual(deferred.status_code, 200)
        self.assertIn("marked for later review", deferred.text)
        self.assertIn('hx-swap-oob="true"', deferred.text)

        reopened = self._resolve("reopen")
        self.assertIn("Keep your local value", reopened.text)  # back to the editable field

        accepted = self._resolve("incoming")
        self.assertIn("accepted Goodreads value", accepted.text)
        self.assertIn("via web", accepted.text)

        page = self.client.get("/conflicts")
        self.assertIn("Recently decided", page.text)


if __name__ == "__main__":
    unittest.main()
