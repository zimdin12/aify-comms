"""What one terminal flush costs, counted rather than timed.

THE HOT PATH. Every managed agent's console output arrives here, and the write goes to the same
single-writer SQLite file every dashboard poll reads, behind one global lock
(`TerminalOutputWriteQueue._write_lock`). `6035d5a3` did this for the heartbeat and the reasoning is
identical: wall-clock is unmeasurable on this host -- the same code timed 44-47ms and then 22-25ms
minutes apart, because the live fleet is the load -- while a round-trip count is deterministic and
attributable to a line.

MEASURED STEADY STATE, 2026-09-01. `_append_terminal_output` stores a 64KB TAIL in one column and
rewrites the WHOLE column on every flush, so the bytes written per flush do not depend on how much
output arrived:

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
        self.calls.append((sql.strip().split()[0].upper(), params))
        return self

    def verbs(self):
        return [verb for verb, _params in self.calls]


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
            for verb, params in db.calls[before:]:
                if verb == "UPDATE":
                    terminal["output"] = params[0]
                    written += len(params[0])
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

    def test_the_bytes_written_do_not_depend_on_how_much_arrived(self):
        """The measurement this file exists to record, asserted rather than merely described.

        Two runs ingesting very different totals write almost the same number of bytes, because each
        flush rewrites the whole tail. If this stops holding, the amplification has been fixed and the
        paragraph at the top of this file is out of date -- which is the point of asserting it.
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
        self.assertGreater(
            written_small / ingested_small, 100,
            "a 64-byte chunk no longer amplifies 100x -- the storage model changed",
        )
        self.assertGreater(
            (written_small / ingested_small) / (written_large / ingested_large), 10,
            "amplification stopped tracking chunk size, so the tail is no longer rewritten whole",
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
