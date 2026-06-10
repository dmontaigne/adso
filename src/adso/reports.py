"""Human-readable reporting for sync runs."""

from __future__ import annotations

from pathlib import Path

from . import conflicts as conflicts_service
from . import db

_STATUS_ORDER = ("pending", "review_later", "resolved", "ignored")
_STATUS_REPORT_LABELS = {
    "pending": "Pending",
    "review_later": "Deferred",
    "resolved": "Resolved",
    "ignored": "Ignored",
}


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

    deferred = db.deferred_conflict_count(conn)
    if deferred:
        lines.append(
            f"{deferred} conflict{'' if deferred == 1 else 's'} marked for later review still need a decision."
        )

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
    ]
    breakdown = _status_breakdown(conflicts)
    if breakdown:
        lines.append(f"Status: {breakdown}")
    lines.append("")

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
                f"- Status: {_status_label(conflict['status'])}",
            ]
        )
        decision_line = _decision_line(conn, conflict)
        if decision_line:
            lines.append(decision_line)
        lines.extend(
            [
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
            "- Start recording which books you own (and in what format) with `adso edit`.",
        ]
    if run["updated_count"]:
        return [
            "- No manual review is required for this run.",
            "- Export a fresh backup if you want a portable snapshot after the safe updates.",
        ]
    return ["- No action needed."]


def _status_label(status: str) -> str:
    return _STATUS_REPORT_LABELS.get(status, status)


def _status_breakdown(conflicts) -> str:
    counts: dict[str, int] = {}
    for conflict in conflicts:
        counts[conflict["status"]] = counts.get(conflict["status"], 0) + 1
    parts = [
        f"{_STATUS_REPORT_LABELS[status]} {counts[status]}"
        for status in _STATUS_ORDER
        if counts.get(status)
    ]
    return " · ".join(parts)


def _decision_line(conn, conflict) -> str | None:
    """Show the latest decision (with provenance) for a decided conflict."""
    if conflict["status"] not in db.DECIDED_CONFLICT_STATUSES:
        return None
    decisions = db.list_conflict_decisions(conn, conflict["id"])
    last = decisions[-1] if decisions else None
    label = conflicts_service.decision_label(conflict["resolution"] or "")
    if last is None:
        return f"- Decision: {label}"
    when = f" on {last['created_at']}" if last["created_at"] else ""
    return f"- Decision: {label} (via {last['actor']}{when})"


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
