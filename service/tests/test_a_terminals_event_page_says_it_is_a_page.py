r"""`GET /terminals/{id}` says when its event list is a page, and the cap has one owner.

TWO HARDCODED 200s IN DIFFERENT MODULES. `reconcilers/terminal_history.py` pruned each terminal to a
local `keep_events_per_terminal = 200`; `routers/terminals.py` read with a hardcoded `LIMIT 200`.
They agreed by coincidence, and the coincidence was load-bearing both ways: raise the pruner alone
and the extra history is unreachable through the one endpoint that exists to explain a terminal;
raise the reader alone and it asks for rows the pruner has already deleted.

MEASURED on the operator's database, 2026-08-29: of 26 terminals, 21 hold 200 or more events and two
hold 208 and 209. The pruner runs on the 60s sweep and a busy console outruns it, so those two
responses were already truncating -- with nothing in them saying so.

THE ROUTE HAD ALREADY BEEN HALF-FIXED, which is the part worth noticing. Its own comment records
that `ORDER BY id ASC LIMIT 200` returned a terminal's OLDEST events, so everything recent -- "including
whatever it was doing when it died" -- was unreachable, and says "Measured on a live console: the cap
was hit exactly, which is what being truncated looks like from outside". Somebody saw the truncation,
fixed WHICH 200 rows came back, and left the response still not saying there were more.

NOT AN ALARM ABOUT THE OTHER TWO CAPS. `GET /dispatch/runs/{id}` caps events and controls at 200
each; measured the same day, the busiest run has 11 events and 26 controls, and `dispatch_events` is
pruned by age rather than per-run count. Those are far from biting and are left alone, which is why
this file is about terminals and not about "every LIMIT 200".
"""
from __future__ import annotations

import asyncio
import re
import time

from service.api_core.tuning import TERMINAL_EVENTS_KEPT_PER_TERMINAL
from service.db import get_db
from service.tests._base import FastApiTestCase

TERMINAL = "term-page-probe"


class ATerminalsEventPageSaysItIsAPage(FastApiTestCase):
    def _write(self, query: str, params: tuple = ()) -> None:
        async def run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _terminal(self) -> None:
        """`session_id` is NOT NULL and the foreign keys are real, so the row needs a whole family:
        an environment, an agent, a session. Seeded through the API where one exists, because a
        hand-built parent is another copy of the schema. The first version inserted the terminal
        alone and the fixture raised IntegrityError twice -- which is the schema doing its job."""
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "env-1", "label": "probe", "machineId": "probe-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-1", "cwdRoots": ["/w"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"]}],
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "probe-agent", "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "launchMode": "detached",
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        # `spawn_spec_id` and `spawn_request_id` DEFAULT to '' and carry foreign keys, so a row that
        # leans on the defaults fails the FK check against a spawn row with id ''. NULL is passed
        # explicitly, which SQLite exempts. All three product inserts set both columns, so the
        # defaults are unreachable in service code -- this is a fixture hazard, not a defect.
        self._write(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode,"
            " status, started_at, last_seen, spawn_spec_id, spawn_request_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("session-page-probe", "probe-agent", "env-1", "claude-code", "/w", "managed-warm",
             "running", "2026-08-29T00:00:00Z", "2026-08-29T00:00:00Z", None, None),
        )
        self._write(
            "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id,"
            " runtime, workspace, command, status, requested_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (TERMINAL, "session-page-probe", "probe-agent", "env-1", "bridge-1", "claude-code",
             "/w", "claude", "running", "dashboard", "2026-08-29T00:00:00Z",
             "2026-08-29T00:00:00Z"),
        )

    def _events(self, count: int) -> None:
        async def run():
            db = await get_db()
            try:
                await db.executemany(
                    "INSERT INTO terminal_events (terminal_id, event_type, body, created_at)"
                    " VALUES (?,?,?,?)",
                    [(TERMINAL, f"probe-{index:04d}", "{}",
                      time.strftime("%Y-%m-%dT%H:%M:%SZ")) for index in range(count)],
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _fetch(self) -> dict:
        response = self.client.get(f"/api/v1/terminals/{TERMINAL}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_A_SHORT_HISTORY_IS_NOT_A_PAGE(self):
        self._terminal()
        self._events(5)
        body = self._fetch()
        self.assertEqual(body["eventsShowing"], 5)
        self.assertFalse(body["eventsTruncated"])

    def test_THE_DEFECT_a_history_past_the_cap_says_so(self):
        self._terminal()
        self._events(TERMINAL_EVENTS_KEPT_PER_TERMINAL + 9)
        body = self._fetch()
        self.assertEqual(body["eventsShowing"], TERMINAL_EVENTS_KEPT_PER_TERMINAL)
        self.assertTrue(body["eventsTruncated"], (
            "the response carried a full page and claimed nothing was missing; 21 of the operator's "
            "26 terminals are at or over this cap"
        ))

    def test_EXACTLY_the_cap_is_not_truncated(self):
        """The off-by-one that makes a one-row-wider read worth doing at all. A history of exactly
        the cap is complete, and a reader that compared `len(rows) >= cap` would call it a page."""
        self._terminal()
        self._events(TERMINAL_EVENTS_KEPT_PER_TERMINAL)
        body = self._fetch()
        self.assertEqual(body["eventsShowing"], TERMINAL_EVENTS_KEPT_PER_TERMINAL)
        self.assertFalse(body["eventsTruncated"])

    def test_the_page_is_the_NEWEST_events_in_chronological_order(self):
        """What the route's own comment fixed once: the oldest 200 were useless for explaining a
        terminal's death. Pinned here so the one-row-wider change cannot quietly undo it -- reading
        DESC and reversing is exactly where an off-by-one flips which end you keep."""
        self._terminal()
        self._events(TERMINAL_EVENTS_KEPT_PER_TERMINAL + 9)
        events = self._fetch()["events"]
        types = [event["eventType"] for event in events]
        self.assertEqual(types, sorted(types), "the page is not in chronological order")
        newest = f"probe-{TERMINAL_EVENTS_KEPT_PER_TERMINAL + 8:04d}"
        self.assertEqual(types[-1], newest, "the newest event is missing; the page kept the wrong end")
        self.assertNotIn("probe-0000", types, "the page still starts at the oldest event")

    def test_THE_CAP_HAS_ONE_OWNER(self):
        """The reason the two numbers agreed was that nobody had changed either. Raising the owner
        has to move the reader with it, or the extra history is unreachable through the only
        endpoint that serves it.

        READS THE SQL THE MODULES ISSUE, not their text. The first version asserted `"LIMIT 200" not
        in source` and went red on the route's own PROSE -- the comment recording that
        `ORDER BY id ASC LIMIT 200` returned the oldest events. A gate that cannot tell a statement
        from a sentence about a statement would have to be satisfied by deleting the history."""
        from pathlib import Path

        from service.tests.sql_sources import sql_literals

        repo = Path(__file__).resolve().parents[2]
        for relative in ("service/reconcilers/terminal_history.py", "service/routers/terminals.py"):
            path = repo / relative
            source = path.read_text(encoding="utf-8")
            self.assertIn("TERMINAL_EVENTS_KEPT_PER_TERMINAL", source,
                          f"{relative} no longer reads the owned cap")
            # A LITERAL PAGE SIZE, not any literal LIMIT. The pruner's
            # `ORDER BY id DESC LIMIT 1 OFFSET ?` is correct: `LIMIT 1` fetches the single row AT
            # the cutoff and the cap itself is the parameterised OFFSET, which reads the owned
            # constant. A blunter pattern flagged it, which would have meant "fixing" the one
            # statement in this pair that was already right.
            capped = [
                f"{relative}:{line}" for _p, line, text in sql_literals(path.parent)
                if _p == path and "terminal_events" in text
                and any(int(n) >= 50 for n in re.findall(r"LIMIT\s+(\d+)", text, re.IGNORECASE))
            ]
            self.assertEqual(capped, [], (
                f"{relative} issues a terminal_events statement with a literal LIMIT again: {capped}"
            ))

    def test_the_pruner_keeps_what_the_route_can_return(self):
        """The relationship, executed rather than asserted about. Prune a terminal that is over the
        cap and the route must still report a full page -- if the pruner kept FEWER than the route
        reads, every fetch would look truncated forever."""
        from service.reconcilers.terminal_history import _prune_terminal_history

        self._terminal()
        self._events(TERMINAL_EVENTS_KEPT_PER_TERMINAL + 9)

        async def prune():
            db = await get_db()
            try:
                return await _prune_terminal_history(db)
            finally:
                await db.close()

        asyncio.run(prune())
        body = self._fetch()
        self.assertEqual(body["eventsShowing"], TERMINAL_EVENTS_KEPT_PER_TERMINAL)
        self.assertFalse(body["eventsTruncated"], (
            "after pruning, the history is exactly the cap and the response should call it complete"
        ))
