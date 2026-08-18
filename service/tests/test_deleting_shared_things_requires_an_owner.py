"""Deleting an artifact or a channel is the owner's call, not any agent's.

Found while sweeping the tool surface on 2026-08-18. Both endpoints existed with NO acting agent and
NO ownership check — the same shape as H4's unsend — and neither had an MCP tool, so the holes had
never been reachable from an agent. Adding the tools without the checks would have opened them.

WHY IT MATTERED ENOUGH TO ADD THE TOOLS AT ALL: the only agent-reachable way to remove ONE artifact
was `comms_clear(target="shared")`, which wipes every artifact on the hub for every team, and there
was no way to remove a channel at all. A per-item delete missing while a fleet-wide wipe is one call
away is how an agent tidying up destroys somebody else's work.

CHANNEL DELETION IS THE MOST DESTRUCTIVE of the three: it removes the channel, its membership and
every message ever posted to it — shared history for every member. Membership is deliberately NOT
enough; leaving is `comms_channel_leave`.

The refusal texts are quoted verbatim below so `test_every_refusal_is_exercised` can attribute each
raise site here.
"""

from __future__ import annotations

import unittest

from service.tests._base import FastApiTestCase

#: Verbatim, for the refusal gate. See the module docstring.
REFUSALS = (
    "deleting a shared artifact requires `requestedBy` (the agent that shared it, or an operator "
    "surface). Refused rather than defaulted.",
    "deleting a channel requires `requestedBy` (its creator, or an operator surface). Refused rather "
    "than defaulted: this removes every message in the channel for every member.",
    "'. Only the sharer or an operator surface may remove it.",
    "'. Deleting a channel destroys its history for every member; leave it instead.",
)


class DeletingSharedThingsRequiresAnOwner(FastApiTestCase):
    DB_NAME = "aify-shared-delete-owner-test.db"

    def setUp(self):
        super().setUp()
        for agent in ("owner", "stranger"):
            r = self.client.post("/api/v1/agents", json={"agentId": agent, "role": "coder"})
            self.assertEqual(r.status_code, 200, r.text)

    def _share(self, name: str, sharer: str = "owner"):
        r = self.client.post("/api/v1/shared", data={
            "from_agent": sharer, "name": name, "content": "body", "description": "d"})
        self.assertEqual(r.status_code, 200, r.text)

    def _make_channel(self, name: str, creator: str = "owner"):
        r = self.client.post("/api/v1/channels",
                             json={"name": name, "description": "", "createdBy": creator})
        self.assertIn(r.status_code, (200, 201), r.text)

    # ── artifacts ────────────────────────────────────────────────────────────────────────────

    def test_an_actor_less_artifact_delete_is_refused(self):
        self._share("a.txt")
        r = self.client.delete("/api/v1/shared/a.txt")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("requires `requestedBy`", r.text)

    def test_a_stranger_cannot_delete_someone_elses_artifact(self):
        self._share("a.txt")
        r = self.client.delete("/api/v1/shared/a.txt?requestedBy=stranger")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("Only the sharer or an operator surface may remove it", r.text)

    def test_the_sharer_can_delete_their_own(self):
        # ANTI-VACUITY: refusing everyone is not a fix.
        self._share("a.txt")
        self.assertEqual(self.client.delete("/api/v1/shared/a.txt?requestedBy=owner").status_code, 200)

    def test_an_operator_surface_can_delete_any_artifact(self):
        self._share("a.txt")
        self.assertEqual(self.client.delete("/api/v1/shared/a.txt?requestedBy=dashboard").status_code, 200)

    def test_a_refused_artifact_delete_leaves_it_in_place(self):
        self._share("a.txt")
        self.assertEqual(self.client.delete("/api/v1/shared/a.txt?requestedBy=stranger").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/shared/a.txt").status_code, 200,
                         "the refused delete removed it anyway")

    def test_deleting_a_MISSING_artifact_is_still_idempotent(self):
        """Unchanged on purpose. A caller retrying a delete should not have to distinguish "I removed
        it" from "it was already gone", and there is no owner to check on a row that does not
        exist."""
        r = self.client.delete("/api/v1/shared/never-existed.txt?requestedBy=owner")
        self.assertEqual(r.status_code, 200, r.text)

    # ── channels ─────────────────────────────────────────────────────────────────────────────

    def test_an_actor_less_channel_delete_is_refused(self):
        self._make_channel("ops")
        r = self.client.delete("/api/v1/channels/ops")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("requires `requestedBy`", r.text)

    def test_a_MEMBER_cannot_delete_a_channel_they_did_not_create(self):
        """The distinction that matters: joining a channel does not entitle you to end it for
        everyone. Leaving is the member's tool."""
        self._make_channel("ops")
        joined = self.client.post("/api/v1/channels/ops/join", json={"agentId": "stranger"})
        self.assertEqual(joined.status_code, 200, joined.text)
        r = self.client.delete("/api/v1/channels/ops?requestedBy=stranger")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("leave it instead", r.text)

    def test_the_creator_can_delete_their_channel(self):
        self._make_channel("ops")
        self.assertEqual(self.client.delete("/api/v1/channels/ops?requestedBy=owner").status_code, 200)

    def test_a_refused_channel_delete_keeps_the_channel_and_its_messages(self):
        self._make_channel("ops")
        sent = self.client.post("/api/v1/channels/ops/send", json={
            "from_agent": "owner", "channel": "ops", "body": "keep me", "trigger": False})
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(self.client.delete("/api/v1/channels/ops?requestedBy=stranger").status_code, 403)
        read = self.client.get("/api/v1/channels/ops")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertIn("keep me", read.text, "a refused delete destroyed the channel's history")


if __name__ == "__main__":
    unittest.main()
