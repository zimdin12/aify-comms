"""`GET /contracts?state=X` returns the same rows whatever page size it is asked for.

THE DEFECT. A contract's state is decided by `_contract_state`, in Python, from settings and several
columns -- "what is owed an answer is wider than the flag", as reply_contract.py puts it. The SQL
`WHERE` cannot express that, so it is a PRE-FILTER, deliberately wider than the state it stands in
for. `LIMIT` was applied to that wider set and the derived-state filter ran afterwards, so rows the
caller asked for were discarded before they were ever counted.

MEASURED on the operator's live service, 2026-08-28, one query at three page sizes:

    state=missing_reply    limit=80  ->  0 rows,  summary.total 0
                           limit=120 -> 20 rows,  summary.total 20
                           limit=200 -> 62 rows,  summary.total 62

62 is the true count. `missing_reply` and `closed` share a SQL predicate -- both are
`result_message_id = '' AND status = 'completed'`, differing only by a flag the derivation does not
key on -- so the newest 80 rows matching it were all `closed`, and the answer to "which contracts are
missing a reply" was ZERO while 62 existed.

THE SUMMARY WAS WRONG BY THE SAME MECHANISM, which is the worse half. A caller wanting only a COUNT
got a number that depended entirely on the page size they happened to pass, and nothing in the
response said so.

WHY IT WAS NOT VISIBLE. The dashboard asks for `limit=200` on a state filter, which happens to cover
62 today, so the screen was right by luck. It stops being right when the table grows -- silently,
with the count simply drifting down.

THE FIX is not to make the SQL match the derivation. That is the trap: the derivation reads settings
and is meant to be wider than any single column, and a second copy of it in SQL would be one more
thing to drift. The scan reads a bounded superset, the filter runs, and the page is taken last.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

#: More rows than any page size asked for below, so a page-size-dependent answer is unmistakable.
CLOSED_RUNS = 24
MISSING_REPLY_RUNS = 6


class AContractFilterDoesNotLoseRowsToTheLimitTests(FastApiTestCase):
    """A fixture shaped like the live one: many `closed` rows NEWER than the `missing_reply` rows.

    The order is the whole point. `closed` and `missing_reply` share a SQL predicate, and the query
    is `ORDER BY requested_at DESC`, so a small page fills with the newer `closed` rows and the
    filter then throws all of them away.
    """

    def setUp(self) -> None:
        super().setUp()
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "contract-target", "role": "coder", "runtime": "claude-code",
            "sessionMode": "resident",
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed()

    def _seed(self) -> None:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                # OLDER: completed, no result, reply REQUIRED -> derives `missing_reply`.
                for n in range(MISSING_REPLY_RUNS):
                    await self._insert(db, f"mr-{n:03d}", f"2026-08-01T00:{n:02d}:00Z", require_reply=1)
                # NEWER: completed, no result, reply NOT required -> derives `closed`.
                for n in range(CLOSED_RUNS):
                    await self._insert(db, f"cl-{n:03d}", f"2026-08-20T00:{n:02d}:00Z", require_reply=0)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    async def _insert(self, db, run_id: str, requested_at: str, *, require_reply: int) -> None:
        # The message first: `dispatch_runs.message_id` is a FOREIGN KEY, and the list query LEFT
        # JOINs `messages` for the body and source it renders.
        await db.execute(
            "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
            "priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"msg-{run_id}", "operator", "contract-target", "direct", "request",
             f"subject {run_id}", "body", "normal", requested_at),
        )
        await db.execute(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, message_type, "
            "subject, body, priority, status, require_reply, requested_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, f"msg-{run_id}", "operator", "contract-target", "request",
             f"subject {run_id}", "body", "normal", "completed", require_reply,
             requested_at, requested_at),
        )

    def _get(self, state: str, limit: int):
        response = self.client.get(f"/api/v1/contracts?state={state}&limit={limit}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_fixture_produces_both_states(self) -> None:
        """The control. If the seeded rows do not actually derive the two states, every assertion
        below compares zero to zero and proves nothing -- the wrong zero this repo keeps finding."""
        body = self._get("missing_reply", 500)
        states = {row["state"] for row in body["contracts"]}
        self.assertEqual(states, {"missing_reply"}, f"unexpected states: {states}")
        self.assertEqual(len(body["contracts"]), MISSING_REPLY_RUNS)
        closed = self._get("closed", 500)
        self.assertEqual(len(closed["contracts"]), CLOSED_RUNS)

    def test_the_row_count_does_not_depend_on_the_page_size(self) -> None:
        """The defect itself. A page size smaller than the number of newer rows sharing the SQL
        predicate returned nothing at all."""
        for limit in (5, 10, 20, 120, 500):
            with self.subTest(limit=limit):
                body = self._get("missing_reply", limit)
                expected = min(limit, MISSING_REPLY_RUNS)
                self.assertEqual(
                    len(body["contracts"]), expected,
                    f"asking for {limit} returned {len(body['contracts'])} of {MISSING_REPLY_RUNS} "
                    "matching contracts; the limit is being applied before the state filter",
                )

    def test_the_summary_does_not_depend_on_the_page_size(self) -> None:
        """The worse half: a caller wanting a COUNT should not have to fetch every row to get one."""
        counts = {limit: self._get("missing_reply", limit)["summary"]["total"] for limit in (5, 20, 500)}
        self.assertEqual(
            set(counts.values()), {MISSING_REPLY_RUNS},
            f"summary.total moves with the page size: {counts}",
        )

    def test_a_complete_answer_is_not_marked_truncated(self) -> None:
        """`truncated` has to mean something. If it were always true it would be ignored, and if it
        were always false it would be a lie on the one query that needs it."""
        self.assertFalse(self._get("missing_reply", 500)["truncated"])
        self.assertFalse(self._get("closed", 500)["truncated"])

    def test_an_unfiltered_query_still_honours_its_limit(self) -> None:
        """The scan ceiling applies to state-filtered queries only. Without a filter every row that
        matches is returned, so over-fetching would be work with no purpose."""
        body = self.client.get("/api/v1/contracts?includeClosed=true&limit=3").json()
        self.assertLessEqual(len(body["contracts"]), 3)


if __name__ == "__main__":
    unittest.main()  # noqa: F821
