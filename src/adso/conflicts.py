"""Conflict resolution services.

Like :mod:`adso.catalogue`, this module is intentionally independent of any
interface so the CLI, the web UI, and future agent tools resolve sync conflicts
through the same boundary. It builds on the persistence helpers in
:mod:`adso.db` and the same merge model used by :mod:`adso.sync`.

A conflict is a single Goodreads field on a single book where, during sync, the
incoming value diverged from the previous snapshot *and* the local value also
diverged. The local value is preserved until the user makes a decision:

- ``kept_local``        keep (restore) the local value,
- ``accepted_incoming`` accept the incoming Goodreads value,
- ``custom``            set a custom value,
- ``ignored``           dismiss the conflict without changing the value,
- ``review_later``      defer the decision (stays open, but flagged), or
- ``reopened``          return a decided conflict to pending.

Every decision is recorded as an immutable row in ``conflict_decisions`` (the
audit trail), while ``sync_conflicts`` keeps the current state denormalized for
fast queries. Decisions carry provenance (the ``actor``: cli / web / agent).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import db

FIELD_LABELS: dict[str, str] = {
    "title": "Title",
    "author": "Author",
    "additional_authors": "Additional authors",
    "isbn10": "ISBN-10",
    "isbn13": "ISBN-13",
    "publisher": "Publisher",
    "binding": "Binding",
    "number_of_pages": "Number of pages",
    "year_published": "Year published",
    "original_publication_year": "Original publication year",
    "rating": "My rating",
    "average_rating": "Average rating",
    "reading_status": "Reading status",
    "exclusive_shelf": "Exclusive shelf",
    "shelves_json": "Shelves",
    "date_read": "Date read",
    "date_added": "Date added",
    "my_review": "Review",
    "private_notes": "Private notes",
    "read_count": "Read count",
    "owned_copies": "Owned copies",
}

# Public "choice" vocabulary (what an interface asks for) → internal decision.
VALID_CHOICES = ("local", "incoming", "custom", "ignore", "later", "reopen")

_CHOICE_TO_DECISION = {
    "local": "kept_local",
    "incoming": "accepted_incoming",
    "custom": "custom",
    "ignore": "ignored",
    "later": "review_later",
    "reopen": "reopened",
}

# Decision → the conflict status it leaves behind.
_DECISION_TO_STATUS = {
    "kept_local": "resolved",
    "accepted_incoming": "resolved",
    "custom": "resolved",
    "ignored": "ignored",
    "review_later": "review_later",
    "reopened": "pending",
}

_DECISION_LABELS = {
    "kept_local": "kept local value",
    "accepted_incoming": "accepted Goodreads value",
    "custom": "set to a custom value",
    "ignored": "ignored",
    "review_later": "marked for later review",
    "reopened": "reopened",
}

_STATUS_LABELS = {
    "pending": "pending",
    "resolved": "resolved",
    "ignored": "ignored",
    "review_later": "deferred",
}

# Back-compat alias: older callers imported _RESOLUTION_LABELS / _CHOICE_TO_RESOLUTION.
_RESOLUTION_LABELS = _DECISION_LABELS
_CHOICE_TO_RESOLUTION = _CHOICE_TO_DECISION


def field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


def decision_label(decision: str) -> str:
    return _DECISION_LABELS.get(decision, decision)


def _display(field_name: str, value: str | None) -> str:
    """Turn a stored (serialized) conflict value into display text."""
    if value is None:
        return ""
    if field_name == "shelves_json":
        try:
            shelves = json.loads(value)
        except (ValueError, TypeError):
            return value
        if isinstance(shelves, list):
            return ", ".join(str(item) for item in shelves)
    return value


def pending_count(conn: sqlite3.Connection) -> int:
    return db.pending_conflict_count(conn)


def deferred_count(conn: sqlite3.Connection) -> int:
    return db.deferred_conflict_count(conn)


def list_open_conflicts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return open conflicts (pending + deferred) grouped by book, for display."""
    groups: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in db.list_conflicts(conn, status=db.OPEN_CONFLICT_STATUSES):
        book_id = row["book_id"]
        if book_id not in groups:
            groups[book_id] = {
                "book_id": book_id,
                "goodreads_id": row["goodreads_id"],
                "title": row["title"],
                "author": row["author"],
                "conflicts": [],
            }
            order.append(book_id)
        field = row["field_name"]
        groups[book_id]["conflicts"].append(
            {
                "id": row["id"],
                "field_name": field,
                "field_label": field_label(field),
                "base": _display(field, row["old_source_value"]),
                "local": _display(field, row["local_value"]),
                "incoming": _display(field, row["incoming_value"]),
                "status": row["status"],
                "deferred": row["status"] == "review_later",
            }
        )
    return [groups[book_id] for book_id in order]


def list_decided_conflicts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return decided conflicts (resolved + ignored) grouped by book, for display."""
    groups: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in db.list_conflicts(conn, status=db.DECIDED_CONFLICT_STATUSES):
        book_id = row["book_id"]
        if book_id not in groups:
            groups[book_id] = {
                "book_id": book_id,
                "goodreads_id": row["goodreads_id"],
                "title": row["title"],
                "author": row["author"],
                "conflicts": [],
            }
            order.append(book_id)
        field = row["field_name"]
        decision = row["resolution"] or ""
        last = _latest_decision(conn, row["id"])
        groups[book_id]["conflicts"].append(
            {
                "id": row["id"],
                "field_name": field,
                "field_label": field_label(field),
                "status": row["status"],
                "status_label": _STATUS_LABELS.get(row["status"], row["status"]),
                "decision": decision,
                "decision_label": decision_label(decision),
                "value": _active_value(field, row, last),
                "actor": last["actor"] if last is not None else None,
                "decided_at": row["resolved_at"],
            }
        )
    return [groups[book_id] for book_id in order]


def conflict_field_view(conn: sqlite3.Connection, conflict_id: int) -> dict[str, Any]:
    """Display dict for one conflict's field (used to re-render it on reopen)."""
    row = db.get_conflict(conn, conflict_id)
    if row is None:
        raise ValueError(f"No conflict with id {conflict_id}")
    field = row["field_name"]
    return {
        "id": row["id"],
        "field_name": field,
        "field_label": field_label(field),
        "base": _display(field, row["old_source_value"]),
        "local": _display(field, row["local_value"]),
        "incoming": _display(field, row["incoming_value"]),
        "status": row["status"],
        "deferred": row["status"] == "review_later",
    }


def conflict_history(conn: sqlite3.Connection, conflict_id: int) -> list[dict[str, Any]]:
    """Return the full decision/audit trail for one conflict, oldest first."""
    conflict = db.get_conflict(conn, conflict_id)
    if conflict is None:
        raise ValueError(f"No conflict with id {conflict_id}")
    field = conflict["field_name"]
    history = []
    for row in db.list_conflict_decisions(conn, conflict_id):
        history.append(
            {
                "decision": row["decision"],
                "decision_label": decision_label(row["decision"]),
                "value": _display(field, row["resulting_value"]),
                "actor": row["actor"],
                "created_at": row["created_at"],
            }
        )
    return history


def decide_conflict(
    conn: sqlite3.Connection,
    conflict_id: int,
    *,
    decision: str,
    value: str | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Apply a decision to a conflict, record it in the audit trail, return outcome.

    Value decisions (kept_local / accepted_incoming / custom) write the chosen
    value to the book — kept_local explicitly restores the preserved local value
    so an accept → reopen → keep-local cycle is correct. ignored / review_later
    leave the value untouched. reopened returns a decided conflict to pending.

    Closed decisions (resolved / ignored) are idempotent: re-deciding without a
    reopen first just reports the existing state, so a double-click can't flip a
    value. Deferred (review_later) conflicts are still open and can be decided.
    """
    if decision not in _DECISION_TO_STATUS:
        raise ValueError(f"Unknown decision: {decision!r}")

    conflict = db.get_conflict(conn, conflict_id)
    if conflict is None:
        raise ValueError(f"No conflict with id {conflict_id}")

    field = conflict["field_name"]
    current_status = conflict["status"]

    if decision == "reopened":
        if current_status == "pending":
            return _outcome_for_conflict(conn, conflict)
        db.reopen_conflict(conn, conflict_id)
        db.record_conflict_decision(conn, conflict_id, decision="reopened", actor=actor)
        conn.commit()
        return _outcome(conflict_id, field, status="pending", decision="reopened", value="", actor=actor)

    # Closed decisions are idempotent — reopen to change them.
    if current_status in db.DECIDED_CONFLICT_STATUSES:
        return _outcome_for_conflict(conn, conflict)

    if decision == "kept_local":
        resulting = conflict["local_value"]
        _write_book_field(conn, conflict, field, resulting)
    elif decision == "accepted_incoming":
        resulting = conflict["incoming_value"]
        _write_book_field(conn, conflict, field, resulting)
    elif decision == "custom":
        resulting = value
        _write_book_field(conn, conflict, field, resulting)
    else:  # ignored, review_later — value is left untouched
        resulting = None

    status = _DECISION_TO_STATUS[decision]
    db.set_conflict_status(conn, conflict_id, status=status, resolution=decision)
    db.record_conflict_decision(
        conn, conflict_id, decision=decision, resulting_value=resulting, actor=actor
    )
    conn.commit()
    display_value = _display(field, resulting) if resulting is not None else ""
    return _outcome(conflict_id, field, status=status, decision=decision, value=display_value, actor=actor)


def resolve_conflict(
    conn: sqlite3.Connection,
    conflict_id: int,
    *,
    choice: str,
    custom_value: str | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Apply a choice (the interface-facing vocabulary) to a single conflict."""
    if choice not in VALID_CHOICES:
        raise ValueError(f"Unknown resolution choice: {choice!r}")
    decision = _CHOICE_TO_DECISION[choice]
    value = custom_value if choice == "custom" else None
    return decide_conflict(conn, conflict_id, decision=decision, value=value, actor=actor)


def resolve_book(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    choice: str,
    actor: str = "cli",
) -> dict[str, Any]:
    """Apply one choice to every open conflict for a book (pending + deferred)."""
    if choice not in ("local", "incoming", "ignore"):
        raise ValueError("Bulk resolution supports only 'local', 'incoming', or 'ignore'.")
    targets = db.list_conflicts(conn, status=db.OPEN_CONFLICT_STATUSES, book_id=book_id)
    book = db.get_book(conn, book_id)
    for row in targets:
        resolve_conflict(conn, row["id"], choice=choice, actor=actor)
    decision = _CHOICE_TO_DECISION[choice]
    return {
        "book_id": book_id,
        "title": book["title"] if book is not None else f"Book {book_id}",
        "count": len(targets),
        "decision": decision,
        "resolution_label": decision_label(decision),
    }


def _write_book_field(conn: sqlite3.Connection, conflict: sqlite3.Row, field: str, value: Any) -> None:
    db.update_book_goodreads_fields(
        conn, conflict["book_id"], {field: value}, import_run_id=conflict["import_run_id"]
    )


def _latest_decision(conn: sqlite3.Connection, conflict_id: int) -> sqlite3.Row | None:
    decisions = db.list_conflict_decisions(conn, conflict_id)
    return decisions[-1] if decisions else None


def _active_value(field: str, conflict: sqlite3.Row, last: sqlite3.Row | None) -> str:
    """The value now in effect for a decided conflict, for display."""
    if last is not None and last["resulting_value"] is not None:
        return _display(field, last["resulting_value"])
    decision = conflict["resolution"]
    if decision == "accepted_incoming":
        return _display(field, conflict["incoming_value"])
    if decision == "kept_local":
        return _display(field, conflict["local_value"])
    return ""


def _outcome_for_conflict(conn: sqlite3.Connection, conflict: sqlite3.Row) -> dict[str, Any]:
    """Build an outcome dict from an already-decided conflict (idempotent path)."""
    field = conflict["field_name"]
    decision = conflict["resolution"] or "kept_local"
    last = _latest_decision(conn, conflict["id"])
    return _outcome(
        conflict["id"],
        field,
        status=conflict["status"],
        decision=decision,
        value=_active_value(field, conflict, last),
        actor=last["actor"] if last is not None else None,
    )


def _outcome(
    conflict_id: int,
    field: str,
    *,
    status: str,
    decision: str,
    value: str,
    actor: str | None,
) -> dict[str, Any]:
    label = decision_label(decision)
    return {
        "id": conflict_id,
        "field_name": field,
        "field_label": field_label(field),
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status),
        "decision": decision,
        "decision_label": label,
        # Back-compat keys still consumed by templates / CLI / tests.
        "resolution": decision,
        "resolution_label": label,
        "value": value,
        "actor": actor,
    }
