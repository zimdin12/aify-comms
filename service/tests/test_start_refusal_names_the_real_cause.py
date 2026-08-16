"""The dashboard Start button must report WHY cold-start refused, not assert a cause.

`_coldstart_spawn_request_for_dispatch` refuses for five distinct reasons and records which one in
the `warnings` list a caller passes. The router's Start branch passed no list, so the reason was
discarded and every cause rendered one sentence:

    Could not start "<agent>" — no environment bridge is available to run it.
    Start one on its host with `aify-comms`.

That is the N8 defect (operator-reported 2026-07-31 and 2026-08-07, fixed for the SEND path) still
live on the Start path, and this shape of message is worse than a vague one because it NAMES a cause.

WHICH CAUSES ACTUALLY REACHED IT, measured rather than assumed — the helper lists five, and only
three are reachable from this endpoint:

  * a runtime that cannot be cold-started      → REACHABLE, and no bridge would ever fix it
  * a corrupt environment row (no id)          → REACHABLE, likewise
  * the environment could not be resolved      → REACHABLE; the old sentence was roughly right here
  * a spawn already in flight                  → NOT reachable: returns 200 + spawnPending first
  * the agent is RESIDENT                      → NOT reachable: guarded earlier, with a good message

For the first two the sentence sent the operator to start a bridge that was already running — and a
bare `aify-comms` on a host that already has one SUPERSEDES the live bridge and reaps its managed
workers, which took nine agents down on 2026-08-11. A wrong diagnosis here steers into an outage.

So the test is not "a 409 is returned". It is that DIFFERENT causes produce DIFFERENT diagnoses. A
single generic sentence satisfies any assertion written per-cause; only comparing the causes against
each other catches it — and only after the agent id is normalised out, because the old sentence
interpolated the id and so varied between agents while saying the same wrong thing.
"""

import asyncio

from service.db import get_db
from service.clock import now as _now
from service.tests._base import FastApiTestCase


class StartRefusalNamesTheRealCauseTests(FastApiTestCase):
    DB_NAME = "aify-start-refusal-cause-test.db"

    def _register(self, agent_id: str, *, runtime: str = "hermes", session_mode: str = "managed"):
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": runtime, "sessionMode": session_mode,
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _start(self, agent_id: str):
        return self.client.post(f"/api/v1/agents/{agent_id}/control", json={"action": "start"})

    def _refusal(self, agent_id: str) -> str:
        r = self._start(agent_id)
        self.assertEqual(r.status_code, 409, f"expected a refusal, got {r.status_code} {r.text}")
        return r.json().get("detail", "").lower()

    # ── each cause names itself ──────────────────────────────────────────────────────────────

    def test_unresolvable_environment_says_so(self):
        self._register("src-noenv")
        self.assertIn("environment bound to this agent could not be resolved", self._refusal("src-noenv"))

    def test_a_non_coldstartable_runtime_says_so(self):
        """Not "no bridge available" — no bridge could ever help; the runtime is the problem."""
        self._register("src-badruntime", runtime="notarealruntime")
        detail = self._refusal("src-badruntime")
        self.assertIn("cold-startable", detail)

    def test_a_resident_agent_is_refused_EARLIER_by_its_own_guard(self):
        """Measured, not assumed: the resident refusal inside `_coldstart_spawn_request_for_dispatch`
        is UNREACHABLE from this endpoint. The router stops a resident agent before cold-start with a
        message that already names the cause and the fix, so cause 2 was never part of this defect.

        Pinned because it is the reason the cause list here is shorter than the helper's, and because
        a first draft of this file "proved" the resident cause through the generic message — the old
        sentence embedded the AGENT ID, and an id containing "resident" made the assertion pass
        against the unfixed router.
        """
        self._register("src-resident", session_mode="resident")
        detail = self._refusal("src-resident")
        self.assertIn("is resident", detail)
        self.assertIn("switch it to managed", detail, "the message should name the fix")
        self.assertNotIn("cannot start managed", detail, "this must NOT be the cold-start refusal")

    # ── the causes are DISTINGUISHABLE, which is the whole point ─────────────────────────────

    def _refusal_shape(self, agent_id: str) -> str:
        """The refusal with the agent id removed.

        Without this the comparison is vacuous: the OLD generic sentence interpolated the agent id,
        so two refusals for two different agents differed as strings while carrying an identical
        diagnosis. Comparing raw text made a broken router look like a fixed one.
        """
        return self._refusal(agent_id).replace(agent_id, "<agent>")

    def test_the_reachable_causes_do_not_share_one_diagnosis(self):
        """A single hardcoded sentence satisfies any per-cause assertion that quotes a word it
        happens to contain. Comparing the causes against each other is what makes this gate real."""
        self._register("src-cmp-noenv")
        self._register("src-cmp-runtime", runtime="notarealruntime")
        shapes = {self._refusal_shape("src-cmp-noenv"), self._refusal_shape("src-cmp-runtime")}
        self.assertEqual(
            len(shapes), 2,
            f"an unresolvable environment and a non-cold-startable runtime produced the SAME "
            f"diagnosis — the recorded reason is being discarded and a cause asserted in its "
            f"place: {shapes}",
        )

    def test_no_refusal_claims_a_missing_bridge_it_did_not_check(self):
        """The specific false claim that sent an operator to run a bare `aify-comms`. A runtime that
        cannot be cold-started is not a bridge problem, and no bridge would fix it."""
        self._register("src-claim-runtime", runtime="notarealruntime")
        detail = self._refusal("src-claim-runtime")
        self.assertNotIn("environment bridge is available", detail)
        self.assertNotIn("`aify-comms`", detail, "advice that can reap a live fleet")

    # ── the idempotent case is still not a failure ──────────────────────────────────────────

    def test_a_spawn_already_in_flight_is_still_reported_as_pending_not_refused(self):
        """Guards the earlier fix (2026-07-19) that this change routes around: an in-flight spawn
        returns 200 + spawnPending BEFORE any refusal message is rendered."""
        self._register("src-inflight")

        async def _seed():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO spawn_requests
                        (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    ("spawn-src-inflight", "spec-1", "env-1", "src-inflight", "hermes", "queued", _now(), _now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_seed())
        r = self._start("src-inflight")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("spawnPending"), r.text)
