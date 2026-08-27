"""A terminal records how its process ended, which nothing did until 2026-08-26.

THE JOIN, across three components. node-pty hands the bridge `{exitCode, signal}`;
`terminal-runtime.js` spreads both into the exit detail on all four exit paths -- the PTY, the
DELEGATED aify-env process, the piped child and a forced stop. `terminal-manager.mjs` then posted
only an output marker and a status, so both numbers died one hop short of this table, and
`TerminalOutputRequest` had nowhere to put them anyway.

WHAT IT COST, in the open. sc-claude and sc-architect died mid-turn on 2026-08-26. The operator asked
why. Every record said `status='stopped'`, an empty `error`, and nothing else: the console tail could
show what the agent had been DOING and nothing whatsoever about the stopping. A terminal that dies
took its reason with it.

NULL IS NOT ZERO, and that distinction is the whole design of these two columns. Zero is a clean exit
and the most common value there is. A column that could not tell "exited cleanly" from "nobody told
me" would answer the question wrongly rather than not at all -- so the migration defaults to NULL,
the model types `exitCode` as an int rather than coercing, the route tests `is not None` rather than
truthiness, and the bridge OMITS the field instead of sending null. Every one of those four is a
place a truthiness test would have destroyed the most common case.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "bridge-exit-probe"
ENVIRONMENT = "linux:test-host:default"


class TerminalRecordsHowItEndedTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    AGENT = "exit-probe-agent"
    TERMINAL = "term_exit_probe"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed_terminal()

    def _seed_terminal(self) -> None:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                # spawn_spec_id / spawn_request_id are named and NULLed deliberately: both are
                # nullable FOREIGN KEYs whose column DEFAULT is '', which no spawn row has, so
                # omitting them fails the constraint with no column named in the error.
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"sess-{self.AGENT}", self.AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z", None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, f"sess-{self.AGENT}", ENVIRONMENT, "claude-code",
                     BRIDGE, "claude-aify --aify-agent x", "attached", "", "",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _post_exit(self, **body):
        return self.client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"bridgeId": BRIDGE, "output": "\n[terminal exited]\n", "status": "stopped", **body},
        )

    def _row(self) -> dict:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT exit_code, exit_signal, status FROM terminal_sessions WHERE id = ?",
                    (self.TERMINAL,),
                )).fetchone()
                return {k: row[k] for k in ("exit_code", "exit_signal", "status")}
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_columns_start_as_NULL_rather_than_zero(self) -> None:
        """The control, and the design. A fresh terminal has not exited, and 'has not exited' must not
        read as 'exited cleanly' -- which is exactly what a DEFAULT 0 would have produced for every
        row in the table."""
        row = self._row()
        self.assertIsNone(row["exit_code"], "a terminal that has not exited claims an exit code")
        self.assertIn(row["exit_signal"], (None, ""))

    def test_a_clean_exit_records_zero(self) -> None:
        """The case a truthiness test destroys, end to end. If any hop used `if code:` this records
        NULL and the most common death in the fleet stays unexplained."""
        response = self._post_exit(exitCode=0)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._row()["exit_code"], 0)

    def test_a_failing_exit_records_its_code(self) -> None:
        self.assertEqual(self._post_exit(exitCode=137).status_code, 200)
        self.assertEqual(self._row()["exit_code"], 137)

    def test_a_signal_kill_records_the_signal(self) -> None:
        self.assertEqual(self._post_exit(exitSignal="SIGKILL").status_code, 200)
        row = self._row()
        self.assertEqual(row["exit_signal"], "SIGKILL")
        self.assertIsNone(row["exit_code"], "a signalled death invented an exit code")

    def test_an_older_bridge_that_sends_neither_still_works(self) -> None:
        """The deployment skew this will actually meet: the service updates before every wrapper
        relaunches. A bridge that knows nothing about these fields must keep posting output."""
        response = self._post_exit()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self._row()["exit_code"])

    def test_a_later_output_chunk_cannot_blank_a_recorded_exit(self) -> None:
        """Bytes can still arrive after the exit POST on a busy terminal. The exit is written with
        COALESCE for that reason, and the output queue's UPDATE names only output, seq and status."""
        self.assertEqual(self._post_exit(exitCode=42).status_code, 200)
        later = self.client.post(
            f"/api/v1/terminals/{self.TERMINAL}/output",
            json={"bridgeId": BRIDGE, "output": "trailing bytes\n"},
        )
        self.assertEqual(later.status_code, 200, later.text)
        self.assertEqual(self._row()["exit_code"], 42, "a trailing output chunk erased the exit code")

    def test_a_duplicate_exit_report_does_not_unset_it(self) -> None:
        """A retry or a duplicate flush must not turn a known code back into unknown."""
        self.assertEqual(self._post_exit(exitCode=3).status_code, 200)
        self.assertEqual(self._post_exit().status_code, 200)
        self.assertEqual(self._row()["exit_code"], 3)

    def test_the_console_tail_reports_how_it_ended(self) -> None:
        """The reader. A column nothing serves would be the same defect one layer down -- which is
        the shape this round has already found twice."""
        self.assertEqual(self._post_exit(exitCode=137).status_code, 200)
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertTrue(body.get("historical"), body.get("message"))
        self.assertEqual(body.get("exitCode"), 137)

    def test_the_terminal_record_every_other_consumer_gets_carries_the_exit(self) -> None:
        """THE OTHER READER, and it was missing for a day.

        `GET /agents/{id}/console` is one consumer of these columns. `_terminal_session_to_dict` is
        the OTHER, and it is the one almost everything gets: `GET /terminals/{id}`, the console
        start and stop payloads, the virtual-terminal ensure, the session-ops rows the dashboard
        renders. It serialised `status` and `error` and dropped both new columns, so every one of
        those consumers still said 'stopped' and nothing more -- the same silence the columns were
        added to end, one serialiser over. Found by auditing the feature's own readers rather than
        by anything going red.
        """
        before = self.client.get(f"/api/v1/terminals/{self.TERMINAL}").json()["terminal"]
        # The control: the field must be PRESENT and null before the exit, so the assertion below
        # can fail. A serialiser that omitted the key entirely would also read as None from .get().
        self.assertIn("exitCode", before, "the terminal record does not carry the field at all")
        self.assertIsNone(before["exitCode"], "a terminal that has not exited claims a code")

        self.assertEqual(self._post_exit(exitCode=137, exitSignal="SIGKILL").status_code, 200)
        after = self.client.get(f"/api/v1/terminals/{self.TERMINAL}").json()["terminal"]
        self.assertEqual(after["exitCode"], 137)
        self.assertEqual(after["exitSignal"], "SIGKILL")

    def test_a_clean_exit_reaches_that_record_as_zero_rather_than_as_silence(self) -> None:
        """The truthiness trap again, at the serialiser. `row["exit_code"] or None` and
        `... or ""` both turn the most common death in the fleet back into 'nobody said'."""
        self.assertEqual(self._post_exit(exitCode=0).status_code, 200)
        terminal = self.client.get(f"/api/v1/terminals/{self.TERMINAL}").json()["terminal"]
        self.assertEqual(terminal["exitCode"], 0)
        self.assertNotEqual(terminal["exitCode"], None, "a clean exit serialised as no exit at all")


    def test_the_run_summary_says_a_KILLED_worker_was_killed(self) -> None:
        """THE SENTENCE THE REQUESTER ACTUALLY READS, and it was making a claim nobody checked.

        When a terminal ends under an open run, the run is closed with
        `f"Terminal {status} before an explicit reply was recorded."` -- and the bridge reports
        `stopped` for every ending that is not a spawn failure. So a worker something SIGKILLed
        mid-turn told its requester its terminal had STOPPED, which is the word for a deliberate
        shutdown, while the signal that says otherwise sat in the same row, written moments earlier
        in the same request. On 2026-08-26 the operator asked why agents kept dropping and every
        instance of this sentence said `stopped`.
        """
        run_id = self._open_run()
        self.assertEqual(self._post_exit(exitSignal="SIGKILL").status_code, 200)
        summary = self._run_summary(run_id)
        self.assertIn("KILLED by SIGKILL", summary, f"the run still says: {summary!r}")
        self.assertNotIn("Terminal stopped", summary)

    def test_the_run_summary_carries_a_non_zero_exit_code(self) -> None:
        run_id = self._open_run()
        self.assertEqual(self._post_exit(exitCode=137).status_code, 200)
        self.assertIn("exited with code 137", self._run_summary(run_id))

    def test_a_terminal_that_reported_NOTHING_keeps_the_original_wording(self) -> None:
        """The control, and the honest case. An older bridge sends no exit fields at all, and
        inventing a cause for a death nobody described would be the defect this repo keeps finding."""
        run_id = self._open_run()
        self.assertEqual(self._post_exit().status_code, 200)
        summary = self._run_summary(run_id)
        self.assertIn("Terminal stopped before an explicit reply was recorded", summary)

    def test_a_clean_exit_under_an_open_run_says_it_was_clean(self) -> None:
        """Zero is not silence. A worker that exited 0 without replying still failed to reply, and
        saying "exited cleanly" is a different diagnosis from "we do not know"."""
        run_id = self._open_run()
        self.assertEqual(self._post_exit(exitCode=0).status_code, 200)
        self.assertIn("exited cleanly (code 0)", self._run_summary(run_id))

    def _open_run(self) -> str:
        """A dispatch run claimed against this agent's terminal, so the exit has something to close.

        Inserted directly: opening one through the dispatch API would need a live claimer, and this
        test is about what the CLOSING writes, not about how the run got there.
        """
        import asyncio

        from service.db import get_db

        run_id = f"run_probe_{len(self.TERMINAL)}"

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    # require_reply IS SET EXPLICITLY, and the omission was a real trap. The column
                    # DEFAULTS TO 0 while message_type defaults to 'request', so a bare insert produces
                    # a row the product never writes: `_dispatch_requires_reply` would have stored 1
                    # for a request nobody opted out of. These tests are about the EXIT-CAUSE half of
                    # the sentence, and they need a run that is owed a reply for the other half to be
                    # present at all -- which the schema default silently denied them.
                    "INSERT INTO dispatch_runs (id, target_agent, from_agent, subject, body, status, "
                    "dispatch_mode, execution_mode, require_reply, requested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, self.AGENT, "requester", "probe", "body", "running", "terminal",
                     "managed", 1, "2026-08-26T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())
        return run_id

    def _run_summary(self, run_id: str) -> str:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT status, summary, error_text FROM dispatch_runs WHERE id = ?", (run_id,),
                )).fetchone()
                return f"{row['summary'] or ''} {row['error_text'] or ''}".strip()
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_console_tail_says_so_when_nothing_was_reported(self) -> None:
        """A terminal that ended without reporting is a real answer and must read as one, not as a
        clean exit and not as silence."""
        self.assertEqual(self._post_exit().status_code, 200)
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertIsNone(body.get("exitCode"))
        self.assertIn("no exit code was recorded", body.get("message") or body.get("output") or "")


if __name__ == "__main__":
    import unittest

    unittest.main()
