"""Tests for the conflict resolution service and its DB layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adso import conflicts as conflicts_service
from adso import db


class ConflictResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "adso.sqlite")
        db.initialize(self.conn)

        # Seed a book whose reading_status diverged locally, then record the
        # conflict the way sync.py would (base/local/incoming all differ).
        run = db.create_import_run(
            self.conn, source="goodreads", source_path="x.csv", mode="sync", row_count=1
        )
        self.book_id = db.insert_book_from_goodreads(
            self.conn,
            {"goodreads_id": "1", "title": "Test Book", "reading_status": "To Read", "shelves_json": "[]"},
            import_run_id=run,
        )
        self.conn.execute(
            "UPDATE books SET reading_status = ? WHERE id = ?",
            ("Currently Reading", self.book_id),
        )
        db.add_conflict(
            self.conn,
            import_run_id=run,
            book_id=self.book_id,
            source="goodreads",
            field_name="reading_status",
            old_source_value="To Read",
            local_value="Currently Reading",
            incoming_value="Read",
        )
        self.conn.commit()
        self.conflict_id = self.conn.execute("SELECT id FROM sync_conflicts").fetchone()[0]

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _reading_status(self) -> str:
        return self.conn.execute(
            "SELECT reading_status FROM books WHERE id = ?", (self.book_id,)
        ).fetchone()[0]

    def test_migration_adds_resolution_columns(self) -> None:
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(sync_conflicts)")}
        self.assertIn("resolution", cols)
        self.assertIn("resolved_at", cols)

    def test_list_open_conflicts_groups_and_labels(self) -> None:
        groups = conflicts_service.list_open_conflicts(self.conn)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["title"], "Test Book")
        self.assertEqual(len(group["conflicts"]), 1)
        conflict = group["conflicts"][0]
        self.assertEqual(conflict["field_label"], "Reading status")
        self.assertEqual(conflict["base"], "To Read")
        self.assertEqual(conflict["local"], "Currently Reading")
        self.assertEqual(conflict["incoming"], "Read")

    def test_keep_local_preserves_book_and_marks_resolved(self) -> None:
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="local")
        self.assertEqual(outcome["resolution"], "kept_local")
        self.assertEqual(self._reading_status(), "Currently Reading")
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)

    def test_accept_incoming_writes_book_value(self) -> None:
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming")
        self.assertEqual(outcome["resolution"], "accepted_incoming")
        self.assertEqual(self._reading_status(), "Read")
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)

    def test_custom_value_writes_book_value(self) -> None:
        outcome = conflicts_service.resolve_conflict(
            self.conn, self.conflict_id, choice="custom", custom_value="Paused"
        )
        self.assertEqual(outcome["resolution"], "custom")
        self.assertEqual(outcome["value"], "Paused")
        self.assertEqual(self._reading_status(), "Paused")
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)

    def test_resolution_is_idempotent(self) -> None:
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming")
        # Resolving again should not raise or change the (already accepted) value.
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="local")
        self.assertEqual(self._reading_status(), "Read")
        self.assertEqual(outcome["resolution"], "accepted_incoming")

    def test_invalid_choice_raises(self) -> None:
        with self.assertRaises(ValueError):
            conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="bogus")

    def test_bulk_resolve_book_accepts_all(self) -> None:
        # Add a second conflicting field for the same book.
        run = self.conn.execute("SELECT import_run_id FROM sync_conflicts LIMIT 1").fetchone()[0]
        self.conn.execute("UPDATE books SET rating = 2 WHERE id = ?", (self.book_id,))
        db.add_conflict(
            self.conn,
            import_run_id=run,
            book_id=self.book_id,
            source="goodreads",
            field_name="rating",
            old_source_value="1",
            local_value="2",
            incoming_value="5",
        )
        self.conn.commit()
        self.assertEqual(conflicts_service.pending_count(self.conn), 2)

        outcome = conflicts_service.resolve_book(self.conn, self.book_id, choice="incoming")
        self.assertEqual(outcome["count"], 2)
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)
        self.assertEqual(self._reading_status(), "Read")
        self.assertEqual(
            self.conn.execute("SELECT rating FROM books WHERE id = ?", (self.book_id,)).fetchone()[0],
            5,
        )

    def test_ignore_closes_without_changing_value(self) -> None:
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="ignore")
        self.assertEqual(outcome["status"], "ignored")
        self.assertEqual(self._reading_status(), "Currently Reading")
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)
        self.assertEqual(conflicts_service.deferred_count(self.conn), 0)

    def test_review_later_stays_open_and_is_flagged_deferred(self) -> None:
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="later")
        self.assertEqual(outcome["status"], "review_later")
        # Deferred is not "pending" (the act-now badge) but is still open.
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)
        self.assertEqual(conflicts_service.deferred_count(self.conn), 1)
        groups = conflicts_service.list_open_conflicts(self.conn)
        self.assertTrue(groups[0]["conflicts"][0]["deferred"])

    def test_reopen_returns_decided_conflict_to_pending(self) -> None:
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming")
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="reopen")
        self.assertEqual(outcome["status"], "pending")
        self.assertEqual(conflicts_service.pending_count(self.conn), 1)

    def test_keep_local_restores_value_after_accept_then_reopen(self) -> None:
        # The reversibility cycle: accept incoming, reopen, then keep-local must
        # restore the original local value to the book (not silently leave it).
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming")
        self.assertEqual(self._reading_status(), "Read")
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="reopen")
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="local")
        self.assertEqual(self._reading_status(), "Currently Reading")

    def test_decided_conflict_is_idempotent_until_reopened(self) -> None:
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="ignore")
        # A second decision without reopening first is a no-op (can't flip a value).
        outcome = conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming")
        self.assertEqual(outcome["status"], "ignored")
        self.assertEqual(self._reading_status(), "Currently Reading")
        self.assertEqual(len(conflicts_service.conflict_history(self.conn, self.conflict_id)), 1)

    def test_audit_trail_records_each_decision_with_provenance(self) -> None:
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="later", actor="web")
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="reopen", actor="cli")
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming", actor="web")
        history = conflicts_service.conflict_history(self.conn, self.conflict_id)
        self.assertEqual(
            [h["decision"] for h in history], ["review_later", "reopened", "accepted_incoming"]
        )
        self.assertEqual([h["actor"] for h in history], ["web", "cli", "web"])
        self.assertEqual(history[-1]["value"], "Read")

    def test_bulk_resolve_ignore(self) -> None:
        outcome = conflicts_service.resolve_book(self.conn, self.book_id, choice="ignore")
        self.assertEqual(outcome["count"], 1)
        self.assertEqual(conflicts_service.pending_count(self.conn), 0)
        self.assertEqual(self._reading_status(), "Currently Reading")

    def test_list_decided_conflicts_reports_decision_and_value(self) -> None:
        conflicts_service.resolve_conflict(self.conn, self.conflict_id, choice="incoming", actor="web")
        decided = conflicts_service.list_decided_conflicts(self.conn)
        self.assertEqual(len(decided), 1)
        conflict = decided[0]["conflicts"][0]
        self.assertEqual(conflict["decision"], "accepted_incoming")
        self.assertEqual(conflict["value"], "Read")
        self.assertEqual(conflict["actor"], "web")

    def test_invalid_decision_raises(self) -> None:
        with self.assertRaises(ValueError):
            conflicts_service.decide_conflict(self.conn, self.conflict_id, decision="bogus")

    def test_legacy_resolved_conflicts_are_backfilled(self) -> None:
        # Mimic a pre-audit catalogue: a conflict resolved via the low-level
        # setter (which writes no audit row), with an empty audit table.
        db.set_conflict_resolution(self.conn, self.conflict_id, resolution="accepted_incoming")
        self.conn.execute("DELETE FROM conflict_decisions")
        self.conn.commit()
        db._migrate_conflict_decisions(self.conn)
        history = conflicts_service.conflict_history(self.conn, self.conflict_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["decision"], "accepted_incoming")
        self.assertEqual(history[0]["actor"], "legacy")
        self.assertEqual(history[0]["value"], "Read")


if __name__ == "__main__":
    unittest.main()
