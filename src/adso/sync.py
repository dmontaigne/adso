"""Import and sync workflows for Adso."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .goodreads import GoodreadsRecord, read_goodreads_csv


@dataclass(frozen=True)
class SyncSummary:
    import_run_id: int
    source: str
    mode: str
    row_count: int
    created: int
    updated: int
    unchanged: int
    conflicts: int
    skipped: int = 0


def import_goodreads_csv(conn, csv_path: str | Path, *, mode: str = "sync") -> SyncSummary:
    db.initialize(conn)
    records = read_goodreads_csv(csv_path)
    import_run_id = db.create_import_run(
        conn,
        source="goodreads",
        source_path=str(csv_path),
        mode=mode,
        row_count=len(records),
    )

    created = updated = unchanged = conflicts = skipped = 0

    for index, record in enumerate(records, start=1):
        normalized = record.normalized
        goodreads_id = normalized.get("goodreads_id", "")
        title = normalized.get("title", "")
        if not goodreads_id or not title:
            skipped += 1
            continue

        db.insert_raw_row(
            conn,
            import_run_id=import_run_id,
            source="goodreads",
            row_index=index,
            source_record_id=goodreads_id,
            raw=record.raw,
            normalized=normalized,
        )

        existing = db.get_book_by_goodreads_id(conn, goodreads_id)
        if existing is None:
            db.insert_book_from_goodreads(conn, normalized, import_run_id=import_run_id)
            created += 1
            continue

        result = _sync_existing_book(conn, existing, record, import_run_id=import_run_id)
        updated += 1 if result["updated"] else 0
        unchanged += 1 if result["unchanged"] else 0
        conflicts += result["conflicts"]

    db.update_import_run_counts(
        conn,
        import_run_id,
        created=created,
        updated=updated,
        unchanged=unchanged,
        conflicts=conflicts,
    )
    conn.commit()
    return SyncSummary(
        import_run_id=import_run_id,
        source="goodreads",
        mode=mode,
        row_count=len(records),
        created=created,
        updated=updated,
        unchanged=unchanged,
        conflicts=conflicts,
        skipped=skipped,
    )


def _sync_existing_book(conn, existing, record: GoodreadsRecord, *, import_run_id: int) -> dict[str, Any]:
    book_id = existing["id"]
    normalized = record.normalized
    safe_updates: dict[str, Any] = {}
    conflict_count = 0
    changed = False

    for field in db.GOODREADS_FIELDS:
        incoming_value = normalized.get(field)
        local_value = existing[field]
        old_source_value = db.get_source_snapshot(conn, book_id, "goodreads", field)
        incoming_serialized = db.serialize_value(incoming_value)
        local_serialized = db.serialize_value(local_value)

        if incoming_serialized == old_source_value:
            continue

        if old_source_value is None or local_serialized == old_source_value:
            safe_updates[field] = incoming_value
            changed = True
            continue

        if local_serialized == incoming_serialized:
            changed = True
            continue

        db.add_conflict(
            conn,
            import_run_id=import_run_id,
            book_id=book_id,
            source="goodreads",
            field_name=field,
            old_source_value=old_source_value,
            local_value=local_value,
            incoming_value=incoming_value,
        )
        conflict_count += 1

    db.update_book_goodreads_fields(conn, book_id, safe_updates, import_run_id=import_run_id)
    db.upsert_source_snapshots(conn, book_id, "goodreads", normalized, import_run_id)
    return {
        "updated": bool(safe_updates),
        "unchanged": not changed and conflict_count == 0,
        "conflicts": conflict_count,
    }
