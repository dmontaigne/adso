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


def _filter_sql(filters: BookFilters) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if filters.status:
        clauses.append("reading_status = ?")
        params.append(filters.status)
    if filters.owned is not None:
        clauses.append("owned = ?")
        params.append(1 if filters.owned else 0)
    if filters.location:
        clauses.append("location LIKE ? COLLATE NOCASE")
        params.append(f"%{filters.location}%")
    if filters.author:
        clauses.append(
            "(author LIKE ? COLLATE NOCASE OR additional_authors LIKE ? COLLATE NOCASE)"
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
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }
