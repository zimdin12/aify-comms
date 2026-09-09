"""The launch answer, driven through the plugin's OWN control pass.

X-6 CLOSED THE CLAIM ANSWER. This is the other response the host tier reads, and it is the one with
teeth: `GET /terminals/{id}/launch` says what program to run, with which arguments, in which
directory. The plugin reads `launch.argv`, `launch.cwd`, `launch.env` and `launch.agentId`, and a
field that stops arriving does not raise anywhere — it produces a host that starts nothing, or
starts it in the wrong place, while the control reports and the service waits.

WHAT ONLY THIS SIDE CAN ANSWER. aify-env's own tests drive this pass against a fixture aify-env
wrote. That proves the pass handles the shape its author had in mind; it cannot notice this service
composing a different one. Nothing ran the plugin against a launch THIS app produced until here.

THE VERDICT IS WHAT GOT STARTED, not which properties were read. A name check is satisfied by a name
— the failure this seam produced four times in one session — so what is asserted is the spec the
host would actually execute: the argv this service composed, in the workspace this service named.

AND THE REFUSAL IS ASSERTED TOO, because it is the safety property. `launch.cwd` is checked against
the roots THIS environment advertised, and the plugin's own comment calls it the riskiest step in
the pass: it is what stops a service launching a process anywhere on the host. A test that only ever
sees the happy path cannot tell a working guard from a deleted one.

IT STARTS NO PROCESS AND REACHES NO NETWORK. `runOneControl` takes `processes`, `handles`,
`buildSpec`, `resolveCandidates`, `baseEnv` and `sender` by injection; the fake process host records
what it was asked to start and never spawns. Starting aify-env supersedes the one serving this host,
so nothing here imports its entrypoint.

WHAT IT DOES NOT COVER: adoption, the second-worker refusal, stop and resize controls — all of them
aify-env's own logic, covered by aify-env's suite, and none of them turning on a field THIS service
sends. Stated so a green run is not read as covering the control pass generally.

IT SKIPS BY NAME rather than passing when the checkout is absent, like its siblings.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import (
    PLUGIN_DIR, env_repo,
)

MACHINE_ID = "linux:launch-host"
ENVIRONMENT_ID = f"{MACHINE_ID}:default"
AGENT_ID = "sc-lead"
RUNTIME = "claude-code"
ROLE = "coder"
WORKSPACE = "/work"
TERMINAL_ID = "term-1"
SESSION_ID = "sess-launch"
CONTROL_ID = "ctl-1"

#: What `runOneControl` returns when it has started the worker and carried its output. Read off
#: `terminal-controls.mjs` rather than taken from a red test's diff.
STARTED = "started"

#: Node, driving the plugin's OWN control pass on this service's real launch answer.
#:
#: EVERY DEPENDENCY IS INJECTED AND NONE OF THEM SPAWNS. `processes.start` records the spec it was
#: handed and answers with a handle; `subscribe` answers a release function. `buildSpec` and
#: `resolveCandidates` stand in for this machine's launcher allowlist, which is aify-env's business
#: and not this seam's -- what is under test is the payload flowing through them, so they pass it on
#: unchanged and record nothing of their own.
#:
#: `withinRoots` IS THE REAL ONE, imported from the plugin. Substituting a permissive stub would
#: delete the safety property this file exists to exercise.
HARNESS = """
import { readFileSync } from 'node:fs';
import { runOneControl, createHandleBook } from '%(controls)s';
import { workspaceWithinRoots } from '%(claim)s';
import { buildStartSpec } from '%(startspec)s';

const launch = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const started = [];
const reported = [];

const processes = {
  start: async (spec) => { started.push(spec); return { id: 'proc-1', pid: 4242 }; },
  subscribe: (handle) => (handle === 'proc-1' ? () => {} : null),
  pidFor: () => 4242,
};

const api = {
  launch: async () => launch,
  reportControl: async (id, patch) => { reported.push({ id: String(id), patch }); return { ok: true }; },
  terminalOutput: async () => ({ ok: true }),
};

const outcome = await runOneControl({
  control: { id: '%(control)s', terminalId: '%(terminal)s', action: 'start' },
  api,
  processes,
  cwdRoots: %(roots)s,
  windows: false,
  log: () => {},
  withinRoots: workspaceWithinRoots,
  // THE REAL BUILDER, and a pass-through here was a reproduced false green. The stub ignored
  // `service`, so the plugin sending an empty one -- which this builder refuses outright with "a
  // start request must name the service it is for" -- passed every assertion. It is a pure function
  // taking `readFile`, `platform` and `dirExists` by injection, so running it needs no installed
  // launcher and touches no filesystem.
  buildSpec: (spec) => buildStartSpec(spec, {
    // The minimum a launcher must be to be allowed to run: a shebang on the first line, and a
    // SUBSTITUTED version marker. Both are the allowlist's own conditions, so this text is what a
    // rendered wrapper looks like to it rather than a value invented to pass.
    readFile: () => '#!/bin/bash' + String.fromCharCode(10)
      + 'HARNESS_WRAPPER_VERSION="1.2.3"' + String.fromCharCode(10),
    platform: 'linux',
    dirExists: () => false,
  }),
  // THE RESOLVER STAYS STUBBED, deliberately and as an explicit boundary: where this machine keeps
  // `claude-aify` is the host's business and depends on an install this test must not require.
  // What it answers with is a path, because the builder judges a FILE.
  resolveCandidates: (command) => ['/opt/aify/' + command],
  handles: createHandleBook(),
  // COLLIDING ON PURPOSE. Precedence is only observable where the two maps overlap, and with a
  // `baseEnv` of PATH alone the overlay could be merged either way round with nothing noticing.
  // These are the two collisions that matter: an agent id inherited from whatever launched this
  // host is how a worker comes up reporting as somebody else, and an inherited
  // AIFY_ENVIRONMENT_BRIDGE=1 turns a worker into a second environment bridge.
  baseEnv: {
    PATH: '/usr/bin',
    AIFY_AGENT_ID: 'parent-other',
    AIFY_ENVIRONMENT_BRIDGE: '1',
  },
  sender: { send: () => {}, flush: async () => {} },
});

console.log(JSON.stringify({ outcome, started, reported }));
"""


class TheEnvPluginCanRunWhatTheLaunchAnswers(FastApiTestCase):
    DB_NAME = "aify-test-plugin-launch.db"

    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so the plugin's control pass was NOT run against this "
                          "service's launch answer")

    # ── seeding, the way this endpoint's own test does ───────────────────────────────────────

    def _register(self) -> None:
        heartbeat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT_ID, "machineId": MACHINE_ID, "os": "linux", "kind": "linux",
            "bridgeId": "bridge-launch", "cwdRoots": [WORKSPACE],
            "runtimes": [{"runtime": RUNTIME, "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self._client.post("/api/v1/agents", json={
            "agentId": AGENT_ID, "role": ROLE, "runtime": RUNTIME, "sessionMode": "managed",
            "machineId": MACHINE_ID, "bridgeId": "bridge-launch",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

    def _terminal(self, workspace: str = WORKSPACE) -> None:
        """Seeded directly, as this endpoint's own test does and for the same reason: a terminal row
        is created by the control plane during a spawn, and reproducing that path would test the
        spawn rather than the launch answer."""
        import asyncio

        from service.db import get_db

        argv = ["claude-aify", "--aify-agent", AGENT_ID]

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (SESSION_ID, AGENT_ID, ENVIRONMENT_ID, RUNTIME, "running", "console",
                     TERMINAL_ID, "attached", "2026-09-03T00:00:00Z", "2026-09-03T00:00:00Z",
                     None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, argv, workspace, status, output, "
                    "error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL_ID, AGENT_ID, SESSION_ID, ENVIRONMENT_ID, RUNTIME, "bridge-launch",
                     f"claude-aify --aify-agent {AGENT_ID}", json.dumps(argv), workspace,
                     "attached", "", "", "2026-09-03T00:00:00Z", "2026-09-03T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _launch_answer(self) -> dict:
        """What this service really answers a host asking what to run."""
        answered = self._client.get(f"/api/v1/terminals/{TERMINAL_ID}/launch")
        self.assertEqual(answered.status_code, 200, answered.text)
        return answered.json()

    # ── the plugin's own pass, on that answer ────────────────────────────────────────────────

    def _run_by_the_plugin(self, answer: dict, roots: list[str] | None = None) -> dict:
        controls = (self.repo / PLUGIN_DIR / "terminal-controls.mjs").as_uri()
        claim = (self.repo / PLUGIN_DIR / "claim.mjs").as_uri()
        startspec = (self.repo / "lib" / "start-spec.mjs").as_uri()
        script = Path(tempfile.mkdtemp()) / "drive-the-control-pass.mjs"
        payload = script.with_name("launch.json")
        payload.write_text(json.dumps(answer), encoding="utf-8")
        script.write_text(
            HARNESS % {"controls": controls, "claim": claim, "startspec": startspec,
                       "control": CONTROL_ID,
                       "terminal": TERMINAL_ID,
                       "roots": json.dumps(roots if roots is not None else [WORKSPACE])},
            encoding="utf-8")
        done = subprocess.run(["node", str(script), str(payload)],
                              cwd=self.repo, capture_output=True, text=True)
        # A PAYLOAD FROM A PROCESS THAT DID NOT SUCCEED IS NOT EVIDENCE.
        if done.returncode != 0:
            raise AssertionError(
                f"the plugin's control pass could not be run (exit {done.returncode}): "
                f"{done.stdout[-500:]}{done.stderr[-500:]}")
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(f"the pass printed nothing: {done.stdout[-500:]}")
        return json.loads(lines[-1])

    # ── the control ──────────────────────────────────────────────────────────────────────────

    def test_this_service_answered_a_launch_for_the_plugin_to_run(self):
        """POSITIVE CONTROL. A blank launch is the silence this whole tier keeps being bitten by,
        and it would satisfy every check below by making the pass refuse for the wrong reason."""
        self._register()
        self._terminal()
        launch = self._launch_answer()["launch"]
        self.assertTrue(launch.get("argv"), f"this service composed no argv: {launch}")
        self.assertEqual(launch.get("cwd"), WORKSPACE,
                         f"this service named a workspace this test did not seed: {launch}")

    # ── the claim ────────────────────────────────────────────────────────────────────────────

    def test_the_plugin_starts_what_this_service_composed(self):
        """THE SPEC THAT WOULD ACTUALLY BE EXECUTED, which is the only thing worth asserting.

        The program, its arguments and its directory all travel on this answer, and each fails
        differently and quietly: a dropped argv makes the host refuse ("carries no argv, only a
        command string"), a dropped cwd makes the roots guard refuse, and a renamed one of either
        does the same while looking like a host problem.
        """
        self._register()
        self._terminal()
        launch = self._launch_answer()["launch"]
        read = self._run_by_the_plugin({"launch": launch})

        self.assertEqual(
            read["outcome"].get("outcome"), STARTED,
            f"the plugin did not start a worker from this service's own launch: {read['outcome']}")
        self.assertEqual(len(read["started"]), 1,
                         f"expected exactly one process start, got {read['started']}")

        spec = read["started"][0]
        # THE LAUNCHER AND ITS ARGUMENTS, as the REAL builder records them. `command` is whatever
        # the interpreter turned out to be, and `launcher` is the file that was judged -- the
        # discriminating half, which is why the builder keeps it beside the command.
        self.assertTrue(
            str(spec["launcher"]).endswith(launch["argv"][0]),
            f"the host would run {spec['launcher']!r}, which is not the program this service named "
            f"({launch['argv'][0]!r})")
        self.assertEqual(spec["args"][-len(launch["argv"]) + 1:], launch["argv"][1:],
                         "the arguments the host would pass are not the ones this service composed")
        self.assertEqual(spec["cwd"], launch["cwd"],
                         "the directory the host would run in is not the one this service named")
        self.assertEqual(spec["service"], "aify-comms",
                         "the start was not attributed to this service, which the builder refuses "
                         "outright and a permissive stub could not see")

    def test_the_aify_variables_this_service_sends_reach_the_process(self):
        """The overlay, and it is a separate obligation from starting at all.

        A worker with no `AIFY_AGENT_ID` is structurally dead — registered, and its status can never
        be right — and the role variable has its own recorded incident: a worker that did not receive
        it self-registered as `coder`, and re-register is a full state refresh, so the spawn's role
        was destroyed. Both ride on this answer, so both are checked as ARRIVING rather than as sent.
        """
        self._register()
        self._terminal()
        launch = self._launch_answer()["launch"]
        read = self._run_by_the_plugin({"launch": launch})

        env = read["started"][0]["env"]

        # PINNED AGAINST WHAT WAS SEEDED, BEFORE the general comparison. The sweep below quantifies
        # over whatever `launch.env` contains, so a service emitting NO aify variables satisfied it
        # vacuously -- reproduced by review, and this test named both of these in its own prose
        # while asserting neither. A worker with no `AIFY_AGENT_ID` is structurally dead, and one
        # with no `AIFY_AGENT_ROLE` self-registers as `coder`, destroying the spawn's role.
        self.assertEqual(env.get("AIFY_AGENT_ID"), AGENT_ID,
                         "the worker would come up without the agent id this service issued, so "
                         "its status could never be right")
        self.assertEqual(env.get("AIFY_AGENT_ROLE"), ROLE,
                         "the worker would come up without its role and self-register as the "
                         "default, and re-register is a full state refresh")

        # AND THE COLLISIONS RESOLVE THE SERVICE'S WAY. `baseEnv` deliberately carries a DIFFERENT
        # agent id and a bridge flag, because precedence is only observable where the maps overlap:
        # with disjoint inputs the merge could be reversed with nothing noticing.
        self.assertNotEqual(
            env.get("AIFY_AGENT_ID"), "parent-other",
            "an agent id INHERITED from whatever launched this host won over the one the spawn "
            "chose, so the worker would come up reporting as another agent")
        self.assertEqual(
            env.get("AIFY_ENVIRONMENT_BRIDGE"), "0",
            "an inherited AIFY_ENVIRONMENT_BRIDGE=1 won over the value this service sent, which "
            "would make the worker a second environment bridge on a host that already has one")

        missing = {name: value for name, value in (launch.get("env") or {}).items()
                   if env.get(name) != value}
        self.assertEqual(missing, {}, (
            "variables this service sent did not reach the process the host would start, so a "
            "worker would come up without them and nothing would report a problem: "
            f"{missing}"))
        self.assertEqual(
            env.get("PATH"), "/usr/bin",
            "the host's own base environment was lost, so the overlay REPLACED it rather than "
            "layering over it")

    # ── the safety property, driven by making the answer violate it ─────────────────────────

    def test_a_cwd_OUTSIDE_the_advertised_roots_is_refused(self):
        """THE RISKIEST STEP IN THE PASS, in the plugin's own words, exercised on a real payload.

        `launch.cwd` is what stops a service launching a process anywhere on this machine, and it is
        checked against the roots THIS environment advertised rather than against the request. A
        suite that only ever sees a cwd inside the roots cannot tell a working guard from a deleted
        one — and the guard runs on a value this service supplies, so a change here could defeat it
        without touching aify-env at all.

        Driven by narrowing what the HOST advertised rather than by inventing a payload, so the
        answer stays one this service really produced.
        """
        self._register()
        self._terminal()
        launch = self._launch_answer()["launch"]
        read = self._run_by_the_plugin({"launch": launch}, roots=["/somewhere-else"])

        self.assertEqual(
            read["outcome"].get("outcome"), "refused",
            "a launch whose cwd is outside every root this host advertised was STARTED, so nothing "
            f"stops a service running a process anywhere on this machine: {read['outcome']}")
        self.assertEqual(read["started"], [],
                         "the host started the process before refusing it")
        self.assertIn(
            WORKSPACE, str(read["outcome"].get("detail") or ""),
            "the refusal does not name the directory it refused, which is what an operator needs")


    def test_a_launch_carrying_NO_cwd_is_refused_rather_than_run_anywhere(self):
        """The empty case, and it is a different branch from a cwd that is merely outside.

        A mutant that made the roots guard admit an EMPTY target survived the test above, because
        that one supplies a real directory outside the roots — the guard refuses it on the
        comparison rather than on the emptiness, so the two branches need two witnesses.

        It is reachable from this service: `terminals.py` composes the launch cwd as
        `terminal.get("workspace") or ""`, so a terminal row with no workspace produces exactly
        this payload. A host that ran it would start a worker in whatever directory it happened to
        be in — the blank-launch silence this endpoint's own tests were written against.
        """
        self._register()
        self._terminal(workspace="")
        launch = self._launch_answer()["launch"]
        self.assertEqual(launch.get("cwd"), "",
                         f"this service did not produce the empty cwd this drives: {launch}")

        read = self._run_by_the_plugin({"launch": launch})
        self.assertEqual(
            read["outcome"].get("outcome"), "refused",
            "a launch carrying no directory at all was STARTED, so the worker would come up "
            f"wherever the host process happened to be: {read['outcome']}")
        self.assertEqual(read["started"], [], "the host started the process before refusing it")


if __name__ == "__main__":
    unittest.main()
