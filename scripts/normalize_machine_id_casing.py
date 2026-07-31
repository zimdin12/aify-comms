#!/usr/bin/env python3
"""One-time normalization of existing machine_id casing in the aify-comms DB.

WHY
---
The host machine_id is "<platform>:<hostname>" (e.g. "win32:DevBox-1").
Different launch paths reported the hostname with different casing
("win32:DevBox-1" vs "win32:DEVBOX-1"). The service compared machine_id
CASE-SENSITIVELY in bridge supersession and dispatch-claim routing, so a
re-registered worker under a different casing did NOT supersede its prior
bridge -> duplicate live bridge_instances per agent and broken managed
delivery. The code fix normalizes machine_id to lowercase at every
store/compare site going forward; this script lowercases the rows that
were written BEFORE the fix so old and new values reconcile.

WHAT
----
Lowercases (via SQL `lower()`) every *machine_id column in the schema:

  agents.machine_id
  bridge_instances.machine_id
  environments.machine_id
  environment_controls.machine_id
  dispatch_runs.claim_machine_id
  dispatch_controls.claim_machine_id
  spawn_requests.claim_machine_id

The platform segment is already lowercase, so lowercasing the whole value
only changes the hostname segment. The operation is idempotent: running it
again is a no-op because already-lowercased values equal their lower().

USAGE (run inside the aify-comms-service container; the DB lives at
/data/aify.db). This script ONLY lowercases existing rows; it does not
alter schema. Take a backup first if you want a rollback point.

    python scripts/normalize_machine_id_casing.py                # /data/aify.db
    python scripts/normalize_machine_id_casing.py /path/to/aify.db
    python scripts/normalize_machine_id_casing.py --dry-run      # report only

DO NOT run this casually; coordinate with a service quiesce so no bridge is
mid-registration. It is safe to re-run.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

DEFAULT_DB_PATH = "/data/aify.db"

# (table, column) for every machine-id-bearing column in the schema.
TARGETS = [
    ("agents", "machine_id"),
    ("bridge_instances", "machine_id"),
    ("environments", "machine_id"),
    ("environment_controls", "machine_id"),
    ("dispatch_runs", "claim_machine_id"),
    ("dispatch_controls", "claim_machine_id"),
    ("spawn_requests", "claim_machine_id"),
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def normalize(db_path: str, dry_run: bool = False) -> int:
    conn = sqlite3.connect(db_path)
    try:
        total_changed = 0
        for table, column in TARGETS:
            if not _table_exists(conn, table):
                print(f"skip {table}.{column}: table not present")
                continue
            # Count rows whose value differs from its lowercase form.
            pending = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} <> lower({column})"
            ).fetchone()[0]
            if pending == 0:
                print(f"ok   {table}.{column}: already normalized (0 rows)")
                continue
            if dry_run:
                print(f"WOULD update {table}.{column}: {pending} row(s)")
                total_changed += pending
                continue
            cur = conn.execute(
                f"UPDATE {table} SET {column} = lower({column}) "
                f"WHERE {column} IS NOT NULL AND {column} <> lower({column})"
            )
            print(f"updated {table}.{column}: {cur.rowcount} row(s)")
            total_changed += cur.rowcount
        if not dry_run:
            conn.commit()
        return total_changed
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path",
        nargs="?",
        default=DEFAULT_DB_PATH,
        help=f"path to the SQLite DB (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing",
    )
    args = parser.parse_args(argv)

    print(f"machine_id casing normalization on {args.db_path}"
          f"{' (dry-run)' if args.dry_run else ''}")
    changed = normalize(args.db_path, dry_run=args.dry_run)
    verb = "would change" if args.dry_run else "changed"
    print(f"done: {changed} row(s) {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
