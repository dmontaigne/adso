"""Reusable catalogue query services.

This module is intentionally independent of the CLI so terminal commands,
future web views, and agent tools can all retrieve catalogue records through
the same boundary.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import db


@dataclass(frozen=True)
class BookFilters:
    status: str | None = None
    owned: bool | None = None
    location: str | None = None
    author: str | None = None
    limit: int | None = None


SEARCH_FIELDS = (
    "title",
    "author",
    "additional_authors",
    "isbn10",
    "isbn13",
    "publisher",
    "binding",
    "reading_status",
    "exclusive_shelf",
    "shelves_json",
    "my_review",
    "private_notes",
    "location",
    "shelf_box",
    "loaned_to",
    "local_notes",
)


def list_books(conn: sqlite3.Connection, filters: BookFilters | None = None) -> list[dict[str, Any]]:
    filters = filters or BookFilters()
    where, params = _filter_sql(filters)
    limit_sql = _limit_sql(filters)
    rows = conn.execute(
        f"""
        SELECT * FROM books
        {where}
        ORDER BY title COLLATE NOCASE, author COLLATE NOCASE
        {limit_sql}
        """,
        params,
    ).fetchall()
    return [_book_result(row) for row in rows]


def search_books(
    conn: sqlite3.Connection,
    query: str,
    filters: BookFilters | None = None,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return list_books(conn, filters)

    filters = filters or BookFilters()
    if _sqlite_supports_fts5(conn):
        return _search_books_fts(conn, query, filters)

    where, params = _filter_sql(filters)
    search_where, search_params = _search_sql(query)
    if where:
        combined_where = f"{where} AND {search_where}"
    else:
        combined_where = f"WHERE {search_where}"
    limit_sql = _limit_sql(filters)

    rows = conn.execute(
        f"""
        SELECT * FROM books
        {combined_where}
        ORDER BY title COLLATE NOCASE, author COLLATE NOCASE
        {limit_sql}
        """,
        [*params, *search_params],
    ).fetchall()
    return [_book_result(row) for row in rows]


def get_book(conn: sqlite3.Connection, goodreads_id: str) -> dict[str, Any] | None:
    row = db.get_book_by_goodreads_id(conn, goodreads_id)
    if row is None:
        return None
    return _book_result(row)


def _search_books_fts(
    conn: sqlite3.Connection,
    query: str,
    filters: BookFilters,
) -> list[dict[str, Any]]:
    fts_query = _fts_query(query)
    if not fts_query:
        return list_books(conn, filters)

    _ensure_search_fts(conn)
    where, params = _filter_sql(filters, table_prefix="books")
    if where:
        combined_where = f"{where} AND adso_books_fts MATCH ?"
    else:
        combined_where = "WHERE adso_books_fts MATCH ?"
    limit_sql = _limit_sql(filters)

    rows = conn.execute(
        f"""
        SELECT books.*
        FROM books
        JOIN temp.adso_books_fts ON adso_books_fts.rowid = books.id
        {combined_where}
        ORDER BY bm25(adso_books_fts), books.title COLLATE NOCASE, books.author COLLATE NOCASE
        {limit_sql}
        """,
        [*params, fts_query],
    ).fetchall()
    return [_book_result(row) for row in rows]


def _sqlite_supports_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.adso_fts_probe USING fts5(value)")
        return True
    except sqlite3.OperationalError:
        return False


def _ensure_search_fts(conn: sqlite3.Connection) -> None:
    fields_sql = ", ".join(SEARCH_FIELDS)
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.adso_books_fts USING fts5({fields_sql})")
    conn.execute("DELETE FROM temp.adso_books_fts")
    conn.execute(
        f"""
        INSERT INTO temp.adso_books_fts (rowid, {fields_sql})
        SELECT id, {", ".join(f"COALESCE({field}, '')" for field in SEARCH_FIELDS)}
        FROM books
        """
    )


def _fts_query(query: str) -> str:
    tokens = []
    token = []
    for char in query:
        if char.isalnum():
            token.append(char)
        elif token:
            tokens.append("".join(token))
            token = []
    if token:
        tokens.append("".join(token))
    return " ".join(f'"{token}"' for token in tokens)


def _filter_sql(filters: BookFilters, table_prefix: str | None = None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    prefix = f"{table_prefix}." if table_prefix else ""

    if filters.status:
        clauses.append(f"{prefix}reading_status = ?")
        params.append(filters.status)
    if filters.owned is not None:
        clauses.append(f"{prefix}owned = ?")
        params.append(1 if filters.owned else 0)
    if filters.location:
        clauses.append(f"{prefix}location LIKE ? COLLATE NOCASE")
        params.append(f"%{filters.location}%")
    if filters.author:
        clauses.append(
            f"({prefix}author LIKE ? COLLATE NOCASE OR {prefix}additional_authors LIKE ? COLLATE NOCASE)"
        )
        author_query = f"%{filters.author}%"
        params.extend([author_query, author_query])

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _search_sql(query: str) -> tuple[str, list[Any]]:
    clauses = [f"{field} LIKE ? COLLATE NOCASE" for field in SEARCH_FIELDS]
    params = [f"%{query}%" for _ in SEARCH_FIELDS]
    return "(" + " OR ".join(clauses) + ")", params


def _limit_sql(filters: BookFilters) -> str:
    if filters.limit is None:
        return ""
    if filters.limit < 1:
        raise ValueError("limit must be at least 1")
    return f"LIMIT {int(filters.limit)}"


def _book_result(row: sqlite3.Row) -> dict[str, Any]:
    data = db.row_to_catalogue_dict(row)
    return {
        "id": data["id"],
        "goodreads_id": data["goodreads_id"],
        "title": data["title"],
        "author": data["author"],
        "additional_authors": data["additional_authors"],
        "isbn10": data["isbn10"],
        "isbn13": data["isbn13"],
        "publisher": data["publisher"],
        "binding": data["binding"],
        "number_of_pages": data["number_of_pages"],
        "year_published": data["year_published"],
        "original_publication_year": data["original_publication_year"],
        "rating": data["rating"],
        "average_rating": data["average_rating"],
        "reading_status": data["reading_status"],
        "exclusive_shelf": data["exclusive_shelf"],
        "shelves": data["shelves"],
        "date_read": data["date_read"],
        "date_added": data["date_added"],
        "my_review": data["my_review"],
        "private_notes": data["private_notes"],
        "read_count": data["read_count"],
        "owned_copies": data["owned_copies"],
        "owned": bool(data["owned"]),
        "copy_count": data["copy_count"],
        "location": data["location"],
        "shelf_box": data["shelf_box"],
        "loaned_to": data["loaned_to"],
        "local_notes": data["local_notes"],
        "cover_path": data.get("cover_path"),
        "cover_status": data.get("cover_status"),
        "cover_url": f"/covers/{data['goodreads_id']}" if data.get("goodreads_id") else None,
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }
