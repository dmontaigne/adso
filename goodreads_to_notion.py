#!/usr/bin/env python3
"""Compatibility wrapper for the original Goodreads-to-Notion script.

Adso now keeps SQLite as the source of truth. This wrapper imports/syncs the
Goodreads CSV into the local catalogue first, then optionally exports the
catalogue to Notion.
"""

from __future__ import annotations

import argparse
import sys

from adso import db
from adso.notion import NotionConfigError, export_to_notion
from adso.sync import import_goodreads_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Goodreads CSV into Adso, then optionally export to Notion")
    parser.add_argument("--csv", required=True, help="Path to Goodreads CSV export")
    parser.add_argument("--db", default="adso.sqlite", help="SQLite database path")
    parser.add_argument("--notion", action="store_true", help="Export the local catalogue to Notion after import")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    try:
        db.initialize(conn)
        summary = import_goodreads_csv(conn, args.csv, mode="sync")
        print(
            f"Run {summary.import_run_id}: {summary.row_count} rows, "
            f"{summary.created} created, {summary.updated} updated, "
            f"{summary.unchanged} unchanged, {summary.conflicts} conflicts"
        )
        if args.notion:
            try:
                result = export_to_notion(conn)
            except NotionConfigError as exc:
                parser.error(str(exc))
            print(
                "Notion export complete: "
                f"{result['created']} created, {result['updated']} updated, {result['errors']} errors"
            )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
