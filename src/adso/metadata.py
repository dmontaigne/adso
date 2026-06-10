"""Parsing helpers for Open Library work metadata.

Open Library work records carry a ``description`` field that is either a bare
string or a ``{"type": "/type/text", "value": ...}`` object. Beyond that
shape difference, the text itself is community-edited and frequently pasted
from retail pages, so a description can open with a short source-attribution
header such as ``Amazon.com Review`` or ``From Publishers Weekly`` before the
actual blurb (observed live on "A Distant Mirror", goodreads_id 568236).

``_parse_description`` normalises both shapes and strips one leading
attribution header using a conservative heuristic: the first line must be
short (under ~40 characters), free of sentence punctuation, and followed by
real content (a blank line or a paragraph). Anything that looks like prose —
a single-paragraph description, a first line that ends a sentence, a long
first line — is left untouched. Stored descriptions are not migrated; they
can be re-fetched with ``adso fetch-metadata --refresh``.
"""

from __future__ import annotations

import re
from typing import Any

# Attribution headers are title-like labels ("Amazon.com Review", "From
# Publishers Weekly"), not prose, so anything longer than a short phrase is
# assumed to be the description itself.
_MAX_ATTRIBUTION_LEN = 40

# A full stop, question mark, or exclamation mark followed by whitespace marks
# a sentence boundary. A bare dot followed by a letter ("Amazon.com") does
# not, which is why this is not a simple "contains a period" check.
_SENTENCE_BREAK = re.compile(r"[.!?]\s")

# Punctuation that ends a sentence (or reads like prose) when it ends the
# line. A title-like attribution header ends in a bare word.
_TRAILING_PUNCTUATION = ".!?,;:"


def _looks_like_attribution(line: str) -> bool:
    """Whether ``line`` reads as a source-attribution header, not prose."""
    if not line or len(line) > _MAX_ATTRIBUTION_LEN:
        return False
    if line[-1] in _TRAILING_PUNCTUATION:
        return False
    if _SENTENCE_BREAK.search(line):
        return False
    return True


def _strip_attribution_header(text: str) -> str:
    """Drop a leading attribution line, keeping the description intact.

    Only the first line is ever considered, and only when something follows
    it — a header with no blurb after it is kept verbatim rather than
    reducing the description to nothing.
    """
    first, newline, rest = text.partition("\n")
    rest = rest.strip()
    if newline and rest and _looks_like_attribution(first.strip()):
        return rest
    return text


def _parse_description(raw: Any) -> str | None:
    """Normalise an Open Library work description to plain text.

    Accepts the bare-string and ``/type/text`` object shapes, trims
    whitespace, and strips a leading source-attribution header. Returns
    ``None`` when there is no usable text.
    """
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return None
    text = raw.replace("\r\n", "\n").strip()
    if not text:
        return None
    return _strip_attribution_header(text)
