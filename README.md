# Adso

> **Goodreads is where your friends are. Adso is where your library lives.**

**Adso is a home for your library** — a sovereign, local-first copy you own, fed from Goodreads, that outlives any single app or service. Stay on Goodreads for the network; keep an easily-synced copy in Adso that is yours, does more than Goodreads' roadmap offers, and happens to also be a backup.

Adso is a local-first Goodreads backup and personal library catalogue. It treats Goodreads, Notion, and future services as sync surfaces or inbound feeds, while your own local catalogue remains the source of truth.

The first version is CLI-first and SQLite-backed so the core can later power a local web app, desktop app, add-ons, or agent workflows without rewriting the sync model.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
adso init
adso import goodreads goodreads_library_export.csv
adso sync goodreads goodreads_library_export.csv
adso report summary
adso report conflicts
adso export json --output exports/catalogue.json
adso export csv --output exports/catalogue.csv
```

By default Adso uses `adso.sqlite` in the current directory. Pass `--db path/to/adso.sqlite` before the command to use another database.

To try Adso without using a private Goodreads export, import the synthetic sample data:

```bash
adso import goodreads examples/goodreads_sample.csv
adso list
adso show 100001
```

For a fuller public-safe walkthrough, see [examples/demo.md](examples/demo.md).

## Installation

The core CLI has **no required runtime dependencies** — it installs with nothing
beyond the standard library and runs on **Python 3.9+**. Optional features add
dependencies via extras (these pull modern FastAPI/requests and need
**Python 3.10+**):

```bash
pip install -e .              # core CLI only
pip install -e ".[web]"       # + local web UI (adso serve)
pip install -e ".[notion]"    # + Notion export adapter
pip install -e ".[web,notion]"
```

### Reproducible install

For a known-good, fully pinned environment covering all extras (Python 3.10+),
install from the committed lockfile before installing Adso itself:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-lock.txt
pip install -e ".[web,notion]"
```

The pinned versions in [requirements-lock.txt](requirements-lock.txt) are what the
project is tested against. `pyproject.toml` keeps looser version ranges for the
extras so unpinned installs still resolve on a fresh machine.

## Core Ideas

- SQLite is canonical.
- Goodreads CSV exports are preserved raw and normalized into the catalogue.
- Local physical-library fields are protected during sync.
- Goodreads activity fields can update safely when the local value has not changed since the previous source snapshot.
- Conflicts are reported instead of silently overwriting local data.
- Cosmetic Goodreads drift is treated as informational — community average rating, publisher/edition relabels, ISBNs, page counts, edition year, additional authors, and title case/whitespace/edition-tag differences are refreshed for display but never count as a change or conflict. Title text/subtitle and author changes stay tracked, so genuine data issues still surface.
- Notion is optional and reads from SQLite.

## Current Commands

```bash
adso init
adso doctor
adso import goodreads path/to/export.csv
adso sync goodreads path/to/export.csv
adso list --owned true --location Office
adso search "winter society" --owned true --limit 10
adso show GOODREADS_ID
adso edit GOODREADS_ID --owned true --copy-count 1 --location Office --shelf-box A1
adso fetch-covers
adso set-cover GOODREADS_ID --url https://example.com/cover.jpg
adso conflicts
adso resolve CONFLICT_ID --accept-incoming
adso report conflicts --output reports/conflicts.md
adso report summary --output reports/summary.md
adso export csv --output exports/catalogue.csv
adso export json --output exports/catalogue.json
adso export notion --dry-run --limit 5
adso export notion --limit 1
```

`adso import goodreads` and `adso sync goodreads` both preserve raw import rows. `sync` additionally writes a conflict report when a Goodreads update would overwrite a local change.

## Configuration profiles

By default Adso uses `adso.sqlite` in the current directory and reads Notion credentials from the environment. *Profiles* let you bundle a database path with a Notion target and switch them as a unit — most usefully to keep a throwaway **sandbox** Notion database separate from your real **production** one, so a test run can't write to the wrong place.

```bash
adso config init                  # write a starter config you can edit
adso config set sandbox notion-database-id <test-db-id> --local
adso config use sandbox           # make it the default profile
adso config list                  # see profiles and which is active
adso config show                  # resolved settings (API key masked)
adso --profile production export notion   # use a specific profile for one command
```

Settings resolve with the precedence **CLI flag → environment variable → active profile → built-in default**, so environment variables stay authoritative for automation (CI, scripts). Config is read from `./adso.ini` first (use `--local` to write there) and then `~/.config/adso/config.ini`, so a portable library folder can carry its own settings. Keep your `NOTION_API_KEY` in the environment rather than in the file. `adso doctor` shows the active profile and which config files are in effect.

## Web UI (experimental preview)

Adso v1 is **CLI-first** — the commands above are the supported, stable surface. An
optional local web UI exists over the same SQLite catalogue, but it is an early
preview and not part of the v1 surface. If you're curious, it installs via an extra:

```bash
pip install -e ".[web]"
adso serve            # opens http://127.0.0.1:8000 — preview, expect rough edges
```

It reuses the same query, import, and conflict-resolution services as the CLI, so it
reads and writes exactly the same database. It will get a proper write-up in a future
v2 release.

## Book covers

Adso can fetch cover art for your catalogue and store the images locally, beside your database in a `covers/` directory. Covers are enrichment — they are never sourced from Goodreads and never participate in conflict resolution — and they are not committed to the repo, so each catalogue builds its own.

Install the optional dependency and fetch covers:

```bash
pip install -e ".[covers]"
adso fetch-covers                 # fetch covers for books that don't have one yet
adso fetch-covers --limit 10 --dry-run   # preview without writing files
adso fetch-covers --retry-missing # re-attempt books previously not found
```

For each book, Adso resolves a cover in order and keeps the first hit:

1. **Open Library** cover by ISBN-13 / ISBN-10
2. **Open Library Search** by title + author
3. **Apple Books (iTunes Search)** by title + author

All three are free public APIs that need no account or key. Adso is polite to them (spaced requests, backed-off retries) and remembers results, so re-running only fills gaps. Books with no cover from any source show a generated placeholder tile (the title's initials) in the web UI.

Set a cover by hand for any book — automatic fetches never overwrite a manual cover:

```bash
adso set-cover GOODREADS_ID --url https://example.com/cover.jpg
adso set-cover GOODREADS_ID --file path/to/cover.jpg
```

Covers are also fetched automatically after `adso import`/`sync` goodreads; pass `--no-covers` to skip that. The downloaded images are stored locally beside your database for personal catalogue use.

## Notion

Install the optional Notion dependencies:

```bash
pip install -e ".[notion]"
```

Set these environment variables before using Notion export:

```bash
export NOTION_API_KEY=secret_...
export NOTION_DB_ID=...
```

Notion export is intentionally an adapter: the local SQLite catalogue remains canonical.

### Live-Test Checklist

Before running against a real Notion database, confirm the database has these
properties:

- `Title` as title
- `Goodreads ID`, `Author`, `ISBN`, `Location`, and `Shelf / Box` as text
- `Source` and `Reading Status` as select
- `Published Year` and `Rating` as number
- `Date Read` as date
- `Owned` as checkbox

Run the first live test in this order:

1. Preview planned writes without creating or updating pages:

   ```bash
   adso export notion --dry-run --limit 5
   ```

2. If the dry-run shows the expected create/update actions, write one page:

   ```bash
   adso export notion --limit 1
   ```

3. Inspect that Notion page, then run a larger limited batch:

   ```bash
   adso export notion --limit 5
   ```

4. Run the full export only after the limited batch looks right:

   ```bash
   adso export notion
   ```

Expected success signals:

- Dry-run output says which pages would be created or updated.
- The summary reports create, update, and error counts.
- Limited exports only affect the requested number of books.
- Re-running export updates existing Notion pages by `Goodreads ID` instead of creating duplicates.

Troubleshooting:

- Missing credentials: set `NOTION_API_KEY` and `NOTION_DB_ID` in the shell where
  you run Adso.
- Missing optional dependency: install with `pip install -e ".[notion]"`.
- Missing Notion properties: add the property named in the Notion API error, then
  retry with `--limit 1`.
- API rate limits: Adso backs off for Notion `429` responses. If a large export is
  still noisy, retry with a smaller `--limit` first.

## Development

Install Adso in editable mode with the optional extras you want to work on:

```bash
pip install -e ".[web,notion,covers,dev]"
```

Run the test suite (stdlib `unittest`, no extra runner needed):

```bash
python -m unittest discover -s tests
```

Lint with [ruff](https://docs.astral.sh/ruff/):

```bash
ruff check .
```

Continuous integration (`.github/workflows/ci.yml`) runs the tests across Python 3.9–3.13, checks that the package installs from a clean checkout, and runs ruff on every push and pull request.

## License

Adso is released under the [MIT License](LICENSE). Copyright (c) 2026 David Whipps.
