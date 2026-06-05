"""Read-only catalogue diagnostics for onboarding."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Mapping


REQUIRED_TABLES = {
    "books",
    "import_runs",
    "sync_conflicts",
}
GOODREADS_HEADERS = {
    "Book Id",
    "Title",
    "Author",
    "Exclusive Shelf",
}


def doctor_report(
    db_path: str | Path,
    *,
    root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    config: "object | None" = None,
) -> str:
    db_path = Path(db_path)
    root_path = Path(root) if root is not None else Path.cwd()
    env = env if env is not None else os.environ

    db_state = _inspect_database(db_path)
    csv_files = _find_goodreads_csvs(root_path)
    if config is not None:
        notion_configured = bool(config.notion_api_key) and bool(config.notion_database_id)
    else:
        notion_configured = bool(env.get("NOTION_API_KEY")) and bool(env.get("NOTION_DB_ID"))

    lines = ["Adso Doctor", "===========", ""]
    lines.extend(_configuration_section(config))
    lines.extend(
        [
            "Catalogue",
            "---------",
            f"Database path: {db_path}",
            f"Database file: {_status_text(db_state['exists'])}",
            f"Initialized: {_status_text(db_state['initialized'])}",
        ]
    )

    if db_state["error"]:
        lines.append(f"Database note: {db_state['error']}")
    if db_state["initialized"]:
        lines.extend(
            [
                f"Books: {db_state['book_count']}",
                f"Latest import: {_latest_import_text(db_state['latest_import'])}",
                f"Pending conflicts: {db_state['pending_conflicts']}",
            ]
        )

    lines.extend(
        [
            "",
            "Nearby Goodreads CSVs",
            "---------------------",
        ]
    )
    if csv_files:
        lines.extend(f"- {path}" for path in csv_files)
    else:
        lines.append("No likely Goodreads CSV files found nearby.")

    lines.extend(
        [
            "",
            "Notion",
            "------",
            f"Credentials configured: {_status_text(notion_configured)}",
        ]
    )

    lines.extend(["", "Suggested Next Commands", "-----------------------"])
    lines.extend(_suggested_commands(db_state, csv_files, notion_configured))
    return "\n".join(lines)


def _configuration_section(config: "object | None") -> list[str]:
    """Render the Configuration block. Tolerates config=None (older callers)."""
    from . import config as config_module

    user_path = config_module.user_config_path()
    project_path = config_module.project_config_path()
    files = [
        f"{label}: {path} ({'found' if path.exists() else 'not present'})"
        for label, path in (("project", project_path), ("user", user_path))
    ]

    if config is not None and getattr(config, "profile", None):
        profile_line = f"Active profile: {config.profile}"
    else:
        profile_line = "Active profile: none (using built-in defaults)"

    target = getattr(config, "notion_target", None) if config is not None else None
    target_line = f"Notion target: {target}" if target else "Notion target: (unnamed)"

    lines = ["Configuration", "-------------", profile_line, target_line]
    lines.extend(f"Config {entry}" for entry in files)
    lines.append("")
    return lines


def _inspect_database(db_path: Path) -> dict[str, object]:
    state: dict[str, object] = {
        "exists": db_path.exists(),
        "initialized": False,
        "book_count": 0,
        "latest_import": None,
        "pending_conflicts": 0,
        "error": None,
    }
    if not db_path.exists():
        return state

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        state["error"] = str(exc)
        return state

    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        initialized = REQUIRED_TABLES.issubset(tables)
        state["initialized"] = initialized
        if not initialized:
            return state

        state["book_count"] = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        state["pending_conflicts"] = conn.execute(
            "SELECT COUNT(*) FROM sync_conflicts WHERE status = 'pending'"
        ).fetchone()[0]
        state["latest_import"] = conn.execute(
            """
            SELECT source, mode, imported_at, row_count, created_count, updated_count,
                unchanged_count, conflict_count
            FROM import_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    return state


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _find_goodreads_csvs(root: Path) -> list[str]:
    candidates: list[str] = []
    if not root.exists():
        return candidates

    for path in sorted(root.rglob("*.csv")):
        if _is_hidden_or_generated(path, root):
            continue
        if _looks_like_goodreads_csv(path):
            candidates.append(str(path.relative_to(root)))
        if len(candidates) >= 5:
            break
    return candidates


def _is_hidden_or_generated(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    parts = set(relative.parts)
    return bool(parts & {".git", ".venv", "__pycache__", "exports", "reports"})


def _looks_like_goodreads_csv(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            header = handle.readline().strip()
    except OSError:
        return False
    columns = {column.strip() for column in header.split(",")}
    return GOODREADS_HEADERS.issubset(columns)


def _status_text(value: object) -> str:
    return "yes" if value else "no"


def _latest_import_text(row) -> str:
    if row is None:
        return "none yet"
    changed = row["created_count"] + row["updated_count"]
    return (
        f"{row['mode']} from {row['source']} at {row['imported_at']} "
        f"({row['row_count']} rows, {changed} changed, {row['conflict_count']} conflicts)"
    )


def _suggested_commands(
    db_state: Mapping[str, object],
    csv_files: list[str],
    notion_configured: bool,
) -> list[str]:
    if not db_state["exists"]:
        commands = ["- `adso init`"]
        if csv_files:
            commands.append(f"- `adso import goodreads {csv_files[0]}`")
        else:
            commands.append("- Add a Goodreads CSV export here, or try `examples/goodreads_sample.csv`.")
        return commands

    if not db_state["initialized"]:
        return ["- `adso init`"]

    if db_state["pending_conflicts"]:
        return ["- `adso report conflicts`"]

    if not db_state["book_count"]:
        if csv_files:
            return [f"- `adso import goodreads {csv_files[0]}`"]
        return ["- `adso import goodreads examples/goodreads_sample.csv`"]

    commands = ["- `adso list`", "- `adso search \"query\"`", "- `adso report summary`"]
    if notion_configured:
        commands.append("- `adso export notion`")
    else:
        commands.append("- Set `NOTION_API_KEY` and `NOTION_DB_ID` before `adso export notion`.")
    return commands
