"""Pushing a status change to dashboards — and the two ways a push can lie.

`service/api_core/status_broadcast.py` is named by no test file. Its own docstring explains why the
pushes exist: the reconcile sweep runs every 60 seconds, so without a push an operator-driven
transition is invisible for up to a minute after the click, which reads as the click not working.

THE PUSH IS ONLY WORTH ANYTHING IF IT AGREES WITH THE NEXT POLL. That is the whole design constraint
and the source of both failure modes here, neither of which raises:

  * a push that serves a DIFFERENT answer than the read would — the dashboard flips to one status on
    the click and back to another a minute later, and the operator cannot tell which is true;
  * a push that overrides a MANUAL status — an operator explicitly stopped the agent, and a
    derivation quietly undoes it in the UI while the persisted row still says stopped.

There are two functions because there are two status paths, and they are deliberately not merged:
each pushes exactly what its corresponding READ returns.

BEST-EFFORT MEANS SWALLOWING EVERYTHING, which is correct — the state change already happened and
the next poll will show it — and it is also what makes every bug here silent. The tests below
therefore assert what was PUSHED, never that a call returned.

AND IT MAKES THREE OF THE EARLY GUARDS UNOBSERVABLE FROM OUTSIDE, which is worth stating rather than
leaving as apparent coverage. Deleting `if ws is None: return`, `if not agent_id: return` or
`if not row: return` from the polled path changes nothing a caller can see: the work that follows
raises, the outer `except Exception: pass` eats it, and the result is the same silence the guard
produced deliberately. Mutations that remove them survive this suite. They are still worth keeping —
a guard that states the precondition beats an exception that happens to have the same effect — but
no test here can distinguish them, and pretending otherwise would be the false green this file
exists to prevent.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import aiosqlite

from service.api_core import status_broadcast
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "sb-agent"


class RecordingWs:
    """Only what these functions touch."""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, dict]] = []
        self._fail = fail

    async def broadcast(self, event: str, payload: dict) -> None:
        if self._fail:
            raise RuntimeError("the socket went away mid-push")
        self.sent.append((event, payload))


class StatusBroadcastTestCase(FastApiTestCase):
    DB_NAME = "aify-status-broadcast-test.db"

    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/agents", json={"agentId": AGENT, "role": "coder"})
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _push(self, fn, agent_id: str = AGENT, *, ws=None, **kwargs) -> RecordingWs:
        recorder = ws if ws is not None else RecordingWs()

        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await fn(recorder, db, agent_id, **kwargs)

        asyncio.run(run())
        return recorder

    def _only_payload(self, recorder: RecordingWs) -> dict:
        self.assertEqual(len(recorder.sent), 1, recorder.sent)
        event, payload = recorder.sent[0]
        self.assertEqual(event, "agent_status")
        return payload


class SharedContractTests(StatusBroadcastTestCase):
    """Both functions, same rules. Parametrised so a new one cannot quietly skip them."""

    FUNCTIONS = ("_broadcast_agent_status", "_broadcast_engine_status")

    def test_a_push_names_the_agent_and_carries_a_status(self):
        for name in self.FUNCTIONS:
            with self.subTest(function=name):
                payload = self._only_payload(self._push(getattr(status_broadcast, name)))
                self.assertEqual(payload["agentId"], AGENT)
                self.assertIn("status", payload)
                self.assertIn("statusNote", payload)

    def test_NO_WEBSOCKET_is_not_an_error(self):
        """`_get_ws` returns None when the app has no connection manager — a headless deployment,
        or a test app. Every caller invokes these unconditionally, so None has to be a no-op."""
        async def run(fn):
            async with aiosqlite.connect(self._db_path) as db:
                await fn(None, db, AGENT)

        for name in self.FUNCTIONS:
            with self.subTest(function=name):
                asyncio.run(run(getattr(status_broadcast, name)))

    def test_a_BLANK_agent_id_pushes_nothing(self):
        """A push with no subject would reach every dashboard and match no row in any of them."""
        for name in self.FUNCTIONS:
            for agent_id in ("", "   ", None):
                with self.subTest(function=name, agent_id=agent_id):
                    recorder = self._push(getattr(status_broadcast, name), agent_id)
                    self.assertEqual(recorder.sent, [])

    def test_an_UNKNOWN_agent_pushes_nothing(self):
        """Callers fire these after a delete as readily as after a stop. Pushing a status for a row
        that is gone would resurrect the agent in every open dashboard until its next refetch."""
        for name in self.FUNCTIONS:
            with self.subTest(function=name):
                self.assertEqual(self._push(getattr(status_broadcast, name), "nobody").sent, [])

    def test_a_FAILING_SOCKET_does_not_raise_into_the_caller(self):
        """These are called after the state change has already been committed. Raising here would
        turn a successful stop into a 500 the operator retries."""
        for name in self.FUNCTIONS:
            with self.subTest(function=name):
                self._push(getattr(status_broadcast, name), ws=RecordingWs(fail=True))

    def test_a_BROKEN_DATABASE_does_not_raise_into_the_caller(self):
        """Same reasoning one layer down, and the reason both bodies are wrapped rather than just
        the broadcast call."""
        class ExplodingDb:
            async def execute(self, *args, **kwargs):
                raise RuntimeError("no such table: agents")

        async def run(fn):
            await fn(RecordingWs(), ExplodingDb(), AGENT)

        for name in self.FUNCTIONS:
            with self.subTest(function=name):
                asyncio.run(run(getattr(status_broadcast, name)))


class ManualStatusTests(StatusBroadcastTestCase):
    """An operator's explicit status outranks anything derived — in BOTH paths."""

    def _stop(self, note: str = "stopped by the operator") -> None:
        self._write("UPDATE agents SET status = 'stopped', status_note = ? WHERE id = ?",
                    (note, AGENT))

    def test_the_polled_path_pushes_a_manual_status_UNCHANGED(self):
        """The derivation is skipped entirely. Deriving over it would show the agent as online
        seconds after the operator stopped it, while the row still says stopped — the UI and the
        database disagreeing about an action the operator took deliberately.

        BOTH the cache and `derive` are forced, and it took two attempts to make this test able to
        fail. Forcing only `derive` was not enough: for a stopped agent the real
        `_compute_live_status_cache` returns no usable `status_inputs`, so the derivation raises
        inside its own `try` and the status stays "stopped" whether the manual guard is there or
        not. The mutation survived a test that looked like it covered it."""
        async def fake_cache(db, row, settings=None):
            return {"status": "stopped", "reason": "stopped by the operator", "status_inputs": {}}

        self._stop()
        with mock.patch.object(status_broadcast, "_compute_live_status_cache", fake_cache), \
             mock.patch.object(status_broadcast, "derive", return_value="working"):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "stopped")

    def test_the_polled_path_pushes_a_manual_status_unchanged_END_TO_END(self):
        """The same claim with nothing forced — the real cache, the real derivation, a real stopped
        row. It cannot distinguish the guard from its absence (see above), which is exactly why it
        is not the only test of this: it is here to show the forced one describes a real state."""
        self._stop()
        payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "stopped")

    def test_the_engine_path_pushes_a_manual_status_UNCHANGED(self):
        """`engine_status` is forced to disagree, for the same reason as the polled twin above."""
        async def fake_engine(db, row, settings=None):
            return "online"

        self._stop()
        with mock.patch.object(status_broadcast, "engine_status", fake_engine):
            payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["status"], "stopped")

    def test_the_engine_path_carries_the_operators_own_NOTE(self):
        """The note is why it was stopped. Blanking it here would leave the operator's reason
        visible on a poll and missing on a push, for the same agent in the same second."""
        self._stop("stopped: hit its usage limit")
        payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["statusNote"], "stopped: hit its usage limit")

    def test_a_manual_status_is_recognised_CASE_INSENSITIVELY_by_the_engine_path(self):
        """It lower-cases the persisted value before the membership test. A row written as
        `Stopped` by any other writer must not slip past into a derivation.

        Also needs a disagreeing engine: a stopped row derives to "stopped" anyway, so without this
        the un-lower-cased version passes and the `.lower()` reads as covered."""
        async def fake_engine(db, row, settings=None):
            return "online"

        self._write("UPDATE agents SET status = 'STOPPED' WHERE id = ?", (AGENT,))
        with mock.patch.object(status_broadcast, "engine_status", fake_engine):
            payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["status"], "stopped")

    def test_an_ORDINARY_status_is_not_treated_as_manual(self):
        """The guard is a membership test against one small set, not "the row has a status". If it
        were the latter, every derived status would be frozen at whatever was last persisted."""
        self._write("UPDATE agents SET status = 'online' WHERE id = ?", (AGENT,))
        with mock.patch.object(status_broadcast, "derive", return_value="working"):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "working")


class PushPollParityTests(StatusBroadcastTestCase):
    """The polled path re-derives, and the note has to follow the status."""

    def test_the_pushed_status_is_the_DERIVED_one(self):
        """The push serves what the read would serve. `derive` over the same assembled inputs is
        the read's answer, so the cached value is not what goes on the wire."""
        with mock.patch.object(status_broadcast, "derive", return_value="working"):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "working")

    def test_a_DISAGREEING_derivation_BLANKS_the_note(self):
        """The 2026-07-10 review point, and the subtlest line in the module. The cached note
        describes the cached status; if the derivation replaces the status, the note now explains a
        status nobody is being shown. Sending it would produce a push whose own two fields
        contradict each other."""
        async def fake_cache(db, row, settings=None):
            return {"status": "offline", "reason": "no heartbeat for 4 minutes",
                    "status_inputs": {}}

        with mock.patch.object(status_broadcast, "_compute_live_status_cache", fake_cache), \
             mock.patch.object(status_broadcast, "derive", return_value="working"):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "working")
        self.assertEqual(payload["statusNote"], "")

    def test_an_AGREEING_derivation_KEEPS_the_note(self):
        """The other half. When the two agree the note still describes the status being pushed, and
        dropping it would lose the only explanation the dashboard gets."""
        async def fake_cache(db, row, settings=None):
            return {"status": "offline", "reason": "no heartbeat for 4 minutes",
                    "status_inputs": {}}

        with mock.patch.object(status_broadcast, "_compute_live_status_cache", fake_cache), \
             mock.patch.object(status_broadcast, "derive", return_value="offline"):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["statusNote"], "no heartbeat for 4 minutes")

    def test_a_FAILING_derivation_falls_back_to_the_cached_answer(self):
        """`derive` is wrapped in its own try. A push is better than no push: the cached status is
        what the poll would have served a moment ago, and staying silent leaves the dashboard on
        something older still."""
        async def fake_cache(db, row, settings=None):
            return {"status": "offline", "reason": "no heartbeat for 4 minutes",
                    "status_inputs": {}}

        def boom(_inputs):
            raise RuntimeError("bad inputs")

        with mock.patch.object(status_broadcast, "_compute_live_status_cache", fake_cache), \
             mock.patch.object(status_broadcast, "derive", boom):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["statusNote"], "no heartbeat for 4 minutes",
                         "the note was blanked for a derivation that never produced a status")


class EngineStatusPathTests(StatusBroadcastTestCase):
    def test_a_derived_engine_status_carries_NO_note(self):
        """Only a manual status has an operator-written reason. The engine's answer is derived from
        proof, and inventing prose for it here would put a note on the wire that no polled read
        would ever produce."""
        async def fake_engine(db, row, settings=None):
            return "online"

        self._write("UPDATE agents SET status_note = ? WHERE id = ?", ("stale prose", AGENT))
        with mock.patch.object(status_broadcast, "engine_status", fake_engine):
            payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["status"], "online")
        self.assertEqual(payload["statusNote"], "")

    def test_SETTINGS_PASSED_IN_are_reused_rather_than_reloaded(self):
        """Callers hand settings in precisely to avoid a second load on a hot path — the turn
        boundaries push on every turn start and end. Reloading anyway would make the parameter a
        lie and put a query back where one was removed."""
        seen = {}

        async def fake_engine(db, row, settings=None):
            seen["settings"] = settings
            return "online"

        sentinel = {"agent_liveness_seconds": 4242}
        with mock.patch.object(status_broadcast, "engine_status", fake_engine), \
             mock.patch.object(status_broadcast, "_load_settings",
                               mock.Mock(side_effect=AssertionError("settings were reloaded"))):
            self._push(status_broadcast._broadcast_engine_status, settings=sentinel)
        self.assertIs(seen["settings"], sentinel)

    def test_settings_are_LOADED_when_the_caller_supplies_none(self):
        """The other direction: the parameter is optional, and a caller that has not already loaded
        them must not push a status derived from nothing."""
        seen = {}

        async def fake_engine(db, row, settings=None):
            seen["settings"] = settings
            return "online"

        with mock.patch.object(status_broadcast, "engine_status", fake_engine):
            self._push(status_broadcast._broadcast_engine_status)
        self.assertIsInstance(seen["settings"], dict)
        self.assertTrue(seen["settings"], "no settings were loaded")


class ManualStatusOwnerTests(unittest.TestCase):
    def test_the_manual_set_is_READ_FROM_ITS_OWNER_not_copied(self):
        """One owner, never a copy. A second copy of this set would let a status be manual in one
        path and derivable in the other — the operator's stop honoured on the poll and undone on
        the push."""
        from service.api_core.manual_status import _MANUAL_STATUSES

        self.assertIs(status_broadcast._borrowed_manual_statuses(), _MANUAL_STATUSES)


if __name__ == "__main__":
    unittest.main()
