"""A hermes gateway's session token is served by one read, never by an agent record (2026-10-26).

The agent listing served `runtimeConfig.gatewayUrl` with its `token=` to every caller holding the shared
API key, so any agent could drive another agent's hermes session, and a record dumped into a transcript
carried a live token. Measured live before the fix: of 40 GET routes, `/agents` and `/agents/{id}`
returned it. The "Open Hermes UI" button now reads it from `GET /agents/{id}/hermes-gateway`.

THE POPULATION IS DERIVED: every GET route the app serves, its path parameters filled with the test
agent's id, so a route added later is probed without anyone listing it.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase

TOKEN = "SENTINELgatewayToken0123456789"
GATEWAY = f"ws://127.0.0.1:9341/api/ws?token={TOKEN}"
AGENT = "hermes-a"


class AGatewayTokenIsServedOnlyToItsOneRead(FastApiTestCase):
    DB_NAME = "aify-test-gateway-token.db"

    def setUp(self):
        super().setUp()
        registered = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "hermes", "sessionMode": "resident",
            "sessionHandle": "hermes-key", "runtimeConfig": {"gatewayUrl": GATEWAY},
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        # A terminal named like the agent, so `/terminals/{id}/launch`, which serves the agent's config
        # to its host, answers rather than 404s.
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (AGENT, "session-a", AGENT, "linux:host:default", "hermes", "2026-10-26T00:00:00Z", "2026-10-26T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_routes(self):
        # From the app's own OpenAPI document: this FastAPI keeps an included router nested, so
        # `app.routes` held 4 entries here, and the probe passed by asking nothing.
        for path, operations in self.client.get("/openapi.json").json()["paths"].items():
            if "get" in operations:
                yield path

    def test_no_get_route_serves_the_token(self):
        probed, leaking = 0, []
        for path in self._get_routes():
            if path.endswith("/hermes-gateway"):
                continue
            url = path
            # Every path parameter gets the agent id; a route about something else answers 404.
            while "{" in url:
                start = url.index("{")
                url = url[:start] + AGENT + url[url.index("}", start) + 1:]
            response = self.client.get(url)
            probed += 1
            if TOKEN in response.text:
                leaking.append(path)
        self.assertGreater(probed, 30, "control: the routes were probed")
        self.assertEqual(leaking, [])

    def test_the_probe_reaches_a_route_built_from_the_stored_config(self):
        # CONTROL: the terminal launch composes the worker's environment from the agent's stored config,
        # token included, so a 404 there would be a probe that never asked.
        self.assertEqual(self.client.get(f"/api/v1/terminals/{AGENT}/launch").status_code, 200)

    def test_the_record_still_says_a_gateway_exists(self):
        # CONTROL: the redaction removes the token, not the gateway, which the bridge's and the
        # dashboard's checks read by its ws:// prefix.
        served = self.client.get(f"/api/v1/agents/{AGENT}").json()["agent"]["runtimeConfig"]["gatewayUrl"]
        self.assertTrue(served.startswith("ws://127.0.0.1:9341/api/ws?token="), served)
        self.assertNotIn(TOKEN, served)

    def test_the_one_read_returns_it(self):
        # CONTROL for the probe above: the token is stored, so its absence there is the redaction.
        response = self.client.get(f"/api/v1/agents/{AGENT}/hermes-gateway")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["gatewayUrl"], GATEWAY)
        missing = self.client.get("/api/v1/agents/no-such-agent/hermes-gateway")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("' has no hermes gateway", missing.json()["detail"])

    def test_with_an_operator_key_the_read_needs_it(self):
        self._app.state.config.operator_key = "synthetic-operator"
        refused = self.client.get(f"/api/v1/agents/{AGENT}/hermes-gateway")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertNotIn(TOKEN, refused.text)
        granted = self.client.get(f"/api/v1/agents/{AGENT}/hermes-gateway",
                                  headers={"X-Aify-Operator-Key": "synthetic-operator"})
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json()["gatewayUrl"], GATEWAY)
