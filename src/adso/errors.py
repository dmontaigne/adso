"""User-facing error types for Adso.

`AdsoError` represents an expected, operational failure (bad input, missing
file, unreachable service) that should be reported to the user as a clean
message — never a traceback. The CLI's error boundary catches it, prints
``Error: <message>`` (plus an optional ``Next: <hint>``) to stderr, and exits
with status 1. Argument-shape problems stay with argparse (exit 2); these are
runtime/data problems.
"""

from __future__ import annotations


class AdsoError(Exception):
    """An expected failure with a humane message and an optional next-step hint."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class GoodreadsImportError(AdsoError):
    """Raised when a Goodreads CSV can't be read or parsed."""
