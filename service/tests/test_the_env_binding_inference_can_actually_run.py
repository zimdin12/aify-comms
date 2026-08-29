r"""The resident-to-managed switch really does infer an environment binding.

THE DEFECT, and it is the "correct but wrong" shape rather than a wrong answer. This function exists
because of an operator report on 2026-06-12: flipping an agent resident -> managed left
`runtime_state` with no `environmentId`, so it rendered in the Sessions page "unassigned" group and
"looked unreachable until someone hand-edited the identity or a spawn re-bound it". The fix tries two
sources in order -- the agent's own latest session row, then the newest online-first environment for
its machine.

Its first statement ordered by `COALESCE(last_seen, created_at)`. `agent_sessions` has no
`created_at` column, and no migration adds one, so that statement raised

    OperationalError: no such column: created_at

on every call from `055647f8` (2026-06-12) to 2026-08-29 -- 78 days. The `except Exception` around
the block caught it, nothing was logged, and the switch continued with an empty binding: precisely
the unassigned agent it was written to prevent.

BOTH SOURCES WERE INSIDE THAT ONE `try`, so the failure of the first skipped the second as well. The
docstring's "two sources are tried in order" was true of the code's shape and false of its behaviour.
They have a guard each now.

WHY NOTHING CAUGHT IT. The handler is covered by `test_switch_agent_session_mode_split_is_inert.py`,
which proves the extraction was a pure move -- it compares source, not behaviour, so it would have
passed just as happily on a statement that always raised. And every test of the switch asserted the
switch SUCCEEDED, which it did: the inference is advisory and its failure is swallowed by design.
Found by asking which single-table statements name an identifier their table does not declare.

THE `except Exception` STAYS. Its stated reason is sound -- "a binding that cannot be inferred must
not fail a switch the operator explicitly asked for" -- and this is a case where a swallow hid a
programming error for 78 days rather than an argument that it should not swallow data errors. What
changes is that the inference is now executed by a test, so a statement that cannot run fails here
instead of failing silently in production.
"""
from __future__ import annotations

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase

AGENT = "switch-probe"


class TheEnvBindingInferenceCanActuallyRun(FastApiTestCase):
    def _write(self, query: str, params: tuple = ()) -> None:
        async def run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _environment(self, environment_id: str, machine_id: str, bridge_id: str = "bridge-1") -> None:
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": environment_id, "label": environment_id, "machineId": machine_id, "os": "linux",
            "kind": "linux", "bridgeId": bridge_id, "cwdRoots": ["/w"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"]}],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _resident_agent(self, machine_id: str = "probe-host") -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "resident", "launchMode": "detached", "machineId": machine_id,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _only_these_sessions(self) -> None:
        """Registering an agent CREATES a resident session bound to its machine's environment, with
        `last_seen` of now -- newer than anything a fixture seeds with a fixed date. The first
        version of this test seeded two sessions, got the auto-created one back, and read as a
        failure of the inference when the inference had answered correctly. Clearing first makes the
        claim about the rows this test controls."""
        self._write("DELETE FROM agent_sessions WHERE agent_id = ?", (AGENT,))

    def _session(self, session_id: str, environment_id: str, last_seen: str) -> None:
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode,"
            " status, started_at, last_seen, spawn_spec_id, spawn_request_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, AGENT, environment_id, "claude-code", "/w", "managed-warm", "ended",
             "2026-08-01T00:00:00Z", last_seen, None, None),
        )

    def _switch_to_managed(self) -> dict:
        response = self.client.patch(f"/api/v1/agents/{AGENT}/session-mode",
                                     json={"mode": "managed", "requestedBy": "dashboard"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _runtime_state(self) -> dict:
        body = self.client.get(f"/api/v1/agents/{AGENT}").json()
        return body["agent"].get("runtimeState") or {}

    def test_THE_DEFECT_the_binding_is_inferred_from_the_latest_session(self):
        """The first source. For 78 days this raised before it could answer."""
        self._environment("env-old", "probe-host")
        self._environment("env-new", "probe-host", bridge_id="bridge-2")
        self._resident_agent()
        self._only_these_sessions()
        self._session("s-old", "env-old", "2026-08-01T00:00:00Z")
        self._session("s-new", "env-new", "2026-08-28T00:00:00Z")
        self._switch_to_managed()
        self.assertEqual(self._runtime_state().get("environmentId"), "env-new", (
            "the switch left the agent with no environment binding, which is the 'unassigned' agent "
            "this inference was written to prevent"
        ))

    def test_THE_SECOND_SOURCE_RUNS_WHEN_THE_FIRST_ANSWERS_NOTHING(self):
        """An agent with no prior session at all falls to the machine lookup. Both sources shared one
        `try` until 2026-08-29, so the second was unreachable whenever the first failed -- and the
        first always failed."""
        self._environment("env-machine", "probe-host")
        self._resident_agent()
        self._only_these_sessions()
        self._switch_to_managed()
        self.assertEqual(self._runtime_state().get("environmentId"), "env-machine")

    def test_an_ONLINE_environment_wins_over_an_offline_one_for_the_machine(self):
        """The ordering inside the second source, which nothing had ever executed."""
        self._environment("env-quiet", "probe-host")
        self._write("UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
                    ("env-quiet",))
        self._environment("env-live", "probe-host", bridge_id="bridge-2")
        self._resident_agent()
        self._only_these_sessions()
        self._switch_to_managed()
        self.assertEqual(self._runtime_state().get("environmentId"), "env-live")

    def test_THE_SWITCH_STILL_SUCCEEDS_WHEN_NOTHING_CAN_BE_INFERRED(self):
        """The property the `except Exception` protects, and the reason this defect stayed invisible:
        the switch is supposed to succeed either way, so its own tests were green throughout."""
        self._resident_agent(machine_id="host-with-no-environment")
        body = self._switch_to_managed()
        self.assertTrue(body.get("ok", True), body)
        self.assertEqual(self._runtime_state().get("environmentId", ""), "")

    def test_THE_STATEMENT_ITSELF_RUNS(self):
        """The narrowest possible statement of the bug, so a future reader sees what broke rather
        than only what it cost. `agent_sessions` has no `created_at`; ordering by it raises."""
        async def probe(sql):
            db = await get_db()
            try:
                await (await db.execute(sql, (AGENT,))).fetchone()
                return None
            except Exception as exc:  # noqa: BLE001 - the message is the assertion
                return str(exc)
            finally:
                await db.close()

        broken = asyncio.run(probe(
            "SELECT environment_id FROM agent_sessions WHERE agent_id = ?"
            " ORDER BY datetime(COALESCE(last_seen, created_at)) DESC LIMIT 1"))
        self.assertIsNotNone(broken, "agent_sessions has grown a created_at column")
        self.assertIn("created_at", broken)

        fixed = asyncio.run(probe(
            "SELECT environment_id FROM agent_sessions WHERE agent_id = ?"
            " ORDER BY datetime(COALESCE(last_seen, started_at)) DESC LIMIT 1"))
        self.assertIsNone(fixed, f"the corrected statement does not run either: {fixed}")

    def test_A_FAILING_FIRST_SOURCE_DOES_NOT_SKIP_THE_SECOND(self):
        """The split, pinned directly rather than inferred.

        Both sources shared one `try` until 2026-08-29, so ANY error in the session lookup -- not
        just the `created_at` one -- took the machine lookup with it. Fixing the column alone would
        leave that arrangement in place for the next failure, so this drives the function with a
        `db` whose first statement raises and requires the second to answer anyway.
        """
        from service.api_core.session_mode_env_binding import (
            _infer_environment_binding_for_managed_switch,
        )

        class OneRow:
            def __init__(self, mapping):
                self._mapping = mapping

            def __getitem__(self, key):
                return self._mapping[key]

        class Cursor:
            def __init__(self, row):
                self._row = row

            async def fetchone(self):
                return self._row

        class FirstStatementFails:
            """First execute raises, as the broken ORDER BY did; the second answers."""

            def __init__(self):
                self.calls = 0

            async def execute(self, sql, params=()):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("no such column: created_at")
                return Cursor(OneRow({"id": "env-from-machine"}))

        runtime_state: dict = {}
        warnings: list = []
        db = FirstStatementFails()
        asyncio.run(_infer_environment_binding_for_managed_switch(
            db, AGENT, {"machine_id": "probe-host"}, "managed", runtime_state, warnings,
        ))
        self.assertEqual(db.calls, 2, "the second source was never attempted")
        self.assertEqual(runtime_state.get("environmentId"), "env-from-machine", (
            "a failing session lookup still swallows the machine lookup with it"
        ))
