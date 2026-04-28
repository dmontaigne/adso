"""Human-readable reporting for sync runs."""

from __future__ import annotations

from pathlib import Path


def latest_sync_summary_markdown(conn) -> str:
    run = _latest_run(conn)
    if run is None:
        return "# Adso Sync Summary\n\nNo import runs found.\n"

    conflicts = _conflicts_for_run(conn, run["id"])
    total_changes = run["created_count"] + run["updated_count"]
    lines = [
        "# Adso Sync Summary",
        "",
        f"Import run: {run['id']}",
        f"Source: {run['source']}",
        f"Mode: {run['mode']}",
        f"Imported at: {run['imported_at']}",
        "",
        "## What happened",
        "",
        f"Adso processed {run['row_count']} Goodreads rows from `{run['source_path']}`.",
    ]

    if run["created_count"]:
        lines.append(f"It added {run['created_count']} new books to the local catalogue.")
    if run["updated_count"]:
        lines.append(
            f"It safely updated {run['updated_count']} existing books where the local value still matched "
            "the previous Goodreads snapshot."
        )
    if run["unchanged_count"]:
        lines.append(f"It left {run['unchanged_count']} books unchanged.")
    if run["conflict_count"]:
        lines.append(
            f"It found {run['conflict_count']} conflicts where Goodreads changed a field that also appears "
            "to have been changed locally."
        )
    if not total_changes and not run["conflict_count"]:
        lines.append("No catalogue changes were needed. Your local catalogue already matched this Goodreads export.")

    lines.extend(["", "## Safety check", ""])
    if run["conflict_count"]:
        lines.append(
            "Local catalogue values were preserved for every conflict. Review the conflict report before accepting "
            "any incoming Goodreads value."
        )
    else:
        lines.append("No conflicts were detected, so no local catalogue decisions need review from this run.")

    lines.extend(["", "## Suggested next action", ""])
    lines.extend(_next_actions(run, conflicts))
    lines.append("")
    return "\n".join(lines)


def latest_conflicts_markdown(conn) -> str:
    run = _latest_run(conn)
    if run is None:
        return "# Adso Conflict Report\n\nNo import runs found.\n"

    conflicts = _conflicts_for_run(conn, run["id"])

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
                f"- Recommendation: {_recommendation_for_conflict(conflict)}",
                "",
            ]
        )
    return "\n".join(lines)


def write_latest_conflicts(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(latest_conflicts_markdown(conn), encoding="utf-8")
    return path


def write_latest_sync_summary(conn, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(latest_sync_summary_markdown(conn), encoding="utf-8")
    return path


def _latest_run(conn):
    return conn.execute("SELECT * FROM import_runs ORDER BY id DESC LIMIT 1").fetchone()


def _conflicts_for_run(conn, import_run_id: int):
    return conn.execute(
        """
        SELECT c.*, b.title, b.author, b.goodreads_id
        FROM sync_conflicts c
        JOIN books b ON b.id = c.book_id
        WHERE c.import_run_id = ?
        ORDER BY b.title COLLATE NOCASE, c.field_name
        """,
        (import_run_id,),
    ).fetchall()


def _next_actions(run, conflicts) -> list[str]:
    if conflicts:
        return [
            "- Run `adso report conflicts` to inspect the preserved local values and incoming Goodreads values.",
            "- Resolve only the fields you trust; local catalogue data remains canonical until then.",
        ]
    if run["created_count"] and not run["updated_count"]:
        return [
            "- Spot-check the new import with `adso export csv` or `adso export json`.",
            "- Start adding local ownership and shelf data with `adso edit`.",
        ]
    if run["updated_count"]:
        return [
            "- No manual review is required for this run.",
            "- Export a fresh backup if you want a portable snapshot after the safe updates.",
        ]
    return ["- No action needed."]


def _recommendation_for_conflict(conflict) -> str:
    field = conflict["field_name"]
    if field in {"reading_status", "exclusive_shelf", "shelves_json"}:
        return (
            "decide whether Goodreads activity or your local catalogue better reflects your current reading state. "
            "The local value was preserved."
        )
    if field in {"rating", "date_read", "read_count", "my_review"}:
        return (
            "review this as reading-history data. Accept the Goodreads value only if it represents an intentional "
            "recent update."
        )
    if field in {"title", "author", "isbn10", "isbn13"}:
        return (
            "treat this as a metadata/edition change and verify it before accepting. This may indicate an edition "
            "mismatch rather than a simple update."
        )
    return "review manually. The local catalogue value was preserved."


def _format_value(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("`", "'")
