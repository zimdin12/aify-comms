"""The terminals aify-env's plugin says it holds are the ones this service keeps.

`test_a_host_ends_the_terminals_it_no_longer_holds.py` pins the reconciler against a body this
repo wrote by hand. That proves the rule and nothing about the seam: a plugin that nested the list
under another key, sent it top-level (the heartbeat model drops an undeclared field in silence),
or named handles instead of terminal ids would leave every test there green while no host's
heartbeat ever ended anything. ABSENT IS NOT EMPTY on this side, so a list that does not arrive is
not an error anywhere -- it is an older aify-env.

So this runs the plugin's OWN pieces, exactly as its heartbeat loop composes them: the real handle
book, the real `heldTerminalIds` predicate over a process list, and the real `CommsApi.heartbeat`
body. That body is posted to this service's route, and the witness is the ROWS, never the stored
metadata (which this service deliberately does not keep).

IT SKIPS BY NAME rather than passing when the checkout is absent, like its siblings.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import (
    PLUGIN_DIR, env_repo,
)

MACHINE_ID = "linux:held-probe"
ENVIRONMENT_ID = f"{MACHINE_ID}:default"
LONG_AGO = "2026-09-03T00:00:00Z"

#: Node: two terminals remembered by the plugin's own book, only ONE of whose handles the runner
#: still lists. The body is whatever the plugin's own `heartbeat()` hands its transport.
HARNESS = """
import { CommsApi, mintBridgeIdentity } from '%(api)s';
import { createHandleBook, heldTerminalIds } from '%(controls)s';
const handles = createHandleBook();
handles.remember('term-held', 'proc-live', 'agent-held');
handles.remember('term-gone', 'proc-dead', 'agent-gone');
const processes = { list: () => [{ id: 'proc-live', pid: 4242 }] };
let sent = null;
const api = new CommsApi({
  endpoint: 'http://probe.invalid:1',
  credential: async () => '',
  identity: mintBridgeIdentity({ version: '0.0.0-probe' }),
  fetchImpl: async (url, init) => {
    sent = JSON.parse(String(init.body));
    return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
  },
});
await api.heartbeat({ id: '%(env)s', machineId: '%(machine)s', terminal: true },
                    { heldTerminals: heldTerminalIds(handles, processes) });
if (!sent) { console.error('the plugin sent no heartbeat at all'); process.exit(3); }
console.log(JSON.stringify(sent));
"""


class TheEnvPluginHeldTerminalsReachTheReconciler(FastApiTestCase):
    DB_NAME = "aify-test-env-plugin-held-terminals.db"

    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so the plugin's held terminals were NOT driven through this "
                          "service's route")
        # AN aify-env OLDER THAN THE LIST sends none, and this service is built to take no action on
        # that -- so the two repos can land in either order. Such a checkout is SKIPPED BY NAME, not
        # passed: the witness below was never run against it. Keyed on the predicate's export, which
        # is what the harness imports, so a plugin that has it and sends the list wrong still fails.
        controls = (self.repo / PLUGIN_DIR / "terminal-controls.mjs").read_text(encoding="utf-8")
        if "export function heldTerminalIds" not in controls:
            self.skipTest(f"the aify-env at {self.repo} predates heldTerminalIds, so it sends no "
                          "held-terminals list and this seam was NOT exercised")

    def _heartbeat_the_plugin_sends(self) -> dict:
        plugin = self.repo / PLUGIN_DIR
        script = Path(tempfile.mkdtemp()) / "drive-held-terminals.mjs"
        script.write_text(HARNESS % {
            "api": (plugin / "api.mjs").as_uri(), "controls": (plugin / "terminal-controls.mjs").as_uri(),
            "env": ENVIRONMENT_ID, "machine": MACHINE_ID,
        }, encoding="utf-8")
        done = subprocess.run(["node", str(script)], cwd=self.repo, capture_output=True, text=True)
        if done.returncode != 0:
            raise AssertionError(f"the plugin's heartbeat could not be driven (exit {done.returncode}): "
                                 f"{done.stdout[-400:]}{done.stderr[-400:]}")
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(f"the harness printed no body: {done.stdout[-400:]}")
        return json.loads(lines[-1])

    def _seed(self, terminal_id: str) -> None:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                agent_id, session_id = f"agent-{terminal_id}", f"sess-{terminal_id}"
                await db.execute(
                    "INSERT INTO agents (id, role, name, runtime, session_mode, registered_at, last_seen) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (agent_id, "coder", agent_id, "claude-code", "managed", LONG_AGO, LONG_AGO))
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, owner_mode, "
                    "terminal_id, terminal_status, started_at, last_seen, spawn_spec_id, spawn_request_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, agent_id, ENVIRONMENT_ID, "claude-code", "running", "console",
                     terminal_id, "attached", LONG_AGO, LONG_AGO, None, None))
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, "
                    "bridge_id, command, argv, workspace, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, agent_id, session_id, ENVIRONMENT_ID, "claude-code", "probe",
                     "claude-aify", "[]", "/work", "attached", "", "", LONG_AGO, LONG_AGO))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _status(self, terminal_id: str) -> str:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT status FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
                return str(row["status"])
            finally:
                await db.close()

        return asyncio.run(go())

    def _beat(self, body: dict) -> dict:
        posted = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(posted.status_code, 200, posted.text)
        return posted.json()

    def test_the_plugin_names_exactly_the_terminal_its_runner_still_holds(self):
        """POSITIVE CONTROL: without a list naming `term-held` and not `term-gone`, the witness
        below could pass by ending everything or nothing."""
        held = (self._heartbeat_the_plugin_sends().get("metadata") or {}).get("heldTerminals")
        self.assertEqual(held, ["term-held"],
                         "the plugin's heartbeat does not carry metadata.heldTerminals naming the one "
                         f"terminal its runner still holds: {held!r}")

    def test_the_terminal_the_plugin_does_not_hold_ends_and_the_held_one_stays(self):
        body = self._heartbeat_the_plugin_sends()
        # The row has to exist, with this bridge owning it, before the rows it names can be seeded.
        self.assertIs((self._beat({**body, "metadata": {k: v for k, v in body["metadata"].items()
                                                         if k != "heldTerminals"}})
                       .get("claimer") or {}).get("accepted"), True)
        self._seed("term-held")
        self._seed("term-gone")
        self.assertIs((self._beat(body).get("claimer") or {}).get("accepted"), True)
        self.assertEqual(self._status("term-gone"), "stopped",
                         "the plugin's heartbeat left a terminal its runner no longer holds live")
        self.assertEqual(self._status("term-held"), "attached",
                         "the plugin's heartbeat ended the terminal its runner still holds")


if __name__ == "__main__":
    unittest.main()
