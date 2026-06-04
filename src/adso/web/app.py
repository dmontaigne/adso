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
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import activity as activity_service
from .. import conflicts as conflicts_service
from .. import db
from .. import sync as sync_service
from ..catalogue import BookFilters, get_book, list_books, search_books

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(db_path: str | Path) -> FastAPI:
    """Build a FastAPI app bound to the SQLite database at ``db_path``."""

    db_path = str(db_path)
    app = FastAPI(
        title="Adso",
        description="Local-first book catalogue web UI.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def get_conn() -> Iterator[sqlite3.Connection]:
        # A fresh connection per request keeps SQLite thread-safe under the
        # uvicorn worker threadpool. initialize() is idempotent.
        conn = db.connect(db_path)
        db.initialize(conn)
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
