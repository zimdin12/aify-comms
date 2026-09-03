"""`/route/{name}/{path}` — start a sub-container on first request, then proxy to it.

`route_request` was among the service functions the suite never entered. It is the door every
request to a managed sub-container comes through, and it does three things in order: resolve which
container actually serves this name, bring it up if it is down, and forward.

ON-DEMAND START IS THE POINT. A GPU model server is expensive to keep running, so it is started by
the first request that needs it — which means the first request has to WAIT, and the ones that
arrive during the start have to be told to retry rather than be forwarded at nothing.

503 IS THE RIGHT REFUSAL EVERYWHERE HERE, and the distinctions inside it matter to a caller:
`starting` carries `Retry-After` because it is temporary and about to resolve; a start that FAILED
carries the reason (an unreachable image, a GPU that could not be allocated) because retrying will
not help until an operator acts; and an unknown container is a 404, not a 503, because no amount of
waiting turns a name nobody defined into a running service.

SHARED CONTAINERS ARE RESOLVED FIRST. Two service names can point at one process — that is the whole
reason `shared_with` exists — so the container that is started, checked and proxied to is the
TARGET, not the name in the URL. Getting that wrong starts a second copy of an expensive model.
"""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.containers import proxy as proxy_module
from service.containers.models import ContainerDefinition, ContainerState, ContainerStatus
from service.routers.containers import router as containers_router
from service.tests.test_container_proxy_forwarding import _FakeClient, _FakeUpstreamResponse


class FakeManager:
    """Only what `route_request` touches: definitions, states, and `start_container`."""

    def __init__(self, definitions: dict[str, ContainerDefinition], *, start_error=None,
                 start_status=ContainerStatus.RUNNING):
        self.definitions = definitions
        self.states = {
            name: ContainerState(name=name, internal_port=defn.internal_port)
            for name, defn in definitions.items()
        }
        self.started: list[str] = []
        self._start_error = start_error
        self._start_status = start_status

    async def start_container(self, name: str) -> ContainerState:
        self.started.append(name)
        if self._start_error is not None:
            raise self._start_error
        state = self.states[name]
        state.status = self._start_status
        state.container_hostname = f"aify-{name}"
        return state


def definition(**kwargs) -> ContainerDefinition:
    base = {"image": "example/image:latest", "internal_port": 8080}
    base.update(kwargs)
    return ContainerDefinition(**base)


class ContainerRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(containers_router)
        self.client = TestClient(self.app)
        self._real_proxy_client = proxy_module._client

    def tearDown(self):
        proxy_module._client = self._real_proxy_client
        self.client.close()

    def _manager(self, definitions: dict, **kwargs) -> FakeManager:
        manager = FakeManager(definitions, **kwargs)
        self.app.state.container_manager = manager
        return manager

    def _upstream(self, **kwargs) -> _FakeClient:
        fake = _FakeClient(_FakeUpstreamResponse(**kwargs))
        proxy_module._client = fake
        return fake

    def _running(self, manager: FakeManager, name: str) -> None:
        state = manager.states[name]
        state.status = ContainerStatus.RUNNING
        state.container_hostname = f"aify-{name}"

    # ── the manager has to exist ─────────────────────────────────────────────────────────────

    def test_with_no_container_manager_the_route_is_503(self):
        """Container support is optional. A 500 here would read as a crash rather than as a
        deployment that never enabled the feature."""
        response = self.client.get("/route/a/v1/models")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("not initialized", response.json()["detail"])

    def test_an_unknown_container_is_404_not_503(self):
        """No amount of waiting turns a name nobody defined into a running service, and 503 invites
        a client to retry forever."""
        self._manager({"a": definition()})
        response = self.client.get("/route/ghost/v1/models")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("ghost", response.json()["detail"])

    # ── on-demand start ──────────────────────────────────────────────────────────────────────

    def test_a_container_that_is_DOWN_is_started_by_the_first_request(self):
        manager = self._manager({"a": definition()})
        self._upstream()
        response = self.client.get("/route/a/v1/models")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(manager.started, ["a"])

    def test_every_down_state_triggers_a_start(self):
        """`defined`, `stopped` and `failed` all mean "not running now" — a failed container that an
        operator fixed upstream must come back on the next request rather than staying dead."""
        for status in (ContainerStatus.DEFINED, ContainerStatus.STOPPED, ContainerStatus.FAILED):
            # THE SUBTEST LABEL IS A STRING, not the enum. Under `pytest -n` the subtest report is
            # serialised across a process boundary by execnet, which cannot encode an arbitrary
            # object -- so passing the enum fails the test in PARALLEL while passing alone, and the
            # traceback names execnet's serializer rather than anything in this file. The value under
            # test is unchanged; only the label a reporter prints is.
            with self.subTest(status=status.name):
                manager = self._manager({"a": definition()})
                manager.states["a"].status = status
                self._upstream()
                self.assertEqual(self.client.get("/route/a/x").status_code, 200)
                self.assertEqual(manager.started, ["a"])

    def test_a_RUNNING_container_is_not_started_again(self):
        """Starting one that is up is how a second process lands on the same GPU."""
        manager = self._manager({"a": definition()})
        self._running(manager, "a")
        self._upstream()
        self.client.get("/route/a/x")
        self.assertEqual(manager.started, [])

    def test_a_START_FAILURE_is_503_carrying_the_reason(self):
        """An unreachable image or an unallocatable GPU will not fix itself on retry — the caller
        needs the cause, not just "unavailable"."""
        self._manager({"a": definition()}, start_error=RuntimeError("GPU 0: 100% used"))
        response = self.client.get("/route/a/x")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("GPU 0", response.json()["detail"])

    def test_a_container_still_STARTING_is_told_to_retry(self):
        """The requests that arrive during a slow start. Forwarding them would hit a host that is
        not listening yet; failing them without `Retry-After` makes a client give up on a container
        that is seconds from ready."""
        manager = self._manager({"a": definition()})
        manager.states["a"].status = ContainerStatus.STARTING
        response = self.client.get("/route/a/x")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("starting", response.json()["detail"])
        self.assertEqual(response.headers.get("retry-after"), "5")
        self.assertEqual(manager.started, [], "a starting container was started a second time")

    def test_a_start_that_reports_something_other_than_running_is_refused(self):
        """The state after `start_container` is not assumed. If it comes back anything but running,
        proxying would forward to a hostname that may not exist."""
        manager = self._manager({"a": definition()}, start_status=ContainerStatus.STOPPING)
        response = self.client.get("/route/a/x")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("stopping", response.json()["detail"])

    # ── shared containers ────────────────────────────────────────────────────────────────────

    def test_a_shared_name_starts_and_proxies_to_its_TARGET(self):
        """Two service names, one process. Starting the sharer instead would bring up a second copy
        of an expensive model server."""
        manager = self._manager({
            "owner": definition(internal_port=9000),
            "sharer": definition(shared_with="owner"),
        })
        fake = self._upstream()
        response = self.client.get("/route/sharer/v1/models")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(manager.started, ["owner"])
        self.assertEqual(fake.built["url"], "http://aify-owner:9000/v1/models")

    def test_a_shared_name_whose_target_is_already_up_starts_nothing(self):
        manager = self._manager({
            "owner": definition(internal_port=9000),
            "sharer": definition(shared_with="owner"),
        })
        self._running(manager, "owner")
        self._upstream()
        self.client.get("/route/sharer/x")
        self.assertEqual(manager.started, [])

    # ── the forwarding itself ────────────────────────────────────────────────────────────────

    def test_the_PATH_is_forwarded_to_the_containers_url(self):
        manager = self._manager({"a": definition()})
        self._running(manager, "a")
        fake = self._upstream()
        self.client.get("/route/a/v1/chat/completions")
        self.assertEqual(fake.built["url"], "http://aify-a:8080/v1/chat/completions")

    def test_every_method_reaches_the_container(self):
        """It fronts an inference API: POST is the one that matters, and OPTIONS is what a browser
        sends first."""
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                manager = self._manager({"a": definition()})
                self._running(manager, "a")
                fake = self._upstream()
                response = self.client.request(method, "/route/a/x")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(fake.built["method"], method)

    def test_the_body_and_query_reach_the_container(self):
        manager = self._manager({"a": definition()})
        self._running(manager, "a")
        fake = self._upstream()
        self.client.post("/route/a/v1/chat", content=b"{}", params={"stream": "true"})
        self.assertEqual(fake.built["content"], b"{}")
        self.assertEqual(fake.built["params"], {"stream": "true"})

    def test_the_upstream_status_and_body_come_back(self):
        manager = self._manager({"a": definition()})
        self._running(manager, "a")
        self._upstream(status_code=201, chunks=(b"strea", b"med"))
        response = self.client.get("/route/a/x")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.content, b"streamed")

    # ── the idle clock ───────────────────────────────────────────────────────────────────────

    def test_a_proxied_request_RESETS_the_idle_clock(self):
        """The reaper stops containers by idle time. A request that does not stamp this is a
        container reaped while it is serving — and the next request pays a cold start."""
        manager = self._manager({"a": definition()})
        self._running(manager, "a")
        self._upstream()
        self.assertIsNone(manager.states["a"].last_request_at)
        self.client.get("/route/a/x")
        self.assertIsNotNone(manager.states["a"].last_request_at)

    def test_the_TARGETS_clock_is_the_one_reset_for_a_shared_name(self):
        """Traffic through the sharer keeps the process that actually serves it alive. Stamping the
        sharer's own row would let the reaper stop a busy container."""
        manager = self._manager({
            "owner": definition(),
            "sharer": definition(shared_with="owner"),
        })
        self._running(manager, "owner")
        self._upstream()
        self.client.get("/route/sharer/x")
        self.assertIsNotNone(manager.states["owner"].last_request_at)

    def test_a_REFUSED_request_does_not_touch_the_idle_clock(self):
        """Retries against a starting container must not keep a half-up container alive forever."""
        manager = self._manager({"a": definition()})
        manager.states["a"].status = ContainerStatus.STARTING
        self.client.get("/route/a/x")
        self.assertIsNone(manager.states["a"].last_request_at)
