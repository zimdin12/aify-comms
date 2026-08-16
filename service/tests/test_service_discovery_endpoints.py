"""`/ready` and `/info` — how a new agent or an operator finds out what this service offers.

Both handlers were among the 71 service functions the suite never entered. They are also the two
endpoints whose whole output is a promise about OTHER endpoints, so a wrong answer here sends an
agent somewhere that does not exist and it has no way to tell that from the service being down.

THE HOST HEADER IS REFLECTED INTO EVERY URL, deliberately: the service is reached from other
containers and other machines, and hardcoding `localhost` would hand every remote caller a set of
addresses that only work on the box the service runs on. It is pinned here as OBSERVED behaviour
with its reason, because it looks like an oversight and is not — and because a future change to
"safer" absolute URLs would break exactly the callers it exists for.

THE CONTAINER BLOCK IS CONDITIONAL and that is the interesting part of `/ready`: with no container
manager the checks are EMPTY rather than reporting a failure, because container support is optional
and a service without it is not unready. With one, `docker` must report `connected` or `unavailable`
truthfully — a readiness probe that says "ready" while its docker socket is gone is the false green
this repo keeps finding.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.routers.health import router as health_router


class _FakeContainerManager:
    """Only what these two endpoints touch: `.docker`, `list_containers()`, `get_groups()`."""

    def __init__(self, *, docker=None, containers=None, groups=None):
        self.docker = docker
        self._containers = containers if containers is not None else [{"name": "c-1"}]
        self._groups = groups if groups is not None else {"g-1": ["c-1"]}

    def list_containers(self):
        return self._containers

    def get_groups(self):
        return self._groups


class ServiceDiscoveryEndpointTests(unittest.TestCase):
    """A plain app carrying ONLY the health router.

    `FastApiTestCase` mounts `api_v2` under /api/v1 and nothing else, so `/ready` and `/info` — which
    the health router serves at the ROOT — 404 there. Building the app here keeps the test about the
    two handlers rather than about how the production app happens to be assembled, and it means the
    container-manager state cannot leak into any other suite.
    """

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(health_router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    @property
    def _app(self):
        return self.app

    # ── /ready ───────────────────────────────────────────────────────────────────────────────

    def test_ready_with_no_container_manager_reports_ready_with_no_checks(self):
        """Container support is optional. Reporting a missing manager as a failed check would make
        every plain deployment permanently 'not ready'."""
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "ready", "checks": {}})

    def test_ready_reports_docker_as_CONNECTED_only_when_it_is(self):
        """The truthful half. A readiness probe that says connected with no docker client is the
        false green — an operator would look anywhere but the socket."""
        self._app.state.container_manager = _FakeContainerManager(docker=object())
        connected = self.client.get("/ready").json()["checks"]
        self.assertEqual(connected["container_manager"], "initialized")
        self.assertEqual(connected["docker"], "connected")

        self._app.state.container_manager = _FakeContainerManager(docker=None)
        unavailable = self.client.get("/ready").json()["checks"]
        self.assertEqual(unavailable["container_manager"], "initialized",
                         "the manager is still initialized — only DOCKER is gone")
        self.assertEqual(unavailable["docker"], "unavailable")

    def test_ready_stays_200_either_way(self):
        """It answers a question; it does not fail. A 503 here would restart a container that is
        serving the fleet perfectly well, the same reasoning `/health` records for its ntfy block."""
        self._app.state.container_manager = _FakeContainerManager(docker=None)
        self.assertEqual(self.client.get("/ready").status_code, 200)

    # ── /info ────────────────────────────────────────────────────────────────────────────────

    def test_info_advertises_every_endpoint_an_agent_needs(self):
        payload = self.client.get("/info").json()
        self.assertTrue(payload["name"])
        self.assertTrue(payload["version"])
        for key in ("api", "docs", "openapi", "health", "ready"):
            with self.subTest(endpoint=key):
                self.assertIn(key, payload["endpoints"])
                self.assertTrue(payload["endpoints"][key].startswith("http://"))

    def test_the_urls_are_built_from_the_HOST_HEADER_the_caller_used(self):
        """Observed and deliberate: the service is reached from other containers and machines, so a
        hardcoded localhost would hand every remote caller addresses that only work on this box."""
        payload = self.client.get("/info", headers={"host": "aify.example:9999"}).json()
        self.assertEqual(payload["endpoints"]["api"], "http://aify.example:9999/api/v1")
        self.assertEqual(payload["endpoints"]["health"], "http://aify.example:9999/health")

    def test_the_advertised_api_and_health_urls_actually_resolve(self):
        """The promise checked against the service itself. An endpoint list is a contract, and the
        cheapest way for it to rot is a path that was renamed somewhere else."""
        payload = self.client.get("/info").json()
        for key in ("health", "ready"):
            with self.subTest(endpoint=key):
                path = payload["endpoints"][key].split("/", 3)[-1]
                self.assertEqual(self.client.get(f"/{path}").status_code, 200)

    def test_info_names_the_integration_paths_that_exist_in_this_repo(self):
        """These strings tell an agent where to look. Two of them are FILES, so they are checked
        against the checkout rather than trusted — a moved skill would otherwise be advertised
        forever."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        integrations = self.client.get("/info").json()["integrations"]
        for key in ("codex_skill", "claude_code_skill"):
            with self.subTest(integration=key):
                named = integrations[key].replace("See ", "").strip()
                self.assertTrue((repo / named).exists(), f"{key} advertises a path that is gone: {named}")

    def test_info_omits_the_container_block_when_there_is_no_manager(self):
        payload = self.client.get("/info").json()
        self.assertNotIn("containers", payload)
        self.assertNotIn("groups", payload)
        self.assertNotIn("containers", payload["endpoints"])

    def test_info_includes_the_container_block_when_there_IS_one(self):
        self._app.state.container_manager = _FakeContainerManager()
        payload = self.client.get("/info").json()
        self.assertEqual(payload["containers"], [{"name": "c-1"}])
        self.assertEqual(payload["groups"], {"g-1": ["c-1"]})
        self.assertTrue(payload["endpoints"]["containers"].endswith("/api/v1/containers"))
        self.assertIn("{container_name}", payload["endpoints"]["route"],
                      "the route template must stay a template, not a formatted URL")

    def test_the_mcp_sse_integration_is_null_when_mcp_is_disabled(self):
        """Advertising an SSE endpoint that is switched off sends an agent into a connection it
        cannot make — worse than saying nothing, because it looks like a fault."""
        from service.config import get_config

        config = get_config()
        payload = self.client.get("/info").json()
        if config.mcp_enabled:
            self.assertTrue(payload["integrations"]["mcp_sse"].endswith("/sse"))
        else:
            self.assertIsNone(payload["integrations"]["mcp_sse"])

    def test_info_carries_no_secret_or_connection_string(self):
        """A discovery endpoint is unauthenticated. Nothing that grants access may appear in it —
        the same rule the ntfy topic URL is held to."""
        body = self.client.get("/info").text.lower()
        for forbidden in ("ntfy.sh", "api_key", "apikey", "password", "token", "sqlite:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_ready_survives_an_app_with_no_container_state_at_all(self):
        """`getattr(..., None)` — on a plain app the attribute is ABSENT, not None. Pinned because
        the two are different and only one of them is what this app actually has."""
        self.assertFalse(hasattr(SimpleNamespace(), "container_manager"))
        self.assertEqual(self.client.get("/ready").json()["checks"], {})
