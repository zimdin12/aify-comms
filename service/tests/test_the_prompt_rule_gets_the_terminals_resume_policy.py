"""The fact only this service holds has to REACH the rule that needs it — from the row that has it.

THE DEFECT THIS WAS WRITTEN AFTER FINDING. The first version of `_answer_console_prompt` read
`terminal["runtime_state"]`. `terminal_sessions` has no such column — `resumePolicy` lives on the
AGENT row, written by `session_restart.py` and read from there by `mcp/stdio/terminal-env.js`. The
read was guarded on the column being present, so nothing raised, nothing logged, and every screen
would have been judged under the default policy for ever. The guard that was meant to make it safe
is what made the mistake invisible.

That is this repo's favourite failure shape inverted. CLAUDE.md records "a declared field with no
reader"; this is a reader with no field, and it is worse, because the reader looks correct and the
evidence of the gap lives in a schema file nobody opens while writing it.

WHY A TEST FOR A VALUE NO RULE CONSULTS YET. The compaction rule that will consult it needs a real
capture of that dialog before it can be written honestly — the last matcher written from what a
screen "looks like" watched its dialog and did nothing. So what is pinned here is the WIRING, which
is the half that fails silently, and the GATE, which is the half that costs the console its
throughput if it regresses.
"""

from __future__ import annotations

import asyncio
import json

from service.api_core import terminal_output as output_module
from service.api_core.terminal_output import _answer_console_prompt
from service.clock import now as _now
from service.db import get_db
from service.terminal_snapshot import feed_live_screen
from service.tests._base import FastApiTestCase

TERMINAL = "term-resume-policy"
AGENT = "sc-policy"
SESSION = "sess-policy"
POLICY = "fresh_context"

#: EVERY FIXTURE HERE OPENS WITH AN ESCAPE, and it is not decoration. `feed_live_screen` refuses to
#: create a screen for a terminal whose first chunk carries no ESC byte -- deliberately, so that a
#: plain log stays a byte-for-byte log instead of being wrapped and truncated into terminal state.
#: A plain-text fixture therefore renders NOTHING, the reader returns early, and every assertion in
#: this file passes vacuously. It cost five red tests whose messages were all about the rule.
_SGR = chr(27) + "[0m"

#: A resume menu. This is the one screen family whose right answer depends on a fact outside the
#: screen, so it is the only one that may spend a query to learn it.
RESUME_SCREEN = (
    _SGR + "  ❯ Resume from summary (recommended)\r\n"
    "    Resume full session as-is\r\n"
    "  Enter to confirm\r\n"
)
#: Anything else. The overwhelming majority of chunks look like this, and none of them may pay.
ORDINARY_SCREEN = _SGR + "running tests...\r\n  42 passed\r\n"


class ThePromptRuleGetsTheTerminalsResumePolicyTests(FastApiTestCase):
    DB_NAME = "aify-test-resume-policy.db"

    def setUp(self):
        super().setUp()
        self._seen: list[str] = []
        real = output_module.answer_for_screen

        def _recording(screen, *, resume_policy: str = ""):
            self._seen.append(resume_policy)
            return real(screen, resume_policy=resume_policy)

        output_module.answer_for_screen = _recording
        self.addCleanup(setattr, output_module, "answer_for_screen", real)

    def _seed(self, runtime_state: str) -> None:
        async def go():
            db = await get_db()
            try:
                stamp = _now()
                await db.execute(
                    "INSERT OR REPLACE INTO agents (id, name, role, runtime, status, "
                    "runtime_state, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (AGENT, AGENT, "coder", "claude-code", "online", runtime_state, stamp, stamp),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _check(self, screen: str) -> None:
        """Drives the REAL entry point with a REAL database, because the two things being measured
        — which table the policy comes from, and when the query happens — are both invisible to a
        test that calls the pure rule directly."""
        feed_live_screen(TERMINAL, screen, cols=80, rows=24)

        async def go():
            db = await get_db()
            try:
                await _answer_console_prompt(db, {
                    "id": TERMINAL, "agent_id": AGENT, "session_id": SESSION,
                    "environment_id": "windows:policy:default", "bridge_id": "bridge-1",
                })
            finally:
                await db.close()

        asyncio.run(go())

    def test_THE_POLICY_ON_THE_AGENT_ROW_REACHES_THE_RULE(self):
        """THE DEFECT. Reading it off the terminal produced "" silently; this fails if that returns."""
        self._seed(json.dumps({"resumePolicy": POLICY}))
        self._check(RESUME_SCREEN)
        self.assertEqual(
            self._seen, [POLICY],
            "the agent's resumePolicy did not reach the rule that decides its dialog",
        )

    def test_an_agent_with_no_policy_reads_as_no_policy(self):
        """NEGATIVE CONTROL. Without it the assertion above passes on an instrument that returns the
        same string whatever it is given — which is precisely the failure it exists to detect."""
        self._seed(json.dumps({"bridgeInstanceId": "b-1"}))
        self._check(RESUME_SCREEN)
        self.assertEqual(self._seen, [""])

    def test_an_unregistered_agent_does_not_raise(self):
        """A terminal can outlive its agent row. This path runs inside the console stream, so an
        exception here trades a stuck prompt for a blind console."""
        self._check(RESUME_SCREEN)
        self.assertEqual(self._seen, [""])

    def test_AN_ORDINARY_SCREEN_SPENDS_NO_QUERY(self):
        """THE COST PROPERTY. Every chunk from every worker passes here, in front of a single SQLite
        writer at roughly forty frames a second. A query per chunk to answer a dialog that appears
        once per session is the wrong trade, and a regression to it would show up as console lag
        rather than as a failure."""
        self._seed(json.dumps({"resumePolicy": POLICY}))
        queried: list[str] = []
        real_lookup = output_module._resume_policy_for_agent

        async def _counting(db, agent_id):
            queried.append(agent_id)
            return await real_lookup(db, agent_id)

        output_module._resume_policy_for_agent = _counting
        self.addCleanup(setattr, output_module, "_resume_policy_for_agent", real_lookup)

        self._check(ORDINARY_SCREEN)
        self.assertEqual(queried, [], "an ordinary screen paid for a policy no rule would consult")
        # CONTROL: the counter is capable of recording, so the empty list above means "did not
        # happen" rather than "was not watching".
        self._check(RESUME_SCREEN)
        self.assertEqual(queried, [AGENT])

    def test_the_rule_is_consulted_at_all(self):
        """CONTROL FOR THE CONTROLS. An empty list would make the assertions above vacuous if the
        prompt check stopped being reached."""
        self._seed(json.dumps({"resumePolicy": POLICY}))
        self._check(ORDINARY_SCREEN)
        self.assertTrue(self._seen, "the write path never consulted the console-prompt rule")
