"""Optional Notion export adapter."""

from __future__ import annotations

import os
import time
from typing import Any

from . import db

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

NOTION_VERSION = "2022-06-28"
RATE_LIMIT_DELAY = 0.8


class NotionConfigError(RuntimeError):
    pass


def export_to_notion(
    conn,
    *,
    api_key: str | None = None,
    database_id: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    try:
        import requests  # noqa: F401
    except ModuleNotFoundError as exc:
        raise NotionConfigError("Install the requests dependency before using Notion export.") from exc

    if limit is not None and limit < 1:
        raise NotionConfigError("limit must be at least 1.")

    api_key = api_key or os.getenv("NOTION_API_KEY")
    database_id = database_id or os.getenv("NOTION_DB_ID")
    if not api_key or not database_id:
        raise NotionConfigError("NOTION_API_KEY and NOTION_DB_ID are required for Notion export.")

    existing = _load_existing_pages(api_key, database_id)
    created = updated = errors = 0
    actions: list[dict[str, str]] = []

    for index, row in enumerate(db.iter_books(conn)):
        if limit is not None and index >= limit:
            break
        book = db.row_to_catalogue_dict(row)
        properties = _build_properties(book)
        page_id = existing.get(book.get("goodreads_id"))
        action = "update" if page_id else "create"
        actions.append(
            {
                "action": action,
                "goodreads_id": str(book.get("goodreads_id") or ""),
                "title": str(book.get("title") or ""),
            }
        )
        if dry_run:
            if page_id:
                updated += 1
            else:
                created += 1
            continue
        if page_id:
            response = _request(
                api_key,
                "patch",
                f"https://api.notion.com/v1/pages/{page_id}",
                json={"properties": properties},
            )
            updated += 1 if response.status_code == 200 else 0
            errors += 0 if response.status_code == 200 else 1
        else:
            response = _request(
                api_key,
                "post",
                "https://api.notion.com/v1/pages",
                json={"parent": {"database_id": database_id}, "properties": properties},
            )
            created += 1 if response.status_code == 200 else 0
            errors += 0 if response.status_code == 200 else 1
        time.sleep(RATE_LIMIT_DELAY)

    return {"created": created, "updated": updated, "errors": errors, "actions": actions}


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(api_key: str, method: str, url: str, **kwargs):
    import requests

    for _ in range(7):
        kwargs.setdefault("timeout", 60)
        try:
            response = requests.request(method, url, headers=_headers(api_key), **kwargs)
        except Exception as exc:
            raise NotionConfigError(f"Could not reach the Notion API: {str(exc)[:500]}") from exc
        if response.status_code != 429:
            return response
        time.sleep(int(response.headers.get("Retry-After", 5)))
    return response


def _load_existing_pages(api_key: str, database_id: str) -> dict[str, str]:
    existing: dict[str, str] = {}
    payload: dict[str, Any] = {"page_size": 100}
    url = f"https://api.notion.com/v1/databases/{database_id}/query"

    while True:
        response = _request(api_key, "post", url, json=payload)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise NotionConfigError(f"Notion database lookup failed: {_response_error(response)}") from exc
        data = response.json()
        for page in data.get("results", []):
            rich = page.get("properties", {}).get("Goodreads ID", {}).get("rich_text", [])
            if rich:
                goodreads_id = rich[0].get("plain_text", "")
                if goodreads_id:
                    existing[goodreads_id] = page["id"]
        if not data.get("has_more"):
            return existing
        payload["start_cursor"] = data["next_cursor"]
        time.sleep(RATE_LIMIT_DELAY)


def _response_error(response) -> str:
    status = getattr(response, "status_code", "unknown status")
    body = str(getattr(response, "text", "") or "").strip()
    if body:
        return f"{status}: {body[:500]}"
    return str(status)


def _build_properties(book: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Title": {"title": [{"text": {"content": str(book.get("title", ""))[:2000]}}]},
        "Source": {"select": {"name": "Adso"}},
    }
    if book.get("author"):
        props["Author"] = {"rich_text": [{"text": {"content": str(book["author"])[:2000]}}]}
    if book.get("isbn13") or book.get("isbn10"):
        props["ISBN"] = {"rich_text": [{"text": {"content": book.get("isbn13") or book.get("isbn10")}}]}
    if book.get("goodreads_id"):
        props["Goodreads ID"] = {"rich_text": [{"text": {"content": str(book["goodreads_id"])}}]}
    if book.get("year_published"):
        props["Published Year"] = {"number": int(book["year_published"])}
    if book.get("reading_status"):
        props["Reading Status"] = {"select": {"name": str(book["reading_status"])}}
    if book.get("rating"):
        props["Rating"] = {"number": int(book["rating"])}
    if book.get("date_read"):
        props["Date Read"] = {"date": {"start": str(book["date_read"])}}
    if book.get("format"):
        props["Format"] = {"select": {"name": str(book["format"]).capitalize()}}
    if book.get("tags"):
        props["Tags"] = {"multi_select": [{"name": str(tag)} for tag in book["tags"]]}
    return props
