# Adso

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

## Web UI (v2, preview)

Adso ships a local web interface over the same canonical SQLite catalogue. Install the web extra and start the server:

```bash
pip install -e ".[web]"
adso serve            # opens http://127.0.0.1:8000
```

Options: `adso serve --host 0.0.0.0 --port 8080 --no-browser`. The `--db` flag applies as usual (`adso --db path/to/adso.sqlite serve`). The web layer reuses the same query and conflict services as the CLI, so it reads and writes exactly the same database. A JSON API lives at `/api/books` and `/api/conflicts` with interactive docs at `/api/docs`.

The web UI's centrepiece is **visual conflict resolution** at `/conflicts`: each conflicting field is shown with your preserved local value and the incoming Goodreads value side by side (plus the previously-synced value for context). Resolve each field by keeping the local value, using the Goodreads value, or entering a custom value — individually or per book. The same operations are available from the terminal with `adso conflicts` and `adso resolve`.

To try conflict resolution on throwaway data, seed a demo database with sample conflicts:

```bash
python examples/seed_conflicts.py        # writes /tmp/adso-demo.sqlite
adso --db /tmp/adso-demo.sqlite serve
```

You can also **import a Goodreads export** straight from the browser at `/import` — upload the CSV and Adso runs it through the same engine as `adso import`/`sync`, showing a summary (new / updated / unchanged / conflicts) with links to review. Imports run against the database the server was started with, and your local fields are protected exactly as on the CLI.

The **activity view** at `/activity` lists every import and sync against your catalogue, newest first, with the row count and the created / updated / unchanged / conflict breakdown for each run.

### Rebuilding styles (contributors only)

The stylesheet at `src/adso/web/static/app.css` is prebuilt and committed, so **running** the app never needs Node.js. To change the styling, rebuild it with Tailwind + Basecoat:

```bash
npm install
npm run build:css     # one-off build; or `npm run watch:css` while iterating
```

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

## License

Adso is released under the [MIT License](LICENSE). Copyright (c) 2026 David Whipps.
