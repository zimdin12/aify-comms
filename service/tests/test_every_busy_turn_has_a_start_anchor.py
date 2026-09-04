"""Every write that sets `turn_busy = 1` must also bind `turn_started_at`.

EXTERNAL REVIEW, Round 8 M12, and it is a correction to a proof this repo already wrote rather than a
new defect. `364b8ea9` pinned DISP-L1 as a non-defect. The CONCLUSION is right; the reasoning under
it is not, and the pin does not guard what makes the conclusion true.

WHAT THE OLD REASONING SAID: an anchorless renewable turn is "already bounded by the STRICT rule,
which is tighter than the ceiling it skips". That is comparing two different quantities. `strict`
bounds the SILENCE GAP -- how long since anything touched the row -- and the ceiling bounds the
DURATION of the turn. A turn touched every thirty seconds for a week never trips strict, and the
ceiling that would have stopped it is the one being skipped. The policy's own docstring says the
touch column cannot bound a turn, one function above.

WHAT THE OLD PIN CHECKED: that a renewable row answers the same as an unverified one. An UNBOUNDED
pair answers the same too -- both say live -- so the assertion holds in exactly the world it is meant
to rule out. It measures parity, and parity is not the property.

WHY DISP-L1 IS STILL A NON-DEFECT: because no anchorless busy row is ever written. There is one
writer of `turn_busy = 1` (`turn_busy_signal.py`) and it stamps `turn_started_at` in the same
statement, plus a boot backfill for rows that predate it. So the ceiling's `if started_epoch` guard
never meets a row without an anchor.

THAT is the invariant, and this file is the gate for it. Written against the WRITERS rather than the
policy, because the policy is correct as it stands: what would break DISP-L1's dismissal is a second
writer arriving that sets busy without an anchor, and no test could see that from the reading side.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SERVICE = REPO / "service"
SKIP_DIRS = {"tests", "__pycache__", ".venv", "node_modules"}


def _sql_writes_busy_one(sql: str) -> bool:
    """Whether this statement SETS turn_busy to 1 (rather than reading or clearing it)."""
    text = " ".join(sql.split()).lower()
    if "agent_turn_state" not in text:
        return False
    # AN ASSIGNMENT, NOT A PREDICATE, and telling them apart is most of this function. `turn_busy = 1`
    # appears in three other shapes that are not writes at all, and the first version of this scan
    # flagged two of them:
    #   * `WHERE ats.turn_busy = 1` -- a reconciler SELECTING busy turns
    #   * `WHEN turn_busy = 1 AND ...` -- the CASE inside the real write, reading the OLD value
    #   * `SET turn_busy = 0` -- the clear
    # A rule that fires on those is unsatisfiable, which is how a gate gets deleted instead of fixed.
    if "insert into agent_turn_state" in text:
        return "turn_busy" in text and ("values (?, 1," in text or "turn_busy = 1," in text)
    if "update agent_turn_state" not in text:
        return False
    # Only the SET clause can assign. Everything from the first WHERE onwards is a predicate.
    body = text.split(" where ", 1)[0]
    body = body.replace("when turn_busy = 1", "")
    return "turn_busy = 1," in body or body.rstrip().endswith("turn_busy = 1")


def _binds_the_anchor(sql: str) -> bool:
    """Whether this busy-write ASSIGNS `turn_started_at`, on every path it can take.

    MENTIONING IT IS NOT BINDING IT, and the first version of this gate could not tell the two apart:
    removing the anchor from the `ON CONFLICT DO UPDATE SET` clause left the name in the INSERT column
    list, so a substring test passed while an EXISTING row flipping to busy kept whatever anchor it
    had -- which is none, for a row that has never been busy. That is precisely the anchorless busy
    row DISP-L1's dismissal depends on never existing, and a mutation proved the check blind to it.

    So an upsert is judged on BOTH halves: the insert must supply it and the conflict update must
    assign it.
    """
    text = " ".join(sql.split()).lower()
    if "on conflict" in text:
        insert_half, update_half = text.split("on conflict", 1)
        return "turn_started_at" in insert_half and "turn_started_at =" in update_half
    return "turn_started_at" in text


def _sql_literals(path: pathlib.Path):
    """Every string constant in the module, which is where this service writes its SQL."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a broken module fails elsewhere, loudly
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _modules():
    for path in sorted(SERVICE.rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        yield path


class EveryBusyTurnHasAStartAnchorTests(unittest.TestCase):

    def test_the_scan_finds_the_writer_it_is_about(self):
        """POSITIVE CONTROL. The rule below is satisfied by finding NO writers at all, which is
        exactly what a broken scan returns -- and this repo has shipped that shape before."""
        found = [p for p in _modules() if any(_sql_writes_busy_one(s) for s in _sql_literals(p))]
        self.assertTrue(
            found,
            "no statement writing `turn_busy = 1` was found anywhere in the service. The scan is "
            "not reaching the code, so the rule below proves nothing.",
        )

    def test_EVERY_WRITER_OF_A_BUSY_TURN_ALSO_BINDS_ITS_START(self):
        """The invariant DISP-L1's dismissal actually rests on.

        The ceiling in `turn_liveness_policy` is guarded by `if started_epoch`, so a busy row with no
        anchor would skip it -- and the strict rule does NOT cover that case, whatever the old pin
        said: strict bounds silence, the ceiling bounds duration, and a turn touched every thirty
        seconds is silent for none of it.

        What makes the dismissal true is that such a row is never written.
        """
        offenders = []
        for path in _modules():
            for sql in _sql_literals(path):
                if not _sql_writes_busy_one(sql):
                    continue
                if not _binds_the_anchor(sql):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}: {' '.join(sql.split())[:110]}")
        self.assertEqual(
            offenders, [],
            "a statement sets `turn_busy = 1` without binding `turn_started_at`:\n  "
            + "\n  ".join(offenders)
            + "\n\nAn anchorless busy row skips the 4-hour delivery ceiling -- `if started_epoch` "
            "guards it -- and the strict rule does not cover the gap, because strict bounds SILENCE "
            "and the ceiling bounds DURATION. A turn touched every 30s is never silent. DISP-L1 was "
            "dismissed as a non-defect precisely because no such row is written; this makes one.",
        )

    def test_the_scan_can_tell_a_WRITE_from_a_read_or_a_clear(self):
        """NEGATIVE CONTROL. A scan that matched any mention of `turn_busy` would flag the clear
        (`SET turn_busy = 0`) and the CASE expression that READS it inside the very statement this
        rule is about -- and the rule would then be unsatisfiable, which is how a gate gets deleted
        rather than fixed."""
        self.assertFalse(
            _sql_writes_busy_one(
                "UPDATE agent_turn_state SET turn_busy = 0, turn_updated_at = ? WHERE agent_id = ?"),
            "clearing a turn was read as starting one",
        )
        self.assertFalse(
            _sql_writes_busy_one("SELECT turn_busy, turn_started_at FROM agent_turn_state WHERE agent_id = ?"),
            "a SELECT was read as a write",
        )
        self.assertTrue(
            _sql_writes_busy_one(
                "UPDATE agent_turn_state SET turn_busy = 1, turn_started_at = ? WHERE agent_id = ?"),
            "a real write was not recognised, so the rule above cannot fire",
        )

    def test_MENTIONING_the_anchor_is_not_BINDING_it(self):
        """The blind spot a mutation found in the first version of this gate.

        An upsert that names `turn_started_at` in its INSERT column list but stops assigning it in the
        `ON CONFLICT DO UPDATE SET` clause leaves an existing row flipping to busy with whatever
        anchor it had -- none, if it has never been busy. A substring test passes on that, which is
        the exact anchorless row this file exists to prevent.
        """
        both = ("INSERT INTO agent_turn_state (agent_id, turn_busy, turn_started_at) VALUES (?, 1, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_started_at = excluded.turn_started_at")
        self.assertTrue(_binds_the_anchor(both), "a correct upsert was rejected")
        insert_only = ("INSERT INTO agent_turn_state (agent_id, turn_busy, turn_started_at) VALUES (?, 1, ?) "
                       "ON CONFLICT(agent_id) DO UPDATE SET turn_busy = 1, turn_runtime = excluded.turn_runtime")
        self.assertFalse(
            _binds_the_anchor(insert_only),
            "an upsert that only MENTIONS the anchor in its insert half was accepted. A row that "
            "already exists then flips to busy with no anchor at all.",
        )
