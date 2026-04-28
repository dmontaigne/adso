"""Command line interface for Adso."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db
from .exports import export_csv, export_json
from .notion import NotionConfigError, export_to_notion
from .reports import (
    latest_conflicts_markdown,
    latest_sync_summary_markdown,
    write_latest_conflicts,
    write_latest_sync_summary,
)
from .sync import import_goodreads_csv


DEFAULT_DB = "adso.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    try:
        if args.command == "init":
            db.initialize(conn)
            print(f"Initialized Adso catalogue at {args.db}")
            return 0

        db.initialize(conn)

        if args.command == "import" and args.source == "goodreads":
            summary = import_goodreads_csv(conn, args.csv, mode="import")
            print(latest_sync_summary_markdown(conn))
            return 0

        if args.command == "sync" and args.source == "goodreads":
            summary = import_goodreads_csv(conn, args.csv, mode="sync")
            print(latest_sync_summary_markdown(conn))
            if summary.conflicts:
                output = Path("reports") / f"conflicts-import-{summary.import_run_id}.md"
                write_latest_conflicts(conn, output)
                print(f"Conflict report: {output}")
            return 0

        if args.command == "edit":
            updates = _local_updates_from_args(args)
            db.update_local_fields(conn, args.goodreads_id, updates)
            print(f"Updated local catalogue fields for Goodreads ID {args.goodreads_id}")
            return 0

        if args.command == "report" and args.report_type == "conflicts":
            if args.output:
                path = write_latest_conflicts(conn, args.output)
                print(f"Wrote conflict report to {path}")
            else:
                print(latest_conflicts_markdown(conn))
            return 0

        if args.command == "report" and args.report_type == "summary":
            if args.output:
                path = write_latest_sync_summary(conn, args.output)
                print(f"Wrote sync summary to {path}")
            else:
                print(latest_sync_summary_markdown(conn))
            return 0

        if args.command == "export" and args.target == "csv":
            path = export_csv(conn, args.output)
            print(f"Exported catalogue CSV to {path}")
            return 0

        if args.command == "export" and args.target == "json":
            path = export_json(conn, args.output)
            print(f"Exported catalogue JSON to {path}")
            return 0

        if args.command == "export" and args.target == "notion":
            try:
                result = export_to_notion(conn)
            except NotionConfigError as exc:
                parser.error(str(exc))
            print(
                "Notion export complete: "
                f"{result['created']} created, {result['updated']} updated, {result['errors']} errors"
            )
            return 0

        parser.error("Unsupported command.")
        return 2
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adso local-first book catalogue")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the local catalogue database")

    import_parser = subparsers.add_parser("import", help="Import source data")
    import_sub = import_parser.add_subparsers(dest="source", required=True)
    goodreads_import = import_sub.add_parser("goodreads", help="Import a Goodreads CSV export")
    goodreads_import.add_argument("csv", help="Path to Goodreads CSV export")

    sync_parser = subparsers.add_parser("sync", help="Sync source data into the local catalogue")
    sync_sub = sync_parser.add_subparsers(dest="source", required=True)
    goodreads_sync = sync_sub.add_parser("goodreads", help="Sync a Goodreads CSV export")
    goodreads_sync.add_argument("csv", help="Path to Goodreads CSV export")

    edit_parser = subparsers.add_parser("edit", help="Edit local physical-library fields")
    edit_parser.add_argument("goodreads_id", help="Goodreads Book ID")
    edit_parser.add_argument("--owned", choices=["true", "false"], help="Whether the book is physically owned")
    edit_parser.add_argument("--copy-count", type=int, help="Number of owned copies")
    edit_parser.add_argument("--location", help="Room or location")
    edit_parser.add_argument("--shelf-box", help="Shelf or box")
    edit_parser.add_argument("--loaned-to", help="Who currently has the book")
    edit_parser.add_argument("--local-notes", help="Local catalogue notes")

    report_parser = subparsers.add_parser("report", help="Generate reports")
    report_sub = report_parser.add_subparsers(dest="report_type", required=True)
    conflicts = report_sub.add_parser("conflicts", help="Show latest conflict report")
    conflicts.add_argument("--output", help="Write report to this path")
    summary = report_sub.add_parser("summary", help="Show latest sync summary")
    summary.add_argument("--output", help="Write summary to this path")

    export_parser = subparsers.add_parser("export", help="Export catalogue data")
    export_sub = export_parser.add_subparsers(dest="target", required=True)
    csv_export = export_sub.add_parser("csv", help="Export catalogue to CSV")
    csv_export.add_argument("--output", default="exports/catalogue.csv")
    json_export = export_sub.add_parser("json", help="Export catalogue to JSON")
    json_export.add_argument("--output", default="exports/catalogue.json")
    export_sub.add_parser("notion", help="Export catalogue to Notion")

    return parser


def _local_updates_from_args(args) -> dict[str, object]:
    updates: dict[str, object] = {}
    if args.owned is not None:
        updates["owned"] = 1 if args.owned == "true" else 0
    if args.copy_count is not None:
        updates["copy_count"] = args.copy_count
    for arg_name, field_name in (
        ("location", "location"),
        ("shelf_box", "shelf_box"),
        ("loaned_to", "loaned_to"),
        ("local_notes", "local_notes"),
    ):
        value = getattr(args, arg_name)
        if value is not None:
            updates[field_name] = value
    if not updates:
        raise SystemExit("No local fields provided to update.")
    return updates

if __name__ == "__main__":
    raise SystemExit(main())
