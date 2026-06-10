from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adso import db
from adso.metadata import (
    OPENLIBRARY_SEARCH,
    PLACES_CAP,
    SUBJECTS_CAP,
    _clean_subjects,
    _parse_description,
    fetch_metadata,
)


class FakeResp:
    def __init__(self, *, status_code: int = 200, json_data=None) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers: dict[str, str] = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def edition_payload(work_key="/works/OL1W", isbn_13=None, isbn_10=None) -> dict:
    payload: dict = {"works": [{"key": work_key}]}
    if isbn_13:
        payload["isbn_13"] = isbn_13
    if isbn_10:
        payload["isbn_10"] = isbn_10
    return payload


def work_payload(description=None, subjects=None, places=None, times=None) -> dict:
    payload: dict = {}
    if description is not None:
        payload["description"] = description
    if subjects is not None:
        payload["subjects"] = subjects
    if places is not None:
        payload["subject_places"] = places
    if times is not None:
        payload["subject_times"] = times
    return payload


def search_payload(work_key="/works/OL1W", cover_edition_key=None, edition_keys=None) -> dict:
    doc: dict = {"key": work_key}
    if cover_edition_key:
        doc["cover_edition_key"] = cover_edition_key
    if edition_keys:
        doc["edition_key"] = edition_keys
    return {"docs": [doc]}


class MetadataHelperTests(unittest.TestCase):
    def test_parse_description_both_shapes(self) -> None:
        self.assertEqual(_parse_description("Plain text."), "Plain text.")
        self.assertEqual(
            _parse_description({"type": "/type/text", "value": " Wrapped. "}), "Wrapped."
        )
        self.assertIsNone(_parse_description(None))
        self.assertIsNone(_parse_description({"type": "/type/text"}))
        self.assertIsNone(_parse_description("   "))
        self.assertIsNone(_parse_description(42))

    def test_clean_subjects_filters_dedupes_and_caps(self) -> None:
        raw = [
            "Accessible book",
            "Protected DAISY",
            "nyt:hardcover-fiction=2020-01-01",
            "Mystery fiction",
            "mystery FICTION",
            "  Monastic   life  ",
            "x" * 80,
            42,
            "",
        ]
        self.assertEqual(_clean_subjects(raw, cap=25), ["Mystery fiction", "Monastic life"])
        many = [f"subject {i}" for i in range(40)]
        self.assertEqual(len(_clean_subjects(many, cap=SUBJECTS_CAP)), SUBJECTS_CAP)
        self.assertEqual(_clean_subjects("not-a-list", cap=PLACES_CAP), [])

    def test_clean_subjects_drops_non_english_tags(self) -> None:
        # OL aggregates subjects across translated editions; foreign duplicates
        # are dropped by the non-ASCII check, the foreign-word set, or the
        # Spanish century pattern.
        raw = [
            "Biografía",          # non-ASCII
            "França",             # non-ASCII
            "14e siècle",         # non-ASCII
            "Noblesse",           # foreign word
            "Kultur",             # foreign word
            "Histoire",           # foreign word
            "France, histoire",   # foreign word inside a phrase
            "S. XIV",             # Spanish century code
            "s.XV",               # Spanish century code, no space
            "Nobility",
            "Middle Ages",
            "Roman Empire",       # must survive: 'roman' is not in the word set
            "Mass media",         # must survive: 'media' is not in the word set
        ]
        self.assertEqual(
            _clean_subjects(raw, cap=25),
            ["Nobility", "Middle Ages", "Roman Empire", "Mass media"],
        )


class MetadataFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = sqlite3.connect(self.root / "adso.sqlite")
        self.conn.row_factory = sqlite3.Row
        db.initialize(self.conn)
        self._sleep_patch = patch("adso.metadata.time.sleep", lambda *_a, **_k: None)
        self._sleep_patch.start()

    def tearDown(self) -> None:
        self._sleep_patch.stop()
        self.conn.close()
        self.tmp.cleanup()

    def _add_book(self, goodreads_id, title, *, isbn13=None, isbn10=None, author=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO books (goodreads_id, title, author, isbn13, isbn10) VALUES (?, ?, ?, ?, ?)",
            (goodreads_id, title, author, isbn13, isbn10),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _book(self, goodreads_id):
        return db.get_book_by_goodreads_id(self.conn, goodreads_id)

    def test_isbn_path_stores_description_and_subjects(self) -> None:
        self._add_book("1", "The Name of the Rose", isbn13="9780156001311")

        def fake_request(method, url, **kwargs):
            if url == "https://openlibrary.org/isbn/9780156001311.json":
                return FakeResp(json_data=edition_payload())
            if url == "https://openlibrary.org/works/OL1W.json":
                return FakeResp(
                    json_data=work_payload(
                        description="A mystery in a medieval abbey.",
                        subjects=["Mystery fiction", "Monasticism"],
                        places=["Italy"],
                        times=["Middle Ages"],
                    )
                )
            raise AssertionError(f"unexpected request to {url}")

        with patch("adso.metadata._request", side_effect=fake_request):
            result = fetch_metadata(self.conn)

        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["isbn_backfilled"], 0)
        book = self._book("1")
        self.assertEqual(book["metadata_status"], "fetched")
        self.assertEqual(book["metadata_source"], "openlibrary:isbn")
        self.assertEqual(book["description"], "A mystery in a medieval abbey.")
        self.assertEqual(json.loads(book["subjects_json"]), ["Mystery fiction", "Monasticism"])
        self.assertEqual(json.loads(book["subject_places_json"]), ["Italy"])
        self.assertEqual(json.loads(book["subject_times_json"]), ["Middle Ages"])

    def test_dict_shaped_description_is_unwrapped(self) -> None:
        self._add_book("2", "Wrapped", isbn13="1111111111111")

        def fake_request(method, url, **kwargs):
            if "isbn" in url:
                return FakeResp(json_data=edition_payload())
            return FakeResp(
                json_data=work_payload(description={"type": "/type/text", "value": "Unwrapped."})
            )

        with patch("adso.metadata._request", side_effect=fake_request):
            fetch_metadata(self.conn)

        self.assertEqual(self._book("2")["description"], "Unwrapped.")

    def test_subjects_without_description_still_counts_as_fetched(self) -> None:
        self._add_book("3", "No Blurb", isbn13="2222222222222")

        def fake_request(method, url, **kwargs):
            if "isbn" in url:
                return FakeResp(json_data=edition_payload())
            return FakeResp(json_data=work_payload(subjects=["History"]))

        with patch("adso.metadata._request", side_effect=fake_request):
            result = fetch_metadata(self.conn)

        self.assertEqual(result["fetched"], 1)
        book = self._book("3")
        self.assertEqual(book["metadata_status"], "fetched")
        self.assertIsNone(book["description"])

    def test_empty_work_is_not_found(self) -> None:
        self._add_book("4", "Empty Work", isbn13="3333333333333")

        def fake_request(method, url, **kwargs):
            if "isbn" in url:
                return FakeResp(json_data=edition_payload())
            return FakeResp(json_data=work_payload())

        with patch("adso.metadata._request", side_effect=fake_request):
            result = fetch_metadata(self.conn)

        self.assertEqual(result["not_found"], 1)
        self.assertEqual(self._book("4")["metadata_status"], "not_found")

    def test_search_path_backfills_missing_isbns(self) -> None:
        self._add_book("5", "No ISBN Here", author="A. Writer")

        def fake_request(method, url, **kwargs):
            if url == OPENLIBRARY_SEARCH:
                return FakeResp(json_data=search_payload(cover_edition_key="OL1M"))
            if url == "https://openlibrary.org/works/OL1W.json":
                return FakeResp(json_data=work_payload(description="Found via search."))
            if url == "https://openlibrary.org/books/OL1M.json":
                return FakeResp(
                    json_data=edition_payload(isbn_13=["9780000000001"], isbn_10=["0000000001"])
                )
            raise AssertionError(f"unexpected request to {url}")

        with patch("adso.metadata._request", side_effect=fake_request):
            result = fetch_metadata(self.conn)

        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["isbn_backfilled"], 1)
        book = self._book("5")
        self.assertEqual(book["metadata_source"], "openlibrary:search")
        self.assertEqual(book["isbn13"], "9780000000001")
        self.assertEqual(book["isbn10"], "0000000001")

    def test_search_path_never_fetches_edition_when_isbn_present(self) -> None:
        self._add_book("6", "Has ISBN", isbn13="4444444444444")
        urls = []

        def fake_request(method, url, **kwargs):
            urls.append(url)
            if "isbn/4444444444444" in url:
                return FakeResp(status_code=404)
            if url == OPENLIBRARY_SEARCH:
                return FakeResp(json_data=search_payload())
            if "works" in url:
                return FakeResp(json_data=work_payload(description="Via search."))
            raise AssertionError(f"unexpected request to {url}")

        with patch("adso.metadata._request", side_effect=fake_request):
            fetch_metadata(self.conn)

        book = self._book("6")
        self.assertEqual(book["isbn13"], "4444444444444")  # untouched
        self.assertFalse(any("/books/" in url for url in urls))  # no edition fetch

    def test_backfill_isbns_never_overwrites(self) -> None:
        book_id = self._add_book("7", "Filled", isbn13="5555555555555")
        changed = db.backfill_isbns(self.conn, book_id, isbn13="9999999999999", isbn10="123456789X")
        book = self._book("7")
        self.assertEqual(book["isbn13"], "5555555555555")
        self.assertEqual(book["isbn10"], "123456789X")  # was empty -> filled
        self.assertTrue(changed)

    def test_idempotency_refresh_and_retry_missing(self) -> None:
        self._add_book("8", "Once", isbn13="6666666666666")

        def hit(method, url, **kwargs):
            if "isbn" in url:
                return FakeResp(json_data=edition_payload())
            return FakeResp(json_data=work_payload(description="Hit."))

        def miss(method, url, **kwargs):
            return FakeResp(status_code=404)

        with patch("adso.metadata._request", side_effect=hit):
            first = fetch_metadata(self.conn)
        self.assertEqual(first["fetched"], 1)

        # Already fetched -> skipped without any HTTP.
        with patch("adso.metadata._request", side_effect=AssertionError("no requests expected")):
            second = fetch_metadata(self.conn)
        self.assertEqual(second["skipped"], 1)

        # --refresh reconsiders it.
        with patch("adso.metadata._request", side_effect=miss):
            third = fetch_metadata(self.conn, refresh=True)
        self.assertEqual(third["not_found"], 1)

        # not_found is skipped unless --retry-missing.
        with patch("adso.metadata._request", side_effect=AssertionError("no requests expected")):
            fourth = fetch_metadata(self.conn)
        self.assertEqual(fourth["skipped"], 1)
        with patch("adso.metadata._request", side_effect=hit):
            fifth = fetch_metadata(self.conn, retry_missing=True)
        self.assertEqual(fifth["fetched"], 1)

    def test_dry_run_writes_nothing_and_limit_caps_attempts(self) -> None:
        self._add_book("9", "Dry", isbn13="7777777777777")
        self._add_book("10", "Beyond Limit", isbn13="8888888888888")

        def fake_request(method, url, **kwargs):
            if "isbn" in url:
                return FakeResp(json_data=edition_payload())
            return FakeResp(json_data=work_payload(description="Dry run."))

        with patch("adso.metadata._request", side_effect=fake_request):
            result = fetch_metadata(self.conn, dry_run=True, limit=1)

        self.assertEqual(result["fetched"], 1)
        self.assertIsNone(self._book("9")["metadata_status"])
        self.assertIsNone(self._book("10")["metadata_status"])

    def test_persistent_429_is_bounded_and_treated_as_miss(self) -> None:
        self._add_book("11", "Throttled", isbn13="9999999999990")

        class FakeRequests:
            def __init__(self):
                self.calls = 0

            def request(self, *a, **k):
                self.calls += 1
                return FakeResp(status_code=429)

        fake = FakeRequests()
        with patch("adso.ol_http.require_requests", return_value=fake), patch(
            "adso.ol_http.time.sleep", lambda *_a, **_k: None
        ):
            result = fetch_metadata(self.conn)

        self.assertEqual(result["not_found"], 1)
        self.assertEqual(self._book("11")["metadata_status"], "not_found")
        self.assertLess(fake.calls, 20)


class MetadataMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "adso.sqlite")
        db.initialize(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_pre_metadata_catalogue_gains_columns_and_search_index(self) -> None:
        # Simulate a catalogue from before metadata: no metadata columns,
        # FTS built over the metadata-less column set.
        self.conn.executescript(
            """
            DROP TRIGGER IF EXISTS books_fts_ai;
            DROP TRIGGER IF EXISTS books_fts_ad;
            DROP TRIGGER IF EXISTS books_fts_au;
            DROP TABLE IF EXISTS books_fts;
            ALTER TABLE books DROP COLUMN description;
            ALTER TABLE books DROP COLUMN subjects_json;
            ALTER TABLE books DROP COLUMN subject_places_json;
            ALTER TABLE books DROP COLUMN subject_times_json;
            ALTER TABLE books DROP COLUMN metadata_source;
            ALTER TABLE books DROP COLUMN metadata_source_url;
            ALTER TABLE books DROP COLUMN metadata_status;
            ALTER TABLE books DROP COLUMN metadata_fetched_at;
            """
        )
        self.conn.execute(
            "INSERT INTO books (goodreads_id, title, loaned_to) VALUES ('1', 'Meditations', 'Sam')"
        )
        self.conn.commit()

        db.initialize(self.conn)

        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(books)")}
        self.assertIn("description", columns)
        self.assertIn("subjects_json", columns)
        self.assertIn("metadata_status", columns)

        from adso.catalogue import search_books

        book_id = int(self.conn.execute("SELECT id FROM books").fetchone()["id"])
        db.set_metadata(
            self.conn,
            book_id,
            description="Stoic reflections.",
            subjects=["Stoicism"],
            subject_places=["Rome"],
            subject_times=[],
            metadata_source="openlibrary:isbn",
            metadata_source_url="https://openlibrary.org/isbn/x.json",
            metadata_status="fetched",
        )
        # FTS was rebuilt over the new tuple: subjects and places are searchable,
        # and so are pre-existing indexed fields.
        self.assertEqual([b["goodreads_id"] for b in search_books(self.conn, "stoicism")], ["1"])
        self.assertEqual([b["goodreads_id"] for b in search_books(self.conn, "rome")], ["1"])
        self.assertEqual([b["goodreads_id"] for b in search_books(self.conn, "sam")], ["1"])


if __name__ == "__main__":
    unittest.main()
