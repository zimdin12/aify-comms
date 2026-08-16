"""Reusing an agent id — the two registration tombstone 410s and the two rename 409s.

An agent id is an identity: it addresses an inbox, keys a dispatch queue and names a session. All
four refusals here exist to stop one id meaning two things. None had a test, and all four read as
exercised until fe1e22ad because `service/tests/data/` holds a pre-split copy of `register_agent`.

    register  410 Agent '<a>' was intentionally removed at <t>; auto re-registration is blocked.
              410 Agent '<a>' was intentionally removed. Pass restoreDeleted=true to register this
                  ID again.
    rename    409 Agent "<n>" already exists
              409 Agent "<n>" was intentionally removed before; clear that ID before reusing it

TWO 410 MESSAGES FOR ONE STATE, DELIBERATELY. An AUTO register — a bridge beating on its own — is
told re-registration is blocked, because a bridge cannot act on advice. A MANUAL one is a person, so
it is told which flag to pass. Collapsing them would take the actionable instruction away from the
human and hand it to a process that cannot use it. That asymmetry is the thing worth pinning: it is
invisible from either message alone, and a refactor that unified them would look like a cleanup.

`_enforce_tombstone_registration_gate` IS THE BLUNTER OF THE TWO GATES. No `restoreDeleted` flag at
all means the caller is not asking to bring the agent back, so the id stays refused whether the beat
is automatic or manual. Its sibling — `_enforce_tombstone_resurrection_gate`, tested in
`test_tombstone_resurrection_gate.py` — handles the harder case where the flag IS set and relaunch
freshness decides.

RENAME REFUSES A TOMBSTONED TARGET even though nothing occupies that id. A tombstone is a decision
that the id is retired; renaming onto it would resurrect the name while the tombstone still claims
it is gone, which is the same disagreement the registration gates exist to prevent.
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException

from service.api_core.registration_gates import _enforce_tombstone_registration_gate
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

REMOVED_AT = "2026-06-03T12:00:00Z"


class _Req:
    def __init__(self, *, restore=False, auto=True, agent_id="lc-coder"):
        self.agentId = agent_id
        self.restoreDeleted = restore
        self.autoRegister = auto


class _Tombstone(dict):
    """Stands in for the sqlite3.Row: subscript and `keys()`."""

    def __getitem__(self, key):
        return dict.get(self, key)


def _run(req, tombstone):
    try:
        asyncio.run(_enforce_tombstone_registration_gate(req, tombstone))
        return None
    except HTTPException as exc:
        return exc


class TombstoneRegistrationGateTests(unittest.TestCase):
    def test_an_AUTO_register_is_told_registration_is_blocked(self):
        """A bridge cannot act on advice, so it gets the fact and the time."""
        exc = _run(_Req(auto=True), _Tombstone(removed_at=REMOVED_AT))
        self.assertIsNotNone(exc, "a tombstoned id was admitted")
        self.assertEqual(exc.status_code, 410)
        self.assertEqual(
            str(exc.detail),
            f"Agent 'lc-coder' was intentionally removed at {REMOVED_AT}"
            + "; auto re-registration is blocked.",
        )

    def test_a_MANUAL_register_is_told_which_flag_to_pass(self):
        """A person can act, so the message is an instruction rather than a report. Collapsing the
        two would hand the actionable half to the bridge and the useless half to the human."""
        exc = _run(_Req(auto=False), _Tombstone(removed_at=REMOVED_AT))
        self.assertIsNotNone(exc)
        self.assertEqual(exc.status_code, 410)
        self.assertEqual(
            str(exc.detail),
            "Agent 'lc-coder' was intentionally removed. "
            + "Pass restoreDeleted=true to register this ID again.",
        )

    def test_the_two_messages_are_genuinely_different(self):
        """Asserted as a relation, not just twice. A refactor that unified them would satisfy each
        test above if the surviving message happened to be the one it asserts."""
        auto = str(_run(_Req(auto=True), _Tombstone(removed_at=REMOVED_AT)).detail)
        manual = str(_run(_Req(auto=False), _Tombstone(removed_at=REMOVED_AT)).detail)
        self.assertNotEqual(auto, manual)
        self.assertIn("restoreDeleted=true", manual)
        self.assertNotIn("restoreDeleted", auto, "a bridge cannot pass a flag")
        self.assertIn(REMOVED_AT, auto, "and it needs the time to correlate with its own logs")

    def test_asking_to_restore_is_not_this_gates_business(self):
        """`restoreDeleted=true` belongs to the resurrection gate, which weighs relaunch freshness.
        This one must pass it through untouched or that decision never gets made."""
        self.assertIsNone(_run(_Req(restore=True, auto=True), _Tombstone(removed_at=REMOVED_AT)))
        self.assertIsNone(_run(_Req(restore=True, auto=False), _Tombstone(removed_at=REMOVED_AT)))

    def test_no_tombstone_means_nothing_to_refuse(self):
        for tombstone in (None, {}):
            with self.subTest(tombstone=tombstone):
                self.assertIsNone(_run(_Req(), tombstone))


class RenameIdReuseTests(FastApiTestCase):
    def _register(self, agent_id: str):
        response = self.client.post(
            "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _rename(self, agent_id: str, new_agent_id: str):
        return self.client.post(
            f"/api/v1/agents/{agent_id}/rename",
            json={"newAgentId": new_agent_id, "requestedBy": "operator"},
        )

    def test_renaming_onto_a_LIVE_id_is_refused(self):
        """Two agents cannot share an id: the target already has an inbox, a queue and sessions."""
        self._register("lc-one")
        self._register("lc-two")
        response = self._rename("lc-one", "lc-two")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], 'Agent "lc-two" already exists')

    def test_renaming_onto_a_TOMBSTONED_id_is_refused_and_says_to_clear_it(self):
        """Nothing occupies the id, and it is still refused. A tombstone is a decision that the id
        is retired; renaming onto it would bring the name back while the tombstone still says it is
        gone — the same disagreement the registration gates exist to prevent."""
        self._register("lc-one")
        self._register("lc-gone")
        removed = self.client.delete("/api/v1/agents/lc-gone")
        self.assertEqual(removed.status_code, 200, removed.text)
        response = self._rename("lc-one", "lc-gone")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            'Agent "lc-gone" was intentionally removed before; clear that ID before reusing it',
        )

    def test_the_two_rename_refusals_are_distinguishable(self):
        """Both are 409 on the same field, and they need different actions from the operator —
        pick a different name, versus clear the tombstone."""
        self._register("lc-one")
        self._register("lc-live")
        self._register("lc-dead")
        self.client.delete("/api/v1/agents/lc-dead")
        live = self._rename("lc-one", "lc-live").json()["detail"]
        dead = self._rename("lc-one", "lc-dead").json()["detail"]
        self.assertNotEqual(live, dead)
        self.assertIn("already exists", live)
        self.assertIn("clear that ID", dead)

    def test_a_rename_to_a_free_id_succeeds(self):
        self._register("lc-one")
        response = self._rename("lc-one", "lc-fresh")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.client.get("/api/v1/agents/lc-fresh").status_code, 200,
            "the agent must be reachable under its new id",
        )

    def test_renaming_an_agent_to_its_own_id_is_a_no_op(self):
        """Before any lookup, so it cannot trip the already-exists check on itself — which is what
        would happen if the equality test were removed."""
        self._register("lc-one")
        response = self._rename("lc-one", "lc-one")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["changed"], False)

    def test_renaming_an_agent_that_does_not_exist_is_404(self):
        response = self._rename("lc-never", "lc-fresh")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], 'Agent "lc-never" not found')
