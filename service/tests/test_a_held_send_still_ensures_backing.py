"""SW4: a send-time hold withholds DELIVERY and never skips BACKING (hard-switch plan, page 1, the ordering rule).

`_launch_recipients_for_dispatch` is driven directly, with every collaborator replaced, so only the hold decides.
Each witness runs beside its twin: the same recipient with the hold off. A held recipient must reach the same backing
step as its twin (a cold start, a console start), and must never be put on a console, which is the only delivery this
path makes. The run itself is created by the caller and waits for the claim gate in both cases; that is not decided
here.
"""
from __future__ import annotations

import asyncio
import types
import unittest
from unittest import mock

from service.api_core import dispatch_launch

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

    def _run(self, *, runtime, execution_mode, held, settings, live_terminal):
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
            # The hold's first term, an active run; the turn term is left out (decision None) so it cannot decide.
            return {"hasActiveRun": held, "queuedRuns": 0}

        async def no_decision(db, agent_id):
            return None

        patches = {
            "_coldstart_spawn_request_for_dispatch": cold,
            "_ensure_managed_pty_for_dispatch": pty,
            "_active_terminal_for_agent": active,
            "_has_live_managed_wrapper_child": no,
            "_has_claimable_spawn_request": no,
            "_auto_return_resident_to_managed_if_possible": passthrough,
            "_get_dispatch_state_for_agent": dispatch_state,
            "turn_busy_decision": no_decision,
        }
        for name, value in patches.items():
            patcher = mock.patch.object(dispatch_launch, name, new=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        observe = mock.patch.object(dispatch_launch.busy_consumers, "observe_queue", new=lambda *a, **k: None)
        observe.start()
        self.addCleanup(observe.stop)

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


if __name__ == "__main__":
    unittest.main()
