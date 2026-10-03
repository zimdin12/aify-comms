"""A change request waiting on a definition that has gone does not block the agent's next one
(external review of 0.8.1, MEDIUM: "a claimed change request never expires").

THE DEFECT. One request per agent may wait, and a claimed request is handed back to its host until that
host reports, so a host that crashes finishes it on its next claim. But when the host is gone for good, the
operator releases the definition, and the waiting request stayed `claimed`: its machine's claims were the
only thing that could settle it and that machine never claims again. Once the id was defined anywhere else,
every change to it was refused with "already has a change waiting" for ever.

The fix settles a waiting request as soon as it can no longer be delivered, by the same rule a claim uses,
before the one-waiting check reads it. The control: a request whose definition is still held keeps blocking
a second one, as C4 requires.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B, valid
from service.tests import test_definition_change_requests as requests_test


class AWaitingRequestDoesNotOutliveItsDefinition(FastApiTestCase):
    DB_NAME = "aify-test-definition-request-outlives.db"
    # The helpers, not the class: a TestCase bound to a name in this module is collected and run here again.
    push = requests_test.AChangeIsQueuedForItsHost.push
    ask = requests_test.AChangeIsQueuedForItsHost.ask
    claim = requests_test.AChangeIsQueuedForItsHost.claim
    rows = requests_test.AChangeIsQueuedForItsHost.rows

    def setUp(self):
        super().setUp()
        for host in (A, B):
            beat = self.client.post("/api/v1/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": ["/work"], "runtimes": [], "metadata": {}})
            self.assertEqual(beat.status_code, 200, beat.text)

    def release(self, agent_id, host):
        return self.client.post(f"/api/v1/agent-definitions/{agent_id}/release",
                                json={"machineId": host["machine"], "requestedBy": "dashboard"})

    def test_a_request_claimed_by_a_host_that_never_returned_does_not_block_the_agent_once_released(self):
        self.push("s1", 1, [valid("coder")])
        first = self.ask("coder", {"role": "reviewer"}).json()["request"]
        self.assertEqual([r["id"] for r in self.claim(A)], [first["id"]], "host A claims it, then is gone for good")
        self.assertEqual(self.release("coder", A).status_code, 200)
        self.push("t1", 1, [valid("coder")], host=B)

        again = self.ask("coder", {"role": "lead"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["request"]["machineId"], B["machine"])
        [stale] = self.rows("SELECT status, outcome FROM definition_requests WHERE id = ?", (first["id"],))
        self.assertEqual(stale, {"status": "refused", "outcome": f"the definition moved to {B['machine']} before its host claimed this"},
                         "the stale request is settled, and says why")

    def test_CONTROL_a_request_whose_definition_is_still_held_still_blocks_the_next(self):
        self.push("s1", 1, [valid("coder")])
        self.ask("coder", {"role": "reviewer"})
        self.claim(A)
        second = self.ask("coder", {"role": "lead"})
        self.assertEqual(second.status_code, 409, second.text)
