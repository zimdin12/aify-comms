"""What one terminal flush costs, counted rather than timed.

THE HOT PATH. Every managed agent's console output arrives here, and the write goes to the same
single-writer SQLite file every dashboard poll reads, behind one global lock
(`TerminalOutputWriteQueue._write_lock`). `6035d5a3` did this for the heartbeat and the reasoning is
identical: wall-clock is unmeasurable on this host -- the same code timed 44-47ms and then 22-25ms
minutes apart, because the live fleet is the load -- while a round-trip count is deterministic and
attributable to a line.

**THE AMPLIFICATION IS FIXED, 2026-09-04, and this file now guards that rather than records it.**
The tail is written on a slower cadence than the stream (`service/api_core/terminal_tail_buffer.py`),
so most flushes carry no `output` column at all: a 64-byte chunk amplifies 0.3x where it used to
amplify 1023.5x. The table below is the BEFORE, kept because the fix is only legible against it and
because the number is what justified spending durability on the change.

That inversion is this file working as designed. Its assertion was pre-registered as a tripwire --
"if this stops holding, the amplification has been fixed and the paragraph at the top of this file is
out of date" -- and it fired on the commit that fixed it.

MEASURED STEADY STATE BEFORE THE FIX, 2026-09-01. `_append_terminal_output` stored a 64KB TAIL in one
column and rewrote the WHOLE column on every flush, so the bytes written per flush did not depend on
how much output arrived:

    chunk      64B -> 1023.5x amplification   (predicted 65536/64 = 1024.0x)
    chunk     256B ->  255.8x                 (predicted 256.0x)
    chunk    1024B ->   63.6x                 (predicted 64.0x)
    chunk    4096B ->   15.5x                 (predicted 16.0x)
    chunk   16384B ->    3.5x                 (predicted 4.0x)

AND THE REAL CHUNK IS NOT 16KB. The queue's `max_batch_chars` is 16 * 1024, which invites the
assumption that flushes are large. They are not: `idle_flush_ms` is 4 and `max_latency_ms` is 24, so a
flush carries whatever arrived in a few milliseconds. Measured from the LIVE database -- 5,329 stored
`terminal_output` events, one row per flush -- the median chunk is 75 bytes, p75 is 80, p90 is 136,
and the mean is 131.6. Only 2.7% reach the 2000-char cap the event body is truncated at, so the true
mean is somewhat higher and unknown above that point.

WHAT THIS FILE DOES NOT DO IS FIX THAT, and the reason is worth recording so the next person does not
spend an afternoon rediscovering it. The obvious remedy -- write the durable tail less often and let
the live screen serve reads in between -- is not a tuning change. EIGHT modules touch
`terminal_sessions.output` directly, seven of them reading it, and two of those are on the status
path: `api_core/status_inputs.py` and `reconcilers/terminal_runs.py` parse the stored tail for
idle-prompt hints. Making that column stale
by a second makes the input that decides whether an agent is idle stale by a second, which is the
flapping this repo spent months removing.

SO WHAT IS GATED HERE IS THE CALL COUNT, which is the part a future edit can silently make worse. The
natural way to add a field to a write path is to add a query for it.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.terminal_output import _append_terminal_output, _trim_terminal_output

#: The cap `_append_terminal_output` trims the stored tail to.
TAIL_CAP = 65536


class RecordingDb:
    """Counts round trips and keeps the parameters, so bytes can be summed as well as calls."""

    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=()):
        # THE WHOLE STATEMENT IS KEPT, not just its verb. The UPDATE's column ORDER is not fixed --
        # since the lazy tail landed, `output` is present only on the flushes that carry it -- so a
        # caller that assumed `params[0]` was the tail would count a 20-character timestamp and
        # report a tiny number whatever the code does. That false green happened here on 2026-09-04
        # and was caught by a mutation that should have reddened this file and did not.
        self.calls.append((sql.strip().split()[0].upper(), params, " ".join(sql.split())))
        return self

    @staticmethod
    def tail_bytes(sql, params):
        """The characters this statement wrote to `output`, found by POSITION in the SET clause."""
        if not sql.upper().startswith("UPDATE TERMINAL_SESSIONS"):
            return 0
        set_clause = sql[sql.upper().index(" SET ") + 5: sql.upper().index(" WHERE ")]
        assignments = [a.strip() for a in set_clause.split(",")]
        placeholders = 0
        for assignment in assignments:
            if assignment.startswith("output = "):
                return len(str(params[placeholders])) if placeholders < len(params) else 0
            placeholders += assignment.count("?")
        return 0

    def verbs(self):
        return [verb for verb, _params, _sql in self.calls]


class Row(dict):
    """A stand-in for the sqlite3.Row the writer is handed: indexable, with `.keys()`."""

    def keys(self):
        return dict.keys(self)


def _drive(chunks, *, seed=""):
    """Run N flushes through the real writer, carrying the stored value forward as the caller does."""
    db = RecordingDb()
    terminal = Row(id="t1", output=seed, status="running", cols=120, rows=30)
    written = 0

    async def run():
        nonlocal written
        for index, chunk in enumerate(chunks):
            before = len(db.calls)
            await _append_terminal_output(db, terminal, chunk, seq=index + 1)
            for verb, params, sql in db.calls[before:]:
                if verb != "UPDATE":
                    continue
                # Carry the stored value forward the way the caller does -- but only when this
                # statement actually wrote one. A flush that skipped the tail leaves the row alone.
                wrote = RecordingDb.tail_bytes(sql, params)
                if wrote:
                    set_clause = sql[sql.upper().index(" SET ") + 5: sql.upper().index(" WHERE ")]
                    idx = 0
                    for assignment in [a.strip() for a in set_clause.split(",")]:
                        if assignment.startswith("output = "):
                            terminal["output"] = params[idx]
                            break
                        idx += assignment.count("?")
                    written += wrote
                break

    asyncio.run(run())
    return db, terminal, written


class TheTerminalWritePathStaysCheapTests(unittest.TestCase):
    def test_one_flush_is_one_update_and_one_insert(self):
        """The gate. Adding a query to this path is the thing that must not pass unnoticed."""
        db, _terminal, _written = _drive(["hello\n"])
        self.assertEqual(
            db.verbs(), ["UPDATE", "INSERT"],
            "the terminal write path changed shape -- it is the hottest write in the service and "
            "shares one SQLite writer with every dashboard poll",
        )

    def test_a_status_only_flush_writes_no_output_event(self):
        """A status change carries no chunk, so it must not pay for an event row."""
        db = RecordingDb()
        terminal = Row(id="t1", output="", status="running", cols=80, rows=24)
        asyncio.run(_append_terminal_output(db, terminal, "", status="stopped"))
        self.assertEqual(db.verbs(), ["UPDATE"])

    def test_the_event_prune_is_amortised_not_per_flush(self):
        """A DELETE on every flush would double the cost of the hottest write path.

        CONTRADICTION ARM: it must appear EVENTUALLY, or "few DELETEs" would also pass on code that
        never prunes at all and lets `terminal_events` grow without bound.
        """
        db, _terminal, _written = _drive(["x\n"] * 250)
        deletes = db.verbs().count("DELETE")
        self.assertGreaterEqual(deletes, 1, "the prune never ran, so terminal_events is unbounded")
        self.assertLessEqual(deletes, 2, f"the prune stopped being amortised: {deletes} in 250 flushes")

    def test_THE_AMPLIFICATION_STAYS_FIXED(self):
        """Inverted 2026-09-04, on the commit that fixed what it used to measure.

        It read: two runs ingesting very different totals write almost the same number of bytes,
        because each flush rewrites the whole tail -- "if this stops holding, the amplification has
        been fixed and the paragraph at the top of this file is out of date." It stopped holding, so
        the paragraph was rewritten and the assertion turned around.

        WHAT IT GUARDS NOW: that a small chunk no longer costs a whole-tail rewrite. Reverting the
        lazy write would send this back over 100x and redden here, which is where an author would
        want to be told.

        THE THRESHOLD IS THE OLD FLOOR, not a tight fit to today's number. A bound sitting one
        percent under the current value fails on any harmless change and gets loosened until it means
        nothing; 10x is comfortably above where the fix put it (0.3x) and comfortably below where the
        defect lived (1023x), so only a real regression crosses it.
        """
        seed = ("z" * 79 + "\n") * (TAIL_CAP // 80)
        small = ["x" * 63 + "\n"] * 200
        large = ["x" * 4095 + "\n"] * 200

        _db_s, _t_s, written_small = _drive(small, seed=seed)
        _db_l, _t_l, written_large = _drive(large, seed=seed)

        ingested_small = sum(len(c) for c in small)
        ingested_large = sum(len(c) for c in large)
        self.assertLess(
            ingested_small * 20, ingested_large,
            "the two runs must differ a lot in INPUT for this comparison to mean anything",
        )
        self.assertLess(
            written_small / ingested_small, 10,
            f"a small chunk amplifies {written_small / ingested_small:.0f}x again -- the lazy tail "
            "has been reverted or bypassed, and every flush is rewriting the whole 64 KB column. "
            "This was 1023x before the fix and 0.3x after it, so the bound is nowhere near either.",
        )
        self.assertLess(
            written_large / ingested_large, 10,
            "the large-chunk run amplifies too, so this is the storage model and not a chunk-size "
            "effect",
        )

    def test_the_tail_is_capped(self):
        """POSITIVE CONTROL on the trim, which is what makes every steady-state flush the same size."""
        self.assertEqual(len(_trim_terminal_output("y" * 200000)), TAIL_CAP)

    def test_the_kept_tail_starts_after_a_newline(self):
        """A cut mid-line -- or mid-ANSI-escape -- is what the line-boundary rule exists to avoid."""
        trimmed = _trim_terminal_output(("a" * 79 + "\n") * 2000)
        self.assertLess(len(trimmed), TAIL_CAP, "a raw character-count slice was kept")
        self.assertTrue(trimmed.startswith("a"), "the kept tail begins mid-line")

    def test_a_short_buffer_is_returned_whole(self):
        """NEGATIVE CONTROL: the trim must not fire when there is nothing to trim."""
        self.assertEqual(_trim_terminal_output("short\n"), "short\n")
        self.assertEqual(_trim_terminal_output(""), "")
