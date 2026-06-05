"""Portable catalogue exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import db

EXPORT_FIELDS = [
    "goodreads_id",
    "title",
    "author",
    "isbn10",
    "isbn13",
    "reading_status",
    "rating",
    "date_read",
    "date_added",
    "shelves",
    "owned",
    "copy_count",
    "location",
    "shelf_box",
    "loaned_to",
    "local_notes",
]


def export_json(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [db.row_to_catalogue_dict(row) for row in db.iter_books(conn)]
    path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return path


def export_csv(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        for row in db.iter_books(conn):
            data = db.row_to_catalogue_dict(row)
            data["shelves"] = ", ".join(data["shelves"])
            writer.writerow({field: data.get(field) for field in EXPORT_FIELDS})
    return path
