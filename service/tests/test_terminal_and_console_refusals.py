"""The last untested refusals: terminal ownership, terminal controls, session delete, pi console.

Six refusals, none of which any test had touched — all of them reading as exercised until fe1e22ad
because `service/tests/data/` holds pre-split copies of the handlers:

    POST   /terminals/{id}/output       409 Terminal is owned by a different bridge
    PATCH  /terminals/controls/{id}     400 Unsupported terminal control status "<s>"
                                        404 Terminal control "<id>" not found
    DELETE /sessions/{id}               409 Session "<s>" is <status>; stop or finish it before
                                            deleting the session record.
    POST   /sessions/{id}/console       409 Pi Console needs a session handle to preserve context.
                                            Set a handle or request freshContext=true.
    POST   /dispatch                    409 Agent "<a>" is migrating from resident to managed
                                            (pi flip pending). …

THE OWNERSHIP 409 HAS AN EXCEPTION THAT IS THE INTERESTING HALF. A real PTY has one owner, so output
from a second bridge is refused — silently accepting it would interleave two processes into one
screen. A VIRTUAL rpc terminal is the opposite: ownership transfers, and a stopped row is REVIVED,
because bridge supersession can race an in-flight dispatch and leave a terminal marked stopped that
something is actively writing to. Both directions are tested, because a gate tested only where it
refuses would pass with the exception deleted.

THE PI CONSOLE GUARD EXISTED TWICE AND ONE COPY WAS DEAD. `session_console.py` repeated it after the
`if runtime == "pi": return …` early return, so it could never fire; the live copy is inside
`_start_virtual_pi_console`. Removed in this commit — an unreachable copy of a guard is worse than
no copy, because the next person to change the rule can edit it, see a green suite, and ship
nothing.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT_ID = "lc-pi"
ENVIRONMENT_ID = "linux:test-host:default"
SESSION_ID = "sess-1"
TERMINAL_ID = "term-1"

#: The statuses a session may be deleted in, from the router's own accessor.
DELETABLE = ("cancelled", "completed", "ended", "failed", "lost", "stopped")
NOT_DELETABLE = ("running", "starting", "recovering", "managed-warm", "restarting", "cli-takeover")


class TerminalAndConsoleRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        # REGISTERED MANAGED, deliberately. Registering a pi agent as RESIDENT stamps
        # `pi_resident_pending_flip` (registration.py, Plan 2), which makes every dispatch 409 —
        # so a suite that registered the default way would have had its mid-flip test passing for
        # a reason it never set, and its "accepts dispatch" test failing for the same one.
        response = self.client.post(
            "/api/v1/agents",
            json={"agentId": AGENT_ID, "role": "coder", "runtime": "pi", "sessionMode": "managed"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self._heartbeat()

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _heartbeat(self, status: str = "online") -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-one",
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "pi", "available": True, "terminal": True}],
                "terminal": True,
                "status": status,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _read(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                row = await cursor.fetchone()
                return dict(row) if row else {}

        return asyncio.run(run())

    def _seed_session(self, status: str = "running", session_handle: str = "",
                      session_id: str = SESSION_ID) -> None:
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status,"
            " session_handle, started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, AGENT_ID, ENVIRONMENT_ID, "pi", "/workspace/proj", status,
             session_handle, "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )

    def _seed_terminal(self, command: str, bridge_id: str = "bridge-one",
                       status: str = "running") -> None:
        self._seed_session()
        self._write(
            "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id,"
            " runtime, workspace, command, output, status, requested_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (TERMINAL_ID, SESSION_ID, AGENT_ID, ENVIRONMENT_ID, bridge_id, "pi", "/workspace/proj",
             command, "", status, "bridge", "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )

    def _post_output(self, bridge_id: str, chunk: str = "hello", status: str = "running"):
        return self.client.post(
            f"/api/v1/terminals/{TERMINAL_ID}/output",
            json={"chunk": chunk, "bridgeId": bridge_id, "status": status},
        )

    # ── a real PTY has one owner ─────────────────────────────────────────────────────────────

    def test_a_second_bridge_cannot_post_output_for_a_real_pty(self):
        """Two processes interleaved into one screen is not a recoverable state — the operator sees
        a terminal whose lines come from two workers with no way to tell which is which."""
        self._seed_terminal(command="/bin/bash")
        response = self._post_output("bridge-two")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "Terminal is owned by a different bridge")

    def test_the_owning_bridge_may_keep_posting(self):
        self._seed_terminal(command="/bin/bash")
        self.assertEqual(self._post_output("bridge-one").status_code, 200)

    def test_a_VIRTUAL_terminal_transfers_ownership_instead_of_refusing(self):
        """The exception, and the half a refusal-only test would miss. A synth row has no process to
        interleave; the arriving POST is proof the new bridge is the one writing."""
        self._seed_terminal(command="aify://virtual-rpc/pi")
        response = self._post_output("bridge-two")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self._read("SELECT bridge_id FROM terminal_sessions WHERE id = ?", (TERMINAL_ID,))[
                "bridge_id"
            ],
            "bridge-two",
        )

    def test_a_virtual_takeover_REVIVES_a_stopped_row(self):
        """Bridge supersession can stop the row while an in-flight dispatch is still writing to it.
        The operator-visible symptom was a terminal reading "started then stopped" while the agent
        kept replying, with frames piling up behind a stale status."""
        self._seed_terminal(command="aify://virtual-rpc/pi", status="stopped")
        self.assertEqual(self._post_output("bridge-two").status_code, 200)
        row = self._read(
            "SELECT status, bridge_id FROM terminal_sessions WHERE id = ?", (TERMINAL_ID,),
        )
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["bridge_id"], "bridge-two")

    def test_the_takeover_is_audited_rather_than_only_applied(self):
        """Ownership changing hands silently is indistinguishable from a bug when someone reads the
        row later; the event log is where that becomes explainable."""
        self._seed_terminal(command="aify://virtual-rpc/pi")
        self.assertEqual(self._post_output("bridge-two").status_code, 200)
        row = self._read(
            "SELECT event_type, body FROM terminal_events WHERE terminal_id = ?"
            " AND event_type = 'virtual_rpc_bridge_takeover'",
            (TERMINAL_ID,),
        )
        self.assertEqual(row.get("event_type"), "virtual_rpc_bridge_takeover")
        self.assertIn("bridge-one", row.get("body") or "")
        self.assertIn("bridge-two", row.get("body") or "")

    # ── terminal controls ────────────────────────────────────────────────────────────────────

    def test_the_terminal_control_status_allowlist_is_exactly_completed_and_failed(self):
        for status in ("pending", "claimed", "done", "", "complete"):
            with self.subTest(status=status):
                response = self.client.patch(
                    "/api/v1/terminals/controls/no-such-control", json={"status": status},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f'Unsupported terminal control status "{status}"',
                )

    def test_a_recognised_status_gets_past_the_allowlist_in_any_casing(self):
        """Normalised before the check, echoed raw in the refusal — the same pair as every other
        control surface. Any casing must reach the missing-control 404, not the 400."""
        for status in ("completed", "failed", "COMPLETED", "  Failed "):
            with self.subTest(status=status):
                response = self.client.patch(
                    "/api/v1/terminals/controls/no-such-control", json={"status": status},
                )
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(
                    response.json()["detail"], 'Terminal control "no-such-control" not found',
                )

    # ── deleting a session record ────────────────────────────────────────────────────────────

    def test_a_live_session_cannot_be_deleted(self):
        """The record is what the reconcilers key on. Deleting it under a live worker leaves a
        process nothing tracks — the row is how it is found again."""
        for status in NOT_DELETABLE:
            with self.subTest(status=status):
                session_id = f"sess-{status}"
                self._seed_session(status=status, session_id=session_id)
                response = self.client.delete(f"/api/v1/sessions/{session_id}")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f'Session "{session_id}" is {status}'
                    + "; stop or finish it before deleting the session record.",
                )

    def test_a_finished_session_can_be_deleted(self):
        for status in DELETABLE:
            with self.subTest(status=status):
                session_id = f"sess-ok-{status}"
                self._seed_session(status=status, session_id=session_id)
                response = self.client.delete(f"/api/v1/sessions/{session_id}")
                self.assertEqual(response.status_code, 200, response.text)

    def test_a_session_with_NO_status_reads_as_active_rather_than_deletable(self):
        """Fail safe on missing evidence: an empty status is not proof the worker is gone, and the
        message says "active" rather than quoting an empty string at the operator."""
        self._seed_session(status="", session_id="sess-blank")
        response = self.client.delete("/api/v1/sessions/sess-blank")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('Session "sess-blank" is active;', response.json()["detail"])

    # ── the pi console handle guard (the LIVE copy) ──────────────────────────────────────────

    def test_a_pi_console_without_a_session_handle_is_refused(self):
        """Starting one without a handle would silently begin a NEW pi conversation while the
        operator believes they are opening the existing one."""
        self._seed_session(session_handle="")
        response = self.client.post(
            f"/api/v1/sessions/{SESSION_ID}/console/start", json={"requestedBy": "dashboard"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            "Pi Console needs a session handle to preserve context. Set a handle or request "
            "freshContext=true.",
        )

    def test_freshContext_is_the_way_past_it(self):
        """The refusal names the flag because discarding context is a legitimate thing to ask for —
        it just has to be asked for."""
        self._seed_session(session_handle="")
        response = self.client.post(
            f"/api/v1/sessions/{SESSION_ID}/console/start",
            json={"requestedBy": "dashboard", "freshContext": True},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_session_that_HAS_a_handle_needs_no_flag(self):
        self._seed_session(session_handle="thread-abc")
        response = self.client.post(
            f"/api/v1/sessions/{SESSION_ID}/console/start", json={"requestedBy": "dashboard"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ── dispatching to an agent mid-flip ─────────────────────────────────────────────────────

    def test_a_pi_agent_mid_flip_refuses_new_dispatches_and_says_to_retry(self):
        """The flip takes seconds and completes on its own, so the refusal is a "not yet" rather
        than a "no" — queuing against a session_mode the runtime no longer supports is what it
        prevents."""
        self._write(
            "UPDATE agents SET runtime_state = ? WHERE id = ?",
            ('{"pi_resident_pending_flip": true}', AGENT_ID),
        )
        response = self.client.post(
            "/api/v1/dispatch",
            json={"from_agent": "operator", "to": AGENT_ID, "subject": "s", "body": "hello"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertIn(
            '" is migrating from resident to managed (pi flip pending). Retry in a few seconds',
            detail,
        )
        self.assertIn(AGENT_ID, detail)

    def test_the_same_agent_accepts_dispatch_once_the_flip_flag_is_gone(self):
        response = self.client.post(
            "/api/v1/dispatch",
            json={"from_agent": "operator", "to": AGENT_ID, "subject": "s", "body": "hello"},
        )
        self.assertEqual(response.status_code, 200, response.text)
