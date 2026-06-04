"""Local web interface for Adso (v2).

A thin FastAPI layer over the canonical SQLite core. Web handlers reuse the
same query/sync services as the CLI (``adso.catalogue`` / ``adso.sync``) so
there is a single source of truth for catalogue logic.
"""

from .app import create_app

__all__ = ["create_app"]
