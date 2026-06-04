# Adso Project Plan

> **Current direction:** The live product thesis and release sequencing are tracked in the Linear doc *Adso Direction: Sovereign Library + v1→v2→v3 Ladder* (`https://linear.app/davidwhippscom/document/adso-direction-sovereign-library-v1v2v3-ladder-56aa9223e1d7`). This file remains accurate for the core domain model, sync engine, and test plan; read the Linear doc for the latest framing.

## Summary

Adso is a **sovereign, self-hosted, no-login home for your library**. It starts as a local-first Goodreads backup and physical library catalogue, then grows into Adso's own interface (a light local web UI) and, optionally, a hosted product later.

The core principle is: **the user's personal catalogue is always the source of truth**. Goodreads is an inbound feed; Notion, CSV, and JSON are optional outbound surfaces (Notion is one output among many, not a destination); Adso's own interface is where you live. Your local copy is always downloadable. This gives users control, reduces lock-in, and keeps the catalogue portable.

## Product Direction

- Use **Adso** as the working project, package, docs, and Linear project name.
- Position Adso as a sovereign home for your library — control, portability, and intelligent sync — not "another Goodreads clone" and not a Notion front-end.
- Treat Goodreads as valuable because of its network effect, but only as an inbound feed, never the canonical home for a user's library data.
- Design for future interoperability with sources such as StoryGraph, LibraryThing, Open Library, and CSV exports.
- Treat Notion as one optional outbound surface among many (CSV, JSON, Notion); avoid making users depend on it.

## Architecture

- Build a reusable **core domain and sync engine** first.
- Keep interfaces as adapters around the core:
  - CLI adapter for v1.
  - Goodreads CSV import adapter.
  - Notion export/sync adapter.
  - Future local web app adapter.
  - Future app/add-on/agent adapters.
- Use SQLite as the canonical local database.
- Store every Goodreads CSV row raw for backup/history, plus normalized records for querying and sync.
- Make the core usable without Notion, without a web app, and without Goodreads after initial import.

## Sync Model

- Model Goodreads as an **inbound activity feed**, not as the source of truth.
- Preserve local catalogue fields across every sync.
- Physical-library fields are local-only in v1:
  - owned status
  - copy count
  - room/location
  - shelf/box
  - loaned-to
  - local notes
- Goodreads-originated activity fields can be updated from fresh exports when safe:
  - reading status
  - shelves
  - rating
  - date read
  - date added
  - review text
  - read count
- Safe auto-update rule:
  - If a Goodreads-owned/activity field has not been locally edited since the last import, accept the new Goodreads value.
  - If the local catalogue changed that field independently, create a conflict instead of overwriting.
- Always preserve the full source history so future connectors can compare against prior imports.
- Generate human-readable conflict reports in v1; interactive conflict resolution can come later.
- Treat volatile Goodreads-side fields as **informational**: community average rating, publisher, ISBNs, page count, edition year, additional authors, and title case/whitespace/edition-tag differences are refreshed for display but never counted as a change or conflict, so cosmetic export drift doesn't churn syncs. Title text/subtitle and author changes remain tracked as possible data issues. (Implemented 2026-06-04; see `INFORMATIONAL_FIELDS` / `_comparison_key` in `sync.py`.)

## CLI And App Compatibility

- Start with a Python package and CLI, but design commands as thin wrappers over core services.
- Initial CLI shape:
  - `adso init`
  - `adso import goodreads path/to/export.csv`
  - `adso sync goodreads path/to/export.csv`
  - `adso report conflicts`
  - `adso export notion`
  - `adso export csv`
  - `adso export json`
- Avoid baking CLI assumptions into the database or sync engine.
- Future web app should use the same core model and sync engine, either directly or through a local HTTP/API layer.
- Future app add-ons should interact through stable catalogue, import, sync, and report interfaces.

## Implementation Milestones

The foundation milestones (1–6) are effectively complete. Remaining work is sequenced as a v1→v2→v3 release ladder.

1. **Project Foundation**: package structure, `pyproject.toml`, CLI entrypoint, tests, `.gitignore`, README, and this plan.
2. **Local Catalogue Core**: SQLite schema, import-run tracking, raw Goodreads row preservation, normalized book records, and idempotent import.
3. **Safe Sync Engine**: field-level source tracking, local edit detection, Goodreads activity feed comparison, safe auto-updates, and conflict report generation.
4. **Physical Library Management**: local fields for ownership, copy count, room/location, shelf/box, loaned-to, and notes.
5. **Notion Adapter**: export from SQLite instead of directly from Goodreads CSV.
6. **Agentic Sync Assistant**: summarize sync runs, explain conflicts, flag suspicious changes, and recommend safe resolutions.

### Release Ladder

- **v1 — Hardened CLI (technical preview).** Near done; gated by the Release Hardening milestone. "Publish a great tool."
- **v2 — Local Web UI.** Adso's own interface over the canonical SQLite core: visual conflict resolution (the crown jewel, since conflicts are inherently visual), an activity view, and catalogue browse/search/edit, plus exports and a single public demo instance seeded with synthetic data. Interactive conflict resolution is built here, not as a separate CLI phase.
  - **Status (2026-06-04):** Shipped a FastAPI + Jinja/HTMX/Alpine + Tailwind/Basecoat web UI (`adso serve`, `src/adso/web/`) reusing the existing services in-process. Done: app/API boundary, catalogue browse/search/detail, **visual conflict resolution** (three-way per-field with keep-local / accept-Goodreads / custom + per-book bulk, `adso conflicts`/`adso resolve` for parity, resolution decisions + audit columns on `sync_conflicts`), **activity view** over `import_runs`, and **web Goodreads import** (upload → run → summary). Remaining: CSV/JSON export downloads, inline catalogue editing, and the public demo instance.
- **v3 — Optional hosted multi-tenant.** A deliberate go/no-go *after* v2, anchored on "your local copy is always downloadable." This is "start a product," a different commitment — do not build v3 plumbing speculatively.

## Linear Project Plan

Work is tracked in the Linear project **Adso** (team Whipps Build Team, key `DAV`; issues are `DAV-NN`). The foundation ticket groups below are complete; current backlog and milestone sequencing live in Linear, and the v1→v2→v3 framing is captured in the *Adso Direction* doc linked at the top of this file.

Initial ticket groups (historical, all done):

- **Foundation**
  - Create Python package structure and CLI entrypoint.
  - Add project README, `.gitignore`, dependency management, and test setup.
  - Write `plan.md` with product, architecture, sync, and roadmap notes.
- **SQLite Catalogue**
  - Design initial SQLite schema.
  - Store raw Goodreads import rows.
  - Normalize Goodreads records into local catalogue entries.
  - Make repeated imports idempotent.
- **Sync Engine**
  - Track import runs and source snapshots.
  - Implement Goodreads ID matching.
  - Add field-level local edit tracking.
  - Implement safe auto-update rules.
  - Generate conflict reports.
- **Physical Library Fields**
  - Add ownership and copy-count fields.
  - Add room/location and shelf/box fields.
  - Add loaned-to and local notes fields.
  - Add CLI commands for editing local catalogue fields.
- **Notion Adapter**
  - Extract existing Notion upsert logic.
  - Export from SQLite to Notion.
  - Preserve Notion as optional, not canonical.
- **Agent/Reports**
  - Generate sync summaries.
  - Explain conflicts in plain language.
  - Add recommendations for safe resolutions.

## Test Plan

- Import a Goodreads CSV into a fresh SQLite database.
- Re-import the same CSV and verify no duplicates.
- Sync a modified Goodreads export and verify safe activity updates are applied.
- Edit local catalogue fields, then sync Goodreads and verify local data is preserved.
- Change the same field locally and in Goodreads, then verify a conflict report is produced.
- Export to Notion from SQLite using mocked Notion responses.
- Export full local catalogue to CSV/JSON and verify portability.

## Assumptions

- Adso is a working name and can change later.
- Goodreads Book ID is the primary matching key for v1.
- SQLite is the canonical local store.
- Goodreads and Notion are connectors, not ownership layers.
- The first implementation is CLI-first, but app and web compatibility are architectural requirements from the start.
