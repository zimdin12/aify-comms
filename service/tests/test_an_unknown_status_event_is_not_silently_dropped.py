"""An event kind this service does not apply is ACCEPTED, and says so.

THE SHAPE. `POST /agents/{id}/status-event` takes `kind: str`, unconstrained. `_apply_status_event`
writes `last_event = kind` into `agent_status_state` whatever it is. `apply_event` understood four
kinds and returned the state unchanged for anything else. So a kind this service has never heard of
produced `{"ok": true}`, a row recording it, no state change, and no signal anywhere. Finding that out
meant reading `apply_event`'s if-chain.

WHY TOLERANCE IS RIGHT AND SILENCE IS NOT. A bridge is operator-launched and may run a NEWER version
than the service -- this repo already reasons that way about resident bridges running "a MIXED bridge
version". Rejecting an unrecognised kind with a 400 would make a newer bridge fail against an older
service, which is worse than ignoring it. What was missing is the difference between "we did what you
asked" and "we filed your request": `applied` in the response, and one warning naming the kind and the
vocabulary.

MEASURED on the live database before changing anything: `agent_status_state.last_event` holds only
`turn_start` (7 rows) and `turn_end` (14), both handled. So this is a gap, not an incident -- the
guard is for the next kind somebody adds to a bridge, which is exactly when nobody will be reading
this file.

THE VOCABULARY IS NOW THE HANDLER TABLE'S KEYS, not a second list beside it. "Derive allowed values,
never list them" -- and an engine whose whole job is agreeing with itself about what an agent is doing
is the last place to keep two copies of what an event can be.
"""

from __future__ import annotations

import unittest

from service.status_engine import KNOWN_EVENT_KINDS, apply_event, is_known_event_kind


class KnownEventKindsTests(unittest.TestCase):
    def test_the_vocabulary_is_not_empty(self):
        """Anti-vacuity: an empty table would make every kind unknown and every test below pass for
        the wrong reason."""
        self.assertGreaterEqual(len(KNOWN_EVENT_KINDS), 4, KNOWN_EVENT_KINDS)

    def test_every_declared_kind_actually_changes_the_state(self):
        """A kind in the table that folds to nothing is the dead-knob defect one layer in: it would
        report `applied: true` and do nothing, which is worse than reporting false.

        Each kind is given the state its OPPOSITE would leave, so a handler that is a no-op cannot
        hide behind a state that already matched.
        """
        opposites = {
            "turn_start": {"in_turn": 0, "turn_run_id": "", "awaiting_input": 1},
            "turn_end": {"in_turn": 1, "turn_run_id": "r1", "awaiting_input": 1},
            "blocked": {"in_turn": 1, "turn_run_id": "r1", "awaiting_input": 0},
            "unblocked": {"in_turn": 1, "turn_run_id": "r1", "awaiting_input": 1},
        }
        for kind in KNOWN_EVENT_KINDS:
            before = opposites.get(kind)
            self.assertIsNotNone(before, f"{kind} is declared but this test has no case for it")
            after = apply_event(before, {"kind": kind, "runId": "r2"})
            self.assertNotEqual(after, before, f"{kind} is in the table and changes nothing")

    def test_is_known_agrees_with_the_table(self):
        for kind in KNOWN_EVENT_KINDS:
            self.assertTrue(is_known_event_kind(kind))
        for kind in ("turn_started", "TURN_START", "", "blocked ", "zz_no_such_kind"):
            self.assertFalse(is_known_event_kind(kind), f"{kind!r} was accepted as known")

    def test_a_near_miss_is_NOT_known(self):
        """The case this exists for. `turn_started` is what somebody types when they mean
        `turn_start`, and it used to be indistinguishable from success."""
        self.assertFalse(is_known_event_kind("turn_started"))
        state = {"in_turn": 0, "turn_run_id": "", "awaiting_input": 0}
        self.assertEqual(apply_event(state, {"kind": "turn_started", "runId": "r1"}), state)

    def test_None_and_missing_kinds_are_handled(self):
        """The endpoint's model allows `kind` to arrive as anything a JSON body can hold."""
        for kind in (None, 0, [], {}):
            self.assertFalse(is_known_event_kind(kind))
        state = {"in_turn": 1, "turn_run_id": "r1", "awaiting_input": 0}
        self.assertEqual(apply_event(state, {}), state)

    def test_apply_event_never_mutates_its_input(self):
        """The caller reads the OLD row to build `cur` and writes the returned dict; mutating in place
        would make the two indistinguishable and hide a handler that did nothing."""
        state = {"in_turn": 0, "turn_run_id": "", "awaiting_input": 0}
        snapshot = dict(state)
        apply_event(state, {"kind": "turn_start", "runId": "r1"})
        self.assertEqual(state, snapshot)

    def test_turn_start_records_the_run_and_clears_a_stale_block(self):
        """Behaviour preserved through the if-chain -> table rewrite, asserted rather than assumed."""
        after = apply_event({"in_turn": 0, "turn_run_id": "", "awaiting_input": 1},
                            {"kind": "turn_start", "runId": "run-9"})
        self.assertEqual(after, {"in_turn": 1, "turn_run_id": "run-9", "awaiting_input": 0})

    def test_turn_end_clears_the_run_and_the_block(self):
        after = apply_event({"in_turn": 1, "turn_run_id": "run-9", "awaiting_input": 1},
                            {"kind": "turn_end"})
        self.assertEqual(after, {"in_turn": 0, "turn_run_id": "", "awaiting_input": 0})

    def test_blocked_and_unblocked_touch_only_awaiting_input(self):
        """They must not end the turn. A blocked agent is still mid-turn -- that is what makes
        `blocked` a distinct status from `available` rather than a flavour of idle."""
        mid = {"in_turn": 1, "turn_run_id": "run-9", "awaiting_input": 0}
        blocked = apply_event(mid, {"kind": "blocked"})
        self.assertEqual(blocked, {"in_turn": 1, "turn_run_id": "run-9", "awaiting_input": 1})
        self.assertEqual(apply_event(blocked, {"kind": "unblocked"}), mid)

    def test_an_unknown_kind_leaves_every_field_alone(self):
        """Not merely 'returns something' -- returns the SAME state. A partial fold would be worse
        than no fold, because it would be invisible AND wrong."""
        state = {"in_turn": 1, "turn_run_id": "run-9", "awaiting_input": 1}
        self.assertEqual(apply_event(state, {"kind": "hibernate", "runId": "run-2"}), state)


if __name__ == "__main__":
    unittest.main()
