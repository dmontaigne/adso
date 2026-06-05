"""Duplicate book detection and merge services.

Goodreads reassigns a book's Book ID when its edition changes. Because Adso keys
records on ``goodreads_id``, a re-IDed book imports as a brand-new record while
the old one is orphaned — leaving two rows for the same work, often with diverging
reading status. ISBNs can't bridge them (new editions frequently carry blank or
different ISBNs), so duplicates are grouped on a normalized title + author key.

Matching is fuzzy, so this module never merges automatically: it *flags* suspected
duplicates (from a catalogue-wide scan and at import time) for the user to review
and then either merge (folding any local enrichment into the keeper) or dismiss.

Like :mod:`adso.conflicts`, this is interface-independent so the CLI and web UI go
through the same boundary, building on the persistence helpers in :mod:`adso.db`.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import db

_SUBTITLE_RE = re.compile(r"[:;].*$")
_PARENS_RE = re.compile(r"\([^()]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _normalize(value: str | None) -> str:
    value = (value or "").lower()
    value = _NON_ALNUM_RE.sub(" ", value)
    return " ".join(value.split())


def _norm_title(title: str | None) -> str:
    title = (title or "").lower()
    title = _SUBTITLE_RE.sub("", title)  # drop subtitle after first : or ;
    title = _PARENS_RE.sub("", title)  # drop edition/series parentheticals
    return _normalize(title)


def group_key(row: sqlite3.Row | dict[str, Any]) -> str:
    """Stable signature grouping editions of the same work by the same author."""
    return f"{_norm_title(row['title'])}|{_normalize(row['author'])}"


def scan_duplicates(conn: sqlite3.Connection) -> int:
    """Find duplicate groups across the whole catalogue and flag them pending.

    Books are grouped by :func:`group_key`; any group of two or more whose key has
    not already been dismissed or merged is recorded as pending duplicate links.
    Returns the number of pending groups after the scan.
    """
    skip = db.dismissed_or_merged_group_keys(conn)
    groups: dict[str, list[int]] = {}
    for row in db.iter_books(conn):
        key = group_key(row)
        groups.setdefault(key, []).append(row["id"])

    flagged = 0
    for key, book_ids in groups.items():
        if len(book_ids) < 2 or key in skip:
            continue
        for book_id in book_ids:
            db.insert_duplicate_link(conn, group_key=key, book_id=book_id, reason="title_author")
        flagged += 1
    conn.commit()
    return flagged


def flag_duplicates_for_book(conn: sqlite3.Connection, book_id: int) -> bool:
    """Import-time hook: flag a newly created book if it duplicates existing ones.

    Returns True if a pending duplicate group was recorded for this book.
    """
    book = db.get_book(conn, book_id)
    if book is None:
        return False
    key = group_key(book)
    if key in db.dismissed_or_merged_group_keys(conn):
        return False
    siblings = [row["id"] for row in db.iter_books(conn) if group_key(row) == key]
    if len(siblings) < 2:
        return False
    for sibling_id in siblings:
        db.insert_duplicate_link(conn, group_key=key, book_id=sibling_id, reason="title_author")
    return True


def _slug(group_key_value: str) -> str:
    """A CSS-id-safe slug (alphanumerics + hyphens only) for DOM ids/targets."""
    return _SLUG_RE.sub("-", group_key_value).strip("-") or "group"


def list_open_duplicates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return pending duplicate groups, ready for display.

    Each group lists its member books and suggests the newest Goodreads record
    (highest import run) as the keeper, since that's the live, up-to-date row.
    """
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in db.list_duplicate_links(conn, status="pending"):
        key = row["group_key"]
        if key not in groups:
            groups[key] = {
                "group_key": key,
                "slug": _slug(key),
                "title": row["title"],
                "author": row["author"],
                "books": [],
            }
            order.append(key)
        groups[key]["books"].append(
            {
                "id": row["book_id"],
                "goodreads_id": row["goodreads_id"],
                "title": row["title"],
                "author": row["author"],
                "reading_status": row["reading_status"],
                "location": row["location"],
                "owned": bool(row["owned"]),
                "local_notes": row["local_notes"],
                "cover_url": f"/covers/{row['goodreads_id']}" if row["goodreads_id"] else None,
                "import_run": row["last_goodreads_import_run_id"] or 0,
            }
        )

    result: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        keeper = max(group["books"], key=lambda b: b["import_run"])
        group["suggested_keeper_id"] = keeper["id"]
        group["count"] = len(group["books"])
        result.append(group)
    return result


def merge_duplicate(
    conn: sqlite3.Connection,
    group_key_value: str,
    *,
    keep_id: int,
) -> dict[str, Any]:
    """Merge every other pending member of a group into ``keep_id``."""
    members = [
        row["book_id"]
        for row in db.list_duplicate_links(conn, status="pending")
        if row["group_key"] == group_key_value
    ]
    if keep_id not in members:
        raise ValueError(f"Keeper {keep_id} is not a pending member of this duplicate group")

    keeper = db.get_book(conn, keep_id)
    title = keeper["title"] if keeper is not None else f"Book {keep_id}"
    merged = 0
    for book_id in members:
        if book_id == keep_id:
            continue
        db.merge_books(conn, keep_id=keep_id, drop_id=book_id)
        merged += 1
    db.set_duplicate_group_status(conn, group_key_value, status="merged")
    conn.commit()
    return {
        "slug": _slug(group_key_value),
        "title": title,
        "merged": merged,
        "outcome_label": f"merged {merged} duplicate{'' if merged == 1 else 's'} into",
    }


def dismiss_duplicate(conn: sqlite3.Connection, group_key_value: str) -> dict[str, Any]:
    """Mark a group as not-a-duplicate so re-scans won't resurface it."""
    db.set_duplicate_group_status(conn, group_key_value, status="dismissed")
    conn.commit()
    return {
        "slug": _slug(group_key_value),
        "title": group_key_value.split("|", 1)[0],
        "merged": 0,
        "outcome_label": "marked as not duplicates",
    }


def pending_count(conn: sqlite3.Connection) -> int:
    return db.pending_duplicate_group_count(conn)
