"""Every field `/stats` emits either has a reader, or is on a list that says it does not.

MEASURED. `/api/v1/stats` returns 24 fields and runs 20 DB round-trips to build them, flat in fleet
size. The dashboard reads exactly TWO: `dispatch_runs_by_status` and `run_failures_24h`, both in
`summary-tiles.mjs`. Eighteen are computed on every refresh -- the poll fallback alone is four times a
minute, plus every event-driven refetch -- and read by nothing in this repo.

THEY ARE NOT REMOVED HERE, deliberately. `/stats` is a public-ish endpoint, the payload is 2,386
bytes (0.6% of the dashboard's 424KB refresh bundle), and 20 indexed COUNTs is small beside the 248
round-trips one reconcile pass makes. Removing emitted fields is a contract change and this is not
the evidence for one. What this file prevents is the set GROWING: a new field must arrive with a
reader, or with a deliberate entry saying it has none.

WHY A LEDGER AND NOT A SCAN. A bare "warn on unread fields" test would be red from the day it was
written and stay red, which teaches everyone to ignore it. Pinning the known set makes the next
addition the only thing that fails -- the same shape as `oversized-allowlist.json` and the
skill-size ratchet, and for the same reason.

MY OWN INSTRUMENT HID THIS FOR MOST OF A DAY. An earlier sweep excluded a hand-listed set of producer
files and asked whether anything ELSE mentioned each field. `routers/stats.py` was not on that list,
so it counted as its own consumer and no field it emits could ever be flagged. The rule here is
producer-agnostic: a field mentioned in exactly one file is emitted there and read nowhere, whichever
file that is.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

REPO = Path(__file__).resolve().parent.parent.parent
STATS = REPO / "service" / "routers" / "stats.py"
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".pytest_cache", ".venv", "venv", "vendor", "fixtures"}

#: Fields `/stats` emits that NOTHING in this repo reads. Measured 2026-08-27.
#:
#: This list may only SHRINK. An entry leaving means somebody wired a reader, which is the good
#: outcome; an entry arriving means a new field was added with no consumer, which is a decision
#: someone should make on purpose rather than discover later.
KNOWN_UNREAD = {
    "active_dm_pairs_24h",
    "active_sessions",
    "channel_posts_24h",
    "channel_unread_messages",
    "completed_runs_24h",
    "direct_messages_24h",
    "dispatch_reply_pending",
    "dispatch_runs_total",
    "failed_spawns_24h",
    "messages_by_agent",
    "messages_today",
    "orphan_unread_messages",
    "shared_size_bytes",
    "shared_size_mb",
    "spawn_requests_by_status",
    "spawn_requests_total",
    "total_messages",
    "unread_messages",
}


def _emitted_fields():
    """The keys of the dict `/stats` returns, read from the source rather than a live service."""
    tree = ast.parse(STATS.read_text(encoding="utf-8"))
    best = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if len(keys) > len(best):
                best = keys
    return best


def _files():
    out = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if not n.endswith((".py", ".js", ".mjs", ".html")):
                continue
            if ".test." in n or n.startswith("test_"):
                continue
            out.append(os.path.join(root, n))
    return out


FILES = _files()


def _mentions(field):
    pattern = re.compile(r"\b" + re.escape(field) + r"\b")
    hits = []
    for path in FILES:
        try:
            if pattern.search(open(path, encoding="utf-8", errors="replace").read()):
                hits.append(path)
        except OSError:
            continue
    return hits


class TheStatsEndpointHasADeadFieldLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.emitted = _emitted_fields()
        cls.unread = {f for f in cls.emitted if len(_mentions(f)) <= 1}

    def test_the_probe_reads_a_real_payload_shape(self):
        """POSITIVE CONTROL. An AST walk that found no return dict would make every set below empty
        and every assertion pass."""
        self.assertGreaterEqual(len(self.emitted), 20, sorted(self.emitted))
        self.assertIn("agents", self.emitted)

    def test_the_file_scan_can_see_the_repo(self):
        """The other half of the control: a scan reading nothing reports every field as unread."""
        self.assertGreater(len(FILES), 200, len(FILES))
        self.assertGreater(len(_mentions("status")), 20, "a common name is barely mentioned")

    def test_the_two_fields_the_dashboard_uses_are_NOT_unread(self):
        """Anti-vacuity with teeth: if the scan called everything unread, these would be in the set
        and the ledger would be meaningless."""
        for field in ("dispatch_runs_by_status", "run_failures_24h"):
            self.assertIn(field, self.emitted, f"{field} is no longer emitted")
            self.assertNotIn(field, self.unread, f"{field} reads as unread; the scan is broken")

    def test_no_NEW_dead_field_has_appeared(self):
        new = self.unread - KNOWN_UNREAD
        self.assertEqual(
            new, set(),
            f"{sorted(new)} are emitted by /stats and read by nothing. Either wire a reader, or add "
            "them to KNOWN_UNREAD deliberately -- each one costs a DB round-trip on every dashboard "
            "refresh, four times a minute at the poll fallback alone.",
        )

    def test_the_ledger_has_not_gone_STALE(self):
        """It may only shrink. A name here that now HAS a reader is a stale entry, and a ledger that
        keeps names nobody needs to think about any more rots into an unchecked list."""
        stale = KNOWN_UNREAD - self.unread
        self.assertEqual(
            stale, set(),
            f"{sorted(stale)} now have a reader (or are no longer emitted). Remove them from "
            "KNOWN_UNREAD: the list may only shrink.",
        )


if __name__ == "__main__":
    unittest.main()
