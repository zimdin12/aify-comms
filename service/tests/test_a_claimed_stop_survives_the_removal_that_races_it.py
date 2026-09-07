"""A successful claim must deliver what it claimed, even when a removal deletes it that instant.

FOUND BY REVIEW on 5f286d66, and it is the half the bounded wait could not cover. `_await_stop_claims`
watches for `status = 'pending'` to reach zero -- and `_claim_terminal_controls_once` used to publish
exactly that state, with `db.commit()`, BEFORE it read the payload it was going to return:

    UPDATE terminal_controls SET status = 'claimed' ...
    await db.commit()                 <-- the wait is released HERE
    for control_id in ids: SELECT * FROM terminal_controls WHERE id = ?      <-- and this
    ...                   SELECT ... FROM terminal_sessions WHERE id = ?     <-- and this

Between the commit and those reads, `unregister_agent` sees zero pending, stops waiting, and deletes
the agent -- which cascades agents -> agent_sessions -> terminal_sessions -> terminal_controls. The
re-reads then find nothing and the host is told `{"ok": true, "controls": []}`.

WHY NO BUDGET COULD FIX IT. This is not the acknowledged limitation that an absent host times out
after 2 seconds. Here the host was present, claimed ON TIME, and the claim SUCCEEDED -- the stop was
lost after that, between the claim and its delivery. Raising `STOP_CLAIM_WAIT_SECONDS` moves nothing.

THE FIX IS ORDERING, NOT WAITING. The whole payload -- the claimed control rows and each target's
pid, agent, runtime and session_mode -- is frozen before the commit publishes the claim, and the
UPDATE now uses `RETURNING *` so it reports the rows it actually won rather than re-reading rows a
concurrent claimer might own.
"""

from __future__ import annotations

import asyncio

from service.models import TerminalControlClaim
from service.tests._base import FastApiTestCase

AGENT = "sc-claim-race"
ENV_ID = "windows:claim-race-host:default"
SESSION_ID = "sess_claim_race"
TERMINAL_ID = "term_claim_race"
BRIDGE_ID = ""


class ClaimedStopSurvivesTheRemovalTests(FastApiTestCase):
    def _seed(self) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": AGENT, "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "win32:claim-race-host",
                "capabilities": ["managed-run"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        async def _go():
            from service.db import get_db
            from service.api_core.events import _append_terminal_control
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO environments (id, machine_id, status, registered_at, last_seen)"
                    " VALUES (?, 'win32:claim-race-host', 'online', ?, ?)",
                    (ENV_ID, "2026-09-07T00:00:00Z", "2099-01-01T00:00:00Z"),
                )
                await _insert(db, "agent_sessions", {
                    "id": SESSION_ID, "agent_id": AGENT, "environment_id": ENV_ID,
                    "runtime": "claude-code", "status": "running",
                    "started_at": "2026-09-07T00:00:00Z", "last_seen": "2099-01-01T00:00:00Z",
                    # NULL, not the column default: these are enforced foreign keys declared
                    # `DEFAULT ''`, and an empty string is checked against a table with no such row.
                    "spawn_spec_id": None, "spawn_request_id": None,
                })
                await _insert(db, "terminal_sessions", {
                    "id": TERMINAL_ID, "session_id": SESSION_ID, "agent_id": AGENT,
                    "environment_id": ENV_ID, "runtime": "claude-code", "status": "running",
                    "process_id": "4242",
                    "created_at": "2026-09-07T00:00:00Z", "updated_at": "2026-09-07T00:00:00Z",
                })
                await _append_terminal_control(
                    db, terminal_id=TERMINAL_ID, environment_id=ENV_ID, bridge_id=BRIDGE_ID,
                    action="stop", requested_by="test", body="stop",
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_go())

    def _claim(self, *, delete_on_commit: bool):
        """Run the real claim, optionally letting a removal win at the exact instant of the commit."""
        from service.api_core import terminal_controls_io as io

        real_get_db = io.get_db

        async def _patched(*args, **kwargs):
            return _CommitHookedDb(await real_get_db(*args, **kwargs), delete_on_commit=delete_on_commit)

        io.get_db = _patched
        try:
            return asyncio.run(io._claim_terminal_controls_once(
                TerminalControlClaim(environmentId=ENV_ID, bridgeId=BRIDGE_ID)))
        finally:
            io.get_db = real_get_db

    def test_POSITIVE_CONTROL_the_claim_returns_the_stop_when_nothing_races_it(self):
        """Without this, a claim that returned nothing for ANY reason would satisfy the test below by
        accident -- and returning nothing is precisely the defect."""
        self._seed()
        result = self._claim(delete_on_commit=False)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["controls"]), 1, "the fixture never produced a claimable stop")
        self.assertEqual(result["controls"][0]["terminalId"], TERMINAL_ID)

    def test_A_SUCCESSFUL_CLAIM_STILL_CARRIES_ITS_PAYLOAD_WHEN_THE_REMOVAL_WINS(self):
        """The reviewer's reproduction, as a test.

        The removal lands at the exact instant the commit publishes the claim -- which is the moment
        `_await_stop_claims` stops waiting, so this is not a contrived schedule but the one the wait
        actively creates. The host claimed on time and succeeded; it must be told what it claimed.
        """
        self._seed()
        result = self._claim(delete_on_commit=True)

        self.assertTrue(result["ok"], "the claim reported failure")
        self.assertEqual(
            len(result["controls"]), 1,
            "a SUCCESSFUL claim returned no controls -- the stop was lost between claiming and "
            "delivering it, and no budget on the wait can repair that",
        )
        control = result["controls"][0]
        self.assertEqual(control["terminalId"], TERMINAL_ID)
        self.assertEqual(control["action"], "stop")
        # The target identity has to survive too: it is read from `terminal_sessions`, which the same
        # cascade removes. A control naming a terminal the host cannot resolve is not deliverable.
        self.assertEqual(control["pid"], "4242", "the target's pid was read after the rows were gone")
        self.assertEqual(control["agentId"], AGENT)

    def test_the_claim_reports_only_the_rows_it_actually_won(self):
        """`RETURNING *` rather than a re-read. The re-read returned a row whoever owned it, so a row
        another claimer had already taken came back as ours."""
        self._seed()

        async def _steal():
            from service.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE terminal_controls SET status = 'claimed', claimed_at = ? WHERE terminal_id = ?",
                    ("2026-09-07T00:00:01Z", TERMINAL_ID),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_steal())
        result = self._claim(delete_on_commit=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["controls"], [], "it claimed a control another bridge already held")


class _CommitHookedDb:
    """The real connection, with a removal wired to land on the first commit.

    A wrapper rather than a fake: the claim helper runs its real SQL against the real schema, so what
    this test proves is a property of the shipped code path and not of a stand-in.
    """

    def __init__(self, inner, *, delete_on_commit: bool):
        self._inner = inner
        self._delete_on_commit = delete_on_commit
        self._fired = False

    async def commit(self):
        if self._delete_on_commit and not self._fired:
            self._fired = True
            # The cascade a real `unregister_agent` triggers: agents -> agent_sessions ->
            # terminal_sessions -> terminal_controls. Issued on this same connection so it is
            # ordered exactly at the commit, with no reliance on cross-connection timing.
            await self._inner.execute("DELETE FROM agents WHERE id = ?", (AGENT,))
        return await self._inner.commit()

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def _insert(db, table: str, values: dict) -> None:
    row = {c: "x" for c in _required_columns(table)}
    row.update(values)
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    await db.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(row.values()))


def _required_columns(table: str) -> list[str]:
    """NOT NULL columns with no default, derived from the DDL this database is built from."""
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
    assert required, f"the DDL parse found no required columns in {table}"
    return required
