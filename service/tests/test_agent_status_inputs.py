"""`_agent_config_defect`, called directly: the reason comms gives beside aify-env's `misconfigured`.

It reaches the served note through `switched_view.misconfigured_note`. It exists because the two fallthroughs it
fed used to report a status that quietly PROMISED RECOVERY: a managed identity with nothing to spawn from said
`available` and a resident with no wake handle said `offline`. Its sibling `_agent_awaiting_input` went in 0.9.9 S3b
with the legacy derivation, the only thing that read it.

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

from service.api_core.liveness import _agent_config_defect


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


class RelocationGuardTests(unittest.TestCase):
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
