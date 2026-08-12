"""A safety net for splitting `get_analytics`, pinned to what it returns TODAY.

WHY THIS EXISTS. `get_analytics` is 314 lines and the reviewer named it the first method-split
target in this refactor series. A split is only provably behaviour-preserving if something already
describes the behaviour, and what existed was three assertions in `test_api_v2_regressions` and one
in `test_chat_analytics` — enough to catch a total failure, not enough to catch a field quietly
changing shape, losing a key, or flipping from `None` to `0`.

THIS IS A CHARACTERIZATION TEST, NOT A SPECIFICATION. It asserts what the endpoint does now,
including anything that might be a wart. If a split changes one of these, that is the split being
caught, not the test being wrong — and if we ever DECIDE to change one, the assertion is the record
of what we chose to change.

THE PART THAT MATTERS MOST is `test_response_key_set_is_exactly_this`. A method split's easiest
failure is a key that stops being assembled because it was computed in the extracted half and never
threaded back. Nothing else here would catch a missing key: FastAPI serialises whatever dict it is
handed, the dashboard tolerates absent fields, and every other assertion reads keys it expects to
find. So the key SET is pinned exactly — extra keys fail too, because a split that invents a key has
also changed the contract.

THE EMPTY-DATABASE CASES exist because the degenerate paths are where a split most plausibly breaks:
`successRate` and `fleetMedianReplyMinutes` are computed as `x if denominator else None`, and a
naive extraction that returns `0` or `0.0` instead of `None` would sail past a seeded test while
changing what the dashboard renders (a real 0% success rate is not the same as "no runs yet").
"""

from __future__ import annotations

import sqlite3
import time

from service.tests._base import FastApiTestCase

#: Every key GET /api/v1/analytics returns. Pinned exactly — see the module docstring.
EXPECTED_KEYS = {
    "ok",
    "messagesPerHour",
    "messagesPerDay",
    "messagesPerMonth",
    "messagesPerAllTime",
    "range",
    "rangeLabel",
    "messageTotal",
    "runsByStatus",
    "runTotal",
    "spawnRequestsByStatus",
    "spawnRequestTotal",
    "liveAgents",
    "onlineAgents",
    "workingAgents",
    "onlineEnvironments",
    "successRate",
    "runsCompleted",
    "runsFailed",
    "openReplyContracts",
    "overdueReplyContracts",
    "fleetMedianReplyMinutes",
    "dispatchOutcomes",
    "agentLeaderboard",
    "busiestChannels",
    "failureReasons",
}

RANGES = ("hour", "day", "month", "all")


class AnalyticsCharacterizationTests(FastApiTestCase):
    def _register(self, agent_id: str) -> None:
        resp = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
        self.assertEqual(resp.status_code, 200, resp.text)

    def _get(self, range_value: str = "all") -> dict:
        resp = self.client.get(f"/api/v1/analytics?range={range_value}")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # ---------------------------------------------------------------- shape

    def test_response_key_set_is_exactly_this(self):
        """The single most important assertion here. See the module docstring."""
        for range_value in RANGES:
            with self.subTest(range=range_value):
                self.assertEqual(set(self._get(range_value)), EXPECTED_KEYS)

    def test_every_range_is_accepted_and_echoed_back(self):
        for range_value in RANGES:
            with self.subTest(range=range_value):
                data = self._get(range_value)
                self.assertTrue(data["ok"])
                self.assertEqual(data["range"], range_value)
                self.assertTrue(str(data["rangeLabel"]).strip(), "rangeLabel must never be blank")

    def test_an_unknown_range_is_rejected_by_the_route_not_the_body(self):
        """The pattern lives on the Query, so a bad range is a 422 before any SQL runs.

        Worth pinning: a split that moved validation into the body would turn this into a 200 with
        a silently-wrong window.
        """
        self.assertEqual(self.client.get("/api/v1/analytics?range=fortnight").status_code, 422)

    def test_bucket_series_have_fixed_lengths_and_stable_item_shape(self):
        data = self._get("all")
        for key, expected_len in (("messagesPerHour", 24), ("messagesPerDay", 30)):
            with self.subTest(series=key):
                series = data[key]
                self.assertEqual(len(series), expected_len)
                for point in series:
                    self.assertEqual(set(point), {"label", "start", "count"})
                    self.assertIsInstance(point["count"], int)
        for key in ("messagesPerMonth", "messagesPerAllTime"):
            with self.subTest(series=key):
                for point in data[key]:
                    self.assertEqual(set(point), {"label", "start", "count"})

    # ------------------------------------------------------- degenerate DB

    def test_empty_database_returns_None_not_zero_for_ratio_fields(self):
        """`None` means "no data"; `0` means "measured, and it is zero". Not interchangeable.

        Both are computed as `value if denominator else None`, which is exactly the shape an
        extraction gets wrong by initialising an accumulator to 0.
        """
        data = self._get("all")
        self.assertIsNone(data["successRate"], "no finished runs must be None, not 0")
        self.assertIsNone(data["fleetMedianReplyMinutes"], "no replies must be None, not 0")

    def test_empty_database_returns_zero_not_None_for_count_fields(self):
        """The mirror of the case above, so a split cannot 'fix' one by breaking the other."""
        data = self._get("all")
        for key in ("messageTotal", "runTotal", "spawnRequestTotal", "runsCompleted", "runsFailed",
                    "openReplyContracts", "overdueReplyContracts", "liveAgents", "onlineAgents",
                    "workingAgents", "onlineEnvironments"):
            with self.subTest(field=key):
                self.assertEqual(data[key], 0, f"{key} must be 0 on an empty DB, not None")

    def test_empty_database_returns_empty_collections_not_None(self):
        data = self._get("all")
        for key in ("runsByStatus", "spawnRequestsByStatus"):
            with self.subTest(field=key):
                self.assertIsInstance(data[key], dict)
        # dispatchOutcomes is NOT empty on an empty DB and is NOT a dict: it is a FIXED 14-day
        # series, zero-filled. I assumed a dict writing this test and the assertion caught me,
        # which is the whole argument for pinning the shape before splitting anything.
        outcomes = data["dispatchOutcomes"]
        self.assertIsInstance(outcomes, list)
        self.assertEqual(len(outcomes), 14, "always 14 days, zero-filled — a dense series, not sparse")
        for point in outcomes:
            self.assertEqual(set(point), {"date", "completed", "failed"})
            self.assertEqual((point["completed"], point["failed"]), (0, 0))
        for key in ("agentLeaderboard", "busiestChannels", "failureReasons"):
            with self.subTest(field=key):
                self.assertIsInstance(data[key], list)
                self.assertEqual(data[key], [])

    # ------------------------------------------------------------- seeded

    def _seed(self):
        """A deterministic fixture with hand-computable aggregates.

        Three finished runs: two completed, one failed => successRate 66.7 (2/3, rounded to 1dp).
        That value is chosen to be a REPEATING decimal, so a split that changes rounding or does the
        division in a different order shows up instead of landing on the same clean number.
        """
        now_ms = int(time.time() * 1000)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen) "
                "VALUES (?,?,?,?,?)",
                ("env_ac", "test-host", "bridge_ac", now_iso, now_iso),
            )
            # three DIRECT messages, which is what the endpoint's message_where admits
            for i in range(3):
                conn.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
                    "priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"ac-msg-{i}", "ac-alpha", "ac-beta", "direct", "request",
                     f"s{i}", "b", "normal", now_ms - i * 1000),
                )
            for i, status in enumerate(("completed", "completed", "failed")):
                conn.execute(
                    "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, "
                    "requested_at, finished_at) VALUES (?,?,?,?,?,?,?)",
                    (f"ac-run-{i}", f"ac-msg-{i}", "ac-alpha", "ac-beta", status, now_iso, now_iso),
                )
            conn.commit()
        finally:
            conn.close()

    def test_seeded_totals_and_success_rate(self):
        self._register("ac-alpha")
        self._register("ac-beta")
        self._seed()
        data = self._get("all")
        self.assertEqual(data["messageTotal"], 3)
        self.assertEqual(data["runTotal"], 3)
        self.assertEqual(data["runsCompleted"], 2)
        self.assertEqual(data["runsFailed"], 1)
        self.assertEqual(data["runsByStatus"].get("completed"), 2)
        self.assertEqual(data["runsByStatus"].get("failed"), 1)
        self.assertEqual(
            data["successRate"], 66.7,
            "2 of 3 finished runs, rounded to one decimal place — pinned because a split that "
            "reorders the division or the rounding lands somewhere else",
        )

    def test_seeded_key_set_is_unchanged_by_having_data(self):
        """Data must not ADD keys either. A conditional key is a contract that varies by content."""
        self._register("ac-alpha")
        self._register("ac-beta")
        self._seed()
        self.assertEqual(set(self._get("all")), EXPECTED_KEYS)
