"""Activity (import history) services.

Like :mod:`adso.catalogue` and :mod:`adso.conflicts`, this is an
interface-independent service so the CLI, web UI, and agent tools surface the
same import history. It reads the ``import_runs`` rows written by every
``adso import`` / ``adso sync`` and shapes them for display.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from . import db


def list_activity(conn: sqlite3.Connection, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return import runs, newest first, as display-ready dicts."""
    runs: list[dict[str, Any]] = []
    for row in db.list_import_runs(conn, limit=limit):
        source_path = row["source_path"] or ""
        runs.append(
            {
                "id": row["id"],
                "imported_at": row["imported_at"],
                "source": row["source"],
                "mode": row["mode"],
                "source_file": os.path.basename(source_path) or source_path,
                "source_path": source_path,
                "row_count": row["row_count"],
                "created": row["created_count"],
                "updated": row["updated_count"],
                "unchanged": row["unchanged_count"],
                "conflicts": row["conflict_count"],
            }
        )
    return runs
