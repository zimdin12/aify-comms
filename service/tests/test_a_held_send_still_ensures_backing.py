"""SW4: a send-time hold withholds DELIVERY and never skips BACKING (hard-switch plan, page 1, the ordering rule).

`_launch_recipients_for_dispatch` is driven directly, with every collaborator replaced, so only the hold decides.
Each witness runs beside its twin: the same recipient with the hold off. A held recipient must reach the same backing
step as its twin (a cold start, a console start), and must never be put on a console, which is the only delivery this
path makes. The run itself is created by the caller and waits for the claim gate in both cases; that is not decided
here.

After the 0.9.8 switch the hold reads the operational view: unknown eligibility holds whatever the queue choice, and
with eligibility known an explicit queue waits for an occupied run or aify-env's busy. The view is replaced here by
the one each case names (`env`), since this loop is driven with a stand-in database.
"""
from __future__ import annotations

import asyncio
import types
import unittest
from unittest import mock

from service.api_core import dispatch_launch, operational_view, switched_view

TERMINAL = {"terminal_id": "term-1", "environment_id": "env-1", "bridge_id": "bridge-1"}


class _Db:
    """Answers the one query this loop makes itself: the recipient's agent row."""

    def __init__(self, row):
        self._row = row

    async def execute(self, *_args, **_kwargs):
        row = self._row

        class _Cursor:
            async def fetchone(self):
                return row

        return _Cursor()


class AHeldSendStillEnsuresBacking(unittest.TestCase):

    def _run(self, *, runtime, execution_mode, held, settings, live_terminal, env="idle"):
        """`env`: what aify-env's record says, 'idle' or 'busy', or 'unknown' for eligibility comms cannot read."""
        calls = {"cold": [], "pty": []}

        async def cold(db, agent_id, **kw):
            calls["cold"].append(agent_id)
            return True

        async def pty(db, agent_id, **kw):
            calls["pty"].append(agent_id)
            return dict(TERMINAL) if live_terminal else None

        async def active(db, agent_id, **kw):
            return dict(TERMINAL) if live_terminal else None

        async def no(*_a, **_k):
            return False

        async def passthrough(db, row, **kw):
            return row, None

        async def dispatch_state(db, agent_id):
            # The hold's run terms.
            return {"hasActiveRun": held, "queuedRuns": 0}

        def view(_row, binding=None, run_occupied=False):
            known = env != "unknown"
            busy = {"idle": False, "busy": True}.get(env)
            return operational_view.View("unknown" if not known else ("working" if busy else "online"), None, True,
                                         "known" if known else "unknown", busy, busy if known else None, False)

        patches = {
            "_coldstart_spawn_request_for_dispatch": cold,
            "_ensure_managed_pty_for_dispatch": pty,
            "_active_terminal_for_agent": active,
            "_has_live_managed_wrapper_child": no,
            "_has_claimable_spawn_request": no,
            "_auto_return_resident_to_managed_if_possible": passthrough,
            "_get_dispatch_state_for_agent": dispatch_state,
        }
        for name, value in patches.items():
            patcher = mock.patch.object(dispatch_launch, name, new=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(switched_view, "eligibility_view", new=view)
        patcher.start()
        self.addCleanup(patcher.stop)

        console, not_started, failed = {}, [], set()
        req = types.SimpleNamespace(queueIfBusy=True, from_agent="dashboard")
        row = {"id": "agent-1", "runtime": runtime}
        asyncio.run(dispatch_launch._launch_recipients_for_dispatch(
            failed, console, _Db(row), [("agent-1", execution_mode)], not_started, req, settings))
        return calls, console

    def test_a_held_recipient_with_no_backing_is_cold_started_like_its_twin(self):
        """Channel-managed claude with no live claimer and no spawn request: the hold must not skip the cold start."""
        settings = {"insert_messages_via_console": False, "managed_terminal_backing_enabled": True}
        held, _ = self._run(runtime="claude-code", execution_mode="channel", held=True, settings=settings,
                            live_terminal=False)
        twin, _ = self._run(runtime="claude-code", execution_mode="channel", held=False, settings=settings,
                            live_terminal=False)
        self.assertTrue(twin["cold"], "the control: the twin with no hold is cold-started")
        self.assertEqual(held, twin, "a held send must still ensure backing, exactly as its twin does")

    def test_a_held_recipient_is_never_put_on_a_console(self):
        """A console-delivered recipient with a live terminal: the hold withholds the console, the twin gets it."""
        settings = {"insert_messages_via_console": True, "managed_terminal_backing_enabled": True}
        _, held = self._run(runtime="generic", execution_mode="", held=True, settings=settings, live_terminal=True)
        _, twin = self._run(runtime="generic", execution_mode="", held=False, settings=settings, live_terminal=True)
        self.assertEqual(twin, {"agent-1": TERMINAL}, "the control: the twin is delivered on its console")
        self.assertEqual(held, {}, "a held send must deliver nothing")

    def test_a_recipient_held_only_by_aify_envs_busy_is_never_put_on_a_console(self):
        """No active run and no queued run: aify-env's busy alone holds. Its twin is the same recipient known idle."""
        settings = {"insert_messages_via_console": True, "managed_terminal_backing_enabled": True}
        _, held = self._run(runtime="generic", execution_mode="", held=False, settings=settings, live_terminal=True,
                            env="busy")
        _, twin = self._run(runtime="generic", execution_mode="", held=False, settings=settings, live_terminal=True)
        self.assertEqual(twin, {"agent-1": TERMINAL}, "the control: the unheld twin is delivered on its console")
        self.assertEqual(held, {}, "a send held only by aify-env's busy must deliver nothing")

    def test_an_unknown_recipient_is_never_put_on_a_console_and_is_still_cold_started(self):
        """Page 1: every delivery route checks eligibility first, so unknown puts nothing on a console; and the
        ordering rule: an unknown agent with no backing is still started, or its record would never come."""
        console_settings = {"insert_messages_via_console": True, "managed_terminal_backing_enabled": True}
        _, held = self._run(runtime="generic", execution_mode="", held=False, settings=console_settings,
                            live_terminal=True, env="unknown")
        _, twin = self._run(runtime="generic", execution_mode="", held=False, settings=console_settings,
                            live_terminal=True)
        self.assertEqual(twin, {"agent-1": TERMINAL}, "the control: the known idle twin is delivered on its console")
        self.assertEqual(held, {}, "an unknown recipient must be put on no console")
        channel_settings = {"insert_messages_via_console": False, "managed_terminal_backing_enabled": True}
        unknown, _ = self._run(runtime="claude-code", execution_mode="channel", held=False, settings=channel_settings,
                               live_terminal=False, env="unknown")
        twin, _ = self._run(runtime="claude-code", execution_mode="channel", held=False, settings=channel_settings,
                            live_terminal=False)
        self.assertTrue(twin["cold"], "the control: the known idle twin with no backing is cold-started")
        self.assertEqual(unknown, twin, "an unknown recipient with no backing is started exactly as its twin is")


if __name__ == "__main__":
    unittest.main()
