"""Reading dispatch runs: the list, one run in full, and one run's paged event log.

`service/routers/dispatch_messages/run_queries.py` is named by no test file. Three GET handlers, and
its own docstring says why their honesty matters more than their size: reading a run is where a caller
forms a belief about it.

TWO BELIEFS ARE EASY TO GET WRONG HERE, and neither raises.

  * A CAPPED PAGE READ AS THE WHOLE SET. `/events` declares `limit` with a floor and no ceiling, then
    silently bounds it to 50 in Python — so a caller asking for 1000 gets 50 and is told `hasMore`.
    A client that ignores `hasMore`, or a server that forgot to compute it, shows an operator a
    truncated history as if it were complete.
  * A RUN'S OWN STATUS CONFLATED WITH SOMEBODY ELSE'S. `blockedBy` is a LIVE lookup, not a stored
    column, and it is computed only for queued runs. A caller polling "is it moving yet" needs those
    as two facts; merged into one they cannot tell "not started" from "not startable".

AUDIT ANCHORS ARE HIDDEN FROM THE LIST AND NOT FROM THE ITEM. The mode-switch audit inserts synthetic
`dispatch_runs` rows only to satisfy a foreign key — never claimed, never started. They would fill the
dashboard's history view, so the list excludes them; they stay individually queryable, which is the
distinction the tests pin.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "rq-worker"
OTHER = "rq-other"
SENDER = "rq-sender"


class RunQueriesTestCase(FastApiTestCase):
    DB_NAME = "aify-run-queries-test.db"

    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, OTHER, SENDER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _seed_run(self, run_id: str, *, target: str = AGENT, sender: str = SENDER,
                  status: str = "queued", dispatch_mode: str = "dispatch",
                  requested_at: str = "2026-08-17T09:00:00Z", subject: str = "s") -> None:
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,"
            " subject, body, status, requested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, f"msg-{run_id}", sender, target, dispatch_mode, subject, "the body",
             status, requested_at),
        )

    def _seed_run_with_null_mode(self, run_id: str) -> None:
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, subject, body,"
            " status, requested_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, f"msg-{run_id}", SENDER, AGENT, "s", "b", "queued",
             "2026-08-17T09:00:00Z"),
        )

    def _seed_control(self, control_id: str, run_id: str, *, source_message_id: str = "msg-src",
                      action: str = "steer", requested_at: str = "2026-08-17T09:00:00Z") -> None:
        self._write(
            "INSERT INTO dispatch_controls (id, run_id, from_agent, source_message_id, action,"
            " body, status, requested_at) VALUES (?,?,?,?,?,?,?,?)",
            (control_id, run_id, SENDER, source_message_id, action, "b", "pending", requested_at),
        )

    def _seed_event(self, run_id: str, event_type: str, *, body: str = "") -> None:
        self._write(
            "INSERT INTO dispatch_events (run_id, event_type, body, created_at)"
            " VALUES (?,?,?,?)",
            (run_id, event_type, body, "2026-08-17T09:00:00Z"),
        )

    def _list(self, **params):
        response = self.client.get("/api/v1/dispatch/runs", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["runs"]

    def _get(self, run_id: str):
        return self.client.get(f"/api/v1/dispatch/runs/{run_id}")

    def _events(self, run_id: str, **params):
        response = self.client.get(f"/api/v1/dispatch/runs/{run_id}/events", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class ListTests(RunQueriesTestCase):
    def test_runs_are_listed_NEWEST_FIRST(self):
        """It is a history view. Ascending would put the oldest run at the top of a page an operator
        opens to see what just happened."""
        self._seed_run("run-old", requested_at="2026-08-17T09:00:00Z")
        self._seed_run("run-new", requested_at="2026-08-17T10:00:00Z")
        self.assertEqual([run["id"] for run in self._list()], ["run-new", "run-old"])

    def test_AUDIT_anchors_are_hidden_from_the_list(self):
        """Synthetic rows the mode-switch audit inserts only to satisfy a foreign key — never
        claimed, never started. Listed, they fill the dashboard's history with entries that describe
        no work."""
        self._seed_run("run-real")
        self._seed_run("run-audit", dispatch_mode="audit")
        self.assertEqual([run["id"] for run in self._list()], ["run-real"])

    def test_a_run_written_without_a_dispatch_mode_is_still_listed(self):
        """It gets the column DEFAULT, `start_if_possible`, and is listed like any other run.

        The clause is `dispatch_mode IS NULL OR dispatch_mode != 'audit'`, and the null arm is
        UNREACHABLE: the column is `NOT NULL` with a default, so no row can carry NULL and a mutation
        removing that arm survives. I first wrote this test believing it covered the arm — it cannot.
        The arm is right to keep (a comparison against NULL is never true, so a future migration that
        relaxed the column would silently empty this endpoint), but nothing here proves it works."""
        self._seed_run_with_null_mode("run-defaulted")
        self.assertEqual([run["id"] for run in self._list()], ["run-defaulted"])

    def test_the_list_filters_by_TARGET_agent(self):
        self._seed_run("run-mine")
        self._seed_run("run-theirs", target=OTHER)
        self.assertEqual([run["id"] for run in self._list(agentId=AGENT)], ["run-mine"])

    def test_the_list_filters_by_SENDER(self):
        """A different question from the target filter: "what did I ask for" rather than "what was
        asked of me"."""
        self._seed_run("run-mine")
        self._seed_run("run-theirs", sender=OTHER)
        self.assertEqual([run["id"] for run in self._list(fromAgent=SENDER)], ["run-mine"])

    def test_the_list_filters_by_STATUS(self):
        self._seed_run("run-queued", status="queued")
        self._seed_run("run-done", status="completed")
        self.assertEqual([run["id"] for run in self._list(status="completed")], ["run-done"])

    def test_the_filters_COMBINE(self):
        """Each filter appends to the same WHERE. One that replaced the clause instead would widen
        the answer silently — the caller asked for an intersection and got a union."""
        self._seed_run("run-hit", target=AGENT, sender=SENDER, status="completed")
        self._seed_run("run-wrong-status", target=AGENT, sender=SENDER, status="queued")
        self._seed_run("run-wrong-target", target=OTHER, sender=SENDER, status="completed")
        runs = self._list(agentId=AGENT, fromAgent=SENDER, status="completed")
        self.assertEqual([run["id"] for run in runs], ["run-hit"])

    def test_the_LIMIT_is_honoured(self):
        for index in range(5):
            self._seed_run(f"run-{index}", requested_at=f"2026-08-17T09:0{index}:00Z")
        self.assertEqual(len(self._list(limit=2)), 2)

    def test_a_limit_ABOVE_THE_CEILING_is_refused_rather_than_silently_capped(self):
        """This one is declared `le=200`, so an over-large ask is a 422 the caller can see. That is
        the opposite choice from `/events` below, which caps quietly — the two are pinned together so
        the inconsistency is visible rather than discovered."""
        self.assertEqual(
            self.client.get("/api/v1/dispatch/runs", params={"limit": 500}).status_code, 422)

    def test_a_limit_of_ZERO_is_refused(self):
        self.assertEqual(
            self.client.get("/api/v1/dispatch/runs", params={"limit": 0}).status_code, 422)


class ListControlsTests(RunQueriesTestCase):
    def test_a_runs_SOURCE_CONTROLS_are_attached(self):
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1", action="steer")
        runs = self._list()
        self.assertEqual([c["action"] for c in runs[0]["sourceControls"]], ["steer"])

    def test_a_run_with_no_source_controls_OMITS_the_key(self):
        """Absent, not an empty list. The dashboard renders the section on presence, and an empty
        array would draw an empty panel on every run in the history."""
        self._seed_run("run-1")
        self.assertNotIn("sourceControls", self._list()[0])

    def test_a_control_with_NO_SOURCE_MESSAGE_is_not_attached(self):
        """These exist to link a control back to the message that asked for it. One with no source
        has nothing to link, and listing it would show an operator a control with no origin."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1", source_message_id="")
        self.assertNotIn("sourceControls", self._list()[0])

    def test_controls_are_attached_to_the_RIGHT_run(self):
        """The batched lookup fetches every page's controls in one query and groups them in Python.
        A grouping slip would attach one run's controls to another — the reason it is worth testing
        with two runs rather than one."""
        self._seed_run("run-a", requested_at="2026-08-17T09:00:00Z")
        self._seed_run("run-b", requested_at="2026-08-17T10:00:00Z")
        self._seed_control("ctl-a", "run-a")
        self._seed_control("ctl-b", "run-b")
        by_run = {run["id"]: run.get("sourceControls", []) for run in self._list()}
        self.assertEqual([c["id"] for c in by_run["run-a"]], ["ctl-a"])
        self.assertEqual([c["id"] for c in by_run["run-b"]], ["ctl-b"])

    def test_controls_arrive_OLDEST_FIRST(self):
        self._seed_run("run-1")
        self._seed_control("ctl-late", "run-1", requested_at="2026-08-17T10:00:00Z")
        self._seed_control("ctl-early", "run-1", requested_at="2026-08-17T09:00:00Z")
        self.assertEqual([c["id"] for c in self._list()[0]["sourceControls"]],
                         ["ctl-early", "ctl-late"])

    def test_at_most_FIFTY_controls_are_attached_per_run(self):
        self._seed_run("run-1")
        for index in range(55):
            self._seed_control(f"ctl-{index:03d}", "run-1")
        self.assertEqual(len(self._list()[0]["sourceControls"]), 50)


class BlockedByTests(RunQueriesTestCase):
    def test_a_QUEUED_run_reports_what_is_blocking_it(self):
        """The distinction the docstring names: the run's own status says "queued", and this says
        why. Without it a caller polling for movement cannot tell "not started" from
        "not startable"."""
        self._seed_run("run-active", status="running")
        self._seed_run("run-queued", status="queued")
        by_run = {run["id"]: run for run in self._list()}
        self.assertEqual(by_run["run-queued"]["blockedByActiveRun"]["runId"], "run-active")

    def test_a_run_is_never_reported_as_blocking_ITSELF(self):
        """A running run must not name itself as its own blocker — that reads as a deadlock that does
        not exist.

        On THIS path the `exclude_run_id` argument is not what achieves it: blockedBy is computed
        only for QUEUED runs, and a queued run is never the active one, so dropping the argument here
        changes nothing and that mutation survives. The exclusion earns its keep in
        `_finalize_dispatch_runs`, which asks about a run that may itself be active. What this test
        pins is the OUTCOME, which is the part a caller sees."""
        self._seed_run("run-running", status="running")
        blocked = self._list()[0].get("blockedByActiveRun")
        self.assertIsNone(blocked)

    def test_blockedBy_is_only_computed_for_QUEUED_runs(self):
        """A live query per row, so it is asked only where the answer means something. Asking for
        every row on a 200-run page would add 200 queries to a poll.

        The key is always PRESENT and null when not computed — `blockedByActiveRun`, not `blockedBy`,
        which is what my first draft assumed and what made eleven of these fail against correct
        code. A caller distinguishes "not blocked" from "not asked" by nothing here, which is worth
        knowing: only the run's own status tells it which."""
        self._seed_run("run-active", status="running")
        self._seed_run("run-done", status="completed")
        by_run = {run["id"]: run for run in self._list()}
        self.assertIn("blockedByActiveRun", by_run["run-done"])
        self.assertIsNone(by_run["run-done"]["blockedByActiveRun"])

    def test_a_queued_run_with_nothing_running_is_not_blocked(self):
        self._seed_run("run-queued", status="queued")
        self.assertIsNone(self._list()[0].get("blockedByActiveRun"))


class GetOneRunTests(RunQueriesTestCase):
    def test_an_UNKNOWN_run_is_404(self):
        response = self._get("run-nope")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("run-nope", response.json()["detail"])

    def test_an_AUDIT_anchor_is_still_individually_queryable(self):
        """Hidden from the list, not from the item. The audit rows are the anchor a mode-switch
        event points at, so a caller following that reference must be able to resolve it."""
        self._seed_run("run-audit", dispatch_mode="audit")
        self.assertEqual(self._get("run-audit").status_code, 200)

    def test_the_single_run_includes_its_BODY(self):
        """The list deliberately omits it — bodies are large and a history page carries many. The
        item view is where the caller asked for one thing, so it gets all of it."""
        self._seed_run("run-1")
        run = self._get("run-1").json()["run"]
        self.assertEqual(run["body"], "the body")
        self.assertNotIn("body", self._list()[0])

    def test_the_single_run_includes_its_EVENT_LOG(self):
        self._seed_run("run-1")
        self._seed_event("run-1", "queued", body="info: s")
        self._seed_event("run-1", "claimed")
        run = self._get("run-1").json()["run"]
        self.assertEqual([event["type"] for event in run["events"]], ["queued", "claimed"])

    def test_the_single_run_includes_EVERY_control_not_just_sourced_ones(self):
        """Wider than the list's attachment on purpose: here the caller is inspecting one run, and a
        control with no source message is still something that happened to it."""
        self._seed_run("run-1")
        self._seed_control("ctl-sourced", "run-1")
        self._seed_control("ctl-bare", "run-1", source_message_id="")
        run = self._get("run-1").json()["run"]
        self.assertEqual(sorted(c["id"] for c in run["controls"]), ["ctl-bare", "ctl-sourced"])

    def test_a_queued_single_run_reports_its_blocker(self):
        self._seed_run("run-active", status="running")
        self._seed_run("run-queued", status="queued")
        run = self._get("run-queued").json()["run"]
        self.assertEqual(run["blockedByActiveRun"]["runId"], "run-active")


class EventPageTests(RunQueriesTestCase):
    def setUp(self):
        super().setUp()
        self._seed_run("run-1")
        for index in range(8):
            self._seed_event("run-1", f"event-{index}")

    def test_an_UNKNOWN_run_is_404(self):
        response = self.client.get("/api/v1/dispatch/runs/run-nope/events")
        self.assertEqual(response.status_code, 404, response.text)

    def test_events_default_to_NEWEST_FIRST(self):
        page = self._events("run-1", limit=3)
        self.assertEqual([event["type"] for event in page["events"]],
                         ["event-7", "event-6", "event-5"])
        self.assertEqual(page["order"], "desc")

    def test_ASCENDING_order_is_available(self):
        page = self._events("run-1", limit=3, order="asc")
        self.assertEqual([event["type"] for event in page["events"]],
                         ["event-0", "event-1", "event-2"])

    def test_an_UNKNOWN_order_is_refused(self):
        """A pattern on the query parameter. Falling back to a default for a typo would silently
        page the wrong way through a log the caller is trying to read in order."""
        self.assertEqual(
            self.client.get("/api/v1/dispatch/runs/run-1/events",
                            params={"order": "sideways"}).status_code, 422)

    def test_HAS_MORE_is_reported_when_the_page_is_not_the_whole_log(self):
        """The one field that stops a truncated history being read as complete. It is computed by
        fetching one row beyond the page rather than by counting, so it cannot disagree with what
        was returned."""
        page = self._events("run-1", limit=3)
        self.assertIs(page["hasMore"], True)
        self.assertEqual(len(page["events"]), 3)

    def test_HAS_MORE_is_false_on_the_last_page(self):
        page = self._events("run-1", limit=50)
        self.assertIs(page["hasMore"], False)
        self.assertEqual(len(page["events"]), 8)

    def test_the_limit_is_CAPPED_AT_FIFTY_without_an_error(self):
        """`limit` is declared with a floor and no ceiling, then bounded in Python. A caller asking
        for a thousand is told 50 in the response's own `limit` field — which is the only way it can
        find out."""
        page = self._events("run-1", limit=1000)
        self.assertEqual(page["limit"], 50)

    def test_a_limit_of_ZERO_is_refused(self):
        self.assertEqual(
            self.client.get("/api/v1/dispatch/runs/run-1/events",
                            params={"limit": 0}).status_code, 422)

    def test_NEXT_BEFORE_pages_through_the_log_without_repeating_or_skipping(self):
        """The cursor contract, walked end to end. An off-by-one in either direction is invisible in
        a single page: too inclusive repeats an event, too exclusive drops one."""
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            params = {"limit": 3}
            if cursor:
                params["before"] = cursor
            page = self._events("run-1", **params)
            seen.extend(event["type"] for event in page["events"])
            if not page["hasMore"]:
                break
            cursor = page["nextBefore"]
            self.assertTrue(cursor, "hasMore was true with no cursor to follow")
        self.assertEqual(seen, [f"event-{index}" for index in range(7, -1, -1)])
        self.assertEqual(len(seen), len(set(seen)), "an event was returned twice")

    def test_the_cursor_walks_the_other_way_in_ASCENDING_order(self):
        """`id > ?` rather than `id < ?`. The same cursor field with the opposite comparison — get
        it wrong and an ascending page returns the events before the cursor forever."""
        first = self._events("run-1", limit=3, order="asc")
        second = self._events("run-1", limit=3, order="asc", before=first["nextBefore"])
        self.assertEqual([event["type"] for event in second["events"]],
                         ["event-3", "event-4", "event-5"])

    def test_NEXT_BEFORE_is_EMPTY_on_the_last_page(self):
        """A cursor on a final page invites one more request that returns nothing, and a client that
        loops until the cursor is empty would never stop."""
        page = self._events("run-1", limit=50)
        self.assertEqual(page["nextBefore"], "")

    def test_a_before_of_ZERO_is_refused(self):
        self.assertEqual(
            self.client.get("/api/v1/dispatch/runs/run-1/events",
                            params={"before": 0}).status_code, 422)

    def test_the_event_carries_BOTH_type_names(self):
        """`type` and `eventType` are the same value under two keys — a compatibility duplication.
        Pinned because dropping either breaks a consumer, and nothing else records that both are
        deliberate."""
        event = self._events("run-1", limit=1)["events"][0]
        self.assertEqual(event["type"], event["eventType"])

    def test_event_ids_are_STRINGS(self):
        """They are integers in the database and cursors on the wire. A client that received a
        number and sent it back as one still works; one that concatenates it does not, so the
        boundary picks a single shape."""
        page = self._events("run-1", limit=1)
        self.assertIsInstance(page["events"][0]["id"], str)
        self.assertIsInstance(page["nextBefore"], str)

    def test_a_NULL_event_body_reads_as_an_empty_string(self):
        """Events are written with no body all the time. `None` would render as "null" in a log an
        operator reads."""
        self._write("INSERT INTO dispatch_events (run_id, event_type, body, created_at)"
                    " VALUES (?,?,?,?)", ("run-1", "bodyless", None, "2026-08-17T11:00:00Z"))
        event = self._events("run-1", limit=1)["events"][0]
        self.assertEqual(event["type"], "bodyless")
        self.assertEqual(event["body"], "")


if __name__ == "__main__":
    unittest.main()
