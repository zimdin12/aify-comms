"""`_row_capabilities` — what an agent can do RIGHT NOW, not what it could do when it registered.

The `capabilities` column is written once, at registration, from `_default_capabilities_for`. This
function is the read-time correction, and it exists because the row's own runtime_config can move
underneath that column: a hermes agent whose gateway went away still has `steer` stored, and
advertising it sends a steer down a socket that is not there. Every branch below is one of those
corrections, so the shape is deliberately asymmetric — some runtimes have capabilities STRIPPED
because the stored list over-promises, and some have them ADDED because it under-promises.

It is read by eight non-test modules (claim gating, the dispatch hint, dispatch runs, execution
mode, liveness, the agent record, send preflight, status inputs), which is why it lives in
`capabilities.py` rather than following any one of them. Coverage before this file was hermes (four
cases) and claude resident (two); pi, opencode, the runtimes that pass through untouched, and every
malformed-row path had none.

WHAT THIS FILE DOES NOT ASSERT is that the stored column was right in the first place — that is
`_default_capabilities_for`'s contract and `test_default_capabilities_adapter.py`'s job. Here the
stored list is an INPUT, and it is deliberately seeded wrong in most tests, because a stored list
that already agrees with the answer cannot tell you whether the function did anything.
"""

from __future__ import annotations

import json
import unittest

from service.api_core.capabilities import _row_capabilities

ALL_CAPS = ["resident-run", "managed-run", "resume", "interrupt", "steer", "spawn"]

LIVE_GATEWAY = {"gatewayUrl": "ws://127.0.0.1:9000/api/ws"}


def row(*, runtime="generic", session_mode="resident", caps=(), runtime_config=None, **overrides):
    """A row as sqlite hands it over: JSON text in the two blob columns."""
    base = {
        "id": "agent-x",
        "capabilities": json.dumps(list(caps)),
        "runtime": runtime,
        "session_mode": session_mode,
        "session_handle": "handle-1",
        "runtime_config": json.dumps(runtime_config or {}),
    }
    base.update(overrides)
    return base


class NoRowTests(unittest.TestCase):
    def test_no_row_has_no_capabilities(self):
        """Callers pass the result of a lookup that can miss. `None` must answer "can do nothing",
        not raise — a missing agent is the most common reason to ask."""
        self.assertEqual(_row_capabilities(None), [])

    def test_an_empty_row_has_no_capabilities(self):
        self.assertEqual(_row_capabilities({}), [])


class MalformedColumnTests(unittest.TestCase):
    def test_capabilities_that_are_not_json_read_as_none_at_all(self):
        """A truncated write or a hand-edited row. Failing open here would advertise every
        capability the runtime branch happens to add."""
        self.assertEqual(_row_capabilities(row(capabilities="{not json")), [])

    def test_a_null_capabilities_column_reads_as_none_at_all(self):
        self.assertEqual(_row_capabilities(row(capabilities=None)), [])

    def test_a_row_without_a_runtime_column_is_treated_as_GENERIC(self):
        """Not every caller selects the whole agent row, so the optional columns are read through a
        `in row.keys()` guard — a narrow SELECT must not raise. The fallback has to be a runtime
        with no branch: defaulting to a real one (claude-code, hermes) would apply that runtime's
        correction to every row that was selected narrowly.

        Seeded with the three capabilities every strip removes, because a stored list that no
        branch would touch cannot tell a generic fallback from a claude one."""
        stored = ["resident-run", "resume", "interrupt", "steer"]
        self.assertEqual(
            _row_capabilities({"id": "a", "capabilities": json.dumps(stored)}), stored,
        )

    def test_a_row_without_a_session_mode_column_is_treated_as_RESIDENT(self):
        """Resident is the conservative default: it strips in-place driving rather than granting
        it. Defaulting to managed would hand `managed-run` to a row nobody said was managed."""
        narrow = {"id": "a", "runtime": "pi", "capabilities": json.dumps(["resident-run", "resume"])}
        self.assertEqual(_row_capabilities(narrow), ["resume"])

    def test_a_null_runtime_config_does_not_stop_the_runtime_branches(self):
        """`None` config is not the same as an absent column, and hermes reads it for the gateway
        url. A resident hermes with no config at all is a hermes with no gateway."""
        caps = _row_capabilities(row(runtime="hermes", session_mode="resident",
                                     caps=ALL_CAPS, runtime_config=None))
        self.assertNotIn("resident-run", caps)


class PassThroughRuntimeTests(unittest.TestCase):
    """Runtimes with no read-time correction: the stored column IS the answer."""

    def test_codex_is_returned_untouched(self):
        stored = ["managed-run", "resume", "interrupt", "steer", "spawn"]
        self.assertEqual(_row_capabilities(row(runtime="codex", session_mode="managed",
                                               caps=stored)), stored)

    def test_an_unknown_runtime_is_returned_untouched(self):
        """No branch claims it, so nothing is corrected. The alternative — stripping what is not
        recognised — would silently disable a runtime added on the bridge side first."""
        self.assertEqual(_row_capabilities(row(runtime="something-new", caps=["resume"])), ["resume"])

    def test_managed_claude_keeps_everything_including_steer(self):
        """Managed claude queues injects safely, so the claude correction is resident-only."""
        stored = ["managed-run", "resume", "interrupt", "steer", "spawn"]
        self.assertEqual(_row_capabilities(row(runtime="claude-code", session_mode="managed",
                                               caps=stored)), stored)

    def test_the_stored_ORDER_survives(self):
        """Some callers show this list to an operator. Re-ordering it on every read would make a
        stable agent look like it is changing."""
        stored = ["spawn", "resume", "managed-run"]
        self.assertEqual(_row_capabilities(row(runtime="codex", session_mode="managed",
                                               caps=stored)), stored)


class PiTests(unittest.TestCase):
    def test_a_resident_pi_agent_cannot_be_driven_in_place(self):
        """Pi speaks single-client RPC: there is no way to reach a resident pi session from the
        server, so `resident-run`, `interrupt` and `steer` are stripped however they got stored.
        A pi agent that registers resident is flipped to managed — this is the state it holds in
        between, and dispatching to it on the strength of a stored `resident-run` strands the run."""
        caps = _row_capabilities(row(runtime="pi", session_mode="resident", caps=ALL_CAPS))
        self.assertEqual(caps, ["managed-run", "resume", "spawn"])

    def test_a_managed_pi_agent_gets_the_full_managed_set_added(self):
        """The other direction: a pi row that registered before the managed path existed still
        under-promises, and pi managed is fully driveable."""
        caps = _row_capabilities(row(runtime="pi", session_mode="managed", caps=[]))
        self.assertEqual(set(caps), {"managed-run", "resume", "interrupt", "steer", "spawn"})

    def test_a_managed_pi_agent_is_not_given_the_same_capability_twice(self):
        caps = _row_capabilities(row(runtime="pi", session_mode="managed",
                                     caps=["managed-run", "steer"]))
        self.assertEqual(len(caps), len(set(caps)), caps)

    def test_a_managed_pi_agent_keeps_a_capability_no_branch_knows_about(self):
        caps = _row_capabilities(row(runtime="pi", session_mode="managed", caps=["something-else"]))
        self.assertIn("something-else", caps)


class OpencodeTests(unittest.TestCase):
    def test_a_resident_opencode_agent_cannot_be_driven_in_place(self):
        caps = _row_capabilities(row(runtime="opencode", session_mode="resident", caps=ALL_CAPS))
        self.assertEqual(caps, ["managed-run", "resume", "spawn"])

    def test_a_managed_opencode_agent_is_left_alone(self):
        """The strip is resident-only — there is no managed branch for opencode, so its stored
        column stands. Seeded WITH `interrupt` and `steer`: without the two capabilities the strip
        would take, this test passes just as well against a strip that ignores session_mode."""
        stored = ["managed-run", "resume", "interrupt", "steer", "spawn"]
        self.assertEqual(_row_capabilities(row(runtime="opencode", session_mode="managed",
                                               caps=stored)), stored)


class HermesTests(unittest.TestCase):
    """Hermes is the runtime the whole read-time correction was built for: its delivery path is
    decided by runtime_config, and that config changes without the capabilities column changing."""

    def test_managed_hermes_with_a_channel_gains_steer(self):
        caps = _row_capabilities(row(runtime="hermes", session_mode="managed", caps=[],
                                     runtime_config={"channelEnabled": True}))
        self.assertEqual(set(caps), {"managed-run", "resume", "interrupt", "steer", "spawn"})

    def test_managed_hermes_WITHOUT_a_channel_loses_a_stored_steer(self):
        """The single-client ACP fallback rejects a concurrent session/prompt, so a steer sent on
        the strength of a stale stored capability does not queue — it errors mid-turn."""
        caps = _row_capabilities(row(runtime="hermes", session_mode="managed", caps=ALL_CAPS,
                                     runtime_config={}))
        self.assertNotIn("steer", caps)
        self.assertEqual(set(caps) - {"resident-run"},
                         {"managed-run", "resume", "interrupt", "spawn"})

    def test_resident_hermes_with_a_live_gateway_gains_the_resident_set(self):
        caps = _row_capabilities(row(runtime="hermes", session_mode="resident", caps=[],
                                     runtime_config=LIVE_GATEWAY))
        self.assertEqual(set(caps), {"resident-run", "resume", "interrupt", "steer"})

    def test_resident_hermes_with_NO_gateway_loses_the_resident_set(self):
        caps = _row_capabilities(row(runtime="hermes", session_mode="resident", caps=ALL_CAPS,
                                     runtime_config={}))
        self.assertEqual(caps, ["managed-run", "resume", "spawn"])

    def test_a_gateway_url_that_is_not_a_websocket_is_not_a_gateway(self):
        """`http://` is what a half-configured host writes. The gateway is a websocket; a scheme
        check is the only thing standing between that and a resident dispatch into nothing."""
        caps = _row_capabilities(row(runtime="hermes", session_mode="resident", caps=ALL_CAPS,
                                     runtime_config={"gatewayUrl": "http://127.0.0.1:9000"}))
        self.assertNotIn("resident-run", caps)

    def test_an_empty_gateway_url_is_not_a_gateway(self):
        caps = _row_capabilities(row(runtime="hermes", session_mode="resident", caps=ALL_CAPS,
                                     runtime_config={"gatewayUrl": "   "}))
        self.assertNotIn("resident-run", caps)

    def test_a_secure_gateway_url_counts(self):
        caps = _row_capabilities(row(runtime="hermes", session_mode="resident", caps=[],
                                     runtime_config={"gatewayUrl": "WSS://gateway.example/api/ws"}))
        self.assertIn("resident-run", caps)


class ClaudeResidentTests(unittest.TestCase):
    def test_a_resident_claude_agent_with_its_channel_open_gains_the_resident_set(self):
        caps = _row_capabilities(row(runtime="claude-code", session_mode="resident",
                                     caps=["resume"], runtime_config={"channelEnabled": True}))
        self.assertEqual(set(caps), {"resume", "resident-run", "interrupt", "steer"})

    def test_a_resident_claude_agent_with_no_channel_loses_the_resident_set(self):
        """`channelEnabled` is the MCP channel the bridge holds open. Without it there is nothing
        listening, and a resident dispatch waits for a wake that cannot arrive."""
        caps = _row_capabilities(row(runtime="claude-code", session_mode="resident", caps=ALL_CAPS,
                                     runtime_config={}))
        self.assertEqual(caps, ["managed-run", "resume", "spawn"])

    def test_the_channel_flag_must_be_the_BOOLEAN_true(self):
        """`is True`, not truthiness. A config round-tripped through a shell or a form arrives as
        the string "true", and a string is not evidence that a channel is open."""
        for value in ("true", 1, "yes", [1]):
            with self.subTest(value=value):
                caps = _row_capabilities(row(runtime="claude-code", session_mode="resident",
                                             caps=ALL_CAPS,
                                             runtime_config={"channelEnabled": value}))
                self.assertNotIn("resident-run", caps)


if __name__ == "__main__":
    unittest.main()
