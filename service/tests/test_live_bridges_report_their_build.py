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
        self._write("UPDATE bridge_instances SET last_seen = '2020-01-01T00:00:00Z' WHERE id = 'bridge-quiet'")
        self._write("UPDATE bridge_instances SET superseded_by = 'x' WHERE id = 'bridge-gone'")
        self.assertEqual(set(self._bridges()), {"bridge-live"})

    def test_a_channel_sidecar_is_listed_with_the_build_its_heartbeat_carries(self):
        """v0.7 review: sidecars (the Claude channel, the hermes delivery loop) run bridge code too, and
        were left out, so a stale one hid behind a current foreground bridge. A sidecar is registered by
        its heartbeat alone, so the beat carries the build."""
        self._register("bc-hermes", "bridge-foreground", "aaaaaaaaaaaa")
        beat = {"bridgeId": "sidecar-bc-hermes", "bridgeKind": "channel-sidecar", "liveness": True,
                "bridgeBuild": "0123456789ab"}
        response = self.client.post("/api/v1/agents/bc-hermes/heartbeat", json=beat)
        self.assertEqual(response.status_code, 200, response.text)
        row = self._bridges().get("sidecar-bc-hermes")
        self.assertIsNotNone(row, f"the sidecar is not listed: {sorted(self._bridges())}")
        self.assertEqual((row["bridgeKind"], row["build"]), ("channel-sidecar", "0123456789ab"))
        # A beat with an unusable build leaves the stored one alone rather than blanking it.
        self.client.post("/api/v1/agents/bc-hermes/heartbeat", json={**beat, "bridgeBuild": "no; way"})
        self.assertEqual(self._bridges()["sidecar-bc-hermes"]["build"], "0123456789ab")

    def test_the_host_tiers_own_spawn_row_is_not_a_bridge(self):
        """v0.7.1 review (B7). A spawn report writes a `bridge_instances` row keyed by the CLAIMER's id,
        which is aify-env's, with no build. Until the worker's own bridge registered, `/bridges` listed
        it and the doctor told the operator the new agent ran a pre-0.7 bridge and should restart."""
        self._register("bc-coder", "bridge-1", "577c7ca1b2c3")
        now = "2099-01-01T00:00:00Z"
        self._write("INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, "
                    "last_seen, superseded_by) VALUES ('env-bridge', 'bc-coder', 'win:host', 'claude-code', "
                    "'managed', ?, ?, '')", (now, now))
        self.assertIn("env-bridge", self._bridges(), "control: the row is live and listed before it is known")
        self._write("INSERT INTO environments (id, bridge_id, registered_at, last_seen) "
                    "VALUES ('win:host:default', 'env-bridge', ?, ?)", (now, now))
        self.assertEqual(set(self._bridges()), {"bridge-1"})
