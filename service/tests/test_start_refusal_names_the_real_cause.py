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

SINCE D8 none of the cold-start causes reaches this button: an agent no host defines is refused before
cold-start (whatever its runtime or environment), and a defined agent's Start goes to its host's
lifecycle queue (D9c). What the button still refuses itself is an undefined agent and a resident one,
so those are the causes compared here; the cold-start causes are reported on the send path.
"""

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

    def test_an_undefined_agent_of_any_runtime_names_its_definition_and_claims_no_missing_bridge(self):
        """Since D8 the cause for an agent no host defines is that, whatever its runtime: no bridge
        would fix it. Two halves, both needed: the recorded cause must be there, and the specific false
        claim that sent an operator to run a bare `aify-comms` must NOT be -- a message carrying the
        real cause AND the old sentence would pass the first half alone.
        """
        self._register("src-badruntime", runtime="notarealruntime")
        detail = self._refusal("src-badruntime")
        self.assertIn('no host defines "src-badruntime"', detail)
        self.assertNotIn("environment bridge is available", detail)
        self.assertNotIn("`aify-comms`", detail, "advice that can reap a live fleet")

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
        happens to contain. Comparing the causes against each other is what makes this gate real.
        Since D8 a Start reaches cold-start only for a defined agent, whose Start goes to its host
        (D9c); what the button itself still refuses is an undefined agent and a resident one."""
        self._register("src-cmp-undefined")
        self._register("src-cmp-resident", session_mode="resident")
        shapes = {self._refusal_shape("src-cmp-undefined"), self._refusal_shape("src-cmp-resident")}
        self.assertEqual(
            len(shapes), 2,
            f"an undefined agent and a resident one produced the SAME diagnosis — the recorded "
            f"reason is being discarded and a cause asserted in its place: {shapes}",
        )
