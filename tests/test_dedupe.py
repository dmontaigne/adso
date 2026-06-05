"""Tests for the duplicate detection/merge service and its DB layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adso import db
from adso import dedupe


class DedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "adso.sqlite")
        db.initialize(self.conn)
        self.run = db.create_import_run(
            self.conn, source="goodreads", source_path="x.csv", mode="sync", row_count=2
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _insert(self, goodreads_id: str, title: str, author: str = "Andy Weir", **extra) -> int:
        record = {
            "goodreads_id": goodreads_id,
            "title": title,
            "author": author,
            "shelves_json": "[]",
        }
        record.update(extra)
        return db.insert_book_from_goodreads(self.conn, record, import_run_id=self.run)

    def _book_ids(self) -> set[int]:
        return {row["id"] for row in self.conn.execute("SELECT id FROM books")}

    def test_group_key_collapses_subtitle_and_case(self) -> None:
        a = {"title": "Moby-Dick; or, the Whale", "author": "Herman Melville"}
        b = {"title": "Moby Dick", "author": "Herman Melville"}
        self.assertEqual(dedupe.group_key(a), dedupe.group_key(b))

    def test_group_slug_is_css_selector_safe(self) -> None:
        # The slug becomes a DOM id and an hx-target selector, so it must contain
        # only alphanumerics and hyphens — no spaces or pipes (which broke HTMX).
        self._insert("1", "The Writing Life", author="Annie Dillard")
        self._insert("2", "The Writing Life", author="Annie Dillard")
        dedupe.scan_duplicates(self.conn)
        slug = dedupe.list_open_duplicates(self.conn)[0]["slug"]
        self.assertRegex(slug, r"^[a-z0-9-]+$")

    def test_scan_flags_a_two_record_group(self) -> None:
        self._insert("1", "Project Hail Mary")
        self._insert("2", "Project Hail Mary")
        self.assertEqual(dedupe.scan_duplicates(self.conn), 1)
        groups = dedupe.list_open_duplicates(self.conn)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)

    def test_scan_ignores_unique_books(self) -> None:
        self._insert("1", "Project Hail Mary")
        self._insert("2", "The Martian")
        self.assertEqual(dedupe.scan_duplicates(self.conn), 0)
        self.assertEqual(dedupe.list_open_duplicates(self.conn), [])

    def test_suggested_keeper_is_newest_import(self) -> None:
        old = self._insert("1", "Project Hail Mary")
        newer_run = db.create_import_run(
            self.conn, source="goodreads", source_path="y.csv", mode="sync", row_count=1
        )
        new = db.insert_book_from_goodreads(
            self.conn,
            {"goodreads_id": "2", "title": "Project Hail Mary", "author": "Andy Weir", "shelves_json": "[]"},
            import_run_id=newer_run,
        )
        dedupe.scan_duplicates(self.conn)
        group = dedupe.list_open_duplicates(self.conn)[0]
        self.assertEqual(group["suggested_keeper_id"], new)
        self.assertNotEqual(group["suggested_keeper_id"], old)

    def test_merge_keeps_keeper_and_deletes_dropped(self) -> None:
        keep = self._insert("1", "Project Hail Mary", reading_status="Read")
        drop = self._insert("2", "Project Hail Mary", reading_status="Currently Reading")
        dedupe.scan_duplicates(self.conn)
        group = dedupe.list_open_duplicates(self.conn)[0]
        outcome = dedupe.merge_duplicate(self.conn, group["group_key"], keep_id=keep)
        self.assertEqual(outcome["merged"], 1)
        self.assertEqual(self._book_ids(), {keep})
        self.assertEqual(dedupe.pending_count(self.conn), 0)

    def test_merge_folds_local_data_from_dropped_into_keeper(self) -> None:
        keep = self._insert("1", "Project Hail Mary")
        drop = self._insert("2", "Project Hail Mary")
        # The dropped record carries local enrichment the keeper lacks.
        self.conn.execute(
            "UPDATE books SET location = ?, owned = 1, local_notes = ? WHERE id = ?",
            ("Office", "signed copy", drop),
        )
        self.conn.commit()
        dedupe.scan_duplicates(self.conn)
        group = dedupe.list_open_duplicates(self.conn)[0]
        dedupe.merge_duplicate(self.conn, group["group_key"], keep_id=keep)
        kept = db.get_book(self.conn, keep)
        self.assertEqual(kept["location"], "Office")
        self.assertEqual(kept["owned"], 1)
        self.assertEqual(kept["local_notes"], "signed copy")

    def test_merge_cleans_child_rows_for_dropped(self) -> None:
        keep = self._insert("1", "Project Hail Mary")
        drop = self._insert("2", "Project Hail Mary")
        dedupe.scan_duplicates(self.conn)
        group = dedupe.list_open_duplicates(self.conn)[0]
        dedupe.merge_duplicate(self.conn, group["group_key"], keep_id=keep)
        snaps = self.conn.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE book_id = ?", (drop,)
        ).fetchone()[0]
        self.assertEqual(snaps, 0)

    def test_dismiss_does_not_resurface_on_rescan(self) -> None:
        self._insert("1", "Project Hail Mary")
        self._insert("2", "Project Hail Mary")
        dedupe.scan_duplicates(self.conn)
        group = dedupe.list_open_duplicates(self.conn)[0]
        dedupe.dismiss_duplicate(self.conn, group["group_key"])
        self.assertEqual(dedupe.pending_count(self.conn), 0)
        dedupe.scan_duplicates(self.conn)
        self.assertEqual(dedupe.pending_count(self.conn), 0)

    def test_flag_duplicates_for_book_links_a_new_twin(self) -> None:
        self._insert("1", "Project Hail Mary")
        twin = self._insert("2", "Project Hail Mary")
        self.assertTrue(dedupe.flag_duplicates_for_book(self.conn, twin))
        self.conn.commit()
        self.assertEqual(dedupe.pending_count(self.conn), 1)

    def test_merge_validates_keeper_membership(self) -> None:
        self._insert("1", "Project Hail Mary")
        self._insert("2", "Project Hail Mary")
        dedupe.scan_duplicates(self.conn)
        group = dedupe.list_open_duplicates(self.conn)[0]
        with self.assertRaises(ValueError):
            dedupe.merge_duplicate(self.conn, group["group_key"], keep_id=9999)


if __name__ == "__main__":
    unittest.main()
