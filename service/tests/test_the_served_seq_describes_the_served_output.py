"""What `GET /terminals/{id}` serves as `outputSeq` must describe what it serves as content.

R9-H1, found by an external reviewer on 2026-09-05 and confirmed at the cited lines. The lazy-tail
change writes the row's `output` and `output_seq` together once a second, and its own comment argues
that pairing is the design. It is, FOR THE ROW. The defect is that the client is not seeded from the
row: `_attach_terminal_snapshot` renders the LIVE screen, which every chunk feeds, while `outputSeq`
came from the lagging row.

WHY THAT IS A REPAINT STORM RATHER THAN A COSMETIC LAG. `xterm-mount.mjs` seeds `lastSeq` from
`outputSeq`, and `realtime-socket.mjs` resyncs on `seq > lastSeq + 1` -- STRICT CONTIGUITY. A seq one
frame behind therefore makes the very next live frame look like a gap: the frame is dropped, a resync
GET fires, that GET returns the same stale seq because the flush interval has not elapsed, and the
console `term.reset()`s and fully rewrites at frame rate until the terminal falls quiet.

THE INVARIANT THIS PINS is not "the row is self-consistent" -- that was already true and was not
enough. It is that the CONTENT and the SEQ in one response come from the same place: the live pair
while this process holds one, the row's pair otherwise, never one of each.

The tests drive the real serialiser and the real buffer. A test that recomputed the pairing itself
would agree with whatever the code did.
"""

from __future__ import annotations

import sqlite3
import unittest

from service.api_core import terminal_tail_buffer as tail
from service.api_core.records import _terminal_session_to_dict
from service.schema import SCHEMA

TERMINAL_ID = "term-r9h1"


def _required_columns() -> list[str]:
    """Columns `terminal_sessions` declares NOT NULL with no default, read from the real DDL.

    NOT a typed list. The first version of this fixture hand-listed columns and failed one missing
    NOT NULL at a time -- add `runtime`, hit the next, add that, hit the next. That is a second
    source of truth for the schema, and it goes stale the moment a column is added. Deriving it
    means a new NOT NULL column is filled here the day it lands.
    """
    body = SCHEMA.split("CREATE TABLE IF NOT EXISTS terminal_sessions (", 1)[1].split(");", 1)[0]
    required = []
    for line in body.splitlines():
        part = line.strip().rstrip(",")
        if not part or part.startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "--")):
            continue
        upper = part.upper()
        if "NOT NULL" in upper and "DEFAULT" not in upper:
            required.append(part.split()[0])
    assert required, "the DDL parse found no NOT NULL columns, so this fixture proves nothing"
    return required


def _row(output: str, output_seq: int, terminal_id: str = TERMINAL_ID) -> sqlite3.Row:
    """One terminal_sessions row, with every required column filled from the schema itself."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    values = {c: "x" for c in _required_columns()}
    values.update({
        "id": terminal_id, "status": "running", "output": output, "output_seq": output_seq,
        "created_at": "2026-09-05T00:00:00Z", "updated_at": "2026-09-05T00:00:00Z",
    })
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    db.execute(f"INSERT INTO terminal_sessions ({cols}) VALUES ({marks})", tuple(values.values()))
    return db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,)).fetchone()

class ServedSeqDescribesServedOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        tail.reset_for_tests()
        self.addCleanup(tail.reset_for_tests)

    def test_POSITIVE_CONTROL_a_terminal_this_process_never_wrote_serves_the_row(self) -> None:
        """The restart case, and the reason `stored` is a parameter at all."""
        served = _terminal_session_to_dict(_row("row-bytes", 41))
        self.assertEqual(served["output"], "row-bytes")
        self.assertEqual(served["outputSeq"], 41)

    def test_POSITIVE_CONTROL_the_buffer_really_does_hold_back_a_write(self) -> None:
        """If `record` started returning True every time, the defect could not be reproduced and
        every assertion below would pass against code that never had the bug."""
        tail.reset_for_tests()
        self.assertTrue(tail.record(TERMINAL_ID, "first", 1, now=100.0), "the first chunk always writes")
        self.assertFalse(tail.record(TERMINAL_ID, "second", 2, now=100.1), "a chunk inside the interval is held")

    def test_THE_SERVED_SEQ_ADVANCES_WITH_THE_SERVED_CONTENT(self) -> None:
        """The bug, stated as the client experiences it.

        The row still holds the flushed pair. The buffer holds three more chunks that were not
        written. A client seeded now must be told the seq of what it was actually given, or its very
        next live frame is a gap.
        """
        tail.record(TERMINAL_ID, "flushed", 10, now=100.0)   # writes; row would hold (flushed, 10)
        tail.record(TERMINAL_ID, "held-11", 11, now=100.1)   # held
        tail.record(TERMINAL_ID, "held-12", 12, now=100.2)   # held
        tail.record(TERMINAL_ID, "held-13", 13, now=100.3)   # held

        served = _terminal_session_to_dict(_row("flushed", 10))
        self.assertEqual(served["output"], "held-13", "the client was given the live tail")
        self.assertEqual(
            served["outputSeq"], 13,
            "and must be told the seq of the live tail. Serving 10 here is R9-H1: the next live "
            "frame carries 14, the client holds lastSeq=10, 14 > 11 is a gap, and the console "
            "resyncs and fully repaints at frame rate.",
        )

    def test_the_next_live_frame_is_CONTIGUOUS_with_what_was_served(self) -> None:
        """The client's actual rule, applied. `realtime-socket.mjs` resyncs on `seq > lastSeq + 1`,
        so contiguity is the property that matters -- monotonicity is not enough."""
        tail.record(TERMINAL_ID, "flushed", 7, now=200.0)
        for n, chunk in ((8, "a"), (9, "b")):
            tail.record(TERMINAL_ID, chunk, n, now=200.0 + n / 100)

        last_seq = _terminal_session_to_dict(_row("flushed", 7))["outputSeq"]
        next_frame_seq = 10  # the very next chunk the host posts
        self.assertLessEqual(
            next_frame_seq, last_seq + 1,
            f"seeded at {last_seq}, next frame {next_frame_seq}: the client would call this a gap",
        )

    def test_a_forgotten_terminal_falls_back_to_the_row_for_BOTH(self) -> None:
        """Ending a terminal drops its buffer. The pair must fall back together, not one of each."""
        tail.record(TERMINAL_ID, "held", 99, now=300.0)
        tail.forget(TERMINAL_ID)
        served = _terminal_session_to_dict(_row("final-bytes", 42))
        self.assertEqual(served["output"], "final-bytes")
        self.assertEqual(served["outputSeq"], 42)

    def test_one_terminals_buffer_never_answers_for_another(self) -> None:
        """`_BUFFERS` is process-global and keyed by id; a mix-up would serve one console's seq to
        another and desynchronise both."""
        tail.record(TERMINAL_ID, "mine", 55, now=400.0)
        served = _terminal_session_to_dict(_row("other-bytes", 3, terminal_id="some-other-terminal"))
        self.assertEqual(served["outputSeq"], 3, "another terminal's held seq leaked into this row")


if __name__ == "__main__":
    unittest.main()
