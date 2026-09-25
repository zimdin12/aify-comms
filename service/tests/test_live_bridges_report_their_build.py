"""A bridge's build reaches `GET /bridges`, which is what `aify-comms doctor`'s `bridge-current` reads.

Until 0.7.0 that row read environment rows for a `bridgeBuild` only the retired environment bridge
ever sent, so it verified no running bridge and reported green (v0.7 scan B2). Bridges now send
their build when they register and it is stored on their `bridge_instances` row.
"""

import asyncio

import aiosqlite

from service.tests._base import FastApiTestCase


class LiveBridgesReportTheirBuildTests(FastApiTestCase):
    DB_NAME = "aify-live-bridges-build.db"

    def _register(self, agent_id, bridge_id, build):
        body = {"agentId": agent_id, "role": "coder", "bridgeId": bridge_id, "machineId": "win:host"}
        if build is not None:
            body["bridgeBuild"] = build
        response = self.client.post("/api/v1/agents", json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql, params=()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()
        asyncio.run(run())

    def _bridges(self):
        response = self.client.get("/api/v1/bridges")
        self.assertEqual(response.status_code, 200, response.text)
        return {row["id"]: row for row in response.json()["bridges"]}

    def test_a_registered_bridge_is_listed_with_the_build_it_sent(self):
        self._register("bc-coder", "bridge-1", "577c7ca1b2c3")
        row = self._bridges()["bridge-1"]
        self.assertEqual((row["agentId"], row["build"]), ("bc-coder", "577c7ca1b2c3"))

    def test_an_unusable_build_is_dropped_not_refused(self):
        """Self-report: a registration never fails on it, and garbage is never shown as a build."""
        self._register("bc-coder", "bridge-1", "577c7ca; rm -rf /")
        self.assertEqual(self._bridges()["bridge-1"]["build"], "")

    def test_a_bridge_that_sent_no_build_is_listed_with_none(self):
        """So the doctor can say which bridges are too old to report, rather than skip them."""
        self._register("bc-coder", "bridge-1", None)
        self.assertEqual(self._bridges()["bridge-1"]["build"], "")

    def test_only_live_bridges_are_listed(self):
        self._register("bc-live", "bridge-live", "aaaaaaaaaaaa")
        self._register("bc-quiet", "bridge-quiet", "bbbbbbbbbbbb")
        self._register("bc-gone", "bridge-gone", "cccccccccccc")
        self._register("bc-side", "bridge-side", "dddddddddddd")
        self._write("UPDATE bridge_instances SET last_seen = '2020-01-01T00:00:00Z' WHERE id = 'bridge-quiet'")
        self._write("UPDATE bridge_instances SET superseded_by = 'x' WHERE id = 'bridge-gone'")
        self._write("UPDATE bridge_instances SET bridge_kind = 'channel-sidecar' WHERE id = 'bridge-side'")
        self.assertEqual(set(self._bridges()), {"bridge-live"})
