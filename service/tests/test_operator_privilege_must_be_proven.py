"""Naming yourself "operator" must not grant operator privilege. R5-H1, HIGH, my own regression.

THE ATTACK, reported 2026-08-18 and confirmed in code before this test was written. The ownership checks
I added to unsend, channel-delete and artifact-unshare each read:

    if actor not in _UNSEND_OPERATOR_ACTORS and actor != author:   # {"dashboard", "operator"}
        raise HTTPException(403, ...)

`actor` is a request PARAMETER. So any caller could pass `requestedBy="operator"` and delete any
message, delete any channel, or unshare any artifact — with no knowledge of the victim, no credential,
and no relationship to the row. The audit trail then recorded "operator" as the actor, so the operator
was framed for it. A fix for a casual ownership hole opened a universal one.

WHY IT WAS THIS BAD HERE, measured rather than assumed, because severity comes from what else guards
the endpoint:
  * every bridge sends the SAME shared `X-API-Key`, so that key proves "inside the trust boundary" and
    never "I am the dashboard";
  * `api_key` is not configured on this deployment at all (the middleware installs only `if
    config.api_key`) and `cors_origins` is `*`.
So the three destructive endpoints were reachable unauthenticated, gated by a guessable English word.

THE FIX IS A PROVEN CLAIM, not a better word list. `authorize_operator` verifies an
`X-Aify-Operator-Key` header against a configured secret in constant time. The actor string still names
WHO acted, for the audit trail; it grants nothing.

FAIL-CLOSED IN BOTH DIRECTIONS, and the unconfigured case is the one that matters: "no key is set, so
allow the operator strings" would restore the vulnerability by default on every deployment that never
sets one — which is all of them until someone does. A privilege with no credential behind it is not a
privilege.

WHAT THIS TEST DOES NOT CLAIM. On a host where an agent can read `.env` or fetch the dashboard page,
the key is obtainable. This raises the bar from "guess an English word" to "hold a secret" — enough for
the casual and prompt-injected cases, not a boundary against an agent with filesystem access. The real
boundary is authenticating the service itself, which is an operator decision recorded in the v0.6 plan.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.operator_authz import OPERATOR_ACTORS, OPERATOR_KEY_HEADER
from service.db import get_db
from service.tests._base import FastApiTestCase

VICTIM = "victim-agent"
ATTACKER = "attacker-agent"
SECRET = "s3cret-operator-key"


class OperatorPrivilegeMustBeProven(FastApiTestCase):
    DB_NAME = "aify-operator-privilege-test.db"

    def setUp(self):
        super().setUp()
        self._seed()

    # ── fixture: a message, a channel and an artifact, each owned by the victim ──────────────

    def _seed(self):
        async def run():
            db = await get_db()
            try:
                for agent in (VICTIM, ATTACKER):
                    await db.execute(
                        "INSERT INTO agents (id, name, role, runtime, session_mode, status,"
                        " registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                        (agent, agent, "coder", "claude-code", "resident", "online",
                         "2026-08-18T00:00:00Z", "2026-08-18T00:00:00Z"),
                    )
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, type, subject, body, timestamp)"
                    " VALUES (?,?,?,?,?,?,?)",
                    ("msg-victim", VICTIM, ATTACKER, "info", "mine", "body",
                     "2026-08-18T00:00:00Z"),
                )
                await db.execute(
                    "INSERT INTO channels (name, description, created_by, created_at)"
                    " VALUES (?,?,?,?)",
                    ("victim-room", "", VICTIM, "2026-08-18T00:00:00Z"),
                )
                await db.execute(
                    "INSERT INTO shared_artifacts (name, from_agent, description, content,"
                    " shared_at) VALUES (?,?,?,?,?)",
                    ("victim.txt", VICTIM, "", "hello", "2026-08-18T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(run())

    def _set_key(self, key: str):
        self.client.app.state.config.operator_key = key

    # The three destructive endpoints, as (label, callable taking headers+actor).
    def _attacks(self):
        return {
            "unsend": lambda actor, headers: self.client.delete(
                f"/api/v1/messages/msg-victim?requestedBy={actor}", headers=headers),
            "channel-delete": lambda actor, headers: self.client.delete(
                f"/api/v1/channels/victim-room?requestedBy={actor}", headers=headers),
            "artifact-unshare": lambda actor, headers: self.client.delete(
                f"/api/v1/shared/victim.txt?requestedBy={actor}", headers=headers),
        }

    def _still_there(self) -> dict:
        async def run():
            db = await get_db()
            try:
                out = {}
                for label, sql in (
                    ("unsend", "SELECT 1 FROM messages WHERE id = 'msg-victim'"),
                    ("channel-delete", "SELECT 1 FROM channels WHERE name = 'victim-room'"),
                    ("artifact-unshare",
                     "SELECT 1 FROM shared_artifacts WHERE name = 'victim.txt'"),
                ):
                    out[label] = bool(await (await db.execute(sql)).fetchone())
                return out
            finally:
                await db.close()
        return asyncio.run(run())

    # ── the attack ───────────────────────────────────────────────────────────────────────────

    def test_the_reported_attack_is_REFUSED_on_every_endpoint(self):
        """R5-H1 verbatim: claim the operator string, present no credential, destroy another agent's
        data. Runs against all three endpoints because the bug was one idea copied three times."""
        self._set_key(SECRET)
        for actor in sorted(OPERATOR_ACTORS):
            for label, attack in self._attacks().items():
                with self.subTest(actor=actor, endpoint=label):
                    response = attack(actor, {})
                    self.assertEqual(
                        response.status_code, 403,
                        f"a caller naming itself '{actor}' destroyed another agent's data via {label} "
                        f"with no credential (R5-H1). Response: {response.text[:200]}",
                    )
                    self.assertTrue(
                        self._still_there()[label],
                        f"{label}: the row was destroyed despite the refusal",
                    )

    def test_both_refusal_MESSAGES_say_what_went_wrong(self):
        """Quoted verbatim because `test_every_refusal_is_exercised.py` requires each refusal's longest
        static fragment to appear in a test — a refusal nobody has read is a refusal nobody has
        checked. Asserted against real responses, so this proves the wording as well as satisfying the
        gate. Both matter operationally: a 403 an operator cannot explain is how a security fix gets
        reverted."""
        self._set_key(SECRET)
        wrong = self._attacks()["unsend"]("operator", {OPERATOR_KEY_HEADER: "nope"})
        self.assertIn(
            "header. The actor name records WHO acted; it does not grant permission.",
            wrong.text,
            f"the wrong-key refusal no longer explains the distinction: {wrong.text[:200]}",
        )

        self._set_key("")
        unset = self._attacks()["unsend"]("operator", {})
        self.assertIn(
            ", but no operator key is configured on this service, so the claim cannot be verified.",
            unset.text,
            f"the unconfigured-key refusal no longer names the cause: {unset.text[:200]}",
        )

    def test_a_WRONG_key_is_refused(self):
        self._set_key(SECRET)
        for label, attack in self._attacks().items():
            with self.subTest(endpoint=label):
                response = attack("operator", {OPERATOR_KEY_HEADER: "not-the-key"})
                self.assertEqual(response.status_code, 403, response.text[:200])
                self.assertTrue(self._still_there()[label])

    def test_an_UNCONFIGURED_key_refuses_the_claim_rather_than_allowing_it(self):
        """The half that decides whether this fix is real. "No key configured, so allow the operator
        strings" would restore the vulnerability by default on every deployment that never sets one."""
        self._set_key("")
        for label, attack in self._attacks().items():
            with self.subTest(endpoint=label):
                response = attack("operator", {OPERATOR_KEY_HEADER: "anything"})
                self.assertEqual(
                    response.status_code, 403,
                    f"{label} granted operator privilege while no operator key was configured, which "
                    "is the vulnerability restored by default",
                )
                self.assertTrue(self._still_there()[label])
                self.assertIn(
                    "no operator key is configured", response.text.lower(),
                    "the refusal must name the cause: an operator seeing an unexplained 403 reverts "
                    f"the fix. Got: {response.text[:200]}",
                )

    # ── what must keep working ───────────────────────────────────────────────────────────────

    def test_a_VALID_operator_key_still_grants_the_override(self):
        """ANTI-VACUITY, and the load-bearing half: every assertion above would pass if the endpoints
        refused everything — which would silently remove the operator's ability to moderate."""
        self._set_key(SECRET)
        headers = {OPERATOR_KEY_HEADER: SECRET}
        for label, attack in self._attacks().items():
            with self.subTest(endpoint=label):
                response = attack("operator", headers)
                self.assertIn(
                    response.status_code, (200, 204),
                    f"a properly credentialed operator could not {label}: {response.text[:200]}",
                )
                self.assertFalse(self._still_there()[label],
                                 f"{label}: the credentialed operator action did not take effect")

    def test_an_OWNER_still_acts_on_its_own_rows_without_any_key(self):
        """The ownership path must not have been collapsed into the operator path. An agent needs no
        operator key to unsend its own message."""
        self._set_key("")
        response = self.client.delete(f"/api/v1/messages/msg-victim?requestedBy={VICTIM}")
        self.assertIn(response.status_code, (200, 204), response.text[:200])
        self.assertFalse(self._still_there()["unsend"])

    def test_a_NON_OWNER_agent_is_still_refused_for_the_ordinary_reason(self):
        self._set_key(SECRET)
        response = self.client.delete(f"/api/v1/messages/msg-victim?requestedBy={ATTACKER}")
        self.assertEqual(response.status_code, 403, response.text[:200])
        self.assertTrue(self._still_there()["unsend"])

    def test_a_missing_actor_is_still_refused(self):
        """The v0.5.6 mandatory-actor behaviour must survive this change."""
        self._set_key(SECRET)
        self.assertEqual(self.client.delete("/api/v1/messages/msg-victim").status_code, 400)

    # ── the vocabulary has ONE owner ─────────────────────────────────────────────────────────

    def test_no_router_still_carries_its_own_operator_actor_set(self):
        """Three copies of the same frozenset is how two of them stay broken after the third is fixed
        — and this bug was one idea copied to three endpoints."""
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        for rel in ("service/routers/channels.py", "service/routers/shared.py",
                    "service/routers/dispatch_messages/message_removal.py"):
            with self.subTest(module=rel):
                source = (repo / rel).read_text(encoding="utf-8")
                self.assertNotIn(
                    'frozenset({"dashboard", "operator"})', source,
                    f"{rel} declares its own operator-actor set again. The vocabulary belongs to "
                    "service/api_core/operator_authz.py, which is also where the claim is VERIFIED; "
                    "a local copy means a local exemption that skips the verification.",
                )
                self.assertIn(
                    "authorize_operator", source,
                    f"{rel} no longer calls the shared authority",
                )


if __name__ == "__main__":
    unittest.main()
