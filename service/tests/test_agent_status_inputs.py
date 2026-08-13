"""`_agent_awaiting_input` and `_agent_config_defect` — the two status inputs, called directly.

NEITHER HAD A TEST THAT CALLED IT. Both were only ever exercised through the status pipeline, so the
suite could tell you the pipeline still returned *a* status but not that these returned the right one.
They moved out of the control plane in v0.5.4 and this is the focused test that move owed.

They matter more than their size suggests. `_agent_config_defect` exists because the two fallthroughs
it feeds used to report a status that quietly PROMISES RECOVERY — a managed identity with nothing to
spawn from said `available` ("just send to it, it will cold-start") and a resident with no wake handle
said `offline` ("not here right now"). Both false, in the direction that costs an operator the most:
they go hunting a delivery bug that does not exist. `_agent_awaiting_input` is the input that makes
`blocked` reachable at all, since derive() returns `blocked` only for `in_turn AND live AND
awaiting_input`.

WHAT THE MOVE PUT AT RISK, and why one case below looks odd. `_agent_config_defect` contains

    runtime = _normalize_runtime(...) if "_normalize_runtime" in globals() else str(...).strip().lower()

`globals()` resolves against the module the function is DEFINED in, so relocating it can silently
change which branch runs — a behaviour change that a byte-identical body would hide completely. The
last test asserts the guard is satisfied in the new home, which is the property the relocation needed
and the one nothing else checks.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core import liveness
from service.api_core.liveness import _agent_awaiting_input, _agent_config_defect


class FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class FakeDb:
    """Minimal async db stand-in. `rows` is returned once; `raises` makes execute blow up."""

    def __init__(self, row=None, raises: bool = False):
        self._row = row
        self._raises = raises
        self.queries: list[str] = []

    async def execute(self, sql, params=()):
        self.queries.append(sql)
        if self._raises:
            raise RuntimeError("database is locked")
        return FakeCursor(self._row)


def run(coro):
    return asyncio.run(coro)


def agent_row(**overrides):
    """A row with every column `_agent_wake_mode` reads.

    Written as a complete row on purpose. My first version passed `{"runtime": ..., "wake_mode": ...}`
    and got a KeyError from three columns down the call chain — a fixture that is a subset of the real
    row tests the fixture, not the function.
    """
    row = {
        "runtime": "claude-code",
        "session_mode": "resident",
        "session_handle": "",
        "capabilities": "[]",
        "runtime_config": "{}",
        "launch_mode": "detached",
    }
    row.update(overrides)
    return row


class AgentConfigDefectTests(unittest.TestCase):
    def test_a_resident_without_a_wake_handle_is_reported_as_a_defect(self):
        defect = run(_agent_config_defect(None, agent_row(), "resident", missing_handle=True))
        self.assertTrue(defect, "a resident with no wake handle can never be started and must say so")
        self.assertIn("wake handle", defect)

    def test_a_resident_WITH_a_handle_is_not_a_defect(self):
        self.assertEqual(
            run(_agent_config_defect(None, agent_row(), "resident", missing_handle=False)),
            "",
            "the check must stay narrow — a startable identity is not misconfigured",
        )

    def test_a_managed_agent_on_an_unlaunchable_runtime_is_a_defect(self):
        defect = run(_agent_config_defect(None, agent_row(runtime="no-such-runtime"), "managed"))
        self.assertIn("cannot be launched", defect)

    def test_a_managed_agent_on_a_launchable_runtime_is_not_a_defect(self):
        self.assertEqual(run(_agent_config_defect(None, agent_row(), "managed")), "")

    def test_a_managed_agent_with_a_blank_runtime_is_a_defect_reported_as_generic(self):
        """CURRENT, and the `(unset)` branch beside it is DEAD — pinned rather than fixed.

        The message reads `runtime {runtime or '(unset)'!r} cannot be launched`, which looks like it
        names a missing runtime specially. It cannot: `_normalize_runtime("")` returns `"generic"`,
        never a falsy value, so the fallback is unreachable and a blank runtime is reported as
        `'generic'`. Not wrong — the agent genuinely cannot start — but it tells the operator the
        runtime is `generic` rather than that it is missing. Changing either half is a behaviour
        change and does not belong in a relocation slice.
        """
        defect = run(_agent_config_defect(None, agent_row(runtime=""), "managed"))
        self.assertIn("cannot be launched", defect)
        self.assertIn("generic", defect)
        self.assertNotIn("unset", defect, "CURRENT: the (unset) fallback is unreachable")

    def test_missing_handle_does_not_flag_a_MANAGED_agent(self):
        """The narrowness that a previous cut of this got wrong: managed agents cold-start, so a
        missing wake handle is not structural for them."""
        self.assertEqual(
            run(_agent_config_defect(None, agent_row(), "managed", missing_handle=True)),
            "",
        )


class AgentAwaitingInputTests(unittest.TestCase):
    def test_no_db_is_not_awaiting(self):
        self.assertFalse(run(_agent_awaiting_input(None, "agent-a")))

    def test_a_failing_query_is_not_awaiting(self):
        """Fails CLOSED. An agent must never be reported blocked because the database was busy."""
        db = FakeDb(raises=True)
        self.assertFalse(run(_agent_awaiting_input(db, "agent-a")))

    def test_no_live_terminal_is_not_awaiting(self):
        self.assertFalse(run(_agent_awaiting_input(FakeDb(row=None), "agent-a")))

    def test_a_non_claude_runtime_is_never_awaiting_even_with_a_prompt_on_screen(self):
        """The deliberate runtime narrowing. Hermes/Codex/Pi terminal output contains the model's own
        prose, where "which option" is ordinary text rather than proof the harness wants an operator."""
        row = {"output": "which option do you want? (y/n)", "cols": 100, "runtime": "hermes"}
        self.assertFalse(run(_agent_awaiting_input(FakeDb(row=row), "agent-a")))

    def test_a_claude_terminal_with_no_prompt_marker_is_not_awaiting(self):
        row = {"output": "just some ordinary build output\nnothing to decide here", "cols": 100, "runtime": "claude-code"}
        self.assertFalse(run(_agent_awaiting_input(FakeDb(row=row), "agent-a")))

    def test_a_claude_terminal_SHOWING_A_PROMPT_is_awaiting(self):
        """ANTI-VACUITY, and the only case here that can fail if the function returns False always.

        Every other case in this class expects False, so a `_agent_awaiting_input` that had been
        broken into `return False` would satisfy all of them. This is the one that proves the pipeline
        actually reaches the hint. The prompt text is one `test_terminal_awaiting_input_hint.py`
        already pins as producing a hint, so a change to the hint rules fails there first, with a
        clearer message than this would give.
        """
        row = {"output": "Overwrite existing file? (y/n) ", "cols": 100, "runtime": "claude-code"}
        self.assertTrue(run(_agent_awaiting_input(FakeDb(row=row), "agent-prompt")))

    def test_the_same_prompt_under_a_DIFFERENT_agent_id_is_not_served_from_cache(self):
        """`_terminal_prompt_hint_from_raw` caches on a key the caller builds from the agent id. If
        that key were ever dropped, one agent's hint would answer for another — the failure mode a
        shared cache always risks, and one no negative case would reveal."""
        prompt = {"output": "Overwrite existing file? (y/n) ", "cols": 100, "runtime": "claude-code"}
        quiet = {"output": "ordinary build output, nothing to decide", "cols": 100, "runtime": "claude-code"}
        self.assertTrue(run(_agent_awaiting_input(FakeDb(row=prompt), "agent-one")))
        self.assertFalse(
            run(_agent_awaiting_input(FakeDb(row=quiet), "agent-two")),
            "a second agent with a quiet terminal must not inherit the first agent's hint",
        )

    def test_it_queries_only_live_terminals_and_excludes_virtual_ones(self):
        """The query shape is load-bearing: a dead terminal's last screen would strand the agent in
        `blocked`, and a virtual RPC terminal has no operator to wait for."""
        db = FakeDb(row=None)
        run(_agent_awaiting_input(db, "agent-a"))
        self.assertEqual(len(db.queries), 1)
        sql = db.queries[0]
        self.assertIn("terminal_sessions", sql)
        self.assertIn("vterm_", sql, "virtual terminals must be excluded")
        for status in ("starting", "attached", "running", "active", "idle", "recovering"):
            self.assertIn(status, sql, f"live status {status!r} must be in the terminal filter")


class RelocationGuardTests(unittest.TestCase):
    def test_the_globals_guard_in_agent_config_defect_is_satisfied_in_its_new_module(self):
        """`_agent_config_defect` branches on `"_normalize_runtime" in globals()`.

        `globals()` is the DEFINING module's namespace, so this is the one property a byte-identical
        relocation cannot preserve by itself. If the name were not bound here the function would
        silently fall back to `str(...).strip().lower()` — no error, no failing route, just a runtime
        string that skips normalisation and an alias like `claude` no longer matching.
        """
        self.assertIn(
            "_normalize_runtime",
            vars(liveness),
            "liveness.py must bind _normalize_runtime at module level or the moved guard changes branch",
        )

    def test_an_alias_runtime_still_normalises(self):
        """The behaviour the guard protects, asserted rather than assumed: if normalisation were
        skipped, a launchable runtime spelled as an alias would be reported as a defect."""
        from service.api_core.runtime import _normalize_runtime

        alias = "claude"
        self.assertEqual(_normalize_runtime(alias), "claude-code", "precondition: this alias normalises")
        self.assertEqual(
            run(_agent_config_defect(None, agent_row(runtime=alias), "managed")),
            "",
            "a launchable runtime written as an alias must not be called a defect",
        )


if __name__ == "__main__":
    unittest.main()
