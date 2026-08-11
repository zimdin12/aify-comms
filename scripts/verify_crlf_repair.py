#!/usr/bin/env python3
"""Prove the CRLF repair before its backups are deleted. READ-ONLY BY DEFAULT.

WHY THIS EXISTS
---------------
`repair_shared_crlf.py --apply` stripped two bytes from 183 stored artifacts and left each original
beside it as `<name>.pre-crlf-repair`. The post-run checks were good (no file still starts `0d0a`,
no DB/disk size mismatch, 85 files now have valid PNG/ZIP magic at byte 0) but they all confirm the
same thing: the repaired files look right.

The reviewer named the one case they cannot rule out (AUDIT 4/4 F4): a file that LEGITIMATELY began
with CRLF, uploaded through an already-fixed bridge, but before the cutoff. Stripping that file's
real first two bytes is indistinguishable from repairing an injected pair — from the artifact alone.

So this script does not re-examine the artifacts. It establishes the two facts that make the
question answerable, and refuses to delete anything unless both hold.

FACT 1 — PROVENANCE: no repaired artifact can have come from fixed code.
    The fix is commit 4157299, committed 2026-08-10T18:12:44Z. Every repaired artifact was shared
    strictly before that instant, so no bridge anywhere could have been running the corrected
    multipart builder when it was uploaded. The leading CRLF was therefore injected, not content.
    This is what actually retires "every stripped 0d0a was framing" — the artifact cannot.

FACT 2 — DERIVABILITY: the backups carry no information the repaired files lack.
    Each backup must satisfy `backup == b"\\r\\n" + repaired`, byte for byte. Where that holds the
    backup is reconstructible from the repaired file with two bytes of knowledge, so deleting it
    destroys nothing — this is a stronger guarantee than "we kept a copy", and it is the reason
    deletion is safe rather than merely tolerable.

A backup that fails EITHER check is a real finding: it is kept, named, and the script exits 1.

Usage:
    docker exec aify-comms-service python /tmp/verify_crlf_repair.py                  # verify
    docker exec aify-comms-service python /tmp/verify_crlf_repair.py --manifest /data/crlf.json
    docker exec aify-comms-service python /tmp/verify_crlf_repair.py --delete-backups # after green
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys

DEFAULT_DB = "/data/aify.db"
BACKUP_SUFFIX = ".pre-crlf-repair"
INJECTED = b"\r\n"
# 4157299, `fix(share): every binary upload gained a leading CRLF`. Committed 21:12:44+03:00.
# Nothing shared before this instant can have been produced by corrected code.
FIX_COMMITTED_AT = "2026-08-10T18:12:44Z"

# Magic numbers only where they are decisive. A file that does NOT match any of these is not
# evidence of a bad repair — most artifacts are logs, JSON and text-ish payloads with no magic.
MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"PK\x03\x04": "zip",
    b"PK\x05\x06": "zip-empty",
    b"\xff\xd8\xff": "jpeg",
    b"GIF89a": "gif",
    b"%PDF-": "pdf",
    b"\x1f\x8b": "gzip",
}


def classify(head: bytes) -> str:
    for sig, name in MAGIC.items():
        if head.startswith(sig):
            return name
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--manifest", default="", help="Write the full per-file record here as JSON.")
    ap.add_argument("--fix-committed-at", default=FIX_COMMITTED_AT,
                    help="No repaired artifact may have been shared at/after this instant.")
    ap.add_argument("--delete-backups", action="store_true",
                    help="Delete the backups — refused unless every check passes.")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    by_path = {
        str(r["file_path"]): r
        for r in db.execute(
            "SELECT name, from_agent, file_path, size, shared_at, is_binary "
            "FROM shared_artifacts WHERE COALESCE(file_path, '') != ''"
        )
    }

    records, problems = [], []
    roots = {pathlib.Path(p).parent for p in by_path} or {pathlib.Path("/data/shared")}
    backups = sorted({b for root in roots for b in root.glob(f"*{BACKUP_SUFFIX}")})

    for backup in backups:
        repaired = backup.with_name(backup.name[: -len(BACKUP_SUFFIX)])
        row = by_path.get(str(repaired))
        rec: dict = {
            "backup": str(backup),
            "repaired": str(repaired),
            "name": row["name"] if row else None,
            "from_agent": row["from_agent"] if row else None,
            "shared_at": row["shared_at"] if row else None,
            "is_binary": int(row["is_binary"] or 0) if row else None,
        }

        if not repaired.exists():
            rec["verdict"] = "repaired-file-missing"
            problems.append(rec)
            records.append(rec)
            continue

        bdata, rdata = backup.read_bytes(), repaired.read_bytes()
        rec.update(
            backup_size=len(bdata),
            repaired_size=len(rdata),
            db_size=int(row["size"]) if row and row["size"] is not None else None,
            backup_sha256=hashlib.sha256(bdata).hexdigest(),
            repaired_sha256=hashlib.sha256(rdata).hexdigest(),
            backup_head_hex=bdata[:8].hex(),
            repaired_head_hex=rdata[:8].hex(),
            backup_magic=classify(bdata),
            repaired_magic=classify(rdata),
        )

        # FACT 2 — derivability. The whole justification for deleting.
        derivable = bdata == INJECTED + rdata
        rec["derivable_from_repaired"] = derivable

        # FACT 1 — provenance. An artifact with no row cannot be dated, so it cannot be cleared.
        shared_at = str(row["shared_at"]) if row and row["shared_at"] else ""
        rec["predates_fix"] = bool(shared_at) and shared_at < args.fix_committed_at

        # Supporting, never decisive: a repair that produced valid magic where the backup had none
        # is corroboration; the absence of magic means nothing for a log or a JSON blob.
        rec["magic_gained"] = bool(rec["repaired_magic"]) and not rec["backup_magic"]

        if not derivable:
            rec["verdict"] = "NOT-DERIVABLE — backup is not exactly CRLF + repaired"
            problems.append(rec)
        elif not rec["predates_fix"]:
            rec["verdict"] = (
                f"UNPROVEN PROVENANCE — shared_at {shared_at or '(unknown)'} is not before "
                f"{args.fix_committed_at}; a legitimate leading CRLF cannot be ruled out"
            )
            problems.append(rec)
        elif rec["db_size"] is not None and rec["db_size"] != rec["repaired_size"]:
            rec["verdict"] = "DB SIZE MISMATCH — the row does not describe the file on disk"
            problems.append(rec)
        else:
            rec["verdict"] = "ok"
        records.append(rec)

    ok = [r for r in records if r.get("verdict") == "ok"]
    print(f"backups found                 : {len(records)}")
    print(f"  fully verified              : {len(ok)}")
    print(f"  problems                    : {len(problems)}")
    print(f"  gained a valid magic number : {sum(1 for r in records if r.get('magic_gained'))}")
    newest = max((r["shared_at"] or "" for r in records), default="")
    print(f"  newest repaired artifact    : {newest or '(none)'}")
    print(f"  fix committed at            : {args.fix_committed_at}")
    if newest and newest < args.fix_committed_at:
        print("  => PROVENANCE PROVEN: every repaired artifact predates the fix commit, so none of "
              "them can have been uploaded by corrected code.")

    for r in problems[:20]:
        print(f"  ! {r.get('name') or r['backup']}: {r['verdict']}", file=sys.stderr)

    if args.manifest:
        pathlib.Path(args.manifest).write_text(
            json.dumps(
                {
                    "fix_committed_at": args.fix_committed_at,
                    "backup_suffix": BACKUP_SUFFIX,
                    "verified": len(ok),
                    "problems": len(problems),
                    "records": records,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nmanifest written: {args.manifest}")

    if problems:
        print("\nNOT SAFE TO DELETE — the problems above are unresolved.", file=sys.stderr)
        return 1

    if args.delete_backups:
        if not records:
            print("\nNothing to delete.")
            return 0
        if not args.manifest:
            print("\nRefusing to delete without --manifest: the manifest is the record that the "
                  "deletion was justified.", file=sys.stderr)
            return 1
        for r in records:
            pathlib.Path(r["backup"]).unlink()
        print(f"\nDeleted {len(records)} backup(s). Each was exactly CRLF + its repaired file, so "
              "nothing was lost that the repaired file does not already contain.")
    else:
        print("\nRead-only. Re-run with --manifest <path> --delete-backups to remove them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
