"""The agent_sessions row a RESIDENT agent gets when it registers itself.

`service/api_core/resident_session_upsert.py` is named by no test file. It writes the
dashboard-visible session for a CLI an operator already has open, and it carries two recorded fixes
whose symptom was the same: duplicate live resident sessions for one agent.

THE SESSION ID MUST BE STABLE ACROSS RELAUNCHES. `session_handle`, `gatewayUrl` and `bridge_id` all
rotate every launch, so an id derived from any of them could never match on conflict — every relaunch
minted a new `resident_*` row and the old one stayed running. It is now a uuid5 over
`(agent_id, runtime, machine)`, none of which rotate, so a relaunch UPDATES the row it already has.

AND THE COLLAPSE IS THE BELT TO THAT BRACES. Whatever else is live and resident for this agent gets
retired, so exactly one resident session stays running. It is scoped to `mode = 'resident'` — a
resident registration that stopped the agent's MANAGED session would kill a worker that is mid-run,
which is the one thing worse than showing two rows.

WHAT IT DOES NOT WRITE MATTERS TOO. The conflict clause updates runtime, workspace, ownership,
handle, capabilities, telemetry and liveness — but not `environment_id` and not `started_at`. The
session keeps the moment it first started, which is what makes an uptime column mean anything.

NO ENVIRONMENT, NO SESSION. An agent whose machine matches no registered environment gets `""` back
and no row at all: a session belongs to an environment, and inventing one for a host nothing knows
about would put a session on the dashboard that no bridge could ever drive.
"""

from __future__ import annotations

import asyncio
import json
import unittest

import aiosqlite

from service.api_core.resident_session_upsert import _upsert_resident_agent_session
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "rs-agent"
OTHER_AGENT = "rs-other"
MACHINE = "linux:test-host"
NOW = "2026-08-17T12:00:00Z"


class ResidentSessionTestCase(FastApiTestCase):
    DB_NAME = "aify-resident-session-test.db"

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(row) for row in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_env(self, env_id: str = "env-1", *, machine_id: str = MACHINE,
                  status: str = "online", last_seen: str = "2026-08-17T00:00:00Z") -> None:
        self._write(
            "INSERT INTO environments (id, label, machine_id, os, kind, bridge_id, registered_at,"
            " last_seen, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (env_id, "lab", machine_id, "linux", "wsl", "bridge-1",
             "2026-08-17T00:00:00Z", last_seen, status),
        )

    def _seed_session(self, session_id: str, *, agent_id: str = AGENT, mode: str = "resident",
                      status: str = "running") -> None:
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode,"
            " status, started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, agent_id, "env-1", "claude-code", "/w", mode, status,
             "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
        )

    def _upsert(self, *, agent_id: str = AGENT, runtime: str = "claude-code",
                workspace: str = "/w", machine_id: str = MACHINE, session_handle: str = "",
                runtime_config=None, bridge_id: str = "bridge-1", capabilities=None,
                now: str = NOW) -> str:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                session_id = await _upsert_resident_agent_session(
                    db, agent_id=agent_id, runtime=runtime, workspace=workspace,
                    machine_id=machine_id, session_handle=session_handle,
                    runtime_config=runtime_config, bridge_id=bridge_id,
                    capabilities=capabilities, now=now)
                await db.commit()
                return session_id

        return asyncio.run(run())

    def _sessions(self, agent_id: str = AGENT) -> list[dict]:
        return self._rows(
            "SELECT * FROM agent_sessions WHERE agent_id = ? ORDER BY id", (agent_id,))


class EnvironmentResolutionTests(ResidentSessionTestCase):
    def test_a_matching_environment_yields_a_session(self):
        self._seed_env()
        session_id = self._upsert()
        self.assertTrue(session_id.startswith("resident_"))
        self.assertEqual(len(self._sessions()), 1)

    def test_NO_MACHINE_writes_nothing(self):
        """A session belongs to an environment, and without a machine there is nothing to resolve
        one from. Returning an id for a row that was never written would leave the caller holding a
        reference to nothing."""
        self._seed_env()
        self.assertEqual(self._upsert(machine_id=""), "")
        self.assertEqual(self._sessions(), [])

    def test_a_BLANK_machine_does_not_match_a_BLANK_ENVIRONMENT(self):
        """Why the lookup is guarded rather than just run. An environment registered without a
        machine id stores `''`, and `lower('') = lower('')` matches it — so without the guard an
        agent that reported no machine would be bound to whichever environment also has none,
        which is a session attached to a host neither of them named."""
        self._seed_env(machine_id="")
        self.assertEqual(self._upsert(machine_id=""), "")
        self.assertEqual(self._sessions(), [])

    def test_an_UNKNOWN_machine_writes_nothing(self):
        """A host no environment has registered. Inventing a session for it would put a row on the
        dashboard that no bridge could ever drive."""
        self._seed_env(machine_id="linux:some-other-host")
        self.assertEqual(self._upsert(), "")
        self.assertEqual(self._sessions(), [])

    def test_the_machine_match_is_CASE_INSENSITIVE(self):
        """Machine ids are assembled from hostnames, and case is not preserved consistently across
        the platforms this fleet runs on. A case-sensitive match silently drops the session."""
        self._seed_env(machine_id="Linux:Test-Host")
        self.assertTrue(self._upsert(machine_id="linux:TEST-HOST"))

    def test_a_FORGOTTEN_environment_is_not_resolved(self):
        """Forgotten means an operator retired it. Binding a fresh session to it would resurrect a
        row that was deliberately taken out of the fleet."""
        self._seed_env(status="forgotten")
        self.assertEqual(self._upsert(), "")

    def test_the_MOST_RECENTLY_SEEN_environment_wins(self):
        """One machine can carry several registered environments — a WSL distro re-registered under
        a new id, for instance. The live one is the one that beat most recently."""
        self._seed_env("env-stale", last_seen="2026-08-01T00:00:00Z")
        self._seed_env("env-fresh", last_seen="2026-08-17T00:00:00Z")
        self._upsert()
        self.assertEqual(self._sessions()[0]["environment_id"], "env-fresh")


class StableIdentityTests(ResidentSessionTestCase):
    def test_a_RELAUNCH_updates_the_same_row(self):
        """FIX 1, and the whole reason the id is derived the way it is. Every relaunch brings a new
        handle, a new gateway url and a new bridge id; if any of them fed the id, the conflict
        clause could never match and a second live row appeared each time."""
        self._seed_env()
        first = self._upsert(session_handle="handle-A", bridge_id="bridge-A")
        second = self._upsert(session_handle="handle-B", bridge_id="bridge-B")
        self.assertEqual(first, second)
        self.assertEqual(len(self._sessions()), 1)

    def test_the_relaunch_carries_the_NEW_handle_and_owner(self):
        self._seed_env()
        self._upsert(session_handle="handle-A", bridge_id="bridge-A")
        self._upsert(session_handle="handle-B", bridge_id="bridge-B")
        row = self._sessions()[0]
        self.assertEqual(row["session_handle"], "handle-B")
        self.assertEqual(row["owner_bridge_id"], "bridge-B")

    def test_a_DIFFERENT_RUNTIME_is_a_different_session(self):
        """The same agent id running a different CLI is a different thing on the operator's screen,
        and the two must not overwrite each other's row."""
        self._seed_env()
        self.assertNotEqual(self._upsert(runtime="claude-code"), self._upsert(runtime="codex"))

    def test_a_DIFFERENT_AGENT_is_a_different_session(self):
        self._seed_env()
        self.assertNotEqual(self._upsert(agent_id=AGENT), self._upsert(agent_id=OTHER_AGENT))

    def test_the_id_does_not_move_when_the_WORKSPACE_changes(self):
        """Workspace is updated in place, not part of the identity — an operator who reopens the
        same CLI in another directory has not started a second session."""
        self._seed_env()
        first = self._upsert(workspace="/one")
        self.assertEqual(self._upsert(workspace="/two"), first)
        self.assertEqual(self._sessions()[0]["workspace"], "/two")


class WhatTheUpsertPreservesTests(ResidentSessionTestCase):
    def test_STARTED_AT_survives_a_relaunch(self):
        """Absent from the conflict clause on purpose. It is the moment this session first came up,
        and rewriting it on every relaunch makes an uptime column report the last restart instead."""
        self._seed_env()
        self._upsert(now="2026-08-17T09:00:00Z")
        self._upsert(now="2026-08-17T10:00:00Z")
        row = self._sessions()[0]
        self.assertEqual(row["started_at"], "2026-08-17T09:00:00Z")
        self.assertEqual(row["last_seen"], "2026-08-17T10:00:00Z")

    def test_a_session_that_had_ENDED_is_revived_rather_than_duplicated(self):
        """`ended_at = NULL` and `status = 'running'`. The operator closed the CLI and opened it
        again; that is the same session resuming, not a new one beside a corpse."""
        self._seed_env()
        session_id = self._upsert()
        self._write("UPDATE agent_sessions SET status = 'stopped', ended_at = ? WHERE id = ?",
                    ("2026-08-17T11:00:00Z", session_id))
        self._upsert()
        row = self._sessions()[0]
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["ended_at"])
        self.assertEqual(len(self._sessions()), 1)

    def test_the_session_is_marked_RESIDENT_and_owned_by_the_resident_path(self):
        self._seed_env()
        self._upsert()
        row = self._sessions()[0]
        self.assertEqual(row["mode"], "resident")
        self.assertEqual(row["owner_mode"], "resident")

    def test_no_TERMINAL_or_SPAWN_linkage_is_invented(self):
        """Nothing was started here — the process already existed. A terminal id or spawn reference
        on this row would point the console and the spawn reaper at something that never spawned."""
        self._seed_env()
        self._upsert()
        row = self._sessions()[0]
        self.assertEqual(row["terminal_id"], "")
        self.assertIsNone(row["spawn_request_id"])
        self.assertIsNone(row["spawn_spec_id"])


class TelemetryTests(ResidentSessionTestCase):
    def _telemetry(self) -> dict:
        return json.loads(self._sessions()[0]["telemetry"])

    def test_it_always_reports_resident_and_cli_attach(self):
        self._seed_env()
        self._upsert()
        telemetry = self._telemetry()
        self.assertIs(telemetry["resident"], True)
        self.assertIs(telemetry["cliAttach"], True)

    def test_NATIVE_RESUME_follows_the_session_handle(self):
        """It records whether this session can be reopened by the runtime itself. Reporting it
        without a handle would tell the dashboard a resume is available that cannot be performed."""
        self._seed_env()
        self._upsert(session_handle="")
        self.assertIs(self._telemetry()["nativeResume"], False)
        self._upsert(session_handle="handle-A")
        self.assertIs(self._telemetry()["nativeResume"], True)

    def test_BRIDGE_RESUME_follows_the_bridge_id(self):
        self._seed_env()
        self._upsert(bridge_id="")
        self.assertIs(self._telemetry()["bridgeResume"], False)
        self._upsert(bridge_id="bridge-1")
        self.assertIs(self._telemetry()["bridgeResume"], True)

    def test_GATEWAY_follows_a_non_blank_gateway_url(self):
        """Stripped, so a config carrying whitespace does not read as a live gateway."""
        self._seed_env()
        for config, expected in (
            (None, False),
            ({}, False),
            ({"gatewayUrl": "   "}, False),
            ({"gatewayUrl": "ws://127.0.0.1:9000/api/ws"}, True),
        ):
            with self.subTest(config=config):
                self._upsert(runtime_config=config)
                self.assertIs(self._telemetry()["gateway"], expected)

    def test_a_NON_DICT_runtime_config_is_survived(self):
        """Callers pass whatever came off the row. A string or None must not raise inside a
        registration — the session is the point, the telemetry is decoration."""
        self._seed_env()
        for config in (None, "not-a-dict", 42, []):
            with self.subTest(config=config):
                self.assertTrue(self._upsert(runtime_config=config))

    def test_the_APP_SERVER_URL_is_lifted_out_of_the_config(self):
        """Codex resident sessions are driven through it. It is a column rather than telemetry
        because the delivery path reads it."""
        self._seed_env()
        self._upsert(runtime_config={"appServerUrl": "  ws://127.0.0.1:1455  "})
        self.assertEqual(self._sessions()[0]["app_server_url"], "ws://127.0.0.1:1455")

    def test_the_CAPABILITIES_are_recorded_on_the_session(self):
        self._seed_env()
        self._upsert(capabilities=["resident-run", "steer"])
        stored = json.loads(self._sessions()[0]["capabilities"])
        self.assertEqual(stored["capabilities"], ["resident-run", "steer"])

    def test_MISSING_capabilities_record_an_empty_list_not_null(self):
        self._seed_env()
        self._upsert(capabilities=None)
        self.assertEqual(json.loads(self._sessions()[0]["capabilities"])["capabilities"], [])


class DuplicateCollapseTests(ResidentSessionTestCase):
    def test_another_LIVE_resident_session_is_retired(self):
        """RC3. The dashboard showing two live resident sessions for one agent is the symptom; the
        operator cannot tell which one their CLI is."""
        self._seed_env()
        self._seed_session("resident_stale")
        self._upsert()
        rows = {row["id"]: row for row in self._sessions()}
        self.assertEqual(rows["resident_stale"]["status"], "stopped")
        self.assertEqual(rows["resident_stale"]["ended_at"], NOW)

    def test_the_session_just_written_is_NOT_retired(self):
        """`id != ?`. Without it the collapse would stop the row it just created, and every resident
        registration would end with no live session at all."""
        self._seed_env()
        session_id = self._upsert()
        self.assertEqual(
            self._rows("SELECT status FROM agent_sessions WHERE id = ?", (session_id,))[0]["status"],
            "running")

    def test_a_MANAGED_session_for_the_same_agent_is_LEFT_ALONE(self):
        """The scope that matters most. A managed session is a live worker mid-run; stopping it
        because the operator opened a CLI would kill work nobody asked to stop."""
        self._seed_env()
        self._seed_session("managed-1", mode="managed")
        self._upsert()
        rows = {row["id"]: row for row in self._sessions()}
        self.assertEqual(rows["managed-1"]["status"], "running")

    def test_ANOTHER_AGENTS_resident_session_is_left_alone(self):
        self._seed_env()
        self._seed_session("resident_other", agent_id=OTHER_AGENT)
        self._upsert()
        self.assertEqual(self._sessions(OTHER_AGENT)[0]["status"], "running")

    def test_an_ALREADY_TERMINAL_session_is_not_re_stamped(self):
        """A session that already stopped keeps the moment it stopped. Re-ending it would move a
        historical timestamp forward every time the agent registers."""
        self._seed_env()
        for status in ("stopped", "failed", "exited"):
            self._seed_session(f"resident_{status}", status=status)
        self._write("UPDATE agent_sessions SET ended_at = ? WHERE status IN"
                    " ('stopped','failed','exited')", ("2020-06-01T00:00:00Z",))
        self._upsert()
        rows = {row["id"]: row for row in self._sessions()}
        for status in ("stopped", "failed", "exited"):
            with self.subTest(status=status):
                self.assertEqual(rows[f"resident_{status}"]["ended_at"], "2020-06-01T00:00:00Z")
                self.assertEqual(rows[f"resident_{status}"]["status"], status)


if __name__ == "__main__":
    unittest.main()
