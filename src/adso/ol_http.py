"""Polite HTTP client shared by the enrichment fetchers (covers, metadata).

Open Library asks clients to identify themselves with a descriptive User-Agent;
the bounded timeouts and bounded 429 backoff exist because a scalar timeout plus
an unbounded retry loop once let an overnight fetch hang. Every enrichment
fetcher should go through :func:`request` so politeness stays in one place.
"""

from __future__ import annotations

import time

# A descriptive User-Agent is requested by Open Library so they can identify
# polite clients; see https://openlibrary.org/dev/docs/api/covers.
USER_AGENT = "Adso/0.1 (local-first book catalogue; +https://github.com/davidwhipps/adso)"
RATE_LIMIT_DELAY = 0.75

# (connect, read) timeouts: a stalled connection can never hang the whole run.
HTTP_TIMEOUT = (10, 30)


class EnrichmentHTTPError(RuntimeError):
    """A network-level failure reaching an enrichment source."""


def require_requests(error_cls: type[Exception] = EnrichmentHTTPError):
    try:
        import requests
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via extras
        raise error_cls(
            "Install the requests dependency before fetching enrichment data:\n"
            "    pip install -e '.[covers]'"
        ) from exc
    return requests


def request(method: str, url: str, *, error_cls: type[Exception] = EnrichmentHTTPError, **kwargs):
    """HTTP wrapper with bounded timeouts and bounded 429 backoff.

    Both the connect/read timeout and the 429 retry count are capped so that a
    slow or throttling host can never stall a sequential fetch. Network errors
    are raised as ``error_cls`` so each fetcher surfaces its own error type.
    """
    requests = require_requests(error_cls)
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    response = None
    for _ in range(3):
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
        except Exception as exc:  # noqa: BLE001 - network errors become a miss/error upstream
            raise error_cls(f"Could not reach {url}: {str(exc)[:300]}") from exc
        if response.status_code != 429:
            return response
        time.sleep(min(int(response.headers.get("Retry-After", 2) or 2), 5))
    return response  # still 429 after retries -> treated as a miss upstream
