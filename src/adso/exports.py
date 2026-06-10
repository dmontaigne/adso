"""Portable catalogue exports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from . import db

EXPORT_FIELDS = [
    "goodreads_id",
    "title",
    "author",
    "isbn10",
    "isbn13",
    "publisher",
    "binding",
    "number_of_pages",
    "year_published",
    "original_publication_year",
    "reading_status",
    "rating",
    "date_read",
    "date_added",
    "shelves",
    "subjects",
    "format",
    "tags",
    "loaned_to",
    "local_notes",
]


def catalogue_json_string(conn) -> str:
    """Serialize the whole catalogue to a JSON string (the portable export shape)."""
    rows = [db.row_to_catalogue_dict(row) for row in db.iter_books(conn)]
    return json.dumps(rows, indent=2, sort_keys=True)


def catalogue_csv_string(conn) -> str:
    """Serialize the whole catalogue to a CSV string (EXPORT_FIELDS columns)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for row in db.iter_books(conn):
        data = db.row_to_catalogue_dict(row)
        data["shelves"] = ", ".join(data["shelves"])
        data["tags"] = ", ".join(data["tags"])
        # description deliberately stays out of the CSV (multi-paragraph text
        # wrecks spreadsheets); the JSON export carries full fidelity.
        data["subjects"] = ", ".join(data["subjects"])
        writer.writerow({field: data.get(field) for field in EXPORT_FIELDS})
    return buffer.getvalue()


def export_json(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(catalogue_json_string(conn), encoding="utf-8")
    return path


def export_csv(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the csv module's line terminators aren't translated again.
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(catalogue_csv_string(conn))
    return path
