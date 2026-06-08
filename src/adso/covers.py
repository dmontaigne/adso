"""Cover-art enrichment for the local catalogue.

Covers are downloaded from public sources and *owned* locally: image files are
written to a ``covers/`` directory beside the SQLite database and the books row
records a relative path plus provenance. This is enrichment, not a
Goodreads-sourced field, so it deliberately stays out of the
source_snapshots/sync_conflicts machinery.

Source chain (first hit wins):
    1. Open Library cover by ISBN-13 then ISBN-10.
    2. Open Library Search by title + author -> cover id -> cover by id.
    3. iTunes / Apple Books Search by title + author -> artwork.

Open Library is the primary source: it is the open, community source (in keeping
with the local-first ethos), needs no API key, and is lenient about volume.
iTunes is a no-key fallback that fills gaps Open Library lacks art for. Google
Books is deliberately not used — its keyless tier rate-limits (HTTP 429) almost
immediately and its throttled connections can stall.

A manually-set cover (``cover_status == 'manual'``) is never overwritten by an
automatic fetch.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import db

# A descriptive User-Agent is requested by Open Library so they can identify
# polite clients; see https://openlibrary.org/dev/docs/api/covers.
USER_AGENT = "Adso/0.1 (local-first book catalogue; +https://github.com/davidwhipps/adso)"
RATE_LIMIT_DELAY = 0.75

# (connect, read) timeouts: a stalled connection can never hang the whole run.
HTTP_TIMEOUT = (10, 30)

# iTunes' unauthenticated Search API allows ~20 requests/minute, so space the
# iTunes calls (only hit as a fallback) to stay comfortably under that.
ITUNES_MIN_INTERVAL = 2.0

OPENLIBRARY_COVER_ISBN = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
OPENLIBRARY_COVER_ID = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false"
OPENLIBRARY_SEARCH = "https://openlibrary.org/search.json"
ITUNES_SEARCH = "https://itunes.apple.com/search"

# Magic-byte signatures -> file extension. Only these are accepted as covers.
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


class CoversError(RuntimeError):
    pass


def _require_requests():
    try:
        import requests
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via extras
        raise CoversError(
            "Install the requests dependency before fetching covers:\n"
            "    pip install -e '.[covers]'"
        ) from exc
    return requests


def _request(method: str, url: str, **kwargs):
    """HTTP wrapper with bounded timeouts and bounded 429 backoff.

    Both the connect/read timeout and the 429 retry count are capped so that a
    slow or throttling host can never stall the sequential fetch (a scalar
    timeout plus an unbounded 429 loop is what let an overnight run hang).
    """
    requests = _require_requests()
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    response = None
    for _ in range(3):
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
        except Exception as exc:  # noqa: BLE001 - network errors become a miss/error upstream
            raise CoversError(f"Could not reach {url}: {str(exc)[:300]}") from exc
        if response.status_code != 429:
            return response
        time.sleep(min(int(response.headers.get("Retry-After", 2) or 2), 5))
    return response  # still 429 after retries -> treated as a miss upstream


def _detect_image_ext(data: bytes) -> str | None:
    """Return a file extension if ``data`` looks like a supported image, else None.

    WEBP (RIFF....WEBP) is detected separately because the marker is split.
    """
    for signature, ext in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _download_image(url: str) -> tuple[bytes, str] | None:
    """Fetch ``url`` and return (bytes, ext) only if it is a valid image."""
    response = _request("get", url)
    if response is None or response.status_code != 200 or not response.content:
        return None
    ext = _detect_image_ext(response.content)
    if ext is None:
        return None
    return response.content, ext


def _openlibrary_search_cover_id(title: str, author: str) -> int | None:
    """Look up a cover id for a title (+author) via the Open Library Search API."""
    params = {"title": title, "limit": 1, "fields": "cover_i"}
    if author:
        params["author"] = author
    response = _request("get", OPENLIBRARY_SEARCH, params=params)
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    docs = payload.get("docs") or []
    if not docs:
        return None
    cover_id = docs[0].get("cover_i")
    return cover_id if isinstance(cover_id, int) and cover_id > 0 else None


def _itunes_artwork_url(title: str, author: str) -> str | None:
    """Look up cover artwork for a title (+author) via the iTunes Search API.

    The API returns a 100x100 ``artworkUrl100``; the dimension segment can be
    swapped for a larger size to get a usable-resolution image.
    """
    term = f"{title} {author}".strip()
    response = _request(
        "get", ITUNES_SEARCH, params={"term": term, "entity": "ebook", "limit": 1}
    )
    # iTunes is gentle about volume; space calls out to respect its rate limit.
    time.sleep(ITUNES_MIN_INTERVAL)
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    results = payload.get("results") or []
    if not results:
        return None
    artwork = results[0].get("artworkUrl100")
    if not artwork:
        return None
    return artwork.replace("100x100bb", "600x600bb")


def resolve_cover(book: dict[str, Any]) -> tuple[bytes, str, str, str] | None:
    """Resolve a cover for one book.

    Returns ``(image_bytes, source, source_url, ext)`` for the first source that
    yields a valid image, or ``None`` if no source has one.
    """
    isbns = [isbn for isbn in (book.get("isbn13"), book.get("isbn10")) if isbn]

    # 1. Open Library cover by ISBN.
    for isbn in isbns:
        url = OPENLIBRARY_COVER_ISBN.format(isbn=isbn)
        result = _download_image(url)
        if result is not None:
            data, ext = result
            return data, "openlibrary:isbn", url, ext

    # 2. Open Library Search by title + author -> cover id -> cover image.
    title = (book.get("title") or "").strip()
    author = (book.get("author") or "").strip()
    if title:
        cover_id = _openlibrary_search_cover_id(title, author)
        if cover_id:
            url = OPENLIBRARY_COVER_ID.format(cover_id=cover_id)
            result = _download_image(url)
            if result is not None:
                data, ext = result
                return data, "openlibrary:search", url, ext

    # 3. iTunes / Apple Books Search by title + author.
    if title:
        artwork_url = _itunes_artwork_url(title, author)
        if artwork_url:
            result = _download_image(artwork_url)
            if result is not None:
                data, ext = result
                return data, "itunes:search", artwork_url, ext

    return None


def _covers_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "covers"


def _remove_existing(data_dir: str | Path, cover_path: str | None) -> None:
    if not cover_path:
        return
    existing = Path(data_dir) / cover_path
    try:
        existing.unlink()
    except FileNotFoundError:
        pass


def _should_skip(status: str | None, refresh: bool, retry_missing: bool) -> bool:
    if status == "manual":
        return True  # never clobber a manual cover
    if refresh:
        return False  # reconsider everything (except manual)
    if status == "fetched":
        return True
    if status == "not_found":
        return not retry_missing  # retry_missing re-attempts past misses
    return False  # None / error -> always process


def fetch_covers(
    conn,
    data_dir: str | Path,
    *,
    limit: int | None = None,
    refresh: bool = False,
    retry_missing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch covers for books that need them.

    ``limit`` caps the number of books *attempted* (not merely scanned), which
    makes ``--limit 5`` useful for trial runs. ``retry_missing`` re-attempts
    books previously marked ``not_found`` (e.g. after adding a new source) while
    leaving already-fetched and manual covers untouched. Returns summary stats.
    """
    if limit is not None and limit < 1:
        raise CoversError("limit must be at least 1.")

    covers_dir = _covers_dir(data_dir)
    fetched = not_found = errors = skipped = 0
    actions: list[dict[str, str]] = []
    attempted = 0

    for row in db.iter_books(conn):
        book = dict(row)
        goodreads_id = book.get("goodreads_id")
        if not goodreads_id:
            # Without a stable id we can't name a file or serve it in the web UI.
            skipped += 1
            continue
        if _should_skip(book.get("cover_status"), refresh, retry_missing):
            skipped += 1
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1

        title = str(book.get("title") or "")
        try:
            resolved = resolve_cover(book)
        except CoversError:
            errors += 1
            actions.append({"goodreads_id": str(goodreads_id), "title": title, "result": "error"})
            if not dry_run:
                db.set_cover(
                    conn,
                    int(book["id"]),
                    cover_path=book.get("cover_path"),
                    cover_source=book.get("cover_source"),
                    cover_source_url=book.get("cover_source_url"),
                    cover_status="error",
                )
            time.sleep(RATE_LIMIT_DELAY)
            continue

        if resolved is None:
            not_found += 1
            actions.append({"goodreads_id": str(goodreads_id), "title": title, "result": "not_found"})
            if not dry_run:
                db.set_cover(
                    conn,
                    int(book["id"]),
                    cover_path=None,
                    cover_source=None,
                    cover_source_url=None,
                    cover_status="not_found",
                )
            time.sleep(RATE_LIMIT_DELAY)
            continue

        data, source, source_url, ext = resolved
        rel_path = f"covers/{goodreads_id}.{ext}"
        actions.append(
            {"goodreads_id": str(goodreads_id), "title": title, "result": "fetched", "source": source}
        )
        if not dry_run:
            _remove_existing(data_dir, book.get("cover_path"))
            covers_dir.mkdir(parents=True, exist_ok=True)
            (Path(data_dir) / rel_path).write_bytes(data)
            db.set_cover(
                conn,
                int(book["id"]),
                cover_path=rel_path,
                cover_source=source,
                cover_source_url=source_url,
                cover_status="fetched",
            )
        fetched += 1
        time.sleep(RATE_LIMIT_DELAY)

    return {
        "fetched": fetched,
        "not_found": not_found,
        "errors": errors,
        "skipped": skipped,
        "actions": actions,
    }


def set_manual_cover(
    conn,
    data_dir: str | Path,
    goodreads_id: str,
    *,
    url: str | None = None,
    file: str | Path | None = None,
) -> dict[str, Any]:
    """Set a cover from a user-supplied URL or local file.

    The resulting cover is tagged ``manual`` so automatic fetches never replace it.
    """
    if bool(url) == bool(file):
        raise CoversError("Provide exactly one of url or file.")

    row = db.get_book_by_goodreads_id(conn, goodreads_id)
    if row is None:
        raise CoversError(f"No book found for Goodreads ID {goodreads_id}")
    book = dict(row)

    if url:
        result = _download_image(url)
        if result is None:
            raise CoversError(f"{url} did not return a usable image.")
        data, ext = result
        source_url = url
    else:
        path = Path(file)  # type: ignore[arg-type]
        if not path.exists():
            raise CoversError(f"Cover file not found: {path}")
        data = path.read_bytes()
        ext = _detect_image_ext(data)
        if ext is None:
            raise CoversError(f"{path} is not a supported image (JPEG/PNG/GIF/WEBP).")
        source_url = str(path)

    rel_path = f"covers/{goodreads_id}.{ext}"
    _remove_existing(data_dir, book.get("cover_path"))
    _covers_dir(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / rel_path).write_bytes(data)
    db.set_cover(
        conn,
        int(book["id"]),
        cover_path=rel_path,
        cover_source="manual",
        cover_source_url=source_url,
        cover_status="manual",
    )
    return {"goodreads_id": goodreads_id, "title": book.get("title"), "cover_path": rel_path}
