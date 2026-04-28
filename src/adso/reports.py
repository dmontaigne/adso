"""Human-readable reporting for sync runs."""

from __future__ import annotations

from pathlib import Path


def latest_conflicts_markdown(conn) -> str:
    run = conn.execute(
        "SELECT * FROM import_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return "# Adso Conflict Report\n\nNo import runs found.\n"

    conflicts = conn.execute(
        """
        SELECT c.*, b.title, b.author, b.goodreads_id
        FROM sync_conflicts c
        JOIN books b ON b.id = c.book_id
        WHERE c.import_run_id = ?
        ORDER BY b.title COLLATE NOCASE, c.field_name
        """,
        (run["id"],),
    ).fetchall()

    lines = [
        "# Adso Conflict Report",
        "",
        f"Import run: {run['id']}",
        f"Source: {run['source']}",
        f"Imported at: {run['imported_at']}",
        f"Conflicts: {len(conflicts)}",
        "",
    ]

    if not conflicts:
        lines.append("No pending conflicts for the latest import run.")
        lines.append("")
        return "\n".join(lines)

    for conflict in conflicts:
        lines.extend(
            [
                f"## {conflict['title']}",
                "",
                f"- Author: {conflict['author'] or 'Unknown'}",
                f"- Goodreads ID: {conflict['goodreads_id']}",
                f"- Field: `{conflict['field_name']}`",
                f"- Previous Goodreads value: `{_format_value(conflict['old_source_value'])}`",
                f"- Local catalogue value: `{_format_value(conflict['local_value'])}`",
                f"- Incoming Goodreads value: `{_format_value(conflict['incoming_value'])}`",
                "- Recommendation: review manually. The local catalogue was preserved.",
                "",
            ]
        )
    return "\n".join(lines)


def write_latest_conflicts(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(latest_conflicts_markdown(conn), encoding="utf-8")
    return path


def _format_value(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("`", "'")
