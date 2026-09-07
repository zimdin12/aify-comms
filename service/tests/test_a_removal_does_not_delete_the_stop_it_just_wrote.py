"""REMOVE is stop-then-tombstone, and the tombstone was deleting the stop.

MEASURED ON THE OPERATOR'S HOST, 2026-09-07, and it lost three times in one minute. aify-env's own
notices:

    terminal term_...391c371d is GONE from the service (404) but produced output 574s ago
      -- leaving it alone. Something upstream...
    stopping the worker for terminal term_...391c371d: the service no longer has that terminal
      and it has produced nothing for 6..s

Three managed workers streamed into 404s for TEN MINUTES. The host behaved correctly throughout: it
refused to kill workers that were still producing, and only stopped them once they had gone quiet on
its own guard. Nothing told it to stop, because the stop had been deleted.

THE MECHANISM, and `test_THE_CASCADE_IS_REAL` proves it rather than asserting it here.
`unregister_agent` writes a stop control, commits, then deletes the agent -- and `terminal_controls`
cascades from `terminal_sessions`, which cascades from `agent_sessions`, which cascades from
`agents`. The delete wipes the control the same request just wrote. The route's own comment named the
dependency without securing it: "the surviving stop control (claimed before the tombstone delete)
carries the triad-reap". The two commits are milliseconds apart.

A BOUND, NOT A GUARANTEE. A host that is not listening must not block a removal for ever, so the
deadline expires and the delete proceeds exactly as it did before -- never worse than the behaviour
this replaces. What it buys is the ordinary case, where a live claimer holds a long-poll open and
takes the control in milliseconds.

THE TWO TIMING TESTS ARE EACH OTHER'S CONTROL, and they measure the WAIT, not the guard in front of
it. One says a removal that signalled a stop takes about its budget; the other says a removal with
nothing to stop is immediate. Without the second, the first cannot tell a wait from a removal that is
simply slow.

WHAT THEY DO NOT PROVE, measured by mutation rather than assumed: deleting `if signalled:` leaves all
six green. It is not what bounds the latency -- an agent with no live terminal has no pending stop
either, so the wait returns from its first query regardless. The guard saves one COUNT on a path
where the answer is a foregone conclusion; the bound is carried by the empty result set.
"""

from __future__ import annotations

import asyncio
import time

from service.api_core.agent_terminal_ops import (
    STOP_CLAIM_WAIT_SECONDS,
    _await_stop_claims,
    _pending_stop_controls,
)
from service.tests._base import FastApiTestCase

AGENT = "sc-removed"
ENV_ID = "windows:removal-host:default"
SESSION_ID = "sess_removal_1"
TERMINAL_ID = "term_removal_1"

#: Half the real budget. The instrument only has to separate "waited" from "did not wait", and a
#: threshold at the budget itself would go red on a slow host for no defect.
WAITED = STOP_CLAIM_WAIT_SECONDS / 2


class RemovalDoesNotDeleteItsOwnStopTests(FastApiTestCase):
    # ── fixture ─────────────────────────────────────────────────────────────────────────────────

    def _seed_managed_agent_with_a_terminal(self) -> None:
        """A managed agent with a live session and terminal, as a spawn leaves it.

        THE SESSION ROW IS LOAD-BEARING, not scenery. `terminal_sessions` has no foreign key on
        `agent_id` at all -- the cascade runs agents -> agent_sessions -> terminal_sessions ->
        terminal_controls. A terminal seeded with a made-up `session_id` would not cascade, and every
        assertion below would pass while proving the opposite of what it says.
        """
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": AGENT, "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "win32:removal-host",
                "capabilities": ["managed-run"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self._with_db(self._seed_rows)

    async def _seed_rows(self, db) -> None:
        await db.execute(
            "INSERT OR REPLACE INTO environments (id, machine_id, status, registered_at, last_seen)"
            " VALUES (?, 'win32:removal-host', 'online', ?, ?)",
            (ENV_ID, "2026-09-07T00:00:00Z", "2099-01-01T00:00:00Z"),
        )
        await _insert(db, "agent_sessions", {
            "id": SESSION_ID, "agent_id": AGENT, "environment_id": ENV_ID,
            "runtime": "claude-code", "status": "running",
            "started_at": "2026-09-07T00:00:00Z", "last_seen": "2099-01-01T00:00:00Z",
            # NULL, NOT the column default. `spawn_spec_id` and `spawn_request_id` are declared
            # `DEFAULT ''` on an ENFORCED foreign key: an insert that omits them stores an empty
            # string, which is not NULL, so SQLite checks it against `spawn_specs(id)` and the row is
            # refused. Every production insert binds None for exactly this reason.
            "spawn_spec_id": None, "spawn_request_id": None,
        })
        await _insert(db, "terminal_sessions", {
            "id": TERMINAL_ID, "session_id": SESSION_ID, "agent_id": AGENT,
            "environment_id": ENV_ID, "runtime": "claude-code", "status": "running",
            "created_at": "2026-09-07T00:00:00Z", "updated_at": "2026-09-07T00:00:00Z",
        })
        await db.commit()

    def _with_db(self, body):
        """Run `body(db)` on its own connection, so a test reads what the route committed."""
        async def _go():
            from service.db import get_db
            db = await get_db()
            try:
                return await body(db)
            finally:
                await db.close()
        return asyncio.run(_go())

    def _write_pending_stop(self, db):
        from service.api_core.events import _append_terminal_control
        return _append_terminal_control(
            db, terminal_id=TERMINAL_ID, environment_id=ENV_ID, bridge_id="",
            action="stop", requested_by="test", body="stop",
        )

    # ── the mechanism ───────────────────────────────────────────────────────────────────────────

    def test_POSITIVE_CONTROL_removing_an_agent_still_removes_it(self):
        """Every other assertion here is about a stop surviving or a wait happening. A removal that
        had quietly stopped removing anything would satisfy several of them and report green."""
        self._seed_managed_agent_with_a_terminal()
        response = self.client.delete(f"/api/v1/agents/{AGENT}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"], "the removal reported failure")
        listing = self.client.get("/api/v1/agents")
        self.assertNotIn(AGENT, listing.json().get("agents", {}), "the agent survived removal")

    def test_THE_CASCADE_IS_REAL_a_delete_wipes_a_stop_nobody_took(self):
        """The defect itself, so this file's premise is measured rather than believed.

        If a later change breaks this cascade, the whole wait becomes unnecessary and this test is
        where that news arrives -- rather than the wait quietly costing every removal 2 seconds for a
        race that no longer exists.
        """
        self._seed_managed_agent_with_a_terminal()

        async def _body(db):
            await self._write_pending_stop(db)
            await db.commit()
            before = await _count_controls(db, TERMINAL_ID)
            await db.execute("DELETE FROM agents WHERE id = ?", (AGENT,))
            await db.commit()
            return before, await _count_controls(db, TERMINAL_ID)

        before, after = self._with_db(_body)
        self.assertEqual(before, 1, "the fixture never wrote a control, so the delete proves nothing")
        self.assertEqual(after, 0, "the cascade no longer reaches terminal_controls -- see the docstring")

    # ── the call site, and its negative control ─────────────────────────────────────────────────

    def test_A_REMOVAL_THAT_SIGNALLED_A_STOP_WAITS_FOR_IT(self):
        """Nothing claims the stop in this test -- there is no host at all -- so the route spends its
        whole budget and then deletes anyway. Two claims in one measurement: the wait is WIRED (a
        route that never called it returns immediately), and an unlistening host cannot block a
        removal for ever."""
        self._seed_managed_agent_with_a_terminal()
        started = time.monotonic()
        response = self.client.delete(f"/api/v1/agents/{AGENT}")
        elapsed = time.monotonic() - started

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"], "an unlistening host blocked the removal")
        self.assertGreaterEqual(elapsed, WAITED, "the removal did not wait for the stop to be taken")
        self.assertLess(elapsed, STOP_CLAIM_WAIT_SECONDS * 5, "the removal blocked far past its budget")

    def test_a_removal_with_nothing_to_stop_does_not_wait(self):
        """The control for the test above: it rules out a removal that is slow for its own reasons.

        A resident, or a managed agent whose worker already exited, has no live terminal and so no
        pending stop -- and must not pay the budget for a stop that was never written.
        """
        response = self.client.post(
            "/api/v1/agents",
            json={"agentId": "no-terminals", "role": "coder", "runtime": "claude-code",
                  "sessionMode": "managed", "capabilities": ["managed-run"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

        started = time.monotonic()
        removal = self.client.delete("/api/v1/agents/no-terminals")
        elapsed = time.monotonic() - started

        self.assertEqual(removal.status_code, 200, removal.text)
        self.assertLess(elapsed, WAITED, "a removal with no terminals to stop paid the wait anyway")

    # ── the wait itself ─────────────────────────────────────────────────────────────────────────

    def test_the_wait_polls_until_the_deadline_and_returns_at_once_when_claimed(self):
        """Semantics, on an injected clock so the assertion is about behaviour and not about how
        fast this machine is."""
        self._seed_managed_agent_with_a_terminal()

        async def _body(db):
            await self._write_pending_stop(db)
            await db.commit()
            self.assertEqual(await _pending_stop_controls(db, AGENT), 1)

            clock = _SteppingClock(step=0.1)
            slept = []

            async def _sleep(seconds):
                slept.append(seconds)

            took = await _await_stop_claims(
                db, AGENT, budget_seconds=0.25, poll_seconds=0.01, sleep=_sleep, monotonic=clock,
            )
            self.assertFalse(took, "an unclaimed stop reported as taken")
            self.assertTrue(slept, "the wait returned without ever polling -- it did not wait at all")
            self.assertEqual(set(slept), {0.01}, "the wait ignored the poll interval it was given")

            # CLAIMED: the host took it, and the wait ends without consulting the clock again.
            await db.execute(
                "UPDATE terminal_controls SET status = 'claimed', claimed_at = ? WHERE terminal_id = ?",
                ("2026-09-07T00:00:01Z", TERMINAL_ID),
            )
            await db.commit()
            self.assertEqual(await _pending_stop_controls(db, AGENT), 0)

            async def _never(seconds):
                self.fail("a claimed stop slept before answering")

            self.assertTrue(await _await_stop_claims(db, AGENT, budget_seconds=5.0, sleep=_never))

        self._with_db(_body)

    def test_a_zero_budget_still_answers_from_the_database(self):
        """Turning the wait off must not turn the QUESTION off: a caller passing no budget should
        still get the truth about whether anything is pending, not an optimistic True."""
        self._seed_managed_agent_with_a_terminal()

        async def _body(db):
            self.assertTrue(await _await_stop_claims(db, AGENT, budget_seconds=0),
                            "reported a pending stop when none had been written")
            await self._write_pending_stop(db)
            await db.commit()
            self.assertFalse(await _await_stop_claims(db, AGENT, budget_seconds=0),
                             "a zero budget reported success without reading the database")

        self._with_db(_body)


class _SteppingClock:
    """A monotonic clock that advances by a fixed step each time it is read.

    Deterministic, and it cannot hang: every read moves toward the deadline, so a wait that polls
    forever fails the test instead of the suite.
    """

    def __init__(self, *, step: float):
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        now = self._now
        self._now += self._step
        return now


async def _count_controls(db, terminal_id: str) -> int:
    cursor = await db.execute(
        "SELECT COUNT(*) FROM terminal_controls WHERE terminal_id = ?", (terminal_id,))
    row = await cursor.fetchone()
    return int(row[0] or 0) if row else 0


async def _insert(db, table: str, values: dict) -> None:
    """Insert a row, filling every column the DDL requires and the caller did not name.

    The required set is DERIVED from the real schema rather than typed here: a hand-listed fixture
    fails one column at a time as the schema grows, and each failure looks like a defect in the test.
    """
    row = {c: "x" for c in _required_columns(table)}
    row.update(values)
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    await db.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(row.values()))


def _required_columns(table: str) -> list[str]:
    """NOT NULL columns with no default, read out of the DDL this database is actually built from."""
    from service.schema import SCHEMA

    body = SCHEMA.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split("\n);", 1)[0]
    required = []
    for line in body.splitlines():
        part = line.strip().rstrip(",")
        if not part or part.startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "--")):
            continue
        upper = part.upper()
        if "NOT NULL" in upper and "DEFAULT" not in upper:
            required.append(part.split()[0])
    assert required, f"the DDL parse found no required columns in {table}, so this fixture proves nothing"
    return required
