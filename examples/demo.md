# Adso Demo Walkthrough

This walkthrough shows Adso with synthetic Goodreads-style data. It is safe for
public demos because it uses fictional books from `examples/goodreads_sample.csv`,
not a private Goodreads export.

Adso is local-first: SQLite is the catalogue of record on your machine.
Goodreads CSV files are imported snapshots, Notion is an optional export target,
and local physical-library fields such as ownership, location, shelf, loan, and
local notes stay under your control.

## Setup

Install Adso in editable mode from the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Use a demo database so you can run the walkthrough without touching your real
catalogue:

```bash
mkdir -p demo
export ADSO_DEMO_DB=demo/adso-demo.sqlite
adso --db "$ADSO_DEMO_DB" doctor
```

On a brand-new folder, `doctor` should report that the database file does not
exist yet and suggest `adso init` plus an import command.

## Initialize

```bash
adso --db "$ADSO_DEMO_DB" init
adso --db "$ADSO_DEMO_DB" doctor
```

Expected result: Adso says the database exists and is initialized, with `Books:
0`, no latest import, and no pending conflicts.

## Import Synthetic Goodreads Data

```bash
adso --db "$ADSO_DEMO_DB" import goodreads examples/goodreads_sample.csv
```

Expected result: the sync summary says Adso processed 3 Goodreads rows and added
3 new books. The sample includes one read book, one currently-reading book, and
one to-read book.

Run doctor again:

```bash
adso --db "$ADSO_DEMO_DB" doctor
```

Expected result: `Books: 3`, latest import details, `Pending conflicts: 0`, and
suggested commands such as `adso list`, `adso search "query"`, and
`adso report summary`.

## Inspect The Catalogue

List the imported rows:

```bash
adso --db "$ADSO_DEMO_DB" list
```

Expected result: a table with Goodreads ID, title, author, reading status, owned
state, and location. You should see:

- `100001` `The Clockwork Herbarium` with status `Read`
- `100002` `A Cartographer of Small Moons` with status `Currently Reading`
- `100003` `Practical Tea for Time Travelers` with status `To Read`

Search across Goodreads fields and local catalogue fields:

```bash
adso --db "$ADSO_DEMO_DB" search botanical
```

Expected result: one matching row for `The Clockwork Herbarium`. Search covers
title, author, ISBNs, shelves, review text, local notes, location, and shelf/box.

Show a single book:

```bash
adso --db "$ADSO_DEMO_DB" show 100001
```

Expected result: a detail view with two sections:

- `Goodreads Fields`, including title, author, ISBNs, shelves, rating, dates,
  review, and private notes from the synthetic CSV.
- `Local Catalogue Fields`, including owned state, copy count, location,
  shelf/box, loaned-to, and local notes.

## Add Local Library Details

Goodreads can say how many copies appeared in an export, but your local Adso
catalogue is where you track the physical copy you own and where it lives.

```bash
adso --db "$ADSO_DEMO_DB" edit 100001 \
  --owned true \
  --copy-count 1 \
  --location "Living Room" \
  --shelf-box "Shelf A" \
  --local-notes "Demo keeper copy"
```

Expected result: Adso confirms it updated local catalogue fields for Goodreads
ID `100001`.

Check the update:

```bash
adso --db "$ADSO_DEMO_DB" show 100001
adso --db "$ADSO_DEMO_DB" list --owned true --location "Living Room"
```

Expected result: the local section now shows `Owned: yes`, `Copy Count: 1`,
`Location: Living Room`, `Shelf/Box: Shelf A`, and your local notes. The filtered
list should show the same book.

## Sync The Same Sample Again

```bash
adso --db "$ADSO_DEMO_DB" sync goodreads examples/goodreads_sample.csv
```

Expected result: no conflicts and no catalogue changes are needed, because the
same synthetic Goodreads snapshot has already been imported. Local ownership and
shelf details remain preserved.

## Reports

```bash
adso --db "$ADSO_DEMO_DB" report summary
adso --db "$ADSO_DEMO_DB" report conflicts
```

Expected result: the summary describes the latest sync. The conflict report says
there are no pending conflicts for this demo run.

## Export

Create portable local backups:

```bash
adso --db "$ADSO_DEMO_DB" export csv --output demo/catalogue.csv
adso --db "$ADSO_DEMO_DB" export json --output demo/catalogue.json
```

Expected result: Adso writes CSV and JSON exports from the local SQLite
catalogue.

If Notion credentials are configured, you can also export to Notion:

```bash
adso --db "$ADSO_DEMO_DB" export notion
```

Notion remains an export surface. The SQLite catalogue is still canonical.

## Reset The Demo

Remove the demo database and exports when you are finished:

```bash
rm -rf demo/
```
