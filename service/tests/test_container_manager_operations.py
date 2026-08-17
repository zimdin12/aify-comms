"""The manager's remaining operator-facing operations: restart, logs, pull, health, shutdown.

These were the last of the container manager's methods in the 71 the suite never entered. They are
the ones an operator reaches for when something is wrong, which is the worst moment for any of them
to be the thing that is wrong.

RESTART IS STOP-THEN-START, and both halves have to happen. A restart that stops without starting
leaves the agent with no worker and no error; one that starts without stopping leaves two containers
on the same GPU. It is asserted by what the fake Docker was asked to do, not by the final state,
because the final state of a working restart and of a start-that-never-stopped look identical.

LOGS AND PULL ANSWER TO A HUMAN. `get_container_logs` never raises — an operator asking for logs
while docker is down should read the reason, not get a 500 — and `pull_image` does raise, because
its caller is an explicit action that has to report failure rather than silently do nothing.

`_wait_for_health` IS AN HTTP POLL, so the client is replaced. What matters is that it keeps polling
until the deadline rather than giving up on the first refusal: a container that takes twenty seconds
to load a model answers nothing at all for those twenty seconds.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import docker.errors

from service.containers import manager as manager_module
from service.containers.manager import ContainerManager
from service.containers.models import ContainerStatus
from service.tests.test_container_manager_lifecycle import (
    FakeContainer,
    FakeDocker,
    definition,
    run,
)


class FakeImage:
    def __init__(self, tags):
        self.tags = tags


class FakeImages:
    """`docker.images.pull` — the one attribute `pull_image` reaches for."""

    def __init__(self, *, error=None, tags=("example/image:latest",)):
        self.pulled: list[str] = []
        self._error = error
        self._tags = list(tags)

    def pull(self, image):
        self.pulled.append(image)
        if self._error is not None:
            raise self._error
        return FakeImage(self._tags)


class ContainerManagerOperationsTests(unittest.TestCase):
    def _manager(self, definitions: dict, *, docker_client=None) -> ContainerManager:
        client = FakeDocker() if docker_client is None else docker_client
        client.images = FakeImages()
        with mock.patch("docker.from_env", return_value=client):
            manager = ContainerManager(definitions, defaults={})
        manager.fake = client
        return manager

    def _healthy(self, manager, healthy: bool = True):
        async def _wait(*args, **kwargs):
            return healthy

        manager._wait_for_health = _wait

    # ── restart ──────────────────────────────────────────────────────────────────────────────

    def test_a_restart_STOPS_the_old_container_and_starts_a_new_one(self):
        """Asserted on what docker was asked to do. A restart that never stopped and a working one
        leave the same final state — one running container — so the state cannot tell them apart."""
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        first = manager.fake.created[0]

        run(manager.restart_container("a"))

        self.assertTrue(first.stopped and first.removed, "the old container was left running")
        self.assertEqual(len(manager.fake.run_calls), 2, "no new container was started")
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    def test_a_restart_re_allocates_the_GPU_rather_than_double_counting_it(self):
        """The stop releases and the start re-takes. Skipping the release would make the second
        start ask for a fraction the first is still holding — the container never comes back."""
        manager = self._manager({"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.6})})
        self._healthy(manager)
        run(manager.start_container("a"))
        run(manager.restart_container("a"))
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {"a": 0.6})

    def test_restarting_something_that_was_never_started_just_starts_it(self):
        """An operator pressing Restart on a stopped container means "bring it up"."""
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.restart_container("a"))
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    # ── logs ─────────────────────────────────────────────────────────────────────────────────

    def test_logs_come_back_decoded(self):
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        self.assertEqual(manager.get_container_logs("a"), "log line")

    def test_logs_for_a_container_that_is_not_running_are_EMPTY_not_an_error(self):
        """The dashboard asks for logs on every container it lists. Raising for the stopped ones
        would break the page rather than showing nothing for them."""
        manager = self._manager({"a": definition()})
        self.assertEqual(manager.get_container_logs("a"), "")
        self.assertEqual(manager.get_container_logs("no-such-container"), "")

    def test_a_DOCKER_FAILURE_is_reported_in_the_logs_rather_than_raised(self):
        """An operator asking for logs while docker is unreachable needs the reason on screen. This
        is the one place a swallowed exception is the right answer — and the message carries it."""
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))

        def boom(_key):
            raise RuntimeError("docker daemon gone")

        manager.docker.containers.get = boom
        logs = manager.get_container_logs("a")
        self.assertIn("Error", logs)
        self.assertIn("docker daemon gone", logs, "the cause has to survive into what is shown")

    def test_logs_with_no_docker_client_are_empty(self):
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        manager.docker = None
        self.assertEqual(manager.get_container_logs("a"), "")

    def test_the_tail_argument_reaches_docker(self):
        """An unbounded log read on a chatty container is megabytes into the response body."""
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        seen = {}
        container = manager.fake.created[0]
        container.logs = lambda tail=100: seen.setdefault("tail", tail) or b"x"
        manager.get_container_logs("a", tail=7)
        self.assertEqual(seen["tail"], 7)

    # ── pull ─────────────────────────────────────────────────────────────────────────────────

    def test_pulling_names_the_definitions_image(self):
        manager = self._manager({"a": definition(image="example/model:v2")})
        result = run(manager.pull_image("a"))
        self.assertEqual(manager.fake.images.pulled, ["example/model:v2"])
        self.assertIn("Pulled", result)

    def test_pulling_an_unknown_container_RAISES(self):
        """Unlike logs, this is an explicit operator action. Answering "" would look like a pull
        that succeeded and left the image exactly as it was."""
        manager = self._manager({"a": definition()})
        with self.assertRaises(ValueError):
            run(manager.pull_image("nope"))

    def test_pulling_with_no_docker_client_RAISES(self):
        manager = self._manager({"a": definition()})
        manager.docker = None
        with self.assertRaises(RuntimeError):
            run(manager.pull_image("a"))

    def test_a_failed_pull_propagates(self):
        """The registry being unreachable, or the tag not existing, is exactly what the operator ran
        this to find out."""
        manager = self._manager({"a": definition()})
        manager.fake.images = FakeImages(error=docker.errors.ImageNotFound("no such tag"))
        with self.assertRaises(docker.errors.ImageNotFound):
            run(manager.pull_image("a"))

    # ── the health poll ──────────────────────────────────────────────────────────────────────

    def _poll(self, manager, *, answers, timeout=1, interval=0):
        """Drive the real `_wait_for_health` against a scripted sequence of responses."""
        calls = {"n": 0}

        class _Response:
            def __init__(self, code):
                self.status_code = code

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                index = min(calls["n"], len(answers) - 1)
                calls["n"] += 1
                answer = answers[index]
                if answer is None:
                    raise RuntimeError("connection refused")
                return _Response(answer)

        async def body():
            with mock.patch.object(manager_module.httpx, "AsyncClient", _Client):
                return await manager._wait_for_health("host", 8080, "/health", timeout, interval)

        return run(body()), calls

    def test_a_container_that_answers_200_is_healthy(self):
        manager = self._manager({"a": definition()})
        healthy, _ = self._poll(manager, answers=[200])
        self.assertTrue(healthy)

    def test_it_KEEPS_POLLING_past_a_refusal(self):
        """A container loading a model answers nothing for the first seconds. Giving up on the first
        connection refusal would fail every GPU image at startup."""
        manager = self._manager({"a": definition()})
        healthy, calls = self._poll(manager, answers=[None, None, 200])
        self.assertTrue(healthy, "a container that came up on the third poll was declared failed")
        self.assertGreaterEqual(calls["n"], 3)

    def test_a_container_that_only_ever_500s_is_NOT_healthy(self):
        """Up but not serving. Accepting any response would put a wedged container into rotation."""
        manager = self._manager({"a": definition()})
        healthy, _ = self._poll(manager, answers=[500])
        self.assertFalse(healthy)

    def test_it_gives_up_at_the_deadline_rather_than_hanging(self):
        """The caller holds a lock while this runs. An unbounded wait is a container start that
        never returns and a manager nothing else can use."""
        manager = self._manager({"a": definition()})
        healthy, _ = self._poll(manager, answers=[None], timeout=0)
        self.assertFalse(healthy)

    # ── background tasks and shutdown ────────────────────────────────────────────────────────

    def test_starting_background_tasks_starts_both_loops(self):
        manager = self._manager({"a": definition()})

        async def body():
            await manager.start_background_tasks()
            started = (manager._reaper_task is not None, manager._health_task is not None)
            await manager.stop_background_tasks()
            return started

        self.assertEqual(run(body()), (True, True))

    def test_stopping_background_tasks_CANCELS_them(self):
        """They are `while True` loops. Leaving them running holds the event loop open and keeps
        reaping containers after the manager is meant to be gone."""
        manager = self._manager({"a": definition()})

        async def body():
            await manager.start_background_tasks()
            reaper, health = manager._reaper_task, manager._health_task
            await manager.stop_background_tasks()
            return reaper.cancelled() or reaper.done(), health.cancelled() or health.done()

        self.assertEqual(run(body()), (True, True))

    def test_stopping_background_tasks_that_were_never_started_is_harmless(self):
        """`shutdown` runs on every exit path, including one where startup failed early."""
        manager = self._manager({"a": definition()})
        run(manager.stop_background_tasks())

    def test_AUTO_START_containers_come_up_with_the_background_tasks(self):
        manager = self._manager({
            "auto": definition(auto_start=True),
            "manual": definition(auto_start=False),
        })
        self._healthy(manager)

        async def body():
            await manager.start_background_tasks()
            await manager.stop_background_tasks()

        run(body())
        started = [call["name"] for call in manager.fake.run_calls]
        self.assertEqual(started, [f"{manager.project_name}-auto"])

    def test_one_auto_start_FAILING_does_not_stop_the_others(self):
        """Startup runs them in a loop. An unreachable image on one container must not leave the
        rest of the fleet down."""
        manager = self._manager({
            "bad": definition(auto_start=True),
            "good": definition(auto_start=True),
        })
        self._healthy(manager)
        original_start = manager.start_container

        async def start(name):
            if name == "bad":
                raise RuntimeError("image not found")
            return await original_start(name)

        manager.start_container = start

        async def body():
            await manager.start_background_tasks()
            await manager.stop_background_tasks()

        run(body())
        self.assertEqual(manager.states["good"].status, ContainerStatus.RUNNING)

    def test_shutdown_stops_the_loops_and_closes_the_proxy_client(self):
        """The proxy's httpx client is process-global and pooled; leaving it open holds sockets
        after the app is gone."""
        manager = self._manager({"a": definition()})
        closed = {"called": False}

        async def fake_close():
            closed["called"] = True

        async def body():
            await manager.start_background_tasks()
            with mock.patch("service.containers.proxy.close_client", fake_close):
                await manager.shutdown()
            return manager._reaper_task.cancelled() or manager._reaper_task.done()

        cancelled = run(body())
        self.assertTrue(cancelled, "shutdown left the reaper running")
        self.assertTrue(closed["called"], "shutdown left the proxy client open")


# Imported for the fake's container type; referenced so a future refactor of the shared harness
# cannot drop it silently.
assert FakeContainer is not None
