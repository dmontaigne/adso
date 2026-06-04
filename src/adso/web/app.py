"""FastAPI application factory for the Adso local web UI.

The app is intentionally a thin presentation layer: every route delegates to
the existing catalogue services in :mod:`adso.catalogue`, which run in-process
against the same SQLite file the CLI uses. No catalogue or sync logic is
duplicated here.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from html import escape
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import activity as activity_service
from .. import conflicts as conflicts_service
from .. import covers as covers_service
from .. import db
from .. import sync as sync_service
from ..catalogue import BookFilters, get_book, list_books, search_books

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Muted tints for the generated placeholder shown when a book has no cover.
_PLACEHOLDER_TINTS = ("#6b7280", "#7c6f64", "#5f7470", "#6d6875", "#785964", "#4a6670")


def _placeholder_svg(label: str) -> str:
    """Build a small SVG cover placeholder (title initials on a tinted block).

    Generated inline so missing covers need no external request and work offline.
    """
    text = (label or "?").strip()
    words = [w for w in text.split() if w]
    initials = "".join(w[0] for w in words[:2]).upper() or "?"
    initials = escape(initials)  # keep the SVG well-formed for titles like "& Sons"
    tint = _PLACEHOLDER_TINTS[sum(ord(c) for c in text) % len(_PLACEHOLDER_TINTS)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300" width="200" height="300">'
        f'<rect width="200" height="300" fill="{tint}"/>'
        f'<text x="100" y="150" fill="#ffffff" font-family="system-ui, sans-serif" '
        'font-size="72" font-weight="600" text-anchor="middle" dominant-baseline="central">'
        f"{initials}</text></svg>"
    )


def create_app(db_path: str | Path) -> FastAPI:
    """Build a FastAPI app bound to the SQLite database at ``db_path``."""

    db_path = str(db_path)
    # Covers are stored beside the database; resolve relative cover_path values
    # against this root when serving them.
    cover_root = Path(db_path).resolve().parent
    app = FastAPI(
        title="Adso",
        description="Local-first book catalogue web UI.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Initialize the schema ONCE at startup. Doing it per request turned every
    # read (including the ~30 cover thumbnails a catalogue page fires at once)
    # into a write/commit, which contended on the SQLite write lock and returned
    # 500s under load. Requests now use read-only connections.
    _init_conn = db.connect(db_path)
    db.initialize(_init_conn)
    _init_conn.close()

    def get_conn() -> Iterator[sqlite3.Connection]:
        # A fresh connection per request keeps SQLite thread-safe under the
        # uvicorn worker threadpool. The schema is already initialized above.
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _filters(
        status: str | None,
        owned: str | None,
        location: str | None,
        author: str | None,
        limit: int | None,
    ) -> BookFilters:
        owned_bool = None
        if owned in ("true", "false"):
            owned_bool = owned == "true"
        return BookFilters(
            status=status or None,
            owned=owned_bool,
            location=location or None,
            author=author or None,
            limit=limit,
        )

    def _query_books(
        conn: sqlite3.Connection,
        q: str,
        filters: BookFilters,
    ) -> list[dict]:
        if q.strip():
            return search_books(conn, q, filters)
        return list_books(conn, filters)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        q: str = Query("", description="Search query"),
        status: str | None = Query(None),
        owned: str | None = Query(None),
        location: str | None = Query(None),
        author: str | None = Query(None),
        limit: int | None = Query(None, ge=1),
    ) -> HTMLResponse:
        filters = _filters(status, owned, location, author, limit)
        books = _query_books(conn, q, filters)
        return templates.TemplateResponse(
            request,
            "catalogue.html",
            {
                "books": books,
                "q": q,
                "status": status or "",
                "owned": owned or "",
                "location": location or "",
                "author": author or "",
                "count": len(books),
                "pending_count": conflicts_service.pending_count(conn),
            },
        )

    @app.get("/book/{goodreads_id}", response_class=HTMLResponse)
    def book_detail(
        request: Request,
        goodreads_id: str,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        book = get_book(conn, goodreads_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"No book for Goodreads ID {goodreads_id}")
        return templates.TemplateResponse(
            request,
            "book_detail.html",
            {"book": book, "pending_count": conflicts_service.pending_count(conn)},
        )

    @app.get("/covers/{goodreads_id}")
    def cover(
        goodreads_id: str,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        row = db.get_book_by_goodreads_id(conn, goodreads_id)
        if row is not None and row["cover_path"]:
            file_path = cover_root / row["cover_path"]
            if file_path.is_file():
                return FileResponse(
                    file_path,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        label = row["title"] if row is not None else goodreads_id
        return Response(
            content=_placeholder_svg(label),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/books")
    def api_books(
        conn: sqlite3.Connection = Depends(get_conn),
        q: str = Query("", description="Search query"),
        status: str | None = Query(None),
        owned: str | None = Query(None),
        location: str | None = Query(None),
        author: str | None = Query(None),
        limit: int | None = Query(None, ge=1),
    ) -> dict:
        filters = _filters(status, owned, location, author, limit)
        books = _query_books(conn, q, filters)
        return {"count": len(books), "books": books}

    @app.get("/api/books/{goodreads_id}")
    def api_book(
        goodreads_id: str,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict:
        book = get_book(conn, goodreads_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"No book for Goodreads ID {goodreads_id}")
        return book

    @app.get("/conflicts", response_class=HTMLResponse)
    def conflicts_page(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        groups = conflicts_service.list_open_conflicts(conn)
        total = sum(len(group["conflicts"]) for group in groups)
        return templates.TemplateResponse(
            request,
            "conflicts.html",
            {"groups": groups, "total": total, "pending_count": total},
        )

    @app.post("/conflicts/{conflict_id}/resolve", response_class=HTMLResponse)
    def resolve_conflict(
        request: Request,
        conflict_id: int,
        conn: sqlite3.Connection = Depends(get_conn),
        choice: str = Form(...),
        value: str | None = Form(None),
    ) -> HTMLResponse:
        try:
            outcome = conflicts_service.resolve_conflict(
                conn, conflict_id, choice=choice, custom_value=value
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return templates.TemplateResponse(
            request,
            "_conflict_field_resolved.html",
            {"pending_count": conflicts_service.pending_count(conn), **outcome},
        )

    @app.post("/conflicts/book/{book_id}/resolve", response_class=HTMLResponse)
    def resolve_book_conflicts(
        request: Request,
        book_id: int,
        conn: sqlite3.Connection = Depends(get_conn),
        choice: str = Form(...),
    ) -> HTMLResponse:
        try:
            outcome = conflicts_service.resolve_book(conn, book_id, choice=choice)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return templates.TemplateResponse(
            request,
            "_conflict_group_resolved.html",
            {"pending_count": conflicts_service.pending_count(conn), **outcome},
        )

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        runs = activity_service.list_activity(conn)
        return templates.TemplateResponse(
            request,
            "activity.html",
            {"runs": runs, "pending_count": conflicts_service.pending_count(conn)},
        )

    @app.get("/import", response_class=HTMLResponse)
    def import_page(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "import.html",
            {"pending_count": conflicts_service.pending_count(conn)},
        )

    @app.post("/import", response_class=HTMLResponse)
    def import_upload(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        file: UploadFile = File(...),
    ) -> HTMLResponse:
        filename = os.path.basename(file.filename or "") or "upload.csv"
        context: dict = {"filename": filename}

        if not filename.lower().endswith(".csv"):
            context["error"] = "Please choose a Goodreads CSV export (a .csv file)."
        else:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                    tmp.write(file.file.read())
                    tmp_path = tmp.name
                # Label the run "import" on an empty catalogue, otherwise "sync".
                # Behaviour is identical either way; this just reads naturally in Activity.
                book_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
                mode = "import" if book_count == 0 else "sync"
                summary = sync_service.import_goodreads_csv(
                    conn, tmp_path, mode=mode, source_label=filename
                )
                context["summary"] = {
                    "mode": summary.mode,
                    "row_count": summary.row_count,
                    "created": summary.created,
                    "updated": summary.updated,
                    "unchanged": summary.unchanged,
                    "conflicts": summary.conflicts,
                    "skipped": summary.skipped,
                }
                # Best-effort cover enrichment; never let a network hiccup
                # break the import the user just performed.
                try:
                    cover_result = covers_service.fetch_covers(conn, cover_root)
                    context["covers"] = {
                        "fetched": cover_result["fetched"],
                        "not_found": cover_result["not_found"],
                        "errors": cover_result["errors"],
                    }
                except covers_service.CoversError:
                    context["covers"] = None
            except Exception as exc:  # noqa: BLE001 - surface any parse/IO error to the user
                context["error"] = f"Could not import that file: {exc}"
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        context["pending_count"] = conflicts_service.pending_count(conn)
        return templates.TemplateResponse(request, "import.html", context)

    @app.get("/api/conflicts")
    def api_conflicts(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        groups = conflicts_service.list_open_conflicts(conn)
        return {"pending": conflicts_service.pending_count(conn), "books": groups}

    @app.get("/api/activity")
    def api_activity(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        runs = activity_service.list_activity(conn)
        return {"count": len(runs), "runs": runs}

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "db": db_path}

    return app
