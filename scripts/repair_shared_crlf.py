#!/usr/bin/env python3
"""Repair binary artifacts corrupted by the pre-4157299 multipart bug. DRY-RUN BY DEFAULT.

WHAT WAS WRONG
--------------
Until 4157299 the stdio bridge built its multipart body with an extra CRLF after the file part's
header block, which already ended with the blank line that terminates headers. Multipart treats
everything after the FIRST blank line as body, so two bytes of framing became the first two bytes
of every binary upload. Reported with byte evidence: a 23,620-byte .log stored as 23,622, with
`stored[2:] == original`.

Measured on this deployment: 183 of 192 stored binary artifacts are affected.

WHY STRIPPING TWO BYTES IS SAFE HERE
------------------------------------
It is safe even for a file that LEGITIMATELY begins with CRLF. Such a file was stored as
`\\r\\n` + `\\r\\n` + rest, so removing the injected pair leaves `\\r\\n` + rest — the original,
intact. The transformation is exactly "remove the two bytes we added".

The discriminator is clean and was verified, not assumed: every corrupted artifact came from an
AGENT via the bridge, and all nine clean ones came from `dashboard`, whose browser FormData builds
correct multipart. So the bug is per-upload-path, not per-file-type.

WHAT THIS REFUSES TO TOUCH
--------------------------
- Anything not starting with exactly `0d0a`. If the prefix is absent the file is already correct,
  and stripping would destroy real content.
- Anything shared at or after `--since` (default: the fix's deploy). A post-fix upload that
  genuinely begins with CRLF is correct content and must not be "repaired".
- Text artifacts (`is_binary = 0`). They never went through the multipart path.

Every file is backed up alongside itself as `<name>.pre-crlf-repair` before being rewritten, so a
wrong call is reversible without a database restore.

Usage:
    docker exec aify-comms-service python /tmp/repair_shared_crlf.py              # dry run
    docker exec aify-comms-service python /tmp/repair_shared_crlf.py --apply      # do it
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sqlite3
import sys

DEFAULT_DB = "/data/aify.db"
# The bug was fixed in 4157299; anything shared after it is correct by construction.
DEFAULT_SINCE = "2026-08-10T18:00:00Z"
INJECTED = b"\r\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help="Do not touch artifacts shared at/after this ISO instant (the fix deploy).")
    ap.add_argument("--apply", action="store_true", help="Actually rewrite files. Default is a dry run.")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    rows = list(db.execute(
        "SELECT name, from_agent, file_path, size, shared_at FROM shared_artifacts "
        "WHERE is_binary = 1 AND COALESCE(file_path, '') != '' AND shared_at < ? "
        "ORDER BY shared_at",
        (args.since,),
    ))

    repairable, skipped_clean, missing, failed = [], 0, 0, 0
    for r in rows:
        p = pathlib.Path(r["file_path"])
        if not p.exists():
            missing += 1
            continue
        try:
            head = p.open("rb").read(2)
        except Exception:
            failed += 1
            continue
        if head == INJECTED:
            repairable.append((r, p))
        else:
            skipped_clean += 1

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] binary artifacts shared before {args.since}: {len(rows)}")
    print(f"  repairable (start with 0d0a) : {len(repairable)}")
    print(f"  already clean, untouched     : {skipped_clean}")
    print(f"  file missing on disk         : {missing}")
    print(f"  unreadable                   : {failed}")

    if not repairable:
        print("\nNothing to do.")
        return 0

    print(f"\n  first few: {', '.join(r['name'][:34] for r, _ in repairable[:4])}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to rewrite, backing each file up as "
              "<name>.pre-crlf-repair first.")
        return 0

    repaired = 0
    for r, p in repairable:
        data = p.read_bytes()
        if data[:2] != INJECTED:  # re-check: the file may have changed since the scan
            continue
        backup = p.with_suffix(p.suffix + ".pre-crlf-repair")
        try:
            if not backup.exists():
                backup.write_bytes(data)
            fixed = data[2:]
            p.write_bytes(fixed)
            db.execute(
                "UPDATE shared_artifacts SET size = ? WHERE name = ?",
                (len(fixed), r["name"]),
            )
            repaired += 1
        except Exception as exc:  # pragma: no cover - operational
            print(f"  FAILED {r['name']}: {exc}", file=sys.stderr)
    db.commit()
    print(f"\nRepaired {repaired} file(s); backups written as *.pre-crlf-repair.")
    print("Verify a known artifact's hash against its source before deleting backups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
