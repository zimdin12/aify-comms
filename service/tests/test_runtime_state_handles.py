"""The runtime_state handle family, tested directly now that it is reachable.

These three functions spent the series split across the control plane and the agents package, reached
through borrow shims, which is why they had no direct tests: a caller could only exercise them through a
registration or a mode switch. Moving them to a leaf is what makes the asymmetry below assertable.

WHAT IS WORTH GUARDING is not that a dict gets a key. It is that codex addresses a conversation by
`threadId` and every other runtime by `sessionId`. That is a wire-format fact about each runtime's resume
call, so a reasonable-looking cleanup that unified the key would break codex resume and nothing here
would have said so.
"""

from __future__ import annotations

import json
import unittest

from service.api_core.runtime_state import (
    _runtime_handle_from_state,
    _runtime_state_replacing_handle,
    _runtime_state_with_handle,
)


class RuntimeStateWithHandleTests(unittest.TestCase):
    def test_codex_writes_threadId_and_everything_else_writes_sessionId(self):
        self.assertEqual({"threadId": "t-1"}, _runtime_state_with_handle("codex", {}, "t-1"))
        for runtime in ("claude-code", "hermes", "pi", "generic", ""):
            self.assertEqual(
                {"sessionId": "s-1"}, _runtime_state_with_handle(runtime, {}, "s-1"),
                f"{runtime!r} must address a session by sessionId",
            )

    def test_an_empty_handle_leaves_the_state_untouched(self):
        """A blank handle must not write an empty key that later reads as a real handle."""
        for handle in ("", "   ", None):
            self.assertEqual({"a": 1}, _runtime_state_with_handle("codex", {"a": 1}, handle))

    def test_it_does_not_mutate_the_caller_state(self):
        """Callers pass a row's decoded state; mutating it in place would leak into unrelated writes."""
        original = {"a": 1}
        result = _runtime_state_with_handle("codex", original, "t-1")
        self.assertEqual({"a": 1}, original)
        self.assertIsNot(original, result)

    def test_a_json_string_is_decoded_rather_than_rejected(self):
        """runtime_state arrives as TEXT from SQLite on some paths and as a dict on others."""
        self.assertEqual(
            {"a": 1, "sessionId": "s-1"},
            _runtime_state_with_handle("hermes", json.dumps({"a": 1}), "s-1"),
        )

    def test_unparseable_state_degrades_to_empty_rather_than_raising(self):
        self.assertEqual({"sessionId": "s-1"}, _runtime_state_with_handle("hermes", "not json", "s-1"))


class RuntimeHandleFromStateTests(unittest.TestCase):
    def test_codex_prefers_threadId_then_sessionId(self):
        self.assertEqual("t-1", _runtime_handle_from_state("codex", {"threadId": "t-1"}))
        self.assertEqual("s-1", _runtime_handle_from_state("codex", {"sessionId": "s-1"}))
        self.assertEqual(
            "t-1", _runtime_handle_from_state("codex", {"threadId": "t-1", "sessionId": "s-1"}),
            "when both are present codex must resume by threadId",
        )

    def test_pi_and_hermes_have_their_own_fallback_keys(self):
        self.assertEqual("f-1", _runtime_handle_from_state("pi", {"sessionFile": "f-1"}))
        self.assertEqual("k-1", _runtime_handle_from_state("hermes", {"sessionKey": "k-1"}))

    def test_a_missing_handle_is_the_empty_string_not_None(self):
        """Callers concatenate and .strip() this; None would raise on a path that has no handle yet."""
        for state in ({}, None, "", "not json"):
            self.assertEqual("", _runtime_handle_from_state("claude-code", state))

    def test_round_trip_through_with_handle(self):
        for runtime in ("codex", "claude-code", "hermes", "pi"):
            state = _runtime_state_with_handle(runtime, {}, "h-1")
            self.assertEqual("h-1", _runtime_handle_from_state(runtime, state), runtime)


class RuntimeStateReplacingHandleTests(unittest.TestCase):
    def test_it_drops_the_other_runtime_s_key_instead_of_leaving_both(self):
        """A codex->claude switch that left threadId behind would resume the wrong conversation."""
        result = _runtime_state_replacing_handle("claude-code", {"threadId": "old"}, "s-new")
        self.assertEqual({"sessionId": "s-new"}, result)

    def test_it_keeps_unrelated_state(self):
        result = _runtime_state_replacing_handle("codex", {"appServerUrl": "u", "sessionId": "old"}, "t-1")
        self.assertEqual({"appServerUrl": "u", "threadId": "t-1"}, result)

    def test_replacing_with_a_blank_handle_clears_the_old_one(self):
        """Otherwise clearing a handle would silently keep pointing at a dead session."""
        self.assertEqual({}, _runtime_state_replacing_handle("codex", {"threadId": "old"}, ""))


if __name__ == "__main__":
    unittest.main()
