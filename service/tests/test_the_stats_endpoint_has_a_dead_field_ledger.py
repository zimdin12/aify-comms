"""Every field `/stats` emits either has a reader, or is on a list that says it does not.

`/api/v1/stats` returns 24 fields. Eighteen are not named by any other production file in this repo;
the dashboard reads exactly TWO, `dispatch_runs_by_status` and `run_failures_24h`, both in
`summary-tiles.mjs`.

WHAT "UNREAD" MEANS HERE, precisely, because the looser sentence is the one worth attacking: each of
those names occurs in at most ONE scanned production file. That is a LEXICAL floor, not proof of
non-use. Dynamic access (`payload[key]`), whole-object forwarding, code outside the scanned extensions
or outside this repo, and any external API consumer are all outside this instrument's authority. It
can say "nothing here spells this name". It cannot say "nothing reads this field".

WHAT REMOVAL WOULD ACTUALLY SAVE -- corrected 2026-08-27 after review, because the first version of
this file said each unread field costs its own `SELECT COUNT(*)` and that is FALSE IN SOURCE. There
are 20 `db.execute` sites and 20 distinct source values, one per query, but fields do not map to them
1:1. Three unread fields are FREE: `dispatch_runs_total` is derived from `dispatch_by_status`, the very
query that produces the READ field `dispatch_runs_by_status`, and `shared_size_bytes` and
`shared_size_mb` both come from the `shared_row` that `shared_files` needs anyway. Two more,
`spawn_requests_total` and `spawn_requests_by_status`, share one query with each other. So deleting all
eighteen would recover 14 of the 20 round-trips, not 18 -- and deleting `dispatch_runs_total` alone
would recover nothing whatsoever.

THEY ARE NOT REMOVED HERE, and the reviewer's ruling on 2026-08-27 was firmer than my reasoning:
in-repo non-use does not prove external non-use on a public-ish endpoint, and a cheap payload does not
authorise a contract break. Removal needs a compatibility decision -- a deprecation window, evidence
about external consumers, a release note -- and a lexical scan is not that evidence. What this file
prevents is the set growing SILENTLY.

(The runtime figures that motivated the look are EXTERNAL measurements, not anything this gate proves:
20 round-trips at 6, 12 and 24 agents; a 2,386-byte payload against a 424KB refresh bundle; 248
round-trips in one reconcile pass. They are recorded as context for the judgement, and none of them is
re-derived here.)

WHY A LEDGER AND NOT A SCAN. A bare "warn on unread fields" test would be red from the day it was
written and stay red, which teaches everyone to ignore it. Pinning the known set makes the next
addition the only thing that fails -- the same shape as `oversized-allowlist.json` and the
skill-size ratchet, and for the same reason.

MY OWN INSTRUMENT HID THIS FOR MOST OF A DAY. An earlier sweep excluded a hand-listed set of producer
files and asked whether anything ELSE mentioned each field. `routers/stats.py` was not on that list,
so it counted as its own consumer and no field it emits could ever be flagged. The rule here is
producer-agnostic: a field mentioned in exactly one file is emitted there and named nowhere else,
whichever file that is. That is a weaker claim than the sweep was making, and it is the one the
instrument can actually support.
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

#: Fields `/stats` emits that no OTHER production file in this repo names. Measured 2026-08-27.
#:
#: THE INVARIANT IS "NO SILENT GROWTH", not monotonic shrink -- a distinction worth keeping straight,
#: because the looser phrasing describes something this gate does not enforce. Adding a field AND
#: adding its name here in the same commit PASSES, on purpose: the point is that the decision is
#: written down, not that it is forbidden. What cannot happen is a field arriving with no reader and
#: nobody noticing. Entries leaving is the good direction and is required as soon as it is true.
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
            f"{sorted(new)} are emitted by /stats and named by no other production file. Either wire "
            "a reader, or add them to KNOWN_UNREAD deliberately. Most -- not all -- carry their own DB "
            "round-trip on every refresh; check whether the value is derived from a query another "
            "field already needs before assuming it is free or assuming it is not.",
        )

    def test_the_ledger_has_not_gone_STALE(self):
        """A name here that now HAS a reader is a stale entry, and a ledger that keeps names nobody
        needs to think about any more rots into an unchecked list. This is the half of the invariant
        that genuinely only moves one way."""
        stale = KNOWN_UNREAD - self.unread
        self.assertEqual(
            stale, set(),
            f"{sorted(stale)} now have a reader (or are no longer emitted). Remove them from "
            "KNOWN_UNREAD -- a stale entry makes the list look like it is still describing the code.",
        )


if __name__ == "__main__":
    unittest.main()
