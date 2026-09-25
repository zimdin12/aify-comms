"""Sharing under a name another agent already holds is refused, so the owner check on delete holds.

`DELETE /shared/{name}` lets only the sharer or an operator remove an artifact. `POST /shared`
overwrote any existing name and rewrote its owner, so any agent could take a name over and then
delete it: 403, overwrite, 200 (v0.7 scan A2).
"""

from service.tests._base import FastApiTestCase


class ASharedNameCannotBeTakenOverTests(FastApiTestCase):
    DB_NAME = "aify-shared-takeover.db"

    def _share(self, who, body="x"):
        return self.client.post("/api/v1/shared", data={"from_agent": who, "name": "plan.md", "content": body})

    def test_another_agent_cannot_overwrite_and_then_delete(self):
        self.assertEqual(self._share("alice").status_code, 200)
        taken = self._share("mallory", "replaced")
        self.assertEqual(taken.status_code, 409, taken.text)
        self.assertIn("alice", taken.text)
        listed = {row["name"]: row["from"] for row in self.client.get("/api/v1/shared").json()["files"]}
        self.assertEqual(listed["plan.md"], "alice", "the owner must not have changed")
        self.assertEqual(self.client.delete("/api/v1/shared/plan.md", params={"requestedBy": "mallory"}).status_code, 403)

    def test_control_the_sharer_can_update_their_own_artifact(self):
        self.assertEqual(self._share("alice").status_code, 200)
        self.assertEqual(self._share("alice", "v2").status_code, 200)

    def test_a_sender_that_is_not_a_name_is_refused(self):
        r = self.client.post("/api/v1/shared", data={"from_agent": "bad\nname", "name": "x.md", "content": "x"})
        self.assertIn(r.status_code, (400, 422), r.text)
