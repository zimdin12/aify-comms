"""A host that stops pushing its definitions says so on its environment (P0 C11, arm 3).

Its rows keep governing as last pushed, so the operator must be able to see that they are no longer
being refreshed. Before this, nothing read a push's age, and the one timestamp kept (`updated_at`) moved
only when the snapshot changed: aify-env re-sends an unchanged snapshot every minute, the service answers
it as a replay, and an idle host and a gone one looked the same.
"""
from __future__ import annotations

import unittest

from service.api_core.definition_freshness import STALE_AFTER_SECONDS, definition_freshness
from service.api_core.definition_snapshot import snapshot_digest
from service.tests._base import FastApiTestCase
from service.tests.frozen_clock import frozen_service_clock
from service.tests.test_agent_definition_push import A, B, valid

T0 = "2026-10-02T10:00:00Z"
LATER = "2026-10-02T10:10:01Z"  # STALE_AFTER_SECONDS + 1 after T0


class AHostThatStopsPushingSaysSo(FastApiTestCase):
    DB_NAME = "aify-test-definition-freshness.db"

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
            self.assertEqual(beat.status_code, 200, beat.text)

    def push(self, revision, entries):
        body = {"bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "store-a", "revision": revision,
                "snapshotDigest": snapshot_digest(entries), "entries": entries}
        answer = self.client.put(f"/api/v1/environments/{A['env']}/agent-definitions", json=body)
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def definitions(self, env_id):
        envs = {e["id"]: e for e in self.client.get("/api/v1/environments").json()["environments"]}
        return envs[env_id]["definitions"]

    def test_an_idle_host_stays_fresh_and_a_silent_one_goes_stale(self):
        entries = [valid("worker")]
        with frozen_service_clock(T0):
            self.push(1, entries)
            fresh = self.definitions(A["env"])
        self.assertEqual((fresh["machineId"], fresh["notRefreshedSince"], fresh["notice"]), (A["machine"], None, ""))

        with frozen_service_clock(LATER):
            stale = self.definitions(A["env"])
            self.assertEqual(stale["notRefreshedSince"], T0)
            self.assertEqual(stale["notice"], f"definitions from {A['machine']} not refreshed since {T0}")
            # THE SAME SNAPSHOT AGAIN: a replay, which applies nothing and still proves the host is there.
            self.assertEqual(self.push(1, entries)["outcome"], "replay")
            self.assertEqual(self.definitions(A["env"])["notRefreshedSince"], None, "a replay is a push")
            self.assertEqual(self.definitions(A["env"])["pushedAt"], LATER)

    def test_THE_SWEEP_tells_a_connected_dashboard_when_a_host_goes_stale(self):
        """Nothing is written when a host stops pushing, so a dashboard holding its socket heard nothing and
        kept a fresh card for an hour (comms-senior-dev's review of cbcc26d0). The 60 s sweep reports the
        moment the stale set moves, as a change to `definition_stores`, which the environments slice reads."""
        import asyncio
        from unittest import mock

        import service.main as service_main
        from service.reconcilers import definition_staleness

        class Feed:
            def __init__(self):
                self.moved = []

            def derived_moved(self, table):
                self.moved.append(table)

        feed = Feed()
        with mock.patch.object(definition_staleness, "TRACKER", definition_staleness.DefinitionStaleness()), \
                mock.patch.object(definition_staleness, "CHANGE_FEED", feed):
            with frozen_service_clock(T0):
                self.push(1, [valid("worker")])
                first = asyncio.run(service_main._run_dispatch_reconcile_once())
            with frozen_service_clock(LATER):
                crossed = asyncio.run(service_main._run_dispatch_reconcile_once())
                again = asyncio.run(service_main._run_dispatch_reconcile_once())
        self.assertEqual([first["definition_staleness_moved"], crossed["definition_staleness_moved"],
                          again["definition_staleness_moved"]], [0, 1, 0], "reported once, when it moved")
        self.assertEqual(feed.moved, ["definition_stores"])

    def test_an_environment_no_store_pushes_from_carries_none(self):
        with frozen_service_clock(T0):
            self.push(1, [valid("worker")])
        self.assertIsNone(self.definitions(B["env"]))


class TheTracker(unittest.TestCase):
    def test_it_reports_a_move_and_nothing_else(self):
        from service.reconcilers.definition_staleness import DefinitionStaleness
        tracker = DefinitionStaleness()
        self.assertFalse(tracker.moved(frozenset({"m"})), "a first pass has nothing to differ from")
        self.assertFalse(tracker.moved(frozenset({"m"})), "the same set is not a move")
        self.assertTrue(tracker.moved(frozenset()), "fresh again is a move")
        self.assertTrue(tracker.moved(frozenset({"n"})), "another machine is a move")


class TheFeed(unittest.TestCase):
    def test_a_replay_stamp_is_liveness_and_a_snapshot_change_is_not(self):
        from service.change_feed import written_table
        replay = written_table("UPDATE definition_stores SET pushed_at = ? WHERE machine_id = ? AND store_id = ?")
        self.assertTrue(replay.liveness, "a minute's replay must coalesce like a heartbeat")
        change = written_table("UPDATE definition_stores SET outcome = ? WHERE machine_id = ?")
        self.assertFalse(change.liveness)


class TheRule(unittest.TestCase):
    ROW = {"machine_id": "m", "store_id": "s", "revision": 3, "updated_at": T0, "pushed_at": T0}

    def row(self, **over):
        values = {**self.ROW, **over}

        class Row(dict):
            def keys(self):
                return values.keys()
        return Row(values)

    def test_the_boundary_is_ten_minutes(self):
        self.assertEqual(STALE_AFTER_SECONDS, 600)
        self.assertIsNone(definition_freshness(self.row(), "2026-10-02T10:10:00Z")["notRefreshedSince"])
        self.assertEqual(definition_freshness(self.row(), LATER)["notRefreshedSince"], T0)

    def test_a_row_from_before_the_column_falls_back_to_its_last_change(self):
        self.assertEqual(definition_freshness(self.row(pushed_at=None), LATER)["notRefreshedSince"], T0)
        self.assertIsNone(definition_freshness(self.row(pushed_at=None), T0)["notRefreshedSince"])

    def test_an_unreadable_time_is_stale_not_fresh(self):
        unknown = definition_freshness(self.row(pushed_at="", updated_at="not a time"), T0)
        self.assertEqual(unknown["notRefreshedSince"], "not a time")
        nothing = definition_freshness(self.row(pushed_at="", updated_at=""), T0)
        self.assertEqual((nothing["notRefreshedSince"], nothing["notice"]), ("unknown", "definitions from m not refreshed since unknown"))
