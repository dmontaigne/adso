"""Open Library metadata enrichment for the local catalogue.

Descriptions, subjects, and place/time facets are fetched from Open Library and
*owned* locally: like covers, this is enrichment, not a Goodreads-sourced field,
so it deliberately stays out of the source_snapshots/sync_conflicts machinery.

Resolution chain per book (first hit wins):
    1. Edition by ISBN-13 then ISBN-10 -> work record.
    2. Open Library Search by title + author -> work record. When the book has
       no ISBN at all, the matched edition is also fetched to backfill empty
       isbn13/isbn10 columns (fill-only-if-empty, enforced in db.backfill_isbns;
       a guard in adso.sync keeps later Goodreads syncs from blanking them).

Description and subjects are always read from the WORK record so every book
gets the same canonical shape regardless of which path matched it. Open Library
is the only source: open, no API key, and already trusted by the cover fetcher.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from . import db
from .ol_http import RATE_LIMIT_DELAY
from .ol_http import request as _http_request

OPENLIBRARY_EDITION_ISBN = "https://openlibrary.org/isbn/{isbn}.json"
OPENLIBRARY_WORK = "https://openlibrary.org{work_key}.json"
OPENLIBRARY_EDITION = "https://openlibrary.org/books/{olid}.json"
OPENLIBRARY_SEARCH = "https://openlibrary.org/search.json"

# Display caps applied at store time: the stored form IS the display form, the
# same philosophy as db.normalize_tags. Popular works carry 40+ noisy subject
# tags; past these caps they stop being browsable.
SUBJECTS_CAP = 25
PLACES_CAP = 10
TIMES_CAP = 10

# Library-plumbing tags Open Library attaches that say nothing about the book.
_JUNK_SUBJECTS = {
    "accessible book",
    "protected daisy",
    "in library",
    "overdrive",
    "large type books",
    "lending library",
    "popular print disabled books",
    "internet archive wishlist",
    "staff picks",
    "open library staff picks",
}
_JUNK_SUBJECT_RE = re.compile(r"^(nyt:|award:|collection:)")

# Sentence-length classification strings aren't badges; drop them.
_MAX_SUBJECT_LENGTH = 60


class MetadataError(RuntimeError):
    pass


def _request(method: str, url: str, **kwargs):
    """Shared polite HTTP client (see ol_http), raising MetadataError on failure.

    Kept as a module attribute so tests can patch ``adso.metadata._request``.
    """
    return _http_request(method, url, error_cls=MetadataError, **kwargs)


def _get_json(url: str, **kwargs) -> dict[str, Any] | None:
    response = _request("get", url, **kwargs)
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_description(value: Any) -> str | None:
    """Open Library descriptions come as a plain string or {"type", "value"}."""
    if isinstance(value, dict):
        value = value.get("value")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _clean_subjects(raw: Any, *, cap: int) -> list[str]:
    """Normalise an OL subject list: junk filter, dedupe, length cap."""
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        subject = " ".join(entry.split())
        key = subject.lower()
        if (
            not subject
            or len(subject) > _MAX_SUBJECT_LENGTH
            or key in _JUNK_SUBJECTS
            or _JUNK_SUBJECT_RE.match(key)
            or key in seen
        ):
            continue
        seen.add(key)
        cleaned.append(subject)
        if len(cleaned) >= cap:
            break
    return cleaned


def _edition_by_isbn(isbn: str) -> dict[str, Any] | None:
    return _get_json(OPENLIBRARY_EDITION_ISBN.format(isbn=isbn))


def _edition_by_olid(olid: str) -> dict[str, Any] | None:
    return _get_json(OPENLIBRARY_EDITION.format(olid=olid))


def _fetch_work(work_key: str) -> dict[str, Any] | None:
    if not isinstance(work_key, str) or not work_key.startswith("/works/"):
        return None
    return _get_json(OPENLIBRARY_WORK.format(work_key=work_key))


def _work_key_from_edition(edition: dict[str, Any]) -> str | None:
    works = edition.get("works") or []
    if works and isinstance(works[0], dict):
        return works[0].get("key")
    return None


def _search_doc(title: str, author: str) -> dict[str, Any] | None:
    """Find the best work match for a title (+author) via the Search API."""
    params: dict[str, Any] = {
        "title": title,
        "limit": 1,
        "fields": "key,cover_edition_key,edition_key",
    }
    if author:
        params["author"] = author
    payload = _get_json(OPENLIBRARY_SEARCH, params=params)
    if payload is None:
        return None
    docs = payload.get("docs") or []
    return docs[0] if docs and isinstance(docs[0], dict) else None


def _first_isbn(edition: dict[str, Any], field: str) -> str | None:
    values = edition.get(field) or []
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def resolve_metadata(book: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve work metadata (and possibly backfill ISBNs) for one book.

    Returns a dict with description/subjects/places/times, provenance, and any
    backfill ISBNs, or None when no Open Library work could be matched.
    """
    work = None
    source = source_url = None
    backfill_isbn10 = backfill_isbn13 = None

    # 1. Edition by ISBN -> work.
    for isbn in (book.get("isbn13"), book.get("isbn10")):
        if not isbn:
            continue
        edition = _edition_by_isbn(isbn)
        if edition is None:
            continue
        work_key = _work_key_from_edition(edition)
        work = _fetch_work(work_key) if work_key else None
        if work is not None:
            source = "openlibrary:isbn"
            source_url = OPENLIBRARY_EDITION_ISBN.format(isbn=isbn)
            break

    # 2. Search by title + author -> work (+ edition for ISBN backfill).
    if work is None:
        title = (book.get("title") or "").strip()
        author = (book.get("author") or "").strip()
        if not title:
            return None
        doc = _search_doc(title, author)
        if doc is None:
            return None
        work = _fetch_work(doc.get("key"))
        if work is None:
            return None
        source = "openlibrary:search"
        source_url = f"https://openlibrary.org{doc.get('key')}"

        # Backfill only books with no ISBN at all; Goodreads-supplied ISBNs are
        # never second-guessed.
        if not book.get("isbn13") and not book.get("isbn10"):
            olid = doc.get("cover_edition_key") or next(
                (k for k in (doc.get("edition_key") or []) if isinstance(k, str)), None
            )
            if olid:
                edition = _edition_by_olid(olid)
                if edition is not None:
                    backfill_isbn13 = _first_isbn(edition, "isbn_13")
                    backfill_isbn10 = _first_isbn(edition, "isbn_10")

    return {
        "description": _parse_description(work.get("description")),
        "subjects": _clean_subjects(work.get("subjects"), cap=SUBJECTS_CAP),
        "subject_places": _clean_subjects(work.get("subject_places"), cap=PLACES_CAP),
        "subject_times": _clean_subjects(work.get("subject_times"), cap=TIMES_CAP),
        "source": source,
        "source_url": source_url,
        "backfill_isbn10": backfill_isbn10,
        "backfill_isbn13": backfill_isbn13,
    }


def _should_skip(status: str | None, refresh: bool, retry_missing: bool) -> bool:
    if status == "manual":
        return True  # future-proofing: never clobber hand-set metadata
    if refresh:
        return False  # reconsider everything (except manual)
    if status == "fetched":
        return True
    if status == "not_found":
        return not retry_missing  # retry_missing re-attempts past misses
    return False  # None / error -> always process


def fetch_metadata(
    conn,
    *,
    limit: int | None = None,
    refresh: bool = False,
    retry_missing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch Open Library metadata for books that need it.

    Mirrors covers.fetch_covers: ``limit`` caps books *attempted*, statuses are
    fetched / not_found / error, and ``--retry-missing`` re-attempts past
    misses. A matched work with neither description nor any subjects counts as
    not_found so retry semantics stay meaningful. Returns summary stats
    including how many empty ISBNs were backfilled.
    """
    if limit is not None and limit < 1:
        raise MetadataError("limit must be at least 1.")

    fetched = not_found = errors = skipped = isbn_backfilled = 0
    actions: list[dict[str, str]] = []
    attempted = 0

    for row in db.iter_books(conn):
        book = dict(row)
        goodreads_id = book.get("goodreads_id")
        if not goodreads_id:
            skipped += 1
            continue
        if _should_skip(book.get("metadata_status"), refresh, retry_missing):
            skipped += 1
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1

        title = str(book.get("title") or "")
        try:
            resolved = resolve_metadata(book)
        except MetadataError:
            errors += 1
            actions.append({"goodreads_id": str(goodreads_id), "title": title, "result": "error"})
            if not dry_run:
                db.set_metadata(
                    conn,
                    int(book["id"]),
                    description=book.get("description"),
                    subjects=_loads_list(book.get("subjects_json")),
                    subject_places=_loads_list(book.get("subject_places_json")),
                    subject_times=_loads_list(book.get("subject_times_json")),
                    metadata_source=book.get("metadata_source"),
                    metadata_source_url=book.get("metadata_source_url"),
                    metadata_status="error",
                )
            time.sleep(RATE_LIMIT_DELAY)
            continue

        has_content = resolved is not None and (
            resolved["description"]
            or resolved["subjects"]
            or resolved["subject_places"]
            or resolved["subject_times"]
        )
        if not has_content:
            not_found += 1
            actions.append(
                {"goodreads_id": str(goodreads_id), "title": title, "result": "not_found"}
            )
            if not dry_run:
                db.set_metadata(
                    conn,
                    int(book["id"]),
                    description=None,
                    subjects=[],
                    subject_places=[],
                    subject_times=[],
                    metadata_source=None,
                    metadata_source_url=None,
                    metadata_status="not_found",
                )
            time.sleep(RATE_LIMIT_DELAY)
            continue

        action = {
            "goodreads_id": str(goodreads_id),
            "title": title,
            "result": "fetched",
            "source": str(resolved["source"]),
        }
        if not dry_run:
            db.set_metadata(
                conn,
                int(book["id"]),
                description=resolved["description"],
                subjects=resolved["subjects"],
                subject_places=resolved["subject_places"],
                subject_times=resolved["subject_times"],
                metadata_source=resolved["source"],
                metadata_source_url=resolved["source_url"],
                metadata_status="fetched",
            )
            if resolved["backfill_isbn13"] or resolved["backfill_isbn10"]:
                if db.backfill_isbns(
                    conn,
                    int(book["id"]),
                    isbn10=resolved["backfill_isbn10"],
                    isbn13=resolved["backfill_isbn13"],
                ):
                    isbn_backfilled += 1
                    action["isbn_backfilled"] = "yes"
        elif resolved["backfill_isbn13"] or resolved["backfill_isbn10"]:
            isbn_backfilled += 1
            action["isbn_backfilled"] = "yes"
        fetched += 1
        actions.append(action)
        time.sleep(RATE_LIMIT_DELAY)

    return {
        "fetched": fetched,
        "not_found": not_found,
        "errors": errors,
        "skipped": skipped,
        "isbn_backfilled": isbn_backfilled,
        "actions": actions,
    }


def _loads_list(raw: Any) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []
