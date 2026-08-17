"""Whether a managed claim may take a terminal the Console is holding.

`_release_stale_console_owner_for_claim` was among the 71 service functions the suite never
entered. It runs when a managed dispatch wants a runtime handle that an operator's Console session
currently owns, and it answers one question: is that Console owner ALIVE?

BOTH WRONG ANSWERS COST SOMETHING, and the repo has an incident for one of them.

Releasing a live owner: the operator's Console dies mid-session and a fresh PTY is respawned on the
next dispatch. That is the 2026-06-06 "terminal closes constantly" incident, and its cause is
recorded in the code — the old rule released an owner whose OUTPUT was ~90s old. Age is not
liveness: an alive-but-quiet managed worker legitimately prints nothing for minutes, idle between
turns or mid-turn thinking.

Keeping a dead one: the managed claim is refused forever, so the agent stops taking work while the
dashboard shows a Console that no longer exists.

SO LIVENESS IS TWO THINGS, ASSERTED SEPARATELY: the owning environment bridge is online AND still
owns this terminal's `bridge_id` (`bridge_current`), and the PTY has not posted an ending status
(`active_status`). A test that only checked "console owner is kept" would pass with either half
deleted.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiosqlite

from service.api_core.claim_gating import _release_stale_console_owner_for_claim
from service.models import DispatchClaimRequest
from service.tests._base import FastApiTestCase

AGENT = "lc-worker"
ENVIRONMENT = "linux:test-host:default"
BRIDGE = "bridge-one"
SESSION = "sess-1"
TERMINAL = "term-1"


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class ConsoleOwnerReleaseTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/agents", json={"agentId": AGENT, "role": "coder", "runtime": "codex"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(r) for r in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed(self, *, env_last_seen_offset: int = 0, env_status: str = "online",
              terminal_status: str = "running", terminal_bridge: str = BRIDGE,
              with_terminal: bool = True) -> None:
        self._write(
            "INSERT INTO environments (id, label, machine_id, os, kind, bridge_id, cwd_roots,"
            " runtimes, status, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ENVIRONMENT, "Linux", "linux:test-host", "linux", "linux", BRIDGE, "[]", "[]",
             env_status, _iso(-600), _iso(env_last_seen_offset)),
        )
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, owner_mode,"
            " terminal_id, terminal_status, started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (SESSION, AGENT, ENVIRONMENT, "codex", "running", "console",
             TERMINAL if with_terminal else "", terminal_status, _iso(-600), _iso(-60)),
        )
        if with_terminal:
            self._write(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id,"
                " runtime, workspace, command, output, status, requested_by, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (TERMINAL, SESSION, AGENT, ENVIRONMENT, terminal_bridge, "codex", "/w", "bash", "",
                 terminal_status, "dashboard", _iso(-600), _iso(-600)),
            )

    def _release(self, bridge_id: str = BRIDGE):
        """Call the helper exactly as the claim path does, with its own connection."""

        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (SESSION,))
                owner_session = await cursor.fetchone()
                req = DispatchClaimRequest(agentId=AGENT, bridgeId=bridge_id)
                result = await _release_stale_console_owner_for_claim(db, owner_session, req)
                await db.commit()
                return result

        return asyncio.run(run())

    # ── the owner is alive: the claim is refused ─────────────────────────────────────────────

    def test_a_LIVE_console_owner_is_kept_and_the_claim_is_told_why(self):
        self._seed()
        result = self._release()
        self.assertIsNotNone(result, "a live Console owner was released")
        self.assertEqual(result["reason"], "console_owner_active")
        self.assertEqual(result["terminalId"], TERMINAL)
        self.assertIn("Stop or return Console to managed", result["hint"],
                      "the refusal has to tell the operator what to do about it")

    def test_a_QUIET_but_live_owner_is_still_kept(self):
        """THE 2026-06-06 INCIDENT. The old rule released an owner whose OUTPUT was ~90s old, and a
        fresh PTY was respawned on the next dispatch — "terminal closes constantly", plus a pile of
        `terminal_sessions` rows. An idle-between-turns worker legitimately prints nothing."""
        self._seed()
        self._write(
            "UPDATE terminal_sessions SET updated_at = ? WHERE id = ?", (_iso(-3600), TERMINAL),
        )
        self.assertIsNotNone(self._release(), "a quiet but living Console owner was released")

    def test_every_ACTIVE_terminal_status_counts_as_alive(self):
        for status in ("starting", "attached", "running", "active", "idle"):
            with self.subTest(status=status):
                self.setUp()
                self._seed(terminal_status=status)
                self.assertIsNotNone(self._release(), f"{status} was treated as a dead terminal")

    def test_a_DEGRADED_environment_still_counts_as_a_live_bridge(self):
        """`degraded` means the bridge is talking but something about it is unhappy — it is still
        driving the PTY. Treating it as offline releases a Console the operator is using, which is
        the same failure as the output-age rule, reached a different way. No other fixture here uses
        a degraded environment, so this mutation survived until it existed."""
        self._seed(env_status="degraded")
        self.assertIsNotNone(self._release(), "a degraded-but-live environment lost its Console")

    # ── the owner is dead: the terminal is released ──────────────────────────────────────────

    def test_a_terminal_that_has_ENDED_is_released(self):
        """`stopped`/`failed` is the PTY saying it is gone. Keeping the claim refused after that
        stops the agent taking work for a Console that no longer exists."""
        for status in ("stopped", "failed"):
            with self.subTest(status=status):
                self.setUp()
                self._seed(terminal_status=status)
                self.assertIsNone(self._release(), f"a {status} terminal kept its claim")

    def test_an_owner_whose_ENVIRONMENT_is_offline_is_released(self):
        """The bridge that owned the PTY is gone, so nothing can be driving it."""
        self._seed(env_last_seen_offset=-7200)
        self.assertIsNone(self._release())

    def test_an_owner_whose_terminal_belongs_to_ANOTHER_bridge_is_released(self):
        """A superseded bridge's terminal. The environment is online, but this handle is not the one
        the live bridge owns — keeping it would refuse claims on behalf of a dead process."""
        self._seed(terminal_bridge="bridge-superseded")
        self.assertIsNone(self._release())

    def test_a_session_with_NO_terminal_is_released(self):
        """Console ownership without a terminal row is bookkeeping left behind by a crash; there is
        nothing alive to protect."""
        self._seed(with_terminal=False)
        self.assertIsNone(self._release())

    # ── what releasing actually writes ───────────────────────────────────────────────────────

    def test_releasing_hands_the_session_back_to_MANAGED(self):
        """The point of the release: the next managed claim must be able to take the handle. Leaving
        `owner_mode='console'` refuses it again on the very next poll."""
        self._seed(terminal_status="stopped")
        self._release()
        session = self._rows("SELECT owner_mode, terminal_status FROM agent_sessions WHERE id = ?",
                             (SESSION,))[0]
        self.assertEqual(session["owner_mode"], "managed")
        self.assertEqual(session["terminal_status"], "failed")

    def test_releasing_marks_the_terminal_failed_and_stamps_when(self):
        self._seed(terminal_status="stopped")
        self._release()
        terminal = self._rows("SELECT status, stopped_at, error FROM terminal_sessions WHERE id = ?",
                              (TERMINAL,))[0]
        self.assertEqual(terminal["status"], "failed")
        self.assertTrue(terminal["stopped_at"], "a released terminal with no stop time cannot age out")
        self.assertIn("Released stale Console owner", terminal["error"])

    def test_releasing_does_NOT_overwrite_an_existing_error(self):
        """The original error is why the terminal died. Replacing it with the release note loses the
        only record of the actual failure."""
        self._seed(terminal_status="failed")
        self._write(
            "UPDATE terminal_sessions SET error = ? WHERE id = ?",
            ("codex exited: model not found", TERMINAL),
        )
        self._release()
        error = self._rows("SELECT error FROM terminal_sessions WHERE id = ?", (TERMINAL,))[0]["error"]
        self.assertEqual(error, "codex exited: model not found")

    def test_releasing_records_WHO_took_it_and_from_whom(self):
        """Ownership changing hands silently is indistinguishable from a bug when someone reads the
        row later — the same reason the virtual-terminal takeover is audited."""
        self._seed(terminal_bridge="bridge-superseded")
        self._release(bridge_id="bridge-two")
        events = self._rows(
            "SELECT event_type, body FROM terminal_events WHERE terminal_id = ?", (TERMINAL,),
        )
        released = [e for e in events if e["event_type"] == "terminal_owner_released"]
        self.assertEqual(len(released), 1, "the release left no audit trail")
        body = json.loads(released[0]["body"])
        self.assertEqual(body["requestedByBridge"], "bridge-two")
        self.assertEqual(body["previousBridge"], "bridge-superseded")
        self.assertEqual(body["environmentBridge"], BRIDGE)

    def test_a_LIVE_owner_is_left_completely_untouched(self):
        """The refusal path must write nothing at all — a half-release would leave the session
        managed while the Console is still driving the PTY."""
        self._seed()
        self._release()
        session = self._rows("SELECT owner_mode FROM agent_sessions WHERE id = ?", (SESSION,))[0]
        terminal = self._rows("SELECT status FROM terminal_sessions WHERE id = ?", (TERMINAL,))[0]
        self.assertEqual(session["owner_mode"], "console")
        self.assertEqual(terminal["status"], "running")
        self.assertEqual(self._rows("SELECT * FROM terminal_events"), [])
