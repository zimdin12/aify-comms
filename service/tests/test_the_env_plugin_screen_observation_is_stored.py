"""aify-env's own screen observation, sent by its own code, stored by this service's route.

THE SEAM. aify-env evaluates Herdr's screen rules against its headless screen and reports the
result on terminal liveness frames: `{output: "", activity: {state, rule, observedAt}}`. Pydantic
DROPS an undeclared field in silence, and `test_the_env_plugin_addresses_routes_this_service_serves`
cannot see this one -- its probe spreads to nothing, so a body the plugin composes at the call site
never reaches it. A rename on either side would leave both suites green and every status unchanged.

IT IS THE PLUGIN'S CODE END TO END, NOT A MODEL OF IT. Node runs aify-env's real Runner and screen
checkpoint behind a scripted terminal, its real control pass and handle book, and its real `CommsApi`
with a recording transport. The screens are synthetic. What the transport recorded is replayed,
byte for byte, into this service's route, and the row is read back after every frame.

WHAT IT DOES NOT COVER: a live network hop, auth, or a real runtime's screen. Stated so a green run is
not mistaken for the seam being closed.

IT SKIPS BY NAME rather than passing when the checkout is absent, like its siblings.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from service.tests._base import FastApiTestCase
from service.tests.test_status_follows_what_the_host_sees import seed_observed_managed_agent
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import PLUGIN_DIR, env_repo

HARNESS = r"""
import { Runner } from '%(runner)s';
import { PluginProcesses } from '%(plugins)s';
import { loadCheckpointFactory } from '%(checkpoint)s';
import { CommsApi, mintBridgeIdentity } from '%(api)s';
import { createHandleBook, runOneControl, runTerminalControlPass } from '%(controls)s';

if (typeof await loadCheckpointFactory() !== 'function') {
  console.log(JSON.stringify({ error: 'no headless screen: @xterm packages are not installed in this checkout' }));
  process.exit(0);
}
const ESC = String.fromCharCode(27);
const BEL = String.fromCharCode(7);
const RULE = '─'.repeat(60);
const title = (t) => `${ESC}]0;${t}${BEL}`;
const clear = `${ESC}[2J${ESC}[H`;
const SCREENS = [
  `${clear}${title('✳ task')}${RULE}\r\n❯\r\n${RULE}\r\n  ? for shortcuts\r\n`,
  title('⠂ task'),
  `${clear}${title('✳ task')} Bash command\r\n\r\n Do you want to proceed?\r\n ❯ 1. Yes\r\n   2. No\r\n\r\n Esc to cancel · Tab to amend\r\n`,
];

const handlers = [];
const terminal = { pid: 0, cols: 80, rows: 16, onData: (fn) => handlers.push(fn), onExit: () => {},
  write: () => {}, kill: () => {}, resize() {} };
const requests = [];
const api = new CommsApi({
  endpoint: 'http://probe.invalid:1',
  credential: async () => '',
  identity: mintBridgeIdentity({ version: '0.0.0-probe' }),
  fetchImpl: async (url, init) => {
    const path = new URL(url).pathname;
    const method = String(init?.method || 'GET');
    if (path.endsWith('/output')) requests.push({ method, path, body: String(init.body) });
    let answer = {};
    if (path.endsWith('/launch')) answer = { launch: { argv: ['claude-aify'], cwd: '/work', agentId: 'sc-observed', runtime: 'claude-code' } };
    if (path.endsWith('/controls/claim')) answer = { controls: [] };
    if (path.endsWith('/output')) answer = { ok: true, terminal: { status: 'attached' } };
    return { ok: true, status: 200, json: async () => answer, text: async () => JSON.stringify(answer) };
  },
});
const ALLOWED = ['#!/bin/bash', 'HARNESS_WRAPPER_VERSION="0.6.0"', ''].join('\n');
const processes = new PluginProcesses(new Runner({ openTerminal: () => terminal }));
const handles = createHandleBook();
const started = await runOneControl({
  control: { id: 'ctl-1', terminalId: 'term-from-env', action: 'start', cols: 80, rows: 16 },
  api, processes, handles, cwdRoots: ['/work'], windows: false, withinRoots: () => true,
  buildSpec: () => ({ spec: { service: 'aify-comms', fileText: ALLOWED, command: 'fake', args: [] } }),
  resolveCandidates: () => ['/bin/claude-aify'], baseEnv: {},
  sender: { send() {}, async drained() { return true; }, forget() {} },
});
const transitions = () => requests.filter((r) => JSON.parse(r.body).activity).length;
for (const [index, screen] of SCREENS.entries()) {
  for (const fn of handlers) fn(screen);
  const until = Date.now() + 3000;
  while (transitions() < index + 1 && Date.now() < until) await new Promise((r) => setTimeout(r, 10));
}
await runTerminalControlPass({ api, processes, environmentId: 'e', handles, withinRoots: () => true,
  buildSpec: () => ({}), resolveCandidates: () => [] });
console.log(JSON.stringify({ started: started.outcome, requests }));
process.exit(0);
"""


class TheEnvPluginScreenObservationIsStored(FastApiTestCase):
    DB_NAME = "aify-test-env-observation.db"
    ENV = "linux:activity-host:default"
    AGENT = "sc-observed"
    SESSION = "sess-observed"
    TERMINAL = "term-observed"

    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so aify-env's screen observation was NOT replayed into this route")
        seed_observed_managed_agent(self)

    def _sent_by_the_plugin(self) -> dict:
        lib = self.repo / PLUGIN_DIR.parent.parent
        script = Path(tempfile.mkdtemp()) / "observe-a-screen.mjs"
        script.write_text(HARNESS % {
            "runner": (lib / "runner.mjs").as_uri(),
            "plugins": (lib / "service-plugins.mjs").as_uri(),
            "checkpoint": (lib / "screen-checkpoint.mjs").as_uri(),
            "api": (self.repo / PLUGIN_DIR / "api.mjs").as_uri(),
            "controls": (self.repo / PLUGIN_DIR / "terminal-controls.mjs").as_uri(),
        }, encoding="utf-8")
        done = subprocess.run(["node", str(script)], cwd=self.repo, capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError(f"the plugin could not be run (exit {done.returncode}): "
                                 f"{done.stdout[-400:]}{done.stderr[-400:]}")
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(f"the plugin printed nothing: {done.stdout[-400:]}{done.stderr[-400:]}")
        payload = json.loads(lines[-1])
        if payload.get("error"):
            raise AssertionError(payload["error"])
        return payload

    def _row(self) -> dict:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT * FROM terminal_sessions WHERE id = ?", (self.TERMINAL,)).fetchone())
        finally:
            conn.close()

    def test_every_frame_the_plugin_sends_is_stored_as_it_was_sent(self):
        sent = self._sent_by_the_plugin()
        self.assertEqual(sent["started"], "started", "CONTROL: the plugin never started the scripted terminal")
        frames = [(r["path"], json.loads(r["body"])) for r in sent["requests"]]
        observed = [body for _, body in frames if "activity" in body]
        # CONTROL: three transitions and one liveness frame repeating the last, or nothing was compared.
        self.assertEqual([b["activity"]["state"] for b in observed], ["idle", "working", "blocked", "blocked"],
                         f"the plugin did not send the expected observations: {frames}")

        statuses = []
        for path, body in frames:
            self.assertTrue(path.endswith("/terminals/term-from-env/output"), path)
            self.assertNotIn("status", body, "a frame carrying an observation must never carry a status")
            before = self._row()
            answer = self.client.post(path.replace("term-from-env", self.TERMINAL), content=json.dumps(body),
                                      headers={"content-type": "application/json"})
            self.assertEqual(answer.status_code, 200, answer.text)
            after = self._row()
            for column in ("status", "output", "output_seq"):
                self.assertEqual(after[column], before[column], f"a liveness frame moved {column}")
            if "activity" in body:
                activity = body["activity"]
                self.assertEqual(after["activity_state"], activity["state"])
                self.assertEqual(after["activity_rule"], activity["rule"])
                self.assertEqual(after["activity_observed_at"], activity["observedAt"])
                agent = self.client.get(f"/api/v1/agents/{self.AGENT}").json()["agent"]
                statuses.append(agent["status"])
        self.assertEqual(statuses, ["online", "working", "blocked", "blocked"],
                         "the status an API caller reads did not follow what the plugin sent")
