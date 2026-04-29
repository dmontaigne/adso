"""Command line interface for Adso."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db
from .catalogue import BookFilters, get_book, list_books, search_books
from .doctor import doctor_report
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

    if args.command == "doctor":
        print(doctor_report(args.db))
        return 0

    conn = db.connect(args.db)
    try:
        if args.command == "init":
            db.initialize(conn)
            print(f"Initialized Adso catalogue at {args.db}")
            return 0

        db.initialize(conn)

        if args.command == "list":
            books = list_books(conn, _book_filters_from_args(args))
            print(_format_book_table(books))
            return 0

        if args.command == "search":
            books = search_books(conn, args.query, _book_filters_from_args(args))
            print(_format_book_table(books))
            return 0

        if args.command == "show":
            book = get_book(conn, args.goodreads_id)
            if book is None:
                parser.error(f"No book found for Goodreads ID {args.goodreads_id}")
            print(_format_book_detail(book))
            return 0

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
                result = export_to_notion(conn, dry_run=args.dry_run, limit=args.limit)
            except NotionConfigError as exc:
                parser.error(str(exc))
            print(_format_notion_export_result(result, dry_run=args.dry_run))
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
    subparsers.add_parser("doctor", help="Check local Adso setup and suggest next commands")

    list_parser = subparsers.add_parser("list", help="List books in the local catalogue")
    list_parser.add_argument("--status", help="Filter by reading status, e.g. 'Read' or 'To Read'")
    list_parser.add_argument("--owned", choices=["true", "false"], help="Filter by physical ownership")
    list_parser.add_argument("--location", help="Filter by room or location")
    list_parser.add_argument("--author", help="Filter by author")
    list_parser.add_argument("--limit", type=int, help="Maximum number of books to show")

    search_parser = subparsers.add_parser("search", help="Search books in the local catalogue")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--status", help="Filter by reading status, e.g. 'Read' or 'To Read'")
    search_parser.add_argument("--owned", choices=["true", "false"], help="Filter by physical ownership")
    search_parser.add_argument("--location", help="Filter by room or location")
    search_parser.add_argument("--author", help="Filter by author")
    search_parser.add_argument("--limit", type=int, help="Maximum number of books to show")

    show_parser = subparsers.add_parser("show", help="Show detailed information for one book")
    show_parser.add_argument("goodreads_id", help="Goodreads Book ID")

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
    notion_export = export_sub.add_parser("notion", help="Export catalogue to Notion")
    notion_export.add_argument("--dry-run", action="store_true", help="Preview create/update actions without writing")
    notion_export.add_argument("--limit", type=int, help="Maximum number of books to export")

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


def _book_filters_from_args(args) -> BookFilters:
    owned = None
    if getattr(args, "owned", None) is not None:
        owned = args.owned == "true"
    return BookFilters(
        status=getattr(args, "status", None),
        owned=owned,
        location=getattr(args, "location", None),
        author=getattr(args, "author", None),
        limit=getattr(args, "limit", None),
    )


def _format_notion_export_result(result: dict[str, object], *, dry_run: bool) -> str:
    created_label = "would be created" if dry_run else "created"
    updated_label = "would be updated" if dry_run else "updated"
    heading = "Notion dry-run complete" if dry_run else "Notion export complete"
    lines = [
        f"{heading}: "
        f"{result['created']} {created_label}, {result['updated']} {updated_label}, {result['errors']} errors"
    ]
    if dry_run:
        actions = result.get("actions", [])
        if actions:
            lines.append("")
            lines.append("Planned Notion actions:")
            for action in actions:
                if not isinstance(action, dict):
                    continue
                verb = "Would update" if action.get("action") == "update" else "Would create"
                title = action.get("title") or "Untitled"
                goodreads_id = action.get("goodreads_id") or "unknown"
                lines.append(f"- {verb}: {title} (Goodreads ID {goodreads_id})")
        else:
            lines.append("")
            lines.append("No Notion actions planned.")
    return "\n".join(lines)


def _format_book_table(books: list[dict[str, object]]) -> str:
    if not books:
        return "No books found."

    rows = [
        {
            "Goodreads ID": str(book.get("goodreads_id") or ""),
            "Title": str(book.get("title") or ""),
            "Author": str(book.get("author") or ""),
            "Status": str(book.get("reading_status") or ""),
            "Owned": "yes" if book.get("owned") else "no",
            "Location": str(book.get("location") or ""),
        }
        for book in books
    ]
    headers = ["Goodreads ID", "Title", "Author", "Status", "Owned", "Location"]
    widths = {
        header: min(
            max(len(header), *(len(_truncate(row[header], 48)) for row in rows)),
            48,
        )
        for header in headers
    }
    lines = [
        "  ".join(header.ljust(widths[header]) for header in headers),
        "  ".join("-" * widths[header] for header in headers),
    ]
    for row in rows:
        lines.append(
            "  ".join(_truncate(row[header], widths[header]).ljust(widths[header]) for header in headers)
        )
    return "\n".join(lines)


def _format_book_detail(book: dict[str, object]) -> str:
    shelves = book.get("shelves") or []
    if isinstance(shelves, list):
        shelves_text = ", ".join(str(shelf) for shelf in shelves)
    else:
        shelves_text = str(shelves)

    sections = [
        (
            "Goodreads Fields",
            [
                ("Goodreads ID", book.get("goodreads_id")),
                ("Title", book.get("title")),
                ("Author", book.get("author")),
                ("Additional Authors", book.get("additional_authors")),
                ("ISBN-10", book.get("isbn10")),
                ("ISBN-13", book.get("isbn13")),
                ("Reading Status", book.get("reading_status")),
                ("Exclusive Shelf", book.get("exclusive_shelf")),
                ("Shelves", shelves_text),
                ("Rating", book.get("rating")),
                ("Average Rating", book.get("average_rating")),
                ("Date Read", book.get("date_read")),
                ("Date Added", book.get("date_added")),
                ("Read Count", book.get("read_count")),
                ("Owned Copies", book.get("owned_copies")),
                ("Review", book.get("my_review")),
                ("Private Notes", book.get("private_notes")),
            ],
        ),
        (
            "Local Catalogue Fields",
            [
                ("Owned", "yes" if book.get("owned") else "no"),
                ("Copy Count", book.get("copy_count")),
                ("Location", book.get("location")),
                ("Shelf/Box", book.get("shelf_box")),
                ("Loaned To", book.get("loaned_to")),
                ("Local Notes", book.get("local_notes")),
            ],
        ),
    ]
    lines: list[str] = []
    for section, fields in sections:
        if lines:
            lines.append("")
        lines.append(section)
        lines.append("-" * len(section))
        for label, value in fields:
            lines.append(f"{label}: {_display_value(value)}")
    return "\n".join(lines)


def _display_value(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


if __name__ == "__main__":
    raise SystemExit(main())
