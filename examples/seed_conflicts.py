"""Seed a throwaway Adso database with sync conflicts, for trying the UI.

Conflicts only occur when a *local* change collides with an *incoming* Goodreads
change on the same field. This script manufactures that collision against the
synthetic sample data, using the real import/sync engine — so what you see is
exactly what a real Goodreads re-sync would produce.

Usage:
    python examples/seed_conflicts.py [DB_PATH]   # default: /tmp/adso-demo.sqlite

Then point the web UI (or CLI) at the same database:
    adso --db /tmp/adso-demo.sqlite serve
    adso --db /tmp/adso-demo.sqlite conflicts
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from adso import db, sync

SAMPLE = Path(__file__).resolve().parent / "goodreads_sample.csv"
BOOK_ID = "100001"


def main(db_path: str) -> int:
    if os.path.exists(db_path):
        os.remove(db_path)

    # 1. Import the synthetic sample as the starting catalogue.
    conn = db.connect(db_path)
    sync.import_goodreads_csv(conn, SAMPLE, mode="import")

    # 2. Simulate the user editing some Goodreads fields locally since last sync.
    conn.execute(
        "UPDATE books SET reading_status='Currently Reading', "
        "exclusive_shelf='currently-reading', rating=3 WHERE goodreads_id=?",
        (BOOK_ID,),
    )
    conn.commit()
    conn.close()

    # 3. Build a Goodreads export where the SAME book changed differently upstream.
    with open(SAMPLE, newline="") as f:
        rows = list(csv.DictReader(f))
        fields = rows[0].keys()
    for r in rows:
        if r["Book Id"] == BOOK_ID:
            r["Exclusive Shelf"] = "to-read"
            r["My Rating"] = "4"
    changed = Path(db_path).with_suffix(".changed.csv")
    with open(changed, "w", newline="") as f:  # closed (flushed) before sync reads it
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    # 4. Sync the changed export -> collisions become conflicts.
    conn = db.connect(db_path)
    summary = sync.import_goodreads_csv(conn, changed, mode="sync")
    pending = db.pending_conflict_count(conn)
    conn.close()

    print(f"Seeded {db_path} with {summary.conflicts} conflict(s) ({pending} pending).")
    print("Try them out:")
    print(f"    adso --db {db_path} serve        # then open the Conflicts tab")
    print(f"    adso --db {db_path} conflicts     # or resolve from the terminal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/adso-demo.sqlite"))
