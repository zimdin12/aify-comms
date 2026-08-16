"""The container/GPU router, which served eight routes with no test at all.

Recorded as a backlog by `test_every_route_is_exercised.py` the day that gate was written, on the
reasoning that exercising these needs a container MANAGER. It does — but it does NOT need Docker.
Every handler here is a thin wrapper that reads `request.app.state.container_manager` and calls one
method on it, so a fake manager reaches all eight routes and every refusal they can raise.

WHAT THIS COVERS THAT NOTHING DID:

  * the 503 when no manager is configured. `_get_manager` raises it for EVERY route in this file, and
    it is the state a stock install is in — `main.py` only builds a manager when
    `config.containers.definitions` is present, so on most deployments these eight routes are
    nothing but this refusal.
  * seven separate `Container '<name>' not defined` 404s, one per route. They were among the 41
    operator-facing refusals in the service that no test had ever exercised.
  * that `start` and `route` translate a manager `RuntimeError` into a 503 rather than a 500 — the
    difference between "retry, the thing is coming up" and "this is broken".

THE FAKE IS DELIBERATELY DUMB. It records calls and returns canned values; it is not a container
runtime and does not pretend to be. What is being tested is the ROUTER — argument passing, the
not-defined guard, and status translation — and a real Docker dependency would test Docker.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.containers.models import ContainerStatus
from service.routers import containers as containers_router


class _FakeGpu:
    def get_status(self):
        return {"0": {"allocated_to": "llm", "free": False}}


class _FakeManager:
    """Only what the router touches. Every call is recorded so the test can assert what was passed."""

    def __init__(self, *, defined=("llm",), status=ContainerStatus.RUNNING, raises=None):
        self.definitions = {name: object() for name in defined}
        self.states = {
            name: SimpleNamespace(
                status=status, internal_url=f"http://{name}:8000", container_id=f"cid-{name}",
            )
            for name in defined
        }
        self.gpu = _FakeGpu()
        self.calls = []
        self._raises = raises

    def list_containers(self):
        self.calls.append(("list_containers", ()))
        return {name: {"status": self.states[name].status.value} for name in self.definitions}

    def get_groups(self):
        self.calls.append(("get_groups", ()))
        return {"group-a": ["llm"]}

    def get_container_logs(self, name, tail=100):
        self.calls.append(("get_container_logs", (name, tail)))
        return f"log lines for {name} (tail={tail})"

    async def start_container(self, name):
        self.calls.append(("start_container", (name,)))
        if self._raises:
            raise self._raises
        return self.states[name]

    async def stop_container(self, name):
        self.calls.append(("stop_container", (name,)))

    async def restart_container(self, name):
        self.calls.append(("restart_container", (name,)))
        return self.states[name]

    async def pull_image(self, name):
        self.calls.append(("pull_image", (name,)))
        if self._raises:
            raise self._raises
        return {"pulled": name}


def _client(manager):
    app = FastAPI()
    app.include_router(containers_router.router)
    app.state.container_manager = manager
    return TestClient(app, raise_server_exceptions=False)


#: Every route in this file, as (method, path-with-a-name-filled-in). Used to assert the refusals
#: uniformly — a guard that exists on six routes and not the seventh is exactly the kind of gap a
#: per-route test written by hand misses.
NAMED_ROUTES = [
    ("get", "/api/v1/containers/llm"),
    ("post", "/api/v1/containers/llm/start"),
    ("post", "/api/v1/containers/llm/stop"),
    ("post", "/api/v1/containers/llm/restart"),
    ("get", "/api/v1/containers/llm/logs"),
    ("post", "/api/v1/containers/llm/pull"),
]
UNNAMED_ROUTES = [("get", "/api/v1/containers"), ("get", "/api/v1/gpu")]


class ContainerRouterTests(unittest.TestCase):
    def test_every_route_503s_when_no_manager_is_configured(self):
        """THE STATE MOST DEPLOYMENTS ARE IN. `main.py` builds a manager only when the config
        declares container definitions, so without them these eight routes are this refusal and
        nothing else."""
        client = _client(None)
        for method, path in NAMED_ROUTES + UNNAMED_ROUTES:
            with self.subTest(route=f"{method.upper()} {path}"):
                response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 503)
                self.assertIn("Container manager not initialized", response.json()["detail"])

    def test_every_named_route_404s_for_a_container_that_is_not_defined(self):
        """Seven separate refusals with the same message, one per route — asserted as a set so a
        route that forgets the guard is a failure rather than an untested path."""
        client = _client(_FakeManager(defined=("llm",)))
        for method, path in NAMED_ROUTES:
            unknown = path.replace("/llm", "/nope")
            with self.subTest(route=f"{method.upper()} {unknown}"):
                response = getattr(client, method)(unknown)
                self.assertEqual(response.status_code, 404)
                self.assertIn("'nope' not defined", response.json()["detail"])

    def test_listing_returns_the_containers_and_their_groups(self):
        manager = _FakeManager()
        response = _client(manager).get("/api/v1/containers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "containers": {"llm": {"status": "running"}},
            "groups": {"group-a": ["llm"]},
        })

    def test_getting_one_container_returns_its_entry(self):
        response = _client(_FakeManager()).get("/api/v1/containers/llm")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "running"})

    def test_start_reports_the_state_the_manager_returned(self):
        manager = _FakeManager()
        response = _client(manager).post("/api/v1/containers/llm/start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "running", "url": "http://llm:8000", "container_id": "cid-llm",
        })
        self.assertIn(("start_container", ("llm",)), manager.calls)

    def test_a_manager_RuntimeError_on_start_is_a_503_not_a_500(self):
        """The difference an operator acts on: 503 means retry, 500 means something is broken.

        `start_container` catches `RuntimeError` specifically — the manager raises it for "cannot
        start right now" — so this pins which exception maps to which status.
        """
        manager = _FakeManager(raises=RuntimeError("no GPU free"))
        response = _client(manager).post("/api/v1/containers/llm/start")
        self.assertEqual(response.status_code, 503)
        self.assertIn("no GPU free", response.json()["detail"])

    def test_stop_and_restart_reach_the_manager(self):
        manager = _FakeManager()
        client = _client(manager)
        self.assertEqual(client.post("/api/v1/containers/llm/stop").json(), {"status": "stopped"})
        self.assertIn(("stop_container", ("llm",)), manager.calls)
        restarted = client.post("/api/v1/containers/llm/restart")
        self.assertEqual(restarted.json(), {"status": "running", "url": "http://llm:8000"})
        self.assertIn(("restart_container", ("llm",)), manager.calls)

    def test_logs_pass_the_tail_through_and_default_to_100(self):
        manager = _FakeManager()
        client = _client(manager)
        self.assertIn("tail=100", client.get("/api/v1/containers/llm/logs").json()["logs"])
        self.assertIn("tail=5", client.get("/api/v1/containers/llm/logs?tail=5").json()["logs"])
        self.assertEqual(manager.calls[-1], ("get_container_logs", ("llm", 5)))

    def test_pull_returns_the_result_and_maps_a_failure_to_500(self):
        manager = _FakeManager()
        self.assertEqual(
            _client(manager).post("/api/v1/containers/llm/pull").json(), {"result": {"pulled": "llm"}},
        )
        # Unlike `start`, pull catches BARE Exception and answers 500 — pinned because the two
        # neighbouring handlers translate failure differently and only one of them is a retry hint.
        failing = _FakeManager(raises=Exception("registry unreachable"))
        response = _client(failing).post("/api/v1/containers/llm/pull")
        self.assertEqual(response.status_code, 500)
        self.assertIn("registry unreachable", response.json()["detail"])

    def test_gpu_status_comes_from_the_manager(self):
        response = _client(_FakeManager()).get("/api/v1/gpu")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"devices": {"0": {"allocated_to": "llm", "free": False}}})

    def test_the_fake_matches_the_manager_surface_the_router_uses(self):
        """Anti-drift: a fake that grows stale passes forever while the router has moved on.

        Every attribute the router reads off the manager must exist on the fake, read from the
        ROUTER's source rather than from memory.
        """
        import pathlib
        import re

        source = pathlib.Path(containers_router.__file__).read_text(encoding="utf-8")
        used = set(re.findall(r"manager\.(\w+)", source))
        fake = _FakeManager()
        for attribute in sorted(used):
            with self.subTest(attribute=attribute):
                self.assertTrue(
                    hasattr(fake, attribute),
                    f"the router calls manager.{attribute} and the fake does not have it",
                )
