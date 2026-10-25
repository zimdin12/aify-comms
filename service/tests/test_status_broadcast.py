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

There are two functions, named for the moment that calls them; since 0.9.9 S3b both push the one word the poll
serves (the operational view's), so every contract below holds for both.

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
from service.tests import env_records
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
        """aify-env says the agent is busy; the operator stopped it. Pushing aify-env's word would show it
        working seconds after the stop, while the row still says stopped."""
        env_records.report(self, AGENT, busy=True)
        self._stop()
        payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "stopped")

    def test_the_engine_path_carries_the_operators_own_NOTE(self):
        """The note is why it was stopped. Blanking it here would leave the operator's reason
        visible on a poll and missing on a push, for the same agent in the same second."""
        self._stop("stopped: hit its usage limit")
        payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["statusNote"], "stopped: hit its usage limit")

    def test_a_manual_status_is_recognised_CASE_INSENSITIVELY_by_the_engine_path(self):
        """A row written as `STOPPED` by any other writer must not slip past into aify-env's word. aify-env
        reports the agent busy, so an un-lower-cased guard pushes `working`."""
        env_records.report(self, AGENT, busy=True)
        self._write("UPDATE agents SET status = 'STOPPED' WHERE id = ?", (AGENT,))
        payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["status"], "stopped")

    def test_an_ORDINARY_status_is_not_treated_as_manual(self):
        """The guard is a membership test against one small set, not "the row has a status". If it
        were the latter, every pushed status would be frozen at whatever was last persisted. After the
        0.9.8 switch the word that must win is aify-env's."""
        self._write("UPDATE agents SET status = 'online' WHERE id = ?", (AGENT,))
        env_records.report(self, AGENT, busy=True)
        payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "working")


class PushPollParityTests(StatusBroadcastTestCase):
    """The polled path serves the operational view's word (0.9.8 switch), and the note has to follow the status."""

    def test_a_FAILING_view_pushes_unknown_never_the_legacy_word(self):
        """A view that cannot be read pushes `unknown` with why, never a word comms made up. aify-env
        published `offline`, so a push that fell back to anything stored would not read `unknown`."""
        env_records.report(self, AGENT, state="offline")

        async def boom(*_args, **_kwargs):
            raise RuntimeError("unreadable mirror")

        with mock.patch.object(status_broadcast.switched_view, "view_for", boom):
            payload = self._only_payload(self._push(status_broadcast._broadcast_agent_status))
        self.assertEqual(payload["status"], "unknown")
        self.assertEqual(payload["statusNote"], "comms could not read aify-env's record of this agent")


class EngineStatusPathTests(StatusBroadcastTestCase):
    def test_a_derived_engine_status_carries_NO_note(self):
        """Only a manual status has an operator-written reason. aify-env's word carries none, and
        inventing prose for it here would put a note on the wire that no polled read would ever
        produce."""
        self._write("UPDATE agents SET status_note = ? WHERE id = ?", ("stale prose", AGENT))
        env_records.report(self, AGENT)
        payload = self._only_payload(self._push(status_broadcast._broadcast_engine_status))
        self.assertEqual(payload["status"], "online")
        self.assertEqual(payload["statusNote"], "")

if __name__ == "__main__":
    unittest.main()
