"""`comms_search` must find what an agent SENT, and must never stay silent about what it skipped.

REPORTED BY A TEAM USING IT (sc-manager, 2026-08-10), reproduced against the live DB before any
change:

    messages containing "P0-Q"                        101
    search?query=P0-Q                                   0   <- messages never searched
    search?query=P0-Q&agentId=sc-manager                5
    ... of the 101: TO sc-manager 49 (findable), FROM sc-manager 52 (INVISIBLE)

Two separate defects, and the reporter's own hypothesis (bodies not indexed / tokenisation) was
wrong on both counts:

1. `to_agent = ?` only. An agent could not find messages it had SENT — so searching for a term you
   dispatched yourself returned nothing. Half the record was unreachable to the person who wrote it.

2. Omitting `agentId` silently searched shared artifacts ONLY, and the response gave no sign of it.
   That is the dangerous one: the reporter's dispatch-admission gate uses this to check "was this
   already ruled?", so an empty result read as "no" and licensed duplicate work. It FAILED OPEN.

The second fix is not "search more" — the access model is unchanged — it is that the response now
states what it consulted. A search that cannot say what it searched cannot support an absence
claim, and this one was being used for exactly that.
"""

from __future__ import annotations

import unittest

from service.tests._base import FastApiTestCase


class SearchScopeTests(FastApiTestCase):
    DB_NAME = "aify-search-scope.db"

    OLD = "2020-01-01T00:00:00Z"

    def setUp(self):
        super().setUp()
        for agent in ("alice", "bob"):
            self.client.post("/api/v1/agents/register", json={
                "agentId": agent, "name": agent, "role": "coder", "runtime": "claude-code",
            })

    def _send(self, frm, to, subject, body):
        r = self.client.post("/api/v1/messages/send", json={
            "from_agent": frm, "to": to, "type": "info", "subject": subject, "body": body,
        })
        self.assertIn(r.status_code, (200, 201), r.text)

    def _search(self, **params):
        r = self.client.get("/api/v1/messages/search", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ── defect 1: your own sent messages were invisible ──────────────────────────────
    def test_finds_a_message_the_agent_SENT(self):
        self._send("alice", "bob", "P0-Q decomposition", "the P0-Q slice is ruled")
        body = self._search(query="P0-Q", agentId="alice")
        self.assertEqual(body["total"], 1, "an agent must find what it dispatched itself")
        self.assertEqual(body["results"][0]["type"], "message")

    def test_finds_a_message_the_agent_RECEIVED(self):
        self._send("bob", "alice", "P0-Q ruling", "P0-Q accepted")
        body = self._search(query="P0-Q", agentId="alice")
        self.assertEqual(body["total"], 1)

    def test_finds_both_directions_in_one_search(self):
        self._send("alice", "bob", "P0-Q out", "P0-Q dispatched")
        self._send("bob", "alice", "P0-Q back", "P0-Q returned")
        body = self._search(query="P0-Q", agentId="alice")
        self.assertEqual(body["total"], 2, "the record is what you said AND what you were told")

    def test_does_not_leak_a_conversation_the_agent_is_not_part_of(self):
        self.client.post("/api/v1/agents/register", json={
            "agentId": "carol", "name": "carol", "role": "coder", "runtime": "claude-code"})
        self._send("bob", "carol", "P0-Q private", "P0-Q between others")
        body = self._search(query="P0-Q", agentId="alice")
        self.assertEqual(body["total"], 0, "widening to sent must not widen to other people's mail")

    # ── defect 2: silence about what was skipped ─────────────────────────────────────
    def test_without_agentId_it_SAYS_messages_were_not_searched(self):
        self._send("alice", "bob", "P0-Q exists", "P0-Q is definitely here")
        body = self._search(query="P0-Q")
        self.assertEqual(body["total"], 0, "access model unchanged — still no message search")
        self.assertNotIn("messages", body["searched"])
        self.assertTrue(body["skipped"], "an empty result MUST declare what it skipped")
        self.assertIn("agentId", " ".join(body["skipped"]))

    def test_with_agentId_it_reports_messages_as_searched(self):
        self._send("alice", "bob", "P0-Q", "body")
        body = self._search(query="P0-Q", agentId="alice")
        self.assertIn("messages", body["searched"])
        self.assertEqual(body["skipped"], [], "nothing was skipped, so claim nothing")

    def test_an_honest_empty_result_is_distinguishable_from_a_skipped_one(self):
        """THE point of the fix: 'nothing matched' and 'nothing was looked at' must not look alike."""
        genuinely_empty = self._search(query="nonexistent-term-xyz", agentId="alice")
        never_looked = self._search(query="nonexistent-term-xyz")
        self.assertEqual(genuinely_empty["total"], 0)
        self.assertEqual(never_looked["total"], 0)
        self.assertNotEqual(
            (genuinely_empty["searched"], genuinely_empty["skipped"]),
            (never_looked["searched"], never_looked["skipped"]),
            "the two zero-result cases must be tellable apart by the caller",
        )

    def test_scope_shared_reports_only_shared(self):
        body = self._search(query="anything", scope="shared", agentId="alice")
        self.assertEqual(body["searched"], ["shared"])
        self.assertEqual(body["skipped"], [])

    def test_scope_inbox_without_agentId_skips_and_says_so(self):
        body = self._search(query="anything", scope="inbox")
        self.assertEqual(body["searched"], [])
        self.assertTrue(body["skipped"])

    # ── the result shape callers depend on ───────────────────────────────────────────
    def test_message_results_name_both_endpoints(self):
        """Without `to`, a hit on a SENT message reads as if it were received."""
        self._send("alice", "bob", "P0-Q", "body")
        hit = self._search(query="P0-Q", agentId="alice")["results"][0]
        self.assertEqual(hit["from"], "alice")
        self.assertEqual(hit["to"], "bob")

    def test_subject_and_body_are_both_searchable(self):
        self._send("alice", "bob", "subject-token", "unrelated")
        self._send("alice", "bob", "unrelated", "body-token")
        self.assertEqual(self._search(query="subject-token", agentId="alice")["total"], 1)
        self.assertEqual(self._search(query="body-token", agentId="alice")["total"], 1)

    def test_search_is_case_insensitive(self):
        self._send("alice", "bob", "MixedCase", "Body")
        self.assertEqual(self._search(query="mixedcase", agentId="alice")["total"], 1)

    def test_hyphenated_tokens_match(self):
        """The reporter suspected tokenisation. It is a LIKE scan, so hyphens were never the issue —
        pinned so a future switch to a tokenising index cannot silently reintroduce that theory."""
        self._send("alice", "bob", "ruling", "the P0-Q and #124-N slices")
        self.assertEqual(self._search(query="P0-Q", agentId="alice")["total"], 1)
        self.assertEqual(self._search(query="#124-N", agentId="alice")["total"], 1)


if __name__ == "__main__":
    unittest.main()
