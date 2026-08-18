"""Every status a writer can store must be canonical, or normalized before it is published.

THE RULING THIS ENFORCES. `service/routers/agents/listen.py` writes `status='idle'`, and `idle` is not
in `VALID_STATUSES` — it was removed when the proof-based engine replaced time-decay ("an
alive-not-in-turn agent is `online`, never `idle`"). Reported as a finding on 2026-08-18. comms-senior-dev
ruled: do NOT widen the public vocabulary to bless a stored legacy value; keep the writers, on the
condition that **the consuming boundary normalizes it**. That condition is currently met by
`_LEGACY_RAW_STATUS_TO_CANONICAL` in `service/api_core/records.py`, which maps `idle`/`active` to
`online` and `stale` to `offline`.

A CONDITIONAL RULING NEEDS A GATE, or the condition decays into a comment. Nothing measured the two
halves against each other: the map is a reader-side fact, the writers are spread across six modules,
and the only thing joining them is that somebody checked once. The next writer to store a new value —
`busy`, `waiting`, `paused` — publishes it raw to the dashboard, `comms_agent_info` and every roster
consumer, and no existing test notices, because each half is individually consistent.

IT CHECKS THE WRITERS, NOT THE READERS, which is the point. A reader-side test ("the map maps idle")
proves the map contains what it contains. The defect shape is a writer the map has never heard of, so
the population that must be enumerated is the SET OF STORED VALUES — found by scanning what the
product actually writes, not by listing what someone remembers writing. This is the same
already-recorded gap as "the status-split gate checks readers, not writers".

WHY A SOURCE SCAN AND NOT A RUNTIME SWEEP: a value only reachable on an error path or a rare
lifecycle transition would never appear in a database sampled at any one moment, and `agents.status`
is a free-text column, so the schema cannot answer the question either. What a writer *can* store is
a property of the source.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.api_core.records import _LEGACY_RAW_STATUS_TO_CANONICAL
from service.status_engine import VALID_STATUSES

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "service"

#: `UPDATE agents SET status = '<literal>'` — the ordinary write.
_DIRECT_WRITE = re.compile(r"UPDATE\s+agents\s+SET\s+status\s*=\s*'([a-z_]+)'", re.I)

#: `SET status = CASE WHEN status = 'x' THEN status ELSE 'y'` — the conditional write used by the
#: stop/resume and session-mode paths. BOTH literals are values this statement can leave in the
#: column, so both are collected: the `WHEN` arm preserves a value the code expects to already be
#: there, which is just as much a claim about the vocabulary as the `ELSE` arm that writes one.
_CASE_WRITE = re.compile(
    r"status\s*=\s*CASE\s+WHEN\s+status\s*=\s*'([a-z_]+)'\s+THEN\s+status\s+ELSE\s+'([a-z_]+)'", re.I
)

#: The value a registration falls back to when the caller sent none: `str(req.status or "idle")`.
_REGISTRATION_DEFAULT = re.compile(r"req\.status\s+or\s+[\"']([a-z_]+)[\"']")

#: The `agents.status` column default in `service/schema.py` — what every row starts as, and
#: therefore a stored value even if no code ever writes it explicitly.
#:
#: SCOPED TO THE `agents` TABLE, and that scoping is the whole correctness of it. The first version
#: matched any `status TEXT DEFAULT '...'` line in schema.py, which reported `queued` and `pending`
#: as unhandled agent statuses — they are the defaults on `spawn_requests` and `dispatch_runs`, two
#: entirely different vocabularies. It read as a real two-value product defect and was a scanner bug.
_AGENTS_TABLE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+agents\s*\((.*?)^\)", re.S | re.M | re.I
)
_SCHEMA_DEFAULT = re.compile(r"^\s*status\s+TEXT\s+DEFAULT\s+'([a-z_]+)'", re.M)


def _product_sources() -> list[Path]:
    return [
        p for p in SERVICE.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    ]


def _stored_statuses() -> dict[str, set[str]]:
    """Every literal the product can leave in `agents.status`, mapped to the files writing it."""
    found: dict[str, set[str]] = {}
    for path in _product_sources():
        source = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO).as_posix()
        for pattern in (_DIRECT_WRITE, _REGISTRATION_DEFAULT):
            for match in pattern.finditer(source):
                found.setdefault(match.group(1).lower(), set()).add(rel)
        # The schema default is read only from inside the `agents` CREATE TABLE block: every other
        # table's `status` column is a different vocabulary that this gate has no business judging.
        for table in _AGENTS_TABLE.finditer(source):
            for match in _SCHEMA_DEFAULT.finditer(table.group(1)):
                found.setdefault(match.group(1).lower(), set()).add(rel)
        for match in _CASE_WRITE.finditer(source):
            for group in match.groups():
                found.setdefault(group.lower(), set()).add(rel)
    return found


def _publishable(status: str) -> bool:
    """Would this stored value survive to a public surface unrecognised?"""
    return status in VALID_STATUSES or status in _LEGACY_RAW_STATUS_TO_CANONICAL


class StoredStatusesAreCanonicalOrNormalized(unittest.TestCase):
    def test_the_scan_finds_the_known_writers(self):
        """ANTI-VACUITY. If the patterns stop matching — the SQL is reformatted, the writes move to a
        query builder — every assertion below passes against an empty set. This test fails instead,
        because a scan that found nothing must not read like a clean bill of health."""
        stored = _stored_statuses()
        self.assertGreaterEqual(
            len(stored), 5,
            "the stored-status scan found fewer values than the five known when it was written "
            f"(active, idle, offline, stopped, working); it found {sorted(stored)}. The writes were "
            "probably reformatted or moved, and this gate is now measuring nothing.",
        )
        for expected in ("idle", "stopped", "working"):
            with self.subTest(expected=expected):
                self.assertIn(expected, stored,
                              f"the scan no longer sees any writer storing '{expected}'")

    def test_every_stored_status_is_canonical_or_normalized(self):
        stored = _stored_statuses()
        unhandled = {
            status: sorted(files) for status, files in stored.items() if not _publishable(status)
        }
        self.assertEqual(
            unhandled, {},
            "these values can be stored in `agents.status` but are neither in VALID_STATUSES nor in "
            f"_LEGACY_RAW_STATUS_TO_CANONICAL: {unhandled}. They will be published RAW to the "
            "dashboard, comms_agent_info and every roster consumer. comms-senior-dev's 2026-08-18 "
            "ruling on stored `idle` was explicit: a legacy stored value is acceptable only while the "
            "consuming boundary normalizes it, and the public vocabulary is NOT to be widened to "
            "bless one. So the fix is a normalization entry in records.py, not a new VALID_STATUSES "
            "member — unless a reviewer decides the state is genuinely public and distinct.",
        )

    def test_idle_specifically_normalizes_to_online(self):
        """The ruling names this mapping, so it is pinned by name rather than only by the sweep
        above — which would also pass if `idle` were mapped to something absurd."""
        self.assertEqual(
            _LEGACY_RAW_STATUS_TO_CANONICAL.get("idle"), "online",
            "stored 'idle' must publish as 'online'. The proof-based engine's own docstring is the "
            "reason: an alive-not-in-turn agent is `online`, never `idle`.",
        )
        self.assertNotIn(
            "idle", VALID_STATUSES,
            "'idle' was added to the public vocabulary. comms-senior-dev ruled against exactly this "
            "on 2026-08-18: `idle` is a stored legacy/heartbeat detail that normalizes to `online`, "
            "and widening the public taxonomy to bless an old detail state is the move the "
            "terminal-status allowlist ruling already refused.",
        )

    def test_no_normalization_target_is_itself_unknown(self):
        """A map is only a normalization if what it maps TO is canonical. `{"idle": "restings"}` would
        satisfy every other assertion here while still publishing a value no consumer knows."""
        bad = {k: v for k, v in _LEGACY_RAW_STATUS_TO_CANONICAL.items() if v not in VALID_STATUSES}
        self.assertEqual(
            bad, {},
            f"_LEGACY_RAW_STATUS_TO_CANONICAL normalizes to values outside VALID_STATUSES: {bad}. "
            "The map would launder a legacy value into a different unknown one.",
        )

    def test_the_map_does_not_shadow_a_canonical_status(self):
        """Mapping a status that is ALREADY canonical would silently rewrite a live verdict from
        `derive()` on the way out — the engine's decision overruled by a legacy lookup table."""
        shadowed = sorted(set(_LEGACY_RAW_STATUS_TO_CANONICAL) & set(VALID_STATUSES))
        self.assertEqual(
            shadowed, [],
            f"these statuses are canonical AND in the legacy normalization map: {shadowed}. Whatever "
            "derive() decided for them would be rewritten at the record boundary.",
        )


if __name__ == "__main__":
    unittest.main()
