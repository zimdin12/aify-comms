r"""Terminals can be enumerated, which until 2026-08-28 they could not be.

THE ABSENCE WAS THE DEFECT. The API could fetch ONE terminal by id (`GET /terminals/{id}`) and claim
controls for them. There was no way to ask "which terminals are live?" -- not for the dashboard, not
for `aify-comms doctor`, not for anything. Measured against the live OpenAPI document the day this was
written: ten terminal paths, none of them a listing.

WHAT THAT COST, the same evening. aify-env owned a live PTY, pid 155844, running
`claude-aify --aify-agent ef-manager --auto --resume ...`. The control plane's terminal row for that
pid read `stopped`; all 80 most recent sessions read `stopped`; the agent read `available` with a
fresh `lastSeen`, because the orphan was heartbeating on its own behalf. The operator saw a process on
one screen and nothing on the other and asked for exactly this: "aify-env side running process
visibility, to catch orphans like that".

A reconciliation needs BOTH sides enumerable, and the key was already present on both:
`terminal_sessions.process_id` holds the OS pid (99 of 103 rows numeric, measured) and aify-env's
`/processes` reports `pid`. Two lists nobody had put beside each other.

The comparison itself lives in `mcp/stdio/env-process-reconciliation.mjs`. This file covers the read
it depends on.

SEEDED, NEVER SAMPLED. The first version of this file asserted against whatever rows the ambient
database happened to hold -- and on the machine it was written on, that was the OPERATOR'S LIVE
DATABASE. Its clipping test then SKIPPED, because the host path it reached had too few rows, so the
one assertion that needed data proved nothing while looking green. Rows are inserted here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from service.tests._base import FastApiTestCase

from service.routers.terminals import LIVE_TERMINAL_STATUSES, MAX_TERMINAL_LIST


class TheListingExists(FastApiTestCase):
    DB_NAME = "terminals-listing.db"

    def _seed(self, rows):
        """Insert terminal rows directly. The creation path runs through a bridge claim, which is a
        great deal of machinery to stand up for a question about a SELECT."""
        import asyncio

        from service.db import get_db

        # THE AGENT THROUGH THE API, because `agent_sessions.agent_id` is a FOREIGN KEY to `agents`
        # and the registration path owns what a valid agent row looks like. The chain is
        # agents -> agent_sessions + environments -> terminal_sessions, and every link is enforced.
        for agent_id in sorted({row.get("agent_id", "an-agent") for row in rows}):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident",
            })
            self.assertEqual(registered.status_code, 200, registered.text)

        async def write():
            db = await get_db()
            try:
                # THE TWO PARENTS FIRST. `terminal_sessions` has FOREIGN KEYs to `environments(id)`
                # AND `agent_sessions(id)`, and `session_id` is NOT NULL -- so an empty string is a
                # VALUE the constraint enforces, not an absence it permits. Both failures arrive as a
                # bare "FOREIGN KEY constraint failed" naming neither column nor table, which is why
                # this comment exists rather than the next reader rediscovering it.
                for environment_id in {row.get("environment_id", "windows:host:default") for row in rows}:
                    await db.execute(
                        "INSERT OR IGNORE INTO environments (id, machine_id, status, last_seen, registered_at) "
                        "VALUES (?,?,?,?,?)",
                        (environment_id, "test-machine", "online", "2026-08-28T00:00:00Z",
                         "2026-08-28T00:00:00Z"),
                    )
                # NULL, NOT OMITTED, for the two spawn FKs. `spawn_spec_id` and `spawn_request_id`
                # DEFAULT to '' and are FOREIGN KEYs, and `PRAGMA foreign_keys=ON` is set in db.py --
                # so leaving them out enforces a reference to `spawn_specs('')`, a row that never
                # exists. Every production INSERT lists them and passes None (checked: all three), so
                # this is a trap for a writer that omits the columns rather than a live defect.
                await db.execute(
                    "INSERT OR IGNORE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, started_at, last_seen, spawn_spec_id, spawn_request_id) "
                    "VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                    ("sess-test", sorted({r.get("agent_id", "an-agent") for r in rows})[0],
                     "windows:host:default", "claude-code", "running",
                     "2026-08-28T00:00:00Z", "2026-08-28T00:00:00Z"),
                )
                for row in rows:
                    await db.execute(
                        """
                        INSERT INTO terminal_sessions
                            (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,
                             command, status, requested_by, created_at, updated_at, process_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            row["id"], "sess-test", row.get("agent_id", "an-agent"),
                            row.get("environment_id", "windows:host:default"), "", "claude-code", "",
                            "", row["status"], "test", "2026-08-28T00:00:00Z", row["updated_at"],
                            row.get("process_id", ""),
                        ),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(write())

    def test_the_route_is_registered(self) -> None:
        """POSITIVE CONTROL for everything below: an unmounted route makes every request a 404 that
        the assertions could be written to accept."""
        paths = {getattr(route, "path", "") for route in self._app.routes}
        self.assertIn("/api/v1/terminals", paths)
        self.assertIn(
            "/api/v1/terminals/{terminal_id}", paths,
            "the single-terminal route disappeared; a listing must not replace the read it complements",
        )

    def test_the_live_set_is_not_a_second_hand_written_copy(self) -> None:
        """The statuses meaning "a process should exist" are stated once. This repo already fails a
        second hardcoded copy of a status set, for the reason this would repeat: two copies agree
        until one is edited."""
        self.assertEqual(
            set(LIVE_TERMINAL_STATUSES),
            {"starting", "attached", "running", "active", "idle"},
            "the live-terminal vocabulary changed; registration.py's adoption query uses the same set "
            "and must move with it",
        )

    def test_a_listing_carries_the_join_key_and_not_the_replay_buffer(self) -> None:
        """`processId` is the whole point -- it is what aify-env's listing is compared against.
        `output` is the whole point of EXCLUDING something: it is a replay buffer, and a 200-row
        listing carrying it would be the most expensive call in the API."""
        self._seed([{"id": "t1", "status": "attached", "updated_at": "2026-08-28T01:00:00Z",
                     "process_id": "155844"}])
        body = self.client.get("/api/v1/terminals").json()
        self.assertTrue(body["ok"])
        self.assertEqual([t["id"] for t in body["terminals"]], ["t1"])
        terminal = body["terminals"][0]
        self.assertEqual(terminal["processId"], "155844", "the join key is missing from the listing")
        self.assertIn("agentId", terminal)
        self.assertNotIn(
            "output", terminal,
            "the replay buffer is in the listing, which makes a reconciliation read the most "
            "expensive call in the API",
        )

    def test_live_is_the_default_and_a_stopped_terminal_is_not_in_it(self) -> None:
        """THE OPERATOR'S CASE IN ONE ASSERTION. A stopped row asserts nothing about a process
        existing, so a reconciliation must not see it as accounting for one."""
        self._seed([
            {"id": "alive", "status": "attached", "updated_at": "2026-08-28T02:00:00Z", "process_id": "1"},
            {"id": "dead", "status": "stopped", "updated_at": "2026-08-28T03:00:00Z", "process_id": "2"},
        ])
        live = self.client.get("/api/v1/terminals").json()["terminals"]
        self.assertEqual([t["id"] for t in live], ["alive"])
        every = self.client.get("/api/v1/terminals", params={"status": "all"}).json()["terminals"]
        self.assertEqual({t["id"] for t in every}, {"alive", "dead"})

    def test_an_unrecognised_status_filters_to_nothing_rather_than_being_ignored(self) -> None:
        """Ignoring it would answer a question the caller did not ask, with a list that looks
        complete -- the failure mode a reconciliation check cannot survive."""
        self._seed([{"id": "t1", "status": "attached", "updated_at": "2026-08-28T01:00:00Z"}])
        body = self.client.get("/api/v1/terminals", params={"status": "zzq-no-such-status"}).json()
        self.assertEqual(body["terminals"], [])

    def test_filters_narrow_rather_than_widen(self) -> None:
        self._seed([
            {"id": "mine", "status": "attached", "updated_at": "2026-08-28T01:00:00Z", "agent_id": "a"},
            {"id": "theirs", "status": "attached", "updated_at": "2026-08-28T02:00:00Z", "agent_id": "b"},
        ])
        by_agent = self.client.get("/api/v1/terminals", params={"agentId": "a"}).json()["terminals"]
        self.assertEqual([t["id"] for t in by_agent], ["mine"])
        by_env = self.client.get(
            "/api/v1/terminals", params={"environmentId": "wsl:elsewhere:default"},
        ).json()["terminals"]
        self.assertEqual(by_env, [])

    def test_the_limit_is_clamped_rather_than_trusted(self) -> None:
        """`max(1, ...)` alone leaves the upper end open, which is how `comms_files` once dumped 333
        artifacts into an agent's context. A non-integer is refused by FastAPI's own typing before
        this handler runs, which is the right layer for it -- so this covers the RANGE, which is the
        part the handler owns."""
        self._seed([{"id": "t1", "status": "attached", "updated_at": "2026-08-28T01:00:00Z"}])
        for value in (0, -5, 1000000):
            response = self.client.get("/api/v1/terminals", params={"limit": value})
            self.assertEqual(response.status_code, 200, f"limit={value} was refused rather than clamped")
            self.assertLessEqual(len(response.json()["terminals"]), MAX_TERMINAL_LIST)
        self.assertEqual(
            self.client.get("/api/v1/terminals", params={"limit": "not-a-number"}).status_code, 422,
            "a non-integer limit is validated by FastAPI; if that stops being true the handler must "
            "start parsing it itself",
        )

    def test_a_clipped_listing_admits_it(self) -> None:
        """A truncated list that does not say so reads as "that is everything". The reconciliation
        treats `truncated` as UNKNOWN rather than as a pile of orphans, so this flag is what stands
        between a bound and a false accusation."""
        self._seed([
            {"id": "t1", "status": "attached", "updated_at": "2026-08-28T01:00:00Z"},
            {"id": "t2", "status": "attached", "updated_at": "2026-08-28T02:00:00Z"},
        ])
        clipped = self.client.get("/api/v1/terminals", params={"limit": 1}).json()
        self.assertEqual(len(clipped["terminals"]), 1)
        self.assertTrue(clipped["truncated"], "a clipped listing did not say it was clipped")
        full = self.client.get("/api/v1/terminals", params={"limit": 50}).json()
        self.assertEqual(len(full["terminals"]), 2)
        self.assertFalse(full["truncated"], "an unclipped listing claimed it was clipped")

    def test_the_most_recent_rows_are_the_ones_kept(self) -> None:
        """ORDER BY is not decoration on a LIMIT. Without it SQLite may return any N rows, so a
        clipped listing would be an arbitrary sample presented as the newest."""
        self._seed([
            {"id": "old", "status": "attached", "updated_at": "2026-08-01T00:00:00Z"},
            {"id": "new", "status": "attached", "updated_at": "2026-08-28T00:00:00Z"},
        ])
        body = self.client.get("/api/v1/terminals", params={"limit": 1}).json()
        self.assertEqual([t["id"] for t in body["terminals"]], ["new"])


if __name__ == "__main__":
    unittest.main()
