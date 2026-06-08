# Changelog

All notable changes to Adso are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Conflict decisions now support **ignore** and **review-later** alongside
  keep-local / accept-incoming / custom, and any decision can be **reopened**.
- Every decision is recorded in an append-only audit trail with provenance
  (which interface decided), surfaced in `adso conflicts --all`, the conflict
  report, and the web UI's "Recently decided" section.
- `adso resolve` gains `--ignore`, `--review-later`, and `--reopen`.

## [0.1.0] - 2026-06-05

First tagged release — a self-hosted technical preview. Clone it, install it, and
run it against your own Goodreads export.

### Added
- Local-first CLI (`adso`) backed by a canonical SQLite catalogue.
- Goodreads CSV import and sync, preserving raw import rows and protecting local
  physical-library fields, with conflict reporting instead of silent overwrites.
- Catalogue commands: `init`, `doctor`, `import`, `sync`, `list`, `search`,
  `show`, `edit`, `conflicts`, `resolve`, `report`, and `export`.
- Exports to CSV and JSON; optional Notion export adapter (`adso export notion`,
  `[notion]` extra) that reads from SQLite as the source of truth.
- Optional local web UI (`adso serve`, `[web]` extra) over the same catalogue,
  including visual conflict resolution, browser import, and an activity view.
- Packaging for self-hosted installs: MIT `LICENSE`, license metadata and a
  PEP 517 build backend in `pyproject.toml`, and a pinned `requirements-lock.txt`
  for reproducible installs.

[Unreleased]: https://github.com/dmontaigne/adso/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dmontaigne/adso/releases/tag/v0.1.0
