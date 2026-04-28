# Adso Project Plan

## Summary

Adso is a smarter syncing tool for managing both online and offline book catalogues. It starts as a local-first Goodreads backup and physical library catalogue, then grows into a broader personal library layer that can support a web app, app add-ons, agentic recommendations, and friend-to-friend or agent-to-agent catalogue features.

The core principle is: **the user's personal catalogue is always the source of truth**. Goodreads, Notion, and future services are external feeds or sync surfaces, not ownership points. This gives users more control, reduces lock-in, and keeps the catalogue portable if they later move away from Goodreads or Notion.

## Product Direction

- Use **Adso** as the working project, package, docs, and Linear project name.
- Position Adso around control, portability, and intelligent sync rather than "another Goodreads clone."
- Treat Goodreads as valuable because of its network effect, but not as the canonical home for a user's library data.
- Design for future interoperability with sources such as StoryGraph, LibraryThing, Open Library, CSV exports, Notion, and a first-party web app.
- Keep Notion useful in phase one, but avoid making users depend on Notion long-term.

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

1. **Project Foundation**: package structure, `pyproject.toml`, CLI entrypoint, tests, `.gitignore`, README, and this plan.
2. **Local Catalogue Core**: SQLite schema, import-run tracking, raw Goodreads row preservation, normalized book records, and idempotent import.
3. **Safe Sync Engine**: field-level source tracking, local edit detection, Goodreads activity feed comparison, safe auto-updates, and conflict report generation.
4. **Physical Library Management**: local fields for ownership, copy count, room/location, shelf/box, loaned-to, and notes.
5. **Notion Adapter**: export/sync from SQLite instead of directly from Goodreads CSV.
6. **Agentic Sync Assistant**: summarize sync runs, explain conflicts, flag suspicious changes, and recommend safe resolutions.
7. **Future Web/App Layer**: add a local web app or app add-on interface after the core sync behavior is reliable.

## Linear Project Plan

Create a Linear project named **Adso** once the target Linear team is confirmed.

Initial ticket groups:

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
- Linear project creation will happen after confirming the target Linear team.
