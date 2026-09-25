"""`/clear` with `olderThanHours` removes only what is older, on every target it names.

Both tool descriptions tell agents to "prefer olderThanHours over a bare wipe", and until 0.7.0 the
cutoff was applied only to inbox messages: `clear(all, olderThanHours=1)` deleted a shared file and an
agent created seconds earlier (v0.7 scan A1). An unknown target also answered 200 `ok: true` having
done nothing.
"""

from service.tests._base import FastApiTestCase


class ClearHonoursItsAgeFilterTests(FastApiTestCase):
    DB_NAME = "aify-clear-age-filter.db"

    def _fresh_world(self):
        self.assertEqual(self.client.post("/api/v1/agents", json={"agentId": "fresh-agent", "role": "coder"}).status_code, 200)
        shared = self.client.post("/api/v1/shared", data={"from_agent": "fresh-agent", "name": "fresh.txt", "content": "x"})
        self.assertEqual(shared.status_code, 200, shared.text)
        self.assertEqual(self.client.post("/api/v1/channels", json={"name": "fresh-channel", "createdBy": "fresh-agent"}).status_code, 200)
        files, agents = self._names()
        self.assertEqual((("fresh.txt" in files), ("fresh-agent" in agents)), (True, True), "control: the world exists")

    def _names(self):
        return (
            {row["name"] for row in self.client.get("/api/v1/shared").json().get("files", [])},
            set(self.client.get("/api/v1/agents").json().get("agents", {})),
        )

    def test_a_cutoff_keeps_everything_newer_on_every_target(self):
        self._fresh_world()
        for target in ("shared", "agents", "channels", "all"):
            with self.subTest(target):
                r = self.client.post("/api/v1/clear", json={"target": target, "olderThanHours": 1})
                self.assertEqual(r.status_code, 200, r.text)
                files, agents = self._names()
                self.assertIn("fresh.txt", files, f"clear({target}, olderThanHours=1) deleted a new file")
                self.assertIn("fresh-agent", agents, f"clear({target}, olderThanHours=1) deleted a new agent")

    def test_control_without_a_cutoff_the_target_is_cleared(self):
        self._fresh_world()
        self.assertEqual(self.client.post("/api/v1/clear", json={"target": "shared"}).status_code, 200)
        files, agents = self._names()
        self.assertNotIn("fresh.txt", files)
        self.assertIn("fresh-agent", agents, "and only that target")

    def test_an_unknown_target_is_refused(self):
        r = self.client.post("/api/v1/clear", json={"target": "bogus"})
        self.assertEqual(r.status_code, 422, r.text)
