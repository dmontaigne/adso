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

## Core Ideas

- SQLite is canonical.
- Goodreads CSV exports are preserved raw and normalized into the catalogue.
- Local physical-library fields are protected during sync.
- Goodreads activity fields can update safely when the local value has not changed since the previous source snapshot.
- Conflicts are reported instead of silently overwriting local data.
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
adso report conflicts --output reports/conflicts.md
adso report summary --output reports/summary.md
adso export csv --output exports/catalogue.csv
adso export json --output exports/catalogue.json
adso export notion --dry-run --limit 5
adso export notion --limit 1
```

`adso import goodreads` and `adso sync goodreads` both preserve raw import rows. `sync` additionally writes a conflict report when a Goodreads update would overwrite a local change.

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
