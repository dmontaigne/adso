"""SQLite persistence for Adso."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


GOODREADS_FIELDS = (
    "title",
    "author",
    "additional_authors",
    "isbn10",
    "isbn13",
    "publisher",
    "binding",
    "number_of_pages",
    "year_published",
    "original_publication_year",
    "rating",
    "average_rating",
    "reading_status",
    "exclusive_shelf",
    "shelves_json",
    "date_read",
    "date_added",
    "my_review",
    "private_notes",
    "read_count",
    "owned_copies",
)

LOCAL_FIELDS = (
    "owned",
    "copy_count",
    "location",
    "shelf_box",
    "loaned_to",
    "local_notes",
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may run a single request's dependency and
    # endpoint on different threadpool threads, so the per-request connection has
    # to cross threads. It is still only ever used by one thread at a time (never
    # shared between concurrent requests), so disabling the guard is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets a reader (e.g. the web UI) and a writer (e.g. a cover fetch) work
    # the same file concurrently without "database is locked"; busy_timeout makes
    # any remaining contention wait rather than fail. WAL is persisted on the file.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_path TEXT NOT NULL,
            mode TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            row_count INTEGER NOT NULL DEFAULT 0,
            created_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            unchanged_count INTEGER NOT NULL DEFAULT 0,
            conflict_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goodreads_id TEXT UNIQUE,
            title TEXT NOT NULL,
            author TEXT,
            additional_authors TEXT,
            isbn10 TEXT,
            isbn13 TEXT,
            publisher TEXT,
            binding TEXT,
            number_of_pages INTEGER,
            year_published INTEGER,
            original_publication_year INTEGER,
            rating INTEGER,
            average_rating TEXT,
            reading_status TEXT,
            exclusive_shelf TEXT,
            shelves_json TEXT NOT NULL DEFAULT '[]',
            date_read TEXT,
            date_added TEXT,
            my_review TEXT,
            private_notes TEXT,
            read_count INTEGER,
            owned_copies INTEGER,
            owned INTEGER NOT NULL DEFAULT 0,
            copy_count INTEGER NOT NULL DEFAULT 0,
            location TEXT,
            shelf_box TEXT,
            loaned_to TEXT,
            local_notes TEXT,
            cover_path TEXT,
            cover_source TEXT,
            cover_source_url TEXT,
            cover_status TEXT,
            cover_fetched_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_goodreads_import_run_id INTEGER,
            FOREIGN KEY(last_goodreads_import_run_id) REFERENCES import_runs(id)
        );

        CREATE TABLE IF NOT EXISTS source_snapshots (
            book_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_value TEXT,
            import_run_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(book_id, source, field_name),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(import_run_id) REFERENCES import_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS raw_import_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_run_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_record_id TEXT,
            row_index INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            FOREIGN KEY(import_run_id) REFERENCES import_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_run_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_source_value TEXT,
            local_value TEXT,
            incoming_value TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(import_run_id) REFERENCES import_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );
        """
    )
    _migrate_sync_conflicts(conn)
    _migrate_book_covers(conn)
    conn.commit()


def _migrate_sync_conflicts(conn: sqlite3.Connection) -> None:
    """Add resolution columns to sync_conflicts tables created before v2."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(sync_conflicts)")}
    if "resolution" not in existing:
        conn.execute("ALTER TABLE sync_conflicts ADD COLUMN resolution TEXT")
    if "resolved_at" not in existing:
        conn.execute("ALTER TABLE sync_conflicts ADD COLUMN resolved_at TEXT")


def _migrate_book_covers(conn: sqlite3.Connection) -> None:
    """Add cover-art enrichment columns to books tables created before covers."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(books)")}
    for column in ("cover_path", "cover_source", "cover_source_url", "cover_status", "cover_fetched_at"):
        if column not in existing:
            conn.execute(f"ALTER TABLE books ADD COLUMN {column} TEXT")


def create_import_run(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_path: str,
    mode: str,
    row_count: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO import_runs (source, source_path, mode, row_count)
        VALUES (?, ?, ?, ?)
        """,
        (source, source_path, mode, row_count),
    )
    return int(cur.lastrowid)


def update_import_run_counts(
    conn: sqlite3.Connection,
    import_run_id: int,
    *,
    created: int,
    updated: int,
    unchanged: int,
    conflicts: int,
) -> None:
    conn.execute(
        """
        UPDATE import_runs
        SET created_count = ?, updated_count = ?, unchanged_count = ?, conflict_count = ?
        WHERE id = ?
        """,
        (created, updated, unchanged, conflicts, import_run_id),
    )


def list_import_runs(conn: sqlite3.Connection, *, limit: int | None = None) -> list[sqlite3.Row]:
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    return conn.execute(
        f"SELECT * FROM import_runs ORDER BY id DESC {limit_sql}"
    ).fetchall()


def get_book_by_goodreads_id(conn: sqlite3.Connection, goodreads_id: str) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM books WHERE goodreads_id = ?", (goodreads_id,))
    return cur.fetchone()


def get_book(conn: sqlite3.Connection, book_id: int) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    return cur.fetchone()


def iter_books(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    yield from conn.execute("SELECT * FROM books ORDER BY title COLLATE NOCASE, author COLLATE NOCASE")


def row_to_catalogue_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["shelves"] = json.loads(data.pop("shelves_json") or "[]")
    return data


def serialize_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def insert_raw_row(
    conn: sqlite3.Connection,
    *,
    import_run_id: int,
    source: str,
    row_index: int,
    source_record_id: str,
    raw: dict[str, str],
    normalized: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO raw_import_rows
            (import_run_id, source, source_record_id, row_index, raw_json, normalized_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            import_run_id,
            source,
            source_record_id,
            row_index,
            json.dumps(raw, sort_keys=True),
            json.dumps(normalized, sort_keys=True),
        ),
    )


def insert_book_from_goodreads(
    conn: sqlite3.Connection,
    normalized: dict[str, Any],
    *,
    import_run_id: int,
) -> int:
    columns = ["goodreads_id", *GOODREADS_FIELDS, "last_goodreads_import_run_id"]
    values = [normalized.get("goodreads_id")]
    values.extend(normalized.get(field) for field in GOODREADS_FIELDS)
    values.append(import_run_id)
    placeholders = ", ".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO books ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    book_id = int(cur.lastrowid)
    upsert_source_snapshots(conn, book_id, "goodreads", normalized, import_run_id)
    return book_id


def update_book_goodreads_fields(
    conn: sqlite3.Connection,
    book_id: int,
    updates: dict[str, Any],
    *,
    import_run_id: int,
) -> None:
    if not updates:
        return
    assignments = [f"{field} = ?" for field in updates]
    values = list(updates.values())
    assignments.append("last_goodreads_import_run_id = ?")
    values.append(import_run_id)
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    values.append(book_id)
    conn.execute(
        f"UPDATE books SET {', '.join(assignments)} WHERE id = ?",
        values,
    )


def update_local_fields(
    conn: sqlite3.Connection,
    goodreads_id: str,
    updates: dict[str, Any],
) -> None:
    invalid = [field for field in updates if field not in LOCAL_FIELDS]
    if invalid:
        raise ValueError(f"Unsupported local fields: {', '.join(invalid)}")
    if not updates:
        return
    book = get_book_by_goodreads_id(conn, goodreads_id)
    if book is None:
        raise ValueError(f"No book found for Goodreads ID {goodreads_id}")
    assignments = [f"{field} = ?" for field in updates]
    values = list(updates.values())
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    values.append(book["id"])
    conn.execute(f"UPDATE books SET {', '.join(assignments)} WHERE id = ?", values)
    conn.commit()


def set_cover(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    cover_path: str | None,
    cover_source: str | None,
    cover_source_url: str | None,
    cover_status: str,
) -> None:
    """Record the outcome of a cover-art fetch for one book.

    Covers are local enrichment, not a Goodreads-sourced field, so this writes
    directly to the books row without touching source_snapshots/conflicts.
    """
    conn.execute(
        """
        UPDATE books
        SET cover_path = ?,
            cover_source = ?,
            cover_source_url = ?,
            cover_status = ?,
            cover_fetched_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (cover_path, cover_source, cover_source_url, cover_status, book_id),
    )
    conn.commit()


def clear_cover(conn: sqlite3.Connection, book_id: int) -> None:
    """Reset all cover fields for one book (so the next fetch reconsiders it)."""
    conn.execute(
        """
        UPDATE books
        SET cover_path = NULL,
            cover_source = NULL,
            cover_source_url = NULL,
            cover_status = NULL,
            cover_fetched_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (book_id,),
    )
    conn.commit()


def get_source_snapshot(
    conn: sqlite3.Connection,
    book_id: int,
    source: str,
    field_name: str,
) -> str | None:
    cur = conn.execute(
        """
        SELECT field_value FROM source_snapshots
        WHERE book_id = ? AND source = ? AND field_name = ?
        """,
        (book_id, source, field_name),
    )
    row = cur.fetchone()
    return row["field_value"] if row else None


def upsert_source_snapshots(
    conn: sqlite3.Connection,
    book_id: int,
    source: str,
    normalized: dict[str, Any],
    import_run_id: int,
    fields: Iterable[str] = GOODREADS_FIELDS,
) -> None:
    for field in fields:
        conn.execute(
            """
            INSERT INTO source_snapshots
                (book_id, source, field_name, field_value, import_run_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(book_id, source, field_name)
            DO UPDATE SET
                field_value = excluded.field_value,
                import_run_id = excluded.import_run_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, source, field, serialize_value(normalized.get(field)), import_run_id),
        )


def add_conflict(
    conn: sqlite3.Connection,
    *,
    import_run_id: int,
    book_id: int,
    source: str,
    field_name: str,
    old_source_value: Any,
    local_value: Any,
    incoming_value: Any,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_conflicts
            (import_run_id, book_id, source, field_name, old_source_value, local_value, incoming_value)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_run_id,
            book_id,
            source,
            field_name,
            serialize_value(old_source_value),
            serialize_value(local_value),
            serialize_value(incoming_value),
        ),
    )


def get_conflict(conn: sqlite3.Connection, conflict_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT c.*, b.title, b.author, b.goodreads_id
        FROM sync_conflicts c
        JOIN books b ON b.id = c.book_id
        WHERE c.id = ?
        """,
        (conflict_id,),
    ).fetchone()


def list_conflicts(
    conn: sqlite3.Connection,
    *,
    status: str | None = "pending",
    book_id: int | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("c.status = ?")
        params.append(status)
    if book_id is not None:
        clauses.append("c.book_id = ?")
        params.append(book_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"""
        SELECT c.*, b.title, b.author, b.goodreads_id
        FROM sync_conflicts c
        JOIN books b ON b.id = c.book_id
        {where}
        ORDER BY b.title COLLATE NOCASE, c.field_name
        """,
        params,
    ).fetchall()


def set_conflict_resolution(
    conn: sqlite3.Connection,
    conflict_id: int,
    *,
    resolution: str,
) -> None:
    conn.execute(
        """
        UPDATE sync_conflicts
        SET status = 'resolved', resolution = ?, resolved_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (resolution, conflict_id),
    )


def pending_conflict_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE status = 'pending'").fetchone()
    return int(row[0])
