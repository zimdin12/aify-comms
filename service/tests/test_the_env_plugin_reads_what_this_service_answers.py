"""aify-env's own readers, run against THIS service's real responses.

X-1 PROVED THE ADDRESS AND X-2 THE FIELDS IT SENDS. This is the other direction, and it fails just as
quietly: a reader looking for `lastSeen` on a payload that spells it `last_seen` finds `undefined`,
takes its fallback, and decides something. Nothing raises. The symptom is a menu that starts the
wrong agent, or offers none.

IT IS THE PLUGIN'S OWN CODE, NOT A MODEL OF IT. `startable-agents.mjs` is imported and called with
what this service actually answered — seeded here, fetched through the real routes, handed to node.
A test that re-implemented those rules would agree with itself for ever.

WHAT IT COVERS. The two readings that gate "start available agent": whether an agent is startable at
all, and which of its sessions a restart should target. Those are the ones whose silent failure
stops the feature without an error anywhere — and `restartTargetFor` picks "most recently seen"
BY `lastSeen`, so a service that stopped sending it would silently choose the first row instead,
which is the defect that function's own comment says it exists to avoid.

WHAT IT DOES NOT COVER: every other response this plugin reads, and any live round trip. Stated so a
green run is not mistaken for the seam being closed.

IT SKIPS BY NAME rather than passing when the checkout is absent, like its sibling.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import (
    PLUGIN_DIR, env_repo,
)

AGENT_ID = "ef-tester"
MACHINE_ID = "linux:test-host"
ENVIRONMENT_ID = f"{MACHINE_ID}:default"
SESSION_ID = "sess-dead"
#: A SECOND, OLDER session, because `restartTargetFor` picks the most recently seen and with one
#: row it cannot be wrong. Mutating the reader to look for `last_seen` changed nothing until this
#: existed -- `best` is set on the first iteration whatever the comparison says.
OLDER_SESSION_ID = "sess-older"

#: Node, calling the plugin's OWN readers on what this service answered.
HARNESS = """
import { readFileSync } from 'node:fs';
import { restartTargetFor, startabilityOf } from '%(module)s';

const input = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const agent = (input.roster && input.roster.agents) ? input.roster.agents['%(agent)s'] : null;
console.log(JSON.stringify({
  sawAgent: Boolean(agent),
  startable: startabilityOf(agent, { machineId: '%(machine)s' }),
  target: restartTargetFor(input.listing),
}));
"""


class TheEnvPluginReadsWhatThisServiceAnswers(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so the plugin's readers were NOT run against this service's "
                          "answers")
        self._seed()

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        import asyncio

        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _seed(self) -> None:
        """A MANAGED agent on this machine with one dead session — the case the menu offers."""
        registered = self.client.post(
            "/api/v1/agents",
            json={"agentId": AGENT_ID, "role": "coder", "runtime": "codex",
                  "sessionMode": "managed", "capabilities": ["managed-run"]},
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        # `machineId` and the managed mode are what `startabilityOf` reads, and neither is settable
        # through the register payload alone.
        self._write("UPDATE agents SET machine_id = ?, session_mode = ? WHERE id = ?",
                    (MACHINE_ID, "managed", AGENT_ID))
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status,"
            " started_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (SESSION_ID, AGENT_ID, ENVIRONMENT_ID, "codex", "/workspace/proj", "stopped",
             "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status,"
            " started_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (OLDER_SESSION_ID, AGENT_ID, ENVIRONMENT_ID, "codex", "/workspace/proj", "stopped",
             "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        )

    # ── the round trip ───────────────────────────────────────────────────────────────────────

    def _answers(self) -> dict:
        """What this service really answers for the two calls the starter makes."""
        roster = self.client.get("/api/v1/agents")
        self.assertEqual(roster.status_code, 200, roster.text)
        listing = self.client.get(f"/api/v1/sessions?agentId={AGENT_ID}")
        self.assertEqual(listing.status_code, 200, listing.text)
        return {"roster": roster.json(), "listing": listing.json()}

    def _read_by_the_plugin(self, answers: dict) -> dict:
        module = (self.repo / PLUGIN_DIR.parent.parent / "startable-agents.mjs")
        script = Path(tempfile.mkdtemp()) / "read-the-answers.mjs"
        payload = script.with_name("answers.json")
        payload.write_text(json.dumps(answers), encoding="utf-8")
        script.write_text(
            HARNESS % {"module": module.as_uri(), "agent": AGENT_ID, "machine": MACHINE_ID},
            encoding="utf-8")
        done = subprocess.run(["node", str(script), str(payload)],
                              cwd=self.repo, capture_output=True, text=True)
        if done.returncode != 0:
            raise AssertionError(
                f"the plugin's readers could not be run (exit {done.returncode}): "
                f"{done.stdout[-400:]}{done.stderr[-400:]}")
        lines = [l for l in done.stdout.splitlines() if l.startswith("{")]
        if not lines:
            raise AssertionError(f"the readers printed nothing: {done.stdout[-400:]}")
        return json.loads(lines[-1])

    def _node(self, source: str) -> str:
        """Run one line of module code in the plugin's own repo and return its stdout."""
        script = Path(tempfile.mkdtemp()) / "one-liner.mjs"
        script.write_text(source, encoding="utf-8")
        done = subprocess.run(["node", str(script)], cwd=self.repo,
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise AssertionError(f"node exited {done.returncode}: {done.stderr[-300:]}")
        return done.stdout.strip()

    def test_the_service_answered_something_to_read(self):
        """The control. An empty roster or listing satisfies every assertion below."""
        answers = self._answers()
        self.assertTrue(answers["roster"].get("agents"), answers["roster"])
        self.assertTrue(answers["listing"].get("sessions"), answers["listing"])

    def test_the_plugin_finds_this_agent_startable_from_our_answer(self):
        """`startabilityOf` reads sessionMode and machineId off the agent row we serve.

        Either spelled differently and this returns a refusal — with the menu simply not offering an
        agent that is there, and nothing anywhere reporting a problem.
        """
        read = self._read_by_the_plugin(self._answers())
        self.assertTrue(read["sawAgent"], "the plugin could not find the agent in our roster")
        self.assertTrue(read["startable"]["startable"],
                        f"the plugin refused an agent we serve as startable: {read['startable']}")

    def test_the_plugin_targets_the_session_we_answered_with(self):
        """`restartTargetFor` picks by `lastSeen`, which this service sends as `lastSeen`.

        If it stopped, every comparison would be `"" > ""` and the reader would take the FIRST row
        instead — which its own comment says it exists to avoid.
        """
        read = self._read_by_the_plugin(self._answers())
        self.assertEqual(read["target"]["sessionId"], SESSION_ID,
                         f"the plugin did not target the session we answered with: {read['target']}")

    def test_the_readers_LIVE_VOCABULARY_is_one_this_service_can_emit(self):
        """THE SEAM QUESTION, and the one a status-by-status test would miss.

        `restartTargetFor` treats five strings as live. Whether it handles them correctly is
        aify-env's business and its own tests cover it. What only this side can answer is whether
        this service EMITS any of them -- because if the two are talking about different
        vocabularies, the live check never fires, every session reads restartable, and the
        symptom is a start that stops a working agent with nothing anywhere reporting a problem.

        Asserted against the service's OWN declared sets rather than a list retyped here, so a
        vocabulary change on this side has to face this test.
        """
        from service.api_core.liveness import _LIVE_SESSION_STATUSES
        from service.api_core.tuning import LIVE_SESSION_STATUSES

        module = (self.repo / PLUGIN_DIR.parent.parent / "startable-agents.mjs")
        reader = json.loads(self._node(
            "import { LIVE_SESSION_STATUSES } from '" + module.as_uri() + "';"
            " console.log(JSON.stringify(LIVE_SESSION_STATUSES));"))
        self.assertGreaterEqual(len(reader), 3,
                                f"the reader declares almost no live statuses: {reader}")

        ours = {s.lower() for s in (_LIVE_SESSION_STATUSES | LIVE_SESSION_STATUSES)}
        theirs = {str(s).lower() for s in reader}
        self.assertTrue(
            theirs & ours,
            f"aify-env treats {sorted(theirs)} as live and this service declares {sorted(ours)} "
            "-- no overlap means the live check never fires and every session reads restartable")
        self.assertEqual(
            theirs - ours, set(),
            f"aify-env treats {sorted(theirs - ours)} as a live session status and this service "
            "declares no such status anywhere, so a session in that state would be invisible to "
            "the reader as live")

    def test_the_reader_PICKS_BY_LASTSEEN_and_not_by_the_order_we_happen_to_send(self):
        """The reader's own claim, driven against our real payload REVERSED.

        `restartTargetFor` says it chooses the most recently seen "rather than taken from the order
        the service happened to return... a page ordered any other way would silently restart the
        OLDEST backing an agent ever had". This service does serve newest-first today, which means
        a reader that simply took the first row would agree with it and this seam would never
        notice -- so the list is reversed to separate the two answers.

        It is also what makes `lastSeen` load-bearing here. With one session, or with ours in the
        order we send it, renaming the field the reader looks for changes nothing.
        """
        answers = self._answers()
        answers["listing"]["sessions"] = list(reversed(answers["listing"]["sessions"]))
        self.assertEqual(answers["listing"]["sessions"][0]["id"], OLDER_SESSION_ID,
                         "the reversal did not put the older session first, so this proves nothing")
        read = self._read_by_the_plugin(answers)
        self.assertEqual(
            read["target"]["sessionId"], SESSION_ID,
            "the reader took the row we happened to send FIRST rather than the most recently "
            "seen, which is the defect its own comment names",
        )

    def test_the_reader_REFUSES_a_live_status_when_it_sees_one(self):
        """READER-LEVEL, and labelled so: the service's real payload with one status changed.

        The service will not publish a live session with nothing backing it -- its listing route
        reconciles that away, correctly -- so this cannot be driven from the database. What it
        establishes is narrower than the test above and still worth having: the reader does
        discriminate, rather than returning the first session id whatever it is handed.
        """
        answers = self._answers()
        answers["listing"]["sessions"][0]["status"] = "running"
        read = self._read_by_the_plugin(answers)
        self.assertEqual(read["target"]["sessionId"], "",
                         "a live session was offered as a restart target")
        self.assertIn("running", read["target"]["refusal"])

if __name__ == "__main__":
    unittest.main()
