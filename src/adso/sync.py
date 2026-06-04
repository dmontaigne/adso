"""Import and sync workflows for Adso."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .goodreads import GoodreadsRecord, read_goodreads_csv


# Volatile Goodreads-side fields that should never count as a tracked change or
# raise a conflict. Their value is still refreshed silently so the catalogue
# stays current, but routine drift (community ratings moving, edition/metadata
# relabelling) won't churn every sync. Identity fields like title and author are
# deliberately NOT here, so a change there still surfaces as a possible data
# issue. Add fields to this set to ignore them.
INFORMATIONAL_FIELDS = frozenset(
    {
        "average_rating",
        "publisher",
        "number_of_pages",
        "original_publication_year",
        "additional_authors",
        "isbn10",
        "isbn13",
    }
)

# Fields compared loosely when deciding whether a value meaningfully changed.
# For titles, Goodreads churn is mostly cosmetic — recasing and edition labels
# like "(Vintage International)" or "(Screwtape, #1)". We ignore case, whitespace
# and trailing parenthetical tags, but a substantive retitle (including a changed
# subtitle) still counts and can still raise a conflict.
CASE_INSENSITIVE_FIELDS = frozenset({"title"})

# Fields that derive from a single Goodreads column and must move together.
# Goodreads' "Exclusive Shelf" column populates both reading_status and
# exclusive_shelf (see goodreads.normalize_row), so one shelf change is a single
# logical edit. Syncing them independently can leave them inconsistent — e.g. a
# held reading_status conflict while exclusive_shelf silently advances. We treat
# each group as one unit: if any member of the group conflicts, the whole group
# is held (no member is auto-updated) until the user resolves it.
COUPLED_FIELD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("reading_status", "exclusive_shelf"),
)
_FIELD_TO_GROUP = {field: group for group in COUPLED_FIELD_GROUPS for field in group}

_TRAILING_PARENS_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _comparison_key(field: str, serialized: str | None) -> str | None:
    """Normalised value used to decide whether a field meaningfully changed."""
    if serialized is None or field not in CASE_INSENSITIVE_FIELDS:
        return serialized
    value = serialized
    if field == "title":
        # Strip trailing edition tags, including several stacked ones.
        previous = None
        while previous != value:
            previous = value
            value = _TRAILING_PARENS_RE.sub("", value)
    return " ".join(value.lower().split())


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


def import_goodreads_csv(
    conn,
    csv_path: str | Path,
    *,
    mode: str = "sync",
    source_label: str | None = None,
) -> SyncSummary:
    db.initialize(conn)
    records = read_goodreads_csv(csv_path)
    import_run_id = db.create_import_run(
        conn,
        source="goodreads",
        source_path=source_label or str(csv_path),
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
    silent_updates: dict[str, Any] = {}
    conflict_count = 0
    changed = False
    resolved_groups: set[tuple[str, ...]] = set()

    for field in db.GOODREADS_FIELDS:
        if field in INFORMATIONAL_FIELDS:
            # Keep the stored value fresh, but never count it as a change or a
            # conflict, so routine drift in volatile Goodreads-side data does not
            # churn the sync.
            incoming_value = normalized.get(field)
            if db.serialize_value(incoming_value) != db.serialize_value(existing[field]):
                silent_updates[field] = incoming_value
            continue

        # Resolve coupled fields (e.g. reading_status + exclusive_shelf) as a
        # single unit so a shelf change can't be half-applied. Standalone fields
        # are resolved as a group of one, sharing the same logic.
        group = _FIELD_TO_GROUP.get(field, (field,))
        if group in resolved_groups:
            continue
        resolved_groups.add(group)

        result = _resolve_field_group(
            conn, book_id, existing, normalized, group, import_run_id=import_run_id
        )
        safe_updates.update(result["safe_updates"])
        conflict_count += result["conflicts"]
        changed = changed or result["changed"]

    # Apply silent (informational) refreshes alongside tracked safe updates, but
    # only the tracked ones contribute to the "updated" count.
    db.update_book_goodreads_fields(
        conn, book_id, {**silent_updates, **safe_updates}, import_run_id=import_run_id
    )
    db.upsert_source_snapshots(conn, book_id, "goodreads", normalized, import_run_id)
    return {
        "updated": bool(safe_updates),
        "unchanged": not changed and conflict_count == 0,
        "conflicts": conflict_count,
    }


def _resolve_field_group(
    conn,
    book_id: int,
    existing,
    normalized: dict[str, Any],
    fields: tuple[str, ...],
    *,
    import_run_id: int,
) -> dict[str, Any]:
    """Decide updates/conflicts for a set of fields that must move together.

    Each field is classified independently as a safe update, an already-matching
    no-op, or a genuine conflict (local diverged away from both base and
    incoming). If *any* member of the group is a genuine conflict, the whole
    group is held: no member is auto-updated, so coupled fields can't drift apart
    (see COUPLED_FIELD_GROUPS). A group of one reproduces the single-field rules.
    """
    safe: dict[str, Any] = {}
    matched = False
    conflicts: list[dict[str, Any]] = []

    for field in fields:
        incoming_value = normalized.get(field)
        local_value = existing[field]
        old_source_value = db.get_source_snapshot(conn, book_id, "goodreads", field)

        incoming_key = _comparison_key(field, db.serialize_value(incoming_value))
        local_key = _comparison_key(field, db.serialize_value(local_value))
        old_key = _comparison_key(field, old_source_value)

        if incoming_key == old_key:
            # Goodreads didn't change this field; nothing to decide.
            continue

        if old_source_value is None or local_key == old_key:
            safe[field] = incoming_value
        elif local_key == incoming_key:
            matched = True
        else:
            conflicts.append(
                {
                    "field_name": field,
                    "old_source_value": old_source_value,
                    "local_value": local_value,
                    "incoming_value": incoming_value,
                }
            )

    if conflicts:
        # Hold the whole group: drop the safe updates so coupled fields stay
        # consistent, and record only the genuinely diverged fields as conflicts.
        for conflict in conflicts:
            db.add_conflict(
                conn,
                import_run_id=import_run_id,
                book_id=book_id,
                source="goodreads",
                **conflict,
            )
        return {"safe_updates": {}, "conflicts": len(conflicts), "changed": False}

    return {"safe_updates": safe, "conflicts": 0, "changed": bool(safe) or matched}
