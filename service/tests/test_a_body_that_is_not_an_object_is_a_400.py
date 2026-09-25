"""A hand-read JSON body that is not an object is refused with 400, not a 500 and a traceback.

Twelve handlers read `await request.json()` raw and only two checked for an object, so an array or a
string reached `body.get(...)` (v0.7 scan A3). `service/api_core/request_body.py` is the one check.
"""

from service.tests._base import FastApiTestCase


class ABodyThatIsNotAnObjectIsA400Tests(FastApiTestCase):
    DB_NAME = "aify-body-shape.db"

    def setUp(self):
        super().setUp()
        self.assertEqual(self.client.post("/api/v1/agents", json={"agentId": "a1", "role": "coder"}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/channels", json={"name": "c1", "createdBy": "a1"}).status_code, 200)

    ROUTES = [
        ("post", "/api/v1/usage"),
        ("post", "/api/v1/usage/consumption"),
        ("post", "/api/v1/messages/m1/read"),
        ("post", "/api/v1/channels/c1/read"),
    ]

    def test_an_array_or_a_string_or_malformed_json_is_a_400(self):
        for method, path in self.ROUTES:
            for body in (b"[1, 2]", b'"str"', b"{not json"):
                with self.subTest(path=path, body=body):
                    r = getattr(self.client, method)(path, content=body, headers={"content-type": "application/json"})
                    self.assertEqual(r.status_code, 400, r.text)

    def test_a_route_hooks_post_to_degrades_to_no_body_instead(self):
        """Heartbeats and turn boundaries are posted by shell hooks; a beat that errors never lands,
        so there an unreadable body counts as no body. It still must never reach `.get` on a list."""
        for body in (b"[1, 2]", b'"str"', b"{not json"):
            with self.subTest(body=body):
                r = self.client.post("/api/v1/agents/a1/heartbeat", content=body, headers={"content-type": "application/json"})
                self.assertLess(r.status_code, 400, r.text)

    def test_control_an_empty_body_is_still_accepted_where_it_was(self):
        r = self.client.post("/api/v1/agents/a1/heartbeat")
        self.assertNotEqual(r.status_code, 400, r.text)
        self.assertLess(r.status_code, 500, r.text)
