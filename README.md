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
adso import goodreads path/to/export.csv
adso sync goodreads path/to/export.csv
adso edit GOODREADS_ID --owned true --copy-count 1 --location Office --shelf-box A1
adso report conflicts --output reports/conflicts.md
adso report summary --output reports/summary.md
adso export csv --output exports/catalogue.csv
adso export json --output exports/catalogue.json
adso export notion
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
