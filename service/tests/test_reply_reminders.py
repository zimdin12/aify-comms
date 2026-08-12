"""Reply-reminder subsystem tests (Plan Task 5.1).

Verifies the runtime-agnostic reply-reminder pass over open ``require_reply``
dispatch runs:

  * an unanswered required run past the threshold enqueues exactly one reminder;
  * the reminder body reinforces the ``comms_send(..., inReplyTo=...)`` pattern;
  * once a reply lands the run is answered and no further reminders fire;
  * reminders are CAPPED out of the box (sane non-zero default) so a perpetually
    unanswered run never gets nagged forever;
  * the behaviour is identical regardless of runtime (parametrized over codex +
    hermes) because reminders ride each runtime's normal managed wake path.

The reminder mechanism is service-level (``_run_contract_reminders_once``), so
these tests exercise it directly rather than depending on the periodic loop.
"""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now
from service.routers.api_v2 import router
from service.control_plane import DEFAULT_SETTINGS


from service.tests._base import FastApiTestCase


# Every runtime is meant to behave identically — the reminder rides the
# normal managed wake path, which is runtime-agnostic. Parametrize over a
# non-claude runtime (hermes) plus codex to prove there is no claude-specific
# branch in the reminder pass.
_RUNTIMES = ["hermes", "codex"]


class ReplyReminderTests(FastApiTestCase):
    # --- helpers ---------------------------------------------------------

    def _register_live_resident(self, agent_id, *, runtime, bridge_id, port, role="coder"):
        # A resident is deliverable when its bridge is fresh + the runtime's
        # live-endpoint hint is present. codex/opencode use appServerUrl;
        # hermes residents gate on a ws:// gatewayUrl. Set both so the helper
        # is runtime-agnostic.
        runtime_config = {
            "appServerUrl": f"ws://127.0.0.1:{port}",
            "gatewayUrl": f"ws://127.0.0.1:{port}",
        }
        payload = {
            "agentId": agent_id,
            "role": role,
            "runtime": runtime,
            "sessionMode": "resident",
            "sessionHandle": f"{agent_id}-thread",
            "machineId": "linux:test-host",
            "bridgeId": bridge_id,
            "capabilities": ["resident-run", "resume", "interrupt", "steer"],
            "runtimeConfig": runtime_config,
        }
        resp = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _dispatch(self, **payload):
        resp = self.client.post("/api/v1/dispatch", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _execute(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _fetchall(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _make_overdue_required_run(self, runtime):
        """Register lead+coder of the given runtime, dispatch a require_reply
        run from lead→coder, and back-date it so it is overdue."""
        self._register_live_resident("lead", runtime=runtime, bridge_id="lead-bridge", port=1)
        self._register_live_resident("coder", runtime=runtime, bridge_id="coder-bridge", port=2)
        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="please answer",
            body="need a decision",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        # Back-date well past any threshold and force a deliverable status.
        overdue_at = api_v2._iso_from_ms(int((time.time() - 600) * 1000))
        self._execute(
            "UPDATE dispatch_runs SET status = 'delivered', requested_at = ? WHERE id = ?",
            (overdue_at, run_id),
        )
        return run_id

    def _seed_prior_reminders(self, run_id, count):
        for idx in range(count):
            self._execute(
                "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
                (run_id, "reply_reminder", f"old reminder {idx}", api_v2._iso_from_ms(int((time.time() - (90 - idx)) * 1000))),
            )

    def _sent_reminder_message(self, result, run_id):
        """Return the messages row (body, in_reply_to) of the reminder just sent."""
        reminded = [r for r in result["reminded"] if r["runId"] == run_id]
        self.assertEqual(len(reminded), 1, result)
        rows = self._fetchall(
            "SELECT body, in_reply_to FROM messages WHERE id = ?",
            (reminded[0]["messageId"],),
        )
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _reminder_events(self, run_id):
        return self._fetchall(
            "SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'reply_reminder' ORDER BY created_at ASC",
            (run_id,),
        )

    def _run_reminders(self, **kwargs):
        async def _run():
            db = await get_db()
            try:
                result = await api_v2._run_contract_reminders_once(db, **kwargs)
                await db.commit()
                return result
            finally:
                await db.close()

        return asyncio.run(_run())

    # --- tests -----------------------------------------------------------

    def test_default_max_count_is_capped(self):
        """Out of the box the reminder count is bounded — never infinite."""
        cap = int(DEFAULT_SETTINGS.get("reply_reminder_max_count", 0) or 0)
        self.assertGreater(cap, 0, "default reply_reminder_max_count must be a sane non-zero cap")
        self.assertLessEqual(cap, 5, "default cap should be small (sane nag limit)")

    def test_unanswered_required_run_enqueues_one_reminder(self):
        for runtime in _RUNTIMES:
            with self.subTest(runtime=runtime):
                self.setUp()
                try:
                    self.client.put(
                        "/api/v1/settings",
                        json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1},
                    )
                    run_id = self._make_overdue_required_run(runtime)
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    reminded = [r for r in result["reminded"] if r["runId"] == run_id]
                    self.assertEqual(len(reminded), 1, f"{runtime}: expected one reminder, got {result}")
                    events = self._reminder_events(run_id)
                    self.assertEqual(len(events), 1, f"{runtime}: exactly one reply_reminder event")
                finally:
                    self.tearDown()

    def test_reminder_body_reinforces_comms_send_pattern(self):
        for runtime in _RUNTIMES:
            with self.subTest(runtime=runtime):
                self.setUp()
                try:
                    self.client.put(
                        "/api/v1/settings",
                        json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1},
                    )
                    run_id = self._make_overdue_required_run(runtime)
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    reminded = [r for r in result["reminded"] if r["runId"] == run_id]
                    self.assertEqual(len(reminded), 1, result)
                    message_id = reminded[0]["messageId"]
                    row = self._fetchall(
                        "SELECT from_agent, to_agent, in_reply_to, body FROM messages WHERE id = ?",
                        (message_id,),
                    )
                    self.assertEqual(len(row), 1)
                    body = row[0]["body"]
                    self.assertEqual(row[0]["from_agent"], "lead", f"{runtime}: reminder keeps original sender")
                    self.assertEqual(row[0]["to_agent"], "coder", f"{runtime}: reminder goes to owing agent")
                    self.assertIn('to="lead"', body, f"{runtime}: reply must go to original sender")
                    self.assertIn("comms_send", body, f"{runtime}: body must teach comms_send")
                    self.assertIn("inReplyTo", body, f"{runtime}: body must teach inReplyTo anchor")
                    self.assertIn('type="response"', body, f"{runtime}: body must show the response type")
                finally:
                    self.tearDown()

    def test_no_further_reminder_once_answered(self):
        for runtime in _RUNTIMES:
            with self.subTest(runtime=runtime):
                self.setUp()
                try:
                    self.client.put(
                        "/api/v1/settings",
                        json={"reply_reminder_minutes": 1, "reply_reminder_repeat_minutes": 1},
                    )
                    run_id = self._make_overdue_required_run(runtime)
                    # Reply lands → run is answered.
                    self._execute(
                        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
                        ("reply-msg-1", run_id),
                    )
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    reminded = [r for r in result["reminded"] if r["runId"] == run_id]
                    self.assertEqual(len(reminded), 0, f"{runtime}: answered run must not be reminded")
                    self.assertEqual(len(self._reminder_events(run_id)), 0)
                finally:
                    self.tearDown()

    def test_linked_request_closes_reply_contract_without_reminder(self):
        self._register_live_resident("lead", runtime="hermes", bridge_id="lead-bridge", port=1)
        self._register_live_resident("coder", runtime="hermes", bridge_id="coder-bridge", port=2)
        created = self._dispatch(
            from_agent="lead",
            to="coder",
            type="request",
            subject="please answer",
            body="need a decision",
            mode="start_if_possible",
            createMessage=True,
        )
        run_id = created["runs"][0]["runId"]
        reply = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "coder",
                "to": "lead",
                "type": "request",
                "subject": "answer plus follow-up",
                "body": "the answer is yes; please confirm timing",
                "inReplyTo": created["messageId"],
                "trigger": False,
            },
        )
        self.assertEqual(reply.status_code, 200, reply.text)

        rows = self._fetchall("SELECT status, result_message_id FROM dispatch_runs WHERE id = ?", (run_id,))
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["result_message_id"], reply.json()["messageId"])
        result = self._run_reminders(run_id=run_id, ignore_repeat=True)
        self.assertFalse([r for r in result["reminded"] if r["runId"] == run_id])

    def test_reminders_are_capped_after_max_count(self):
        for runtime in _RUNTIMES:
            with self.subTest(runtime=runtime):
                self.setUp()
                try:
                    cap = 2
                    self.client.put(
                        "/api/v1/settings",
                        json={
                            "reply_reminder_minutes": 1,
                            "reply_reminder_repeat_minutes": 1,
                            "reply_reminder_max_count": cap,
                        },
                    )
                    run_id = self._make_overdue_required_run(runtime)
                    # Pre-seed `cap` prior reminders so the run is already at the
                    # cap. The next pass must NOT add another (no infinite nag).
                    for idx in range(cap):
                        self._execute(
                            "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
                            (run_id, "reply_reminder", f"old reminder {idx}", api_v2._iso_from_ms(int((time.time() - (90 - idx)) * 1000))),
                        )
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    reminded = [r for r in result["reminded"] if r["runId"] == run_id]
                    self.assertEqual(len(reminded), 0, f"{runtime}: must stop reminding at the cap; got {result}")
                    skipped = [s for s in result["skipped"] if s["runId"] == run_id]
                    self.assertTrue(skipped, f"{runtime}: capped run should be reported as skipped")
                    self.assertIn("max reminders", skipped[0]["reason"])
                    # Still exactly `cap` reminder events — none added.
                    self.assertEqual(len(self._reminder_events(run_id)), cap)
                finally:
                    self.tearDown()

    def test_capped_run_resumes_reminding_below_cap(self):
        """Below the cap a still-overdue run is reminded again (sanity: the cap
        bounds total reminders, it does not silence a run prematurely)."""
        for runtime in _RUNTIMES:
            with self.subTest(runtime=runtime):
                self.setUp()
                try:
                    self.client.put(
                        "/api/v1/settings",
                        json={
                            "reply_reminder_minutes": 1,
                            "reply_reminder_repeat_minutes": 1,
                            "reply_reminder_max_count": 3,
                        },
                    )
                    run_id = self._make_overdue_required_run(runtime)
                    # One prior reminder, under the cap of 3.
                    self._execute(
                        "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
                        (run_id, "reply_reminder", "old reminder", api_v2._iso_from_ms(int((time.time() - 90) * 1000))),
                    )
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    reminded = [r for r in result["reminded"] if r["runId"] == run_id]
                    self.assertEqual(len(reminded), 1, f"{runtime}: under cap should still remind; got {result}")
                    self.assertEqual(len(self._reminder_events(run_id)), 2)
                finally:
                    self.tearDown()


    # --- light-reminder cadence (reply_reminder_full_every) ----------------

    _FULL_MARKER = "still needs an explicit reply"

    def test_light_reminders_between_full_every_nth(self):
        """Default cadence (full_every=3): reminders 1-2 are LIGHT one-liners,
        3 is FULL, 4-5 light again, 6 full — reminders never stop firing, they
        just get cheaper between the periodic full nudges."""
        expectations = {0: "light", 1: "light", 2: "full", 3: "light", 4: "light", 5: "full"}
        for prior, expected in expectations.items():
            with self.subTest(prior_reminders=prior, expected=expected):
                self.setUp()
                try:
                    self.client.put(
                        "/api/v1/settings",
                        json={
                            "reply_reminder_minutes": 1,
                            "reply_reminder_repeat_minutes": 1,
                            "reply_reminder_max_count": 0,
                            "reply_reminder_full_every": 3,
                        },
                    )
                    run_id = self._make_overdue_required_run("hermes")
                    self._seed_prior_reminders(run_id, prior)
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    msg = self._sent_reminder_message(result, run_id)
                    body = msg["body"]
                    original_id = self._fetchall(
                        "SELECT message_id FROM dispatch_runs WHERE id = ?", (run_id,)
                    )[0]["message_id"]
                    # Both formats stay anchored to the original message.
                    if original_id:
                        self.assertEqual(msg["in_reply_to"], original_id)
                    self.assertIn("inReplyTo", body)
                    if expected == "light":
                        self.assertTrue(body.startswith("Reply owed to"), body)
                        self.assertEqual(len(body.splitlines()), 1, f"light reminder must be one line: {body!r}")
                        self.assertIn(original_id or run_id, body)
                        self.assertIn("please answer", body)  # subject
                        self.assertNotIn(self._FULL_MARKER, body)
                        self.assertNotIn("need a decision", body)  # no original body
                    else:
                        self.assertIn(self._FULL_MARKER, body)
                finally:
                    self.tearDown()

    def test_full_every_zero_or_one_means_always_full(self):
        """full_every=0 or 1 disables the light format entirely — reminder 1
        (which would be light under the default cadence) is already full."""
        for full_every in (0, 1):
            with self.subTest(full_every=full_every):
                self.setUp()
                try:
                    self.client.put(
                        "/api/v1/settings",
                        json={
                            "reply_reminder_minutes": 1,
                            "reply_reminder_repeat_minutes": 1,
                            "reply_reminder_max_count": 0,
                            "reply_reminder_full_every": full_every,
                        },
                    )
                    run_id = self._make_overdue_required_run("hermes")
                    result = self._run_reminders(run_id=run_id, ignore_repeat=True)
                    body = self._sent_reminder_message(result, run_id)["body"]
                    self.assertIn(self._FULL_MARKER, body)
                    self.assertIn("inReplyTo", body)
                finally:
                    self.tearDown()

    def test_full_every_default_registered(self):
        """The setting ships with the operator-decided default (3) and is
        exposed via GET /settings."""
        self.assertEqual(DEFAULT_SETTINGS.get("reply_reminder_full_every"), 3)
        settings = self.client.get("/api/v1/settings")
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json()["reply_reminder_full_every"], 3)


if __name__ == "__main__":
    unittest.main()
