r"""The environment heartbeat is the hottest write path, and its cost is counted rather than felt.

EVERY BRIDGE, EVERY 30 SECONDS, plus aify-env on its own interval since the 2026-08-30 cutover. On the
operator's host that is two beats a minute per environment against a single-worker uvicorn and one
SQLite file -- the same file every dashboard poll reads. A round-trip added here is added everywhere.

COUNTED, NOT TIMED. Wall-clock on this machine is unmeasurable: the same code timed 44-47ms and then
22-25ms minutes apart, because the live fleet is the load. A round-trip count is deterministic,
attributable to a specific line, and does not move when the machine is busy.

MEASURED 2026-08-30, steady state (the row already exists): **4 calls — 3 SELECT, 1 UPDATE.**

The cutover added none: `_canonical_runtimes` and the `HOST_OWNED_METADATA` loop are pure Python over
lists that are already in memory. That is the claim this file exists to keep true, because the natural
way to add a field is to add a query for it.

THE NUMBER IS A CEILING, NOT A TARGET. It may go down. It goes up only as a decision somebody writes
down, the way the size ratchets work -- an extra SELECT here is 2 per minute per environment forever.
"""

from __future__ import annotations

from collections import Counter

import aiosqlite

from service.tests._base import FastApiTestCase

#: Steady-state ceiling. Raising it is a decision; say in the commit what the extra round-trip buys.
HEARTBEAT_DB_CALLS = 4

BEAT = {
    "kind": "windows", "hostname": "cheap-host", "os": "windows", "machineId": "win32:cheap-host",
    "runtimes": [{"runtime": "claude", "available": True, "unavailableReason": ""}],
    "terminalRuntimes": ["claude"], "terminal": True, "pty": True,
}


class TheHeartbeatStaysCheapTests(FastApiTestCase):
    def _count(self, body: dict) -> tuple[int, Counter]:
        seen: list[str] = []
        original = aiosqlite.Connection.execute

        async def counting(connection, sql, *args, **kwargs):
            seen.append(str(sql).strip().split()[0].upper())
            return await original(connection, sql, *args, **kwargs)

        aiosqlite.Connection.execute = counting
        try:
            response = self.client.post("/api/v1/environments/heartbeat", json=body)
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            aiosqlite.Connection.execute = original
        return len(seen), Counter(seen)

    def test_the_counter_counts(self):
        """POSITIVE CONTROL. Every assertion below is an upper bound, which a counter that recorded
        nothing would satisfy for ever."""
        total, _ = self._count(BEAT)
        self.assertGreater(total, 0, "the patch did not intercept anything; the bound proves nothing")

    def test_a_steady_state_beat_costs_no_more_than_it_did(self):
        self._count(BEAT)  # create the row
        total, verbs = self._count(BEAT)  # the beat that repeats for ever
        self.assertLessEqual(total, HEARTBEAT_DB_CALLS, (
            f"a steady-state heartbeat now makes {total} db calls ({dict(verbs)}), above the recorded "
            f"{HEARTBEAT_DB_CALLS}. This runs twice a minute per environment against a single-worker "
            "service and one SQLite file. If the round-trip is genuinely needed, say what it buys."
        ))

    def test_the_ceiling_is_not_slack(self):
        """The other half of a ratchet: a bound well above the real count reports success for ever."""
        self._count(BEAT)
        total, _ = self._count(BEAT)
        self.assertGreaterEqual(total, HEARTBEAT_DB_CALLS - 1, (
            f"the beat costs {total} calls against a ceiling of {HEARTBEAT_DB_CALLS} — work has been "
            "done, so lower the ceiling to the measured value and keep the ratchet biting"
        ))

    def test_a_beat_that_omits_host_facts_is_no_more_expensive(self):
        """The shape the bridge sends after standing down. Preserving what a caller did not mention
        reads the existing row -- which the handler already had -- rather than fetching it again."""
        self._count(BEAT)
        total, _ = self._count({"id": "windows:cheap-host:default", "bridgeId": "bridge-A",
                                "label": "Windows on cheap-host", "cwdRoots": ["C:/Docker"]})
        self.assertLessEqual(total, HEARTBEAT_DB_CALLS, (
            f"the stood-down beat costs {total} calls; preservation is re-reading the row"
        ))
