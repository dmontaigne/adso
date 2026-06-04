"""Conflict resolution services.

Like :mod:`adso.catalogue`, this module is intentionally independent of any
interface so the CLI, the web UI, and future agent tools resolve sync conflicts
through the same boundary. It builds on the persistence helpers in
:mod:`adso.db` and the same merge model used by :mod:`adso.sync`.

A conflict is a single Goodreads field on a single book where, during sync, the
incoming value diverged from the previous snapshot *and* the local value also
diverged. The local value is preserved until the user decides to:

- ``local``    keep the local value (no change to the book),
- ``incoming`` accept the incoming Goodreads value, or
- ``custom``   set a custom value.
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

VALID_CHOICES = ("local", "incoming", "custom")

_RESOLUTION_LABELS = {
    "kept_local": "kept local value",
    "accepted_incoming": "accepted Goodreads value",
    "custom": "set to a custom value",
}

_CHOICE_TO_RESOLUTION = {
    "local": "kept_local",
    "incoming": "accepted_incoming",
    "custom": "custom",
}


def field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


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


def list_open_conflicts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return pending conflicts grouped by book, ready for display."""
    groups: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in db.list_conflicts(conn, status="pending"):
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
            }
        )
    return [groups[book_id] for book_id in order]


def resolve_conflict(
    conn: sqlite3.Connection,
    conflict_id: int,
    *,
    choice: str,
    custom_value: str | None = None,
) -> dict[str, Any]:
    """Apply a resolution to a single conflict and mark it resolved.

    Returns a small display dict describing the outcome.
    """
    if choice not in VALID_CHOICES:
        raise ValueError(f"Unknown resolution choice: {choice!r}")

    conflict = db.get_conflict(conn, conflict_id)
    if conflict is None:
        raise ValueError(f"No conflict with id {conflict_id}")

    field = conflict["field_name"]

    # Idempotent: already-resolved conflicts just report their state.
    if conflict["status"] != "pending":
        return _outcome(conflict_id, field, conflict["resolution"] or "kept_local", _resolved_value(conflict))

    if choice == "local":
        resulting = conflict["local_value"]
    elif choice == "incoming":
        resulting = conflict["incoming_value"]
        db.update_book_goodreads_fields(
            conn, conflict["book_id"], {field: resulting}, import_run_id=conflict["import_run_id"]
        )
    else:  # custom
        resulting = custom_value
        db.update_book_goodreads_fields(
            conn, conflict["book_id"], {field: resulting}, import_run_id=conflict["import_run_id"]
        )

    resolution = _CHOICE_TO_RESOLUTION[choice]
    db.set_conflict_resolution(conn, conflict_id, resolution=resolution)
    conn.commit()
    return _outcome(conflict_id, field, resolution, _display(field, resulting))


def resolve_book(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    choice: str,
) -> dict[str, Any]:
    """Resolve all pending conflicts for one book with the same choice."""
    if choice not in ("local", "incoming"):
        raise ValueError("Bulk resolution supports only 'local' or 'incoming'.")
    pending = db.list_conflicts(conn, status="pending", book_id=book_id)
    book = db.get_book(conn, book_id)
    for row in pending:
        resolve_conflict(conn, row["id"], choice=choice)
    return {
        "book_id": book_id,
        "title": book["title"] if book is not None else f"Book {book_id}",
        "count": len(pending),
        "resolution_label": _RESOLUTION_LABELS[_CHOICE_TO_RESOLUTION[choice]],
    }


def _resolved_value(conflict: sqlite3.Row) -> str:
    field = conflict["field_name"]
    resolution = conflict["resolution"]
    if resolution == "accepted_incoming":
        return _display(field, conflict["incoming_value"])
    return _display(field, conflict["local_value"])


def _outcome(conflict_id: int, field: str, resolution: str, value: str) -> dict[str, Any]:
    return {
        "id": conflict_id,
        "field_name": field,
        "field_label": field_label(field),
        "resolution": resolution,
        "resolution_label": _RESOLUTION_LABELS.get(resolution, resolution),
        "value": value,
    }
