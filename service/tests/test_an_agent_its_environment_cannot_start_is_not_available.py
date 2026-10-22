"""A managed agent whose workspace its environment will not start reads misconfigured, not available (0.9.2 fix C).

Steven, 2026-10-13: "should not be available if env does not allow running". aify-env starts work only below the
folder it was started in and advertises that as `advertisedCwdRoots`; dashboard-manager's workspace was outside it,
read `available`, and every start was refused.

AFTER THE 0.9.8 SWITCH the served word is aify-env's. The rule is aify-env's to apply: an outside-roots definition
publishes `misconfigured` (aify-env f704bcdf, the env-side correction SW5 review R5 asked for), with no reason on the
wire. comms serves that word, never `available`, and keeps its own note beside it, since it holds both facts (the
definition's workspace and the environment's advertised roots). The legacy derivation a rollback serves keeps its own
witnesses below (env_records.rollback_agent).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess

import pytest

from service.api_core.workspace import workspace_within_roots
from service.tests import env_records
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import ENV_REPOS

#: (workspace, roots, windows) specimens, judged by both implementations.
SPECIMENS = [
    ("C:/Docker/aify-dashboard", ["C:/Docker/aify-comms"], True),
    ("C:/Docker/aify-comms/sub", ["C:/Docker/aify-comms"], True),
    ("c:\\docker\\AIFY-COMMS", ["C:/Docker/aify-comms/"], True),
    ("C:/Docker", ["C:\\"], True),
    ("/home/Bob", ["/home/bob"], False),
    ("/home/bob", ["/home/bo"], False),
    ("/home/bob/x", ["/home/bob"], False),
    ("/anything", ["/"], False),
    ("", ["/"], False),
]


def test_the_containment_rule_is_aify_envs_specimen_for_specimen():
    repo = next((r for r in ENV_REPOS if (r / "lib" / "workspace-roots.mjs").is_file()), None)
    if repo is None or shutil.which("node") is None:
        pytest.skip("no aify-env checkout with lib/workspace-roots.mjs, or no node")
    module = (repo / "lib" / "workspace-roots.mjs").as_uri()
    script = (f"import({json.dumps(module)}).then(m => console.log(JSON.stringify(JSON.parse(process.argv[1])"
              ".map(([w, r, windows]) => m.workspaceWithinRoots(w, r, { windows })))))")
    run = subprocess.run(["node", "-e", script, json.dumps(SPECIMENS)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    theirs = json.loads(run.stdout)
    ours = [workspace_within_roots(w, r, windows=windows) for w, r, windows in SPECIMENS]
    assert ours == theirs
    assert True in ours and False in ours, "the specimens must exercise both answers"


class AnAgentItsEnvironmentCannotStartIsNotAvailable(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": "win32:host:default", "machineId": "win32:host", "os": "win32", "kind": "win32",
            "runtimes": [], "metadata": {"launchRoots": ["C:/Docker/aify-comms"]}}).raise_for_status()  # as aify-env sends it
        db = sqlite3.connect(self._db_path)
        try:
            for agent_id, cwd in (("outside", "C:/Docker/aify-dashboard"), ("inside", "C:/Docker/aify-comms/x")):
                db.execute("INSERT INTO agents (id, role, name, runtime, session_mode, cwd, machine_id, runtime_state, "
                           "registered_at, last_seen) VALUES (?, 'coder', ?, 'claude-code', 'managed', ?, 'win32:host', "
                           "?, '2026-10-13T00:00:00Z', '2026-10-13T00:00:00Z')",
                           (agent_id, agent_id, cwd, json.dumps({"environmentId": "win32:host:default"})))
                # Defined on that host: aify-env starts only definitions, and judges the definition's workspace.
                db.execute("INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, "
                           "definition_digest, body, available, unavailable_reason, host_state, host_problems, "
                           "updated_at) VALUES (?, 'win32:host', 's', 1, 1, 'd', ?, 1, '', 'valid', '[]', "
                           "'2026-10-13T00:00:00Z')", (agent_id, json.dumps({"mode": "managed", "workspace": cwd})))
            db.commit()
        finally:
            db.close()

    def status(self, agent_id):
        agent = env_records.rollback_agent(self, agent_id)  # the legacy word and note, which a rollback serves
        return agent["status"], agent.get("statusNote") or ""

    def _env_says(self, agent_id, state):
        """What its host's aify-env publishes for this defined agent with no process: `available` (startable) or, for
        an outside-roots definition (aify-env f704bcdf), `misconfigured` (config), with no reason on the wire."""
        record = dict(env_records.startable(agent_id), state=state,
                      stateCause="config" if state == "misconfigured" else "startable")
        env_records.report(self, agent_id, records={agent_id: record})
        agent = self.client.get(f"/api/v1/agents/{agent_id}").json()["agent"]
        return agent["status"], agent.get("statusNote") or ""

    def test_the_switched_word_serves_aify_envs_misconfigured_with_comms_own_note(self):
        """The served word once its host's aify-env publishes it misconfigured, and comms' reason beside it."""
        status, note = self._env_says("outside", "misconfigured")
        self.assertEqual(status, "misconfigured")
        self.assertIn("Cannot be started:", note)
        self.assertIn("outside the folder aify-env was started in (C:/Docker/aify-comms)", note)

    def test_the_note_is_kept_when_the_legacy_derivation_never_reached_misconfigured(self):
        """The legacy inputs judge the defect only for an agent whose environment answers; with its heartbeat gone they
        say `offline` and carry no defect, while aify-env still knows the definition is outside its roots. The note
        must come from comms' own facts then too, not from the legacy word happening to agree."""
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = 'win32:host:default'")
            db.commit()
        finally:
            db.close()
        self.assertNotEqual(self.status("outside")[0], "misconfigured", "CONTROL: the legacy word is not misconfigured")
        status, note = self._env_says("outside", "misconfigured")
        self.assertEqual(status, "misconfigured")
        self.assertIn("outside the folder aify-env was started in (C:/Docker/aify-comms)", note)

    def test_CONTROL_inside_the_roots_serves_aify_envs_word_with_no_such_note(self):
        status, note = self._env_says("inside", "available")
        self.assertEqual(status, "available")
        self.assertNotIn("Cannot be started", note)

    def test_a_workspace_outside_the_advertised_folders_is_not_available_and_says_why(self):
        # `misconfigured` is the engine's word for an identity no start can reach until a human fixes it.
        status, note = self.status("outside")
        self.assertEqual(status, "misconfigured")
        self.assertIn("outside the folder aify-env was started in (C:/Docker/aify-comms)", note)

    def test_an_undefined_agent_is_not_judged_by_its_record_cwd(self):
        # aify-env never starts it, for its own reason; its record's cwd is no definition's workspace.
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("DELETE FROM agent_definitions WHERE agent_id = 'outside'")
            db.commit()
        finally:
            db.close()
        self.assertNotIn("outside the folder", self.status("outside")[1])

    def test_one_inside_them_stays_available(self):
        status, note = self.status("inside")
        self.assertEqual(status, "available")
        self.assertNotIn("outside the folder", note)

    def beat(self, **body):
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": "win32:host:default", "machineId": "win32:host", "os": "win32", "kind": "win32", **body}).raise_for_status()

    def test_roots_a_caller_sent_as_cwdroots_are_no_evidence(self):
        # 0.9.2 review: cwdRoots are the service's policy, and aify-env never sends them, so the value some caller left
        # in advertisedCwdRoots said nothing about where aify-env starts work.
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("UPDATE environments SET metadata = json_remove(metadata, '$.launchRoots') WHERE id = 'win32:host:default'")
            db.commit()
        finally:
            db.close()
        self.beat(cwdRoots=["C:/Docker/aify-comms"], runtimes=[])
        self.assertEqual(self.status("outside")[0], "available")

    def test_a_bridge_beat_without_the_bound_keeps_it(self):
        self.beat(bridgeId="some-bridge", runtimes=[], metadata={})
        self.assertEqual(self.status("outside")[0], "misconfigured", "a bridge heartbeat erased aify-env's bound")


    def test_aify_envs_own_heartbeat_body_carries_the_bound_the_status_reads(self):
        """Both ends of the field: the body aify-env's daemon builds, posted as it is, makes the status judge."""
        repo = next((r for r in ENV_REPOS if (r / "lib" / "advertise.mjs").is_file()), None)
        if repo is None or shutil.which("node") is None:
            self.skipTest("no aify-env checkout with lib/advertise.mjs, or no node")
        script = (f"import({json.dumps((repo / 'lib' / 'advertise.mjs').as_uri())}).then(m => console.log(JSON.stringify("
                  "m.environmentAdvertisement({ hostname: 'host', kind: 'win32', os: 'win32', machineId: 'win32:host', "
                  "launchRoots: ['C:/Docker/aify-comms'] }))))")
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("UPDATE environments SET metadata = '{}' WHERE id = 'win32:host:default'")
            db.commit()
        finally:
            db.close()
        self.assertEqual(self.status("outside")[0], "available", "the control: no bound, no judgement")
        self.client.post("/api/v1/environments/heartbeat", json={"id": "win32:host:default", **body}).raise_for_status()
        self.assertEqual(self.status("outside")[0], "misconfigured")


class AnAgentDefinedButNeverStartedIsJudgedToo(FastApiTestCase):
    """Whole-diff review of 0.9.2: only a stored environmentId was read, and a definition published but never started
    has none, so it read available while every start of it would be refused. Published the real way, as aify-env does."""

    ENV = {"environment_id": "win32:fixture:default", "machine_id": "win32:fixture", "bridge_id": "env-bridge"}

    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV["environment_id"], "machineId": self.ENV["machine_id"], "os": "win32", "kind": "win32",
            "bridgeId": self.ENV["bridge_id"], "runtimes": [],
            "metadata": {"terminal": True, "pty": True, "terminalRuntimes": ["hermes"], "launchRoots": ["C:/allowed"]},
        }).raise_for_status()

    def status_of(self, workspace):
        from service.tests.defined_agents import _defined_on, definition_entry, publish
        _defined_on(self, self.ENV["machine_id"])["lead"] = definition_entry(
            "lead", runtime="hermes", workspace=workspace, mode="managed", revision=1)
        publish(self, **self.ENV)
        db = sqlite3.connect(self._db_path)
        try:
            state = db.execute("SELECT runtime_state FROM agents WHERE id = 'lead'").fetchone()[0]
        finally:
            db.close()
        self.assertNotIn("environmentId", json.loads(state or "{}"), "the fixture is no longer an unbound agent")
        agent = env_records.rollback_agent(self, "lead")  # the legacy word and note, which a rollback serves
        return agent["status"], agent.get("statusNote") or ""

    def test_outside_the_folder_it_reads_misconfigured(self):
        status, note = self.status_of("C:/work")
        self.assertEqual(status, "misconfigured", note)
        self.assertIn("outside the folder aify-env was started in (C:/allowed)", note)

    def test_inside_the_folder_it_stays_available(self):
        self.assertEqual(self.status_of("C:/allowed/work")[0], "available")


class TheEnginePushKeepsTheReasonWhateverTheRegistrationSaid(FastApiTestCase):
    """SW5 successor review (REVISE_SOURCE_SW5_324966EF_R5_ENGINE_NOTE). The engine push swapped the composed reason for
    the registry's own note whenever the registry's raw word equalled the view's word, treating a registration's
    `status: misconfigured` as an operator override. Only an operator stop is that (`_MANUAL_STATUSES`).

    Everything goes through the real handlers: registration (with the raw word), the environment heartbeat with its
    launch roots, the host's definition snapshot (outside them), and aify-env's record. The idle-registration twin
    differs only in the raw word; the stop-precedence control is an undefined agent the operator stopped."""

    MACHINE, ENV, BRIDGE = "win32:host", "win32:host:default", "bridge-host"

    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "machineId": self.MACHINE, "os": "win32", "kind": "win32", "bridgeId": self.BRIDGE,
            "runtimes": [], "metadata": {"launchRoots": ["C:/Docker/aify-comms"]}}).raise_for_status()

    def _registered_outside(self, agent_id, raw_status):
        from service.tests.defined_agents import define

        self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "machineId": self.MACHINE, "status": raw_status}).raise_for_status()
        define(self, agent_id, environment_id=self.ENV, machine_id=self.MACHINE, bridge_id=self.BRIDGE,
               workspace="C:/Docker/aify-dashboard")
        record = dict(env_records.startable(agent_id), state="misconfigured", stateCause="config")
        env_records.report(self, agent_id, records={agent_id: record})

    def _engine_push(self, agent_id):
        from service.api_core.status_broadcast import _broadcast_engine_status
        from service.db import get_db

        async def push():
            db = await get_db()
            try:
                await _broadcast_engine_status(self.ws, db, agent_id)
            finally:
                await db.close()
        self.ws.broadcasts.clear()
        asyncio.run(push())
        [payload] = [args[1] for args, _ in self.ws.broadcasts if args and args[0] == "agent_status"]
        return payload["status"], payload["statusNote"]

    def test_a_registration_saying_misconfigured_does_not_blank_the_pushed_reason(self):
        self._registered_outside("raw-misconfigured", "misconfigured")
        polled = self.client.get("/api/v1/agents/raw-misconfigured").json()["agent"]
        self.assertEqual(polled["status"], "misconfigured", "CONTROL: the poll")
        self.assertIn("Cannot be started:", polled["statusNote"], "CONTROL: the poll keeps the reason")
        status, note = self._engine_push("raw-misconfigured")
        self.assertEqual(status, "misconfigured")
        self.assertIn("outside the folder aify-env was started in (C:/Docker/aify-comms)", note)

    def test_TWIN_a_registration_saying_idle_keeps_the_pushed_reason(self):
        self._registered_outside("raw-idle", "idle")
        status, note = self._engine_push("raw-idle")
        self.assertEqual(status, "misconfigured")
        self.assertIn("outside the folder aify-env was started in (C:/Docker/aify-comms)", note)

    def test_CONTROL_an_operator_stop_still_pushes_stopped_with_the_operators_note(self):
        self.client.post("/api/v1/agents", json={
            "agentId": "stopped-here", "role": "coder", "runtime": "codex", "sessionMode": "managed",
            "machineId": self.MACHINE}).raise_for_status()
        env_records.report(self, "stopped-here")
        self.client.post("/api/v1/agents/stopped-here/control",
                         json={"action": "stop", "from_agent": "dashboard"}).raise_for_status()
        status, note = self._engine_push("stopped-here")
        self.assertEqual(status, "stopped")
        self.assertEqual(note, "Stopped from dashboard. Resume to allow wake/dispatch again.")
