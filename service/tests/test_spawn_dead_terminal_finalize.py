"""v0.2 WS-2 — a dead worker's spawn_request must be finalized, whatever killed it.

TRACE THIS ENCODES (measured on the live DB, 2026-08-07):

    spawn_1786109794441_a620d173  claimed  13:36:34  status=running
    term_1786109794427_0f32fd75   stopped  13:37:39  (65s failed hermes launch)
    ...spawn still status=running, finished_at=NULL for 97 minutes...
    spawn finished_at 15:14:38    "Superseded by a newer live managed session"

Nothing reconciled it. `_fail_orphaned_running_spawn_requests` correctly skipped it
(the env bridge stayed ONLINE — only the worker died), and `report_terminal_dead`,
which does finalize the spawn, was never called: the terminal row carries no
`console_dead_reported` event and an empty `error`, so a different one of the ~26
`UPDATE terminal_sessions` sites stopped it.

The consequence that made this operator-visible: for 5 minutes after the death,
`_has_pending_or_booting_spawn_request` still reports "a spawn is in flight", so the
dead worker suppresses the very respawn its death required. The last test here
asserts exactly that outcome rather than the mechanism.
"""

import asyncio
import unittest

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now
from service.tests._base import FastApiTestCase
from service.api_core.managed_env import _has_pending_or_booting_spawn_request
from service.api_core import terminal_status  # v0.5.4: call the OWNER
from service.clock import now as _now
from service.reconcilers.spawn_terminal_settlement import _finalize_spawns_with_dead_terminals


class _SpawnSeedMixin:
    """Row-seeding helpers shared by the two suites below.

    A plain mixin, NOT a TestCase, so the test runner does not collect it and
    neither suite re-runs the other's tests.
    """

    OLD = "2020-01-01T00:00:00Z"

    # ---- helpers ----------------------------------------------------------
    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _fetchone(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                return await (await db.execute(query, params)).fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _finalize(self, **kwargs):
        async def _run():
            db = await get_db()
            try:
                return await _finalize_spawns_with_dead_terminals(db, **kwargs)
            finally:
                await db.close()

        return asyncio.run(_run())

    def _has_pending_spawn(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                return await _has_pending_or_booting_spawn_request(db, agent_id)
            finally:
                await db.close()

        return asyncio.run(_run())

    def _seed(
        self,
        agent_id,
        *,
        spawn_status="running",
        terminal_status="stopped",
        died_at=None,
        output="",
        terminal_error="",
        extra_live_terminal=False,
        spawn_updated_at=None,
    ):
        """One agent + session + spawn_request + bound terminal."""
        died_at = died_at or self.OLD
        session_id = f"sess_{agent_id}"
        terminal_id = f"term_{agent_id}"
        spawn_id = f"spawn_{agent_id}"
        self._execute(
            """INSERT INTO agents (id, name, role, runtime, session_mode, status, registered_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (agent_id, agent_id, "coder", "hermes", "managed", "idle", self.OLD, self.OLD),
        )
        self._execute(
            """INSERT INTO environments (id, label, machine_id, bridge_id, status, registered_at, last_seen)
               VALUES (?,?,?,?,?,?,?)""",
            (f"env_{agent_id}", "env", "win32:test", f"bridge_{agent_id}", "online", self.OLD, _now()),
        )
        self._execute(
            """INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (f"spec_{agent_id}", agent_id, f"env_{agent_id}", "hermes", self.OLD, self.OLD),
        )
        # spawn_requests BEFORE agent_sessions: agent_sessions carries FKs to both
        # spawn_specs and spawn_requests, and an empty-string id violates them.
        self._execute(
            """INSERT INTO spawn_requests
                 (id, spawn_spec_id, agent_id, environment_id, runtime, mode, status,
                  session_id, claimed_by_bridge_id, claimed_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                spawn_id, f"spec_{agent_id}", agent_id, f"env_{agent_id}", "hermes", "managed-warm",
                spawn_status, session_id, f"bridge_{agent_id}", self.OLD, self.OLD,
                spawn_updated_at or self.OLD,
            ),
        )
        self._execute(
            """INSERT INTO agent_sessions
                 (id, agent_id, environment_id, runtime, mode, status, spawn_spec_id,
                  spawn_request_id, started_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, agent_id, f"env_{agent_id}", "hermes", "managed-warm", "running",
                f"spec_{agent_id}", spawn_id, self.OLD, self.OLD,
            ),
        )
        self._execute(
            """INSERT INTO terminal_sessions
                 (id, agent_id, session_id, environment_id, runtime, bridge_id, command, status,
                  output, error, created_at, updated_at, stopped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                terminal_id, agent_id, session_id, f"env_{agent_id}", "hermes",
                f"bridge_{agent_id}", "hermes-aify",
                terminal_status, output, terminal_error, self.OLD, died_at, died_at,
            ),
        )
        if extra_live_terminal:
            self._execute(
                """INSERT INTO terminal_sessions
                     (id, agent_id, session_id, environment_id, runtime, bridge_id, command,
                      status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"{terminal_id}_live", agent_id, session_id, f"env_{agent_id}", "hermes",
                    f"bridge_{agent_id}", "hermes-aify", "attached", _now(), _now(),
                ),
            )
        return spawn_id

    def _spawn(self, spawn_id):
        return self._fetchone(
            "SELECT status, finished_at, error FROM spawn_requests WHERE id = ?", (spawn_id,)
        )


class SpawnDeadTerminalFinalizeTests(_SpawnSeedMixin, FastApiTestCase):
    DB_NAME = "aify-spawn-dead-terminal.db"

    # ---- the incident ------------------------------------------------------
    def test_finalizes_a_running_spawn_whose_terminal_is_stopped(self):
        spawn_id = self._seed("dead-worker")
        self.assertEqual(self._finalize(), 1)
        row = self._spawn(spawn_id)
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["finished_at"])
        self.assertIn("term_dead-worker", row["error"])
        self.assertIn("stopped", row["error"])

    def test_records_the_terminals_own_recorded_cause(self):
        """WS-1 composes here: the refusal an agent later reads names the real cause."""
        spawn_id = self._seed(
            "hermes-fatal",
            output=(
                "[terminal attached pid=49060]\n"
                "\x1b[?25h[hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/"
                " did not become ready within 60000ms: fetch failed\r\n"
                "[terminal exited]\n"
            ),
        )
        self.assertEqual(self._finalize(), 1)
        error = self._spawn(spawn_id)["error"]
        self.assertIn("did not become ready", error)
        self.assertNotIn("\x1b", error)

    def test_falls_back_to_the_terminal_error_column(self):
        spawn_id = self._seed("no-output", output="", terminal_error="Console PTY process is no longer alive.")
        self.assertEqual(self._finalize(), 1)
        self.assertIn("no longer alive", self._spawn(spawn_id)["error"])

    def test_says_so_when_nothing_was_recorded(self):
        spawn_id = self._seed("silent", output="[terminal attached pid=1]\n[terminal exited]\n")
        self.assertEqual(self._finalize(), 1)
        self.assertIn("no output was recorded", self._spawn(spawn_id)["error"])

    def test_the_next_coldstart_is_no_longer_suppressed(self):
        """THE operator-visible outcome, asserted end-to-end.

        Seeded with a FRESH updated_at so the 5-minute in-flight window is open —
        which is the state a just-died worker is actually in.
        """
        self._seed("respawn-me", spawn_updated_at=_now())
        self.assertTrue(
            self._has_pending_spawn("respawn-me"),
            "precondition: the dead worker's spawn must look in-flight before the fix runs",
        )
        self.assertEqual(self._finalize(), 1)
        self.assertFalse(
            self._has_pending_spawn("respawn-me"),
            "after finalizing, the dead worker must not suppress its own respawn",
        )

    # ---- safety ------------------------------------------------------------
    def test_leaves_a_spawn_whose_terminal_is_still_live(self):
        spawn_id = self._seed("alive", terminal_status="attached")
        self.assertEqual(self._finalize(), 0)
        self.assertEqual(self._spawn(spawn_id)["status"], "running")

    def test_leaves_a_spawn_when_a_rebind_race_left_a_live_sibling_terminal(self):
        """A managed respawn can create the new terminal before the session is
        re-pointed at it. The dead sibling must NOT fail the healthy worker."""
        spawn_id = self._seed("rebinding", extra_live_terminal=True)
        self.assertEqual(self._finalize(), 0)
        self.assertEqual(self._spawn(spawn_id)["status"], "running")

    def test_a_masked_candidate_is_named_in_the_log(self):
        """The live-sibling guard is correct but SILENT, and a masked row is otherwise
        indistinguishable from "nothing was dead" (reviewer suggestion, 2026-08-07)."""
        self._seed("masked", extra_live_terminal=True)
        # THE LOGGER NAME IS A LOCATION PIN, and `_finalize_spawns_with_dead_terminals` moved to
        # `spawn_terminal_settlement.py` in v0.5.4. `assertLogs` on a module that no longer emits
        # fails with "no logs of level INFO or higher triggered" — which reads like the guard went
        # silent, the exact failure this test exists to detect. Named after the module that logs.
        with self.assertLogs("service.reconcilers.spawn_terminal_settlement", level="INFO") as captured:
            self.assertEqual(self._finalize(), 0)
        joined = " ".join(captured.output)
        self.assertIn("0 finalized, 1 left alone", joined)
        self.assertIn("live sibling terminal", joined)

    def test_nothing_is_logged_when_there_is_nothing_to_report(self):
        """A quiet sweep must stay quiet — this runs every 60s."""
        self._seed("plain-dead")
        with self.assertNoLogs("service.reconcilers.spawn_lifecycle", level="INFO"):
            self.assertEqual(self._finalize(), 1)

    def test_respects_the_grace_window(self):
        spawn_id = self._seed("just-died", died_at=_now())
        self.assertEqual(self._finalize(), 0)
        self.assertEqual(self._spawn(spawn_id)["status"], "running")

    def test_leaves_a_spawn_whose_death_time_is_undeterminable(self):
        spawn_id = self._seed("no-clock")
        self._execute(
            "UPDATE terminal_sessions SET stopped_at = '', updated_at = '' WHERE id = ?",
            ("term_no-clock",),
        )
        self.assertEqual(self._finalize(), 0)
        self.assertEqual(self._spawn(spawn_id)["status"], "running")

    def test_never_touches_an_already_terminal_spawn(self):
        spawn_id = self._seed("done", spawn_status="completed")
        self.assertEqual(self._finalize(), 0)
        self.assertEqual(self._spawn(spawn_id)["status"], "completed")

    def test_never_overwrites_an_existing_error(self):
        spawn_id = self._seed("has-error")
        self._execute("UPDATE spawn_requests SET error = 'original cause' WHERE id = ?", (spawn_id,))
        self.assertEqual(self._finalize(), 1)
        self.assertEqual(self._spawn(spawn_id)["error"], "original cause")

    def test_finalizes_starting_as_well_as_running(self):
        spawn_id = self._seed("starting-worker", spawn_status="starting")
        self.assertEqual(self._finalize(), 1)
        self.assertEqual(self._spawn(spawn_id)["status"], "failed")

    def test_is_idempotent(self):
        self._seed("twice")
        self.assertEqual(self._finalize(), 1)
        self.assertEqual(self._finalize(), 0)

    def test_ignores_a_spawn_with_no_session_binding(self):
        spawn_id = self._seed("unbound")
        self._execute("UPDATE spawn_requests SET session_id = '' WHERE id = ?", (spawn_id,))
        self.assertEqual(self._finalize(), 0)
        self.assertEqual(self._spawn(spawn_id)["status"], "running")

    def test_limit_is_honoured(self):
        for i in range(3):
            self._seed(f"many-{i}")
        self.assertEqual(self._finalize(limit=2), 2)
        self.assertEqual(self._finalize(), 1)


class HistoricalConsoleReadTests(_SpawnSeedMixin, FastApiTestCase):
    """v0.2 WS-1 retrieval half — GET /agents/{id}/console for a DEAD worker.

    The bytes were never missing. Until v0.2 this endpoint answered "no live console"
    and nothing would serve the recording, so the one agent that needed the diagnosis
    could not reach it.
    """

    DB_NAME = "aify-historical-console.db"

    HERMES_FATAL = (
        "[terminal attached pid=49060]\n"
        "\x1b[?25h[hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/"
        " did not become ready within 60000ms: fetch failed\r\n"
        "[hermes-aify] FATAL: managed gateway host for 'sc-architect' did not come up.\r\n"
        "[terminal exited]\n"
    )

    def _console(self, agent_id, **params):
        response = self.client.get(f"/api/v1/agents/{agent_id}/console", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_serves_the_dead_workers_recorded_output(self):
        self._seed("dead-hermes", output=self.HERMES_FATAL)
        body = self._console("dead-hermes")
        self.assertFalse(body["live"])
        self.assertTrue(body["historical"])
        self.assertEqual(body["terminalId"], "term_dead-hermes")
        self.assertEqual(body["status"], "stopped")
        self.assertIn("did not become ready", body["output"])

    def test_leads_with_the_one_line_cause(self):
        self._seed("dead-hermes-2", output=self.HERMES_FATAL)
        body = self._console("dead-hermes-2")
        self.assertEqual(
            body["failureLine"],
            "[hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/"
            " did not become ready within 60000ms: fetch failed",
        )

    def test_says_it_is_not_live_so_it_cannot_be_read_as_a_running_session(self):
        self._seed("dead-hermes-3", output=self.HERMES_FATAL)
        body = self._console("dead-hermes-3")
        self.assertIn("NO live console", body["message"])
        self.assertIn("not a running session", body["message"])

    def test_never_leaks_ansi_or_scaffolding(self):
        self._seed("dead-hermes-4", output=self.HERMES_FATAL)
        body = self._console("dead-hermes-4")
        self.assertNotIn("\x1b", body["output"])
        self.assertNotIn("[terminal exited", body["output"])
        self.assertNotIn("[terminal attached", body["output"])

    def test_falls_back_to_the_error_column_when_no_output_was_recorded(self):
        self._seed("silent-death", output="", terminal_error="Console PTY process is no longer alive.")
        body = self._console("silent-death")
        self.assertTrue(body["historical"])
        self.assertIn("no longer alive", body["failureLine"])

    def test_an_agent_that_never_had_a_terminal_is_unchanged(self):
        """The pre-v0.2 answer must survive for an agent with nothing recorded."""
        self._execute(
            """INSERT INTO agents (id, name, role, runtime, session_mode, status, registered_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("never-ran", "never-ran", "coder", "hermes", "managed", "idle", self.OLD, self.OLD),
        )
        body = self._console("never-ran")
        self.assertFalse(body["live"])
        self.assertFalse(body["historical"])
        self.assertIn("lazy-starts", body["message"])

    def test_unknown_agent_still_404s(self):
        response = self.client.get("/api/v1/agents/no-such-agent/console")
        self.assertEqual(response.status_code, 404, response.text)

    def test_lines_parameter_bounds_the_recording(self):
        self._seed("chatty", output="\n".join(f"line {i}" for i in range(80)))
        body = self._console("chatty", lines=5)
        self.assertLessEqual(len(body["output"].splitlines()), 5)

    def test_read_is_side_effect_free(self):
        """It must never start a worker or resurrect the spawn it reports on."""
        spawn_id = self._seed("read-only", output=self.HERMES_FATAL)
        before = dict(self._spawn(spawn_id))
        self._console("read-only")
        self.assertEqual(dict(self._spawn(spawn_id)), before)
        self.assertEqual(
            self._fetchone("SELECT COUNT(*) c FROM terminal_sessions WHERE agent_id = ?", ("read-only",))["c"],
            1,
        )

    def test_prefers_the_newest_attempt_when_several_terminals_exist(self):
        self._seed("many-tries", output="first attempt: fatal: old cause")
        self._execute(
            """INSERT INTO terminal_sessions
                 (id, agent_id, session_id, environment_id, runtime, bridge_id, command, status,
                  output, created_at, updated_at, stopped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "term_many-tries_new", "many-tries", "sess_many-tries", "env_many-tries", "hermes",
                "bridge_many-tries", "hermes-aify", "failed", "second attempt: fatal: new cause",
                "2026-08-07T13:36:34Z", "2026-08-07T13:37:39Z", "2026-08-07T13:37:39Z",
            ),
        )
        body = self._console("many-tries")
        self.assertEqual(body["terminalId"], "term_many-tries_new")
        self.assertIn("new cause", body["failureLine"])


class TerminalStatusSetAgreementTests(unittest.TestCase):
    """v0.2 WS-4 — the cheap half of the duplication findings.

    N7 was a real bug caused by two sweeps disagreeing about a status literal. The
    expensive remedy is consolidating every copy; the cheap one is a test that the
    copies AGREE. This pins the ordered SQL-binding tuple to the named set, so a
    future edit to one of them fails the suite instead of drifting silently.
    """

    def test_terminal_status_sets_agree(self):
        self.assertEqual(
            set(terminal_status._TERMINAL_END_STATUSES_ORDERED),
            {s.lower() for s in terminal_status._TERMINAL_END_STATUSES},
        )

    def test_ordered_form_is_deterministic_and_lowercase(self):
        ordered = terminal_status._TERMINAL_END_STATUSES_ORDERED
        self.assertEqual(ordered, tuple(sorted(ordered)))
        self.assertTrue(all(s == s.lower() for s in ordered))
        self.assertEqual(len(ordered), len(set(ordered)))


if __name__ == "__main__":
    unittest.main()
