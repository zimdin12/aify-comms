"""The background loops that stop idle containers and restart sick ones, plus the config that
defines them.

`_idle_reaper_loop`, `_health_monitor_loop`, `list_containers`, `get_groups` and `idle_seconds` were
all among the 71 service functions the suite never entered.

THE REAPER IS THE ONE WITH TWO WAYS TO BE WRONG, and they cost opposite things. Too eager and a
model server is torn down between requests, so every request pays a cold start — for a GPU image
that is tens of seconds. Too lax and an idle container holds its GPU fraction against everyone else.
Both exemptions are therefore tested as exemptions, not as an afterthought: `idle_timeout_seconds=0`
means never, and a SHARED container is not the reaper's to stop because it has no container of its
own.

THE LOOPS ARE DRIVEN, NOT WAITED ON. `while True: await asyncio.sleep(30)` cannot be tested by
sleeping; the module's `asyncio.sleep` is replaced with one that returns immediately and cancels the
loop after a fixed number of ticks. That runs the real body the real number of times, with no timing
in the test at all.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from service.containers import manager as manager_module
from service.containers.manager import ContainerManager, load_container_definitions
from service.containers.models import ContainerDefinition, ContainerState, ContainerStatus
from service.tests.test_container_manager_lifecycle import FakeDocker, definition, run


def _ticker(ticks: int):
    """An `asyncio.sleep` replacement that runs the loop body `ticks` times and then cancels it.

    IT MUST NOT AWAIT `asyncio.sleep` ITSELF. Patching `sleep` on the module the manager imported
    patches the attribute on the shared asyncio MODULE — there is one — so a fake that yields via
    `asyncio.sleep(0)` calls itself, burns its budget inside the first tick and cancels the loop
    before the body has run once. That is what my first version did, and it read as "the reaper does
    nothing" across every test here.
    """
    state = {"count": 0}

    async def fake_sleep(_seconds):
        state["count"] += 1
        if state["count"] > ticks:
            raise asyncio.CancelledError()

    return fake_sleep


class _ManagerFixture:
    """Shared construction helpers. A plain mixin, NOT a TestCase: subclassing a TestCase re-runs
    every one of its tests in each subclass, which is how this file briefly reported 38 tests for
    25 distinct ones."""

    def _manager(self, definitions: dict) -> ContainerManager:
        client = FakeDocker()
        with mock.patch("docker.from_env", return_value=client):
            manager = ContainerManager(definitions, defaults={})
        manager.fake = client
        return manager

    def _running(self, manager, name: str, *, idle_seconds: float) -> None:
        state = manager.states[name]
        state.status = ContainerStatus.RUNNING
        state.container_id = f"cid-{name}"
        state.container_hostname = f"aify-{name}"
        state.started_at = datetime.now(timezone.utc)
        state.last_request_at = datetime.now(timezone.utc) - timedelta(seconds=idle_seconds)

    def _reap_once(self, manager) -> None:
        async def body():
            with mock.patch.object(manager_module.asyncio, "sleep", _ticker(1)):
                try:
                    await manager._idle_reaper_loop()
                except asyncio.CancelledError:
                    pass

        run(body())


class IdleReaperTests(_ManagerFixture, unittest.TestCase):
    def test_a_container_idle_past_its_timeout_is_stopped(self):
        manager = self._manager({"a": definition(idle_timeout_seconds=60)})
        self._running(manager, "a", idle_seconds=120)
        self._reap_once(manager)
        self.assertEqual(manager.states["a"].status, ContainerStatus.STOPPED)

    def test_a_container_INSIDE_its_timeout_is_left_running(self):
        """Reaping early makes every request pay a cold start — tens of seconds for a GPU image."""
        manager = self._manager({"a": definition(idle_timeout_seconds=300)})
        self._running(manager, "a", idle_seconds=30)
        self._reap_once(manager)
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    def test_a_timeout_of_ZERO_means_never(self):
        """The documented "0 = never". Treating it as "immediately" would stop a pinned container
        on the first tick, which is the opposite of what an operator asked for."""
        manager = self._manager({"a": definition(idle_timeout_seconds=0)})
        self._running(manager, "a", idle_seconds=99999)
        self._reap_once(manager)
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    def test_a_SHARED_container_is_not_the_reapers_to_stop(self):
        """It has no container of its own — its status mirrors the target's. Reaping it would mark
        it stopped while the process it points at is still serving."""
        manager = self._manager({
            "owner": definition(idle_timeout_seconds=0),
            "sharer": definition(shared_with="owner", idle_timeout_seconds=1),
        })
        self._running(manager, "owner", idle_seconds=0)
        self._running(manager, "sharer", idle_seconds=99999)
        self._reap_once(manager)
        self.assertEqual(manager.states["sharer"].status, ContainerStatus.RUNNING)

    def test_a_container_that_is_not_RUNNING_is_skipped(self):
        manager = self._manager({"a": definition(idle_timeout_seconds=1)})
        manager.states["a"].status = ContainerStatus.FAILED
        manager.states["a"].last_request_at = datetime.now(timezone.utc) - timedelta(seconds=999)
        self._reap_once(manager)
        self.assertEqual(manager.states["a"].status, ContainerStatus.FAILED,
                         "the reaper touched a container it does not own")

    def test_a_container_that_has_never_been_REQUESTED_is_not_idle(self):
        """`idle_seconds` is 0 with no `last_request_at`. A container just started and not yet used
        must not be reaped before its first request arrives."""
        manager = self._manager({"a": definition(idle_timeout_seconds=1)})
        manager.states["a"].status = ContainerStatus.RUNNING
        manager.states["a"].last_request_at = None
        self._reap_once(manager)
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    def test_one_containers_stop_failing_does_not_stop_the_sweep(self):
        """A docker error on one container must not leave the rest holding their GPUs."""
        manager = self._manager({
            "bad": definition(idle_timeout_seconds=1),
            "good": definition(idle_timeout_seconds=1),
        })
        self._running(manager, "bad", idle_seconds=99)
        self._running(manager, "good", idle_seconds=99)

        original_stop = manager.stop_container

        async def stop(name, timeout=30):
            if name == "bad":
                raise RuntimeError("docker exploded")
            return await original_stop(name, timeout)

        manager.stop_container = stop
        self._reap_once(manager)
        self.assertEqual(manager.states["good"].status, ContainerStatus.STOPPED)


class IdleSecondsTests(unittest.TestCase):
    def test_idle_seconds_is_zero_when_nothing_has_been_requested(self):
        self.assertEqual(ContainerState(name="a").idle_seconds, 0.0)

    def test_idle_seconds_grows_from_the_LAST_REQUEST_not_from_the_start(self):
        """A busy container that has been up for hours is not idle. Measuring from `started_at`
        would reap the busiest containers first."""
        state = ContainerState(name="a")
        state.started_at = datetime.now(timezone.utc) - timedelta(hours=5)
        state.last_request_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.assertLess(state.idle_seconds, 60)

    def test_internal_url_needs_both_a_hostname_and_a_port(self):
        state = ContainerState(name="a", internal_port=8080)
        self.assertIsNone(state.internal_url, "a URL with no host would be proxied at nothing")
        state.container_hostname = "aify-a"
        self.assertEqual(state.internal_url, "http://aify-a:8080")


class HealthMonitorTests(_ManagerFixture, unittest.TestCase):
    def _monitor(self, manager, *, ticks: int = 1, status_code: int = 200, boom: bool = False):
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
                if boom:
                    raise RuntimeError("connection refused")
                return _Response(status_code)

        async def body():
            # `patch.object`, not a string target: `service.containers.manager.asyncio.sleep` is an
            # attribute path through a module, which `test_patch_targets_resolve.py` cannot import —
            # and it is right to refuse it, because a string target that never resolves is how a
            # patch outlives the helper it was pointing at.
            with mock.patch.object(manager_module.asyncio, "sleep", _ticker(ticks)), \
                 mock.patch.object(manager_module.httpx, "AsyncClient", _Client):
                try:
                    await manager._health_monitor_loop()
                except asyncio.CancelledError:
                    pass

        run(body())

    def test_a_healthy_container_clears_its_failure_count(self):
        manager = self._manager({"a": definition()})
        self._running(manager, "a", idle_seconds=0)
        manager.states["a"].consecutive_health_failures = 2
        self._monitor(manager)
        self.assertEqual(manager.states["a"].consecutive_health_failures, 0,
                         "a recovered container stayed one failure from a restart")

    def test_failures_have_to_be_CONSECUTIVE_to_count(self):
        """One bad poll on a busy container is not a sick container; restarting on it would drop a
        live request for a blip."""
        manager = self._manager({"a": definition(health_check={"retries": 3})})
        self._running(manager, "a", idle_seconds=0)
        self._monitor(manager, boom=True, ticks=1)
        self.assertEqual(manager.states["a"].consecutive_health_failures, 1)
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    def test_reaching_the_retry_count_restarts_the_container(self):
        manager = self._manager({"a": definition(health_check={"retries": 2})})
        self._running(manager, "a", idle_seconds=0)
        restarted = []
        manager.restart_container = lambda name: restarted.append(name) or asyncio.sleep(0)
        self._monitor(manager, boom=True, ticks=2)
        self.assertEqual(restarted, ["a"], "a container failing every poll was never restarted")

    def test_a_NON_200_counts_as_a_failure(self):
        """A container answering 500 is up but not serving. Treating any response as healthy is how
        a wedged model server stays in rotation."""
        manager = self._manager({"a": definition(health_check={"retries": 5})})
        self._running(manager, "a", idle_seconds=0)
        self._monitor(manager, status_code=503, ticks=2)
        self.assertEqual(manager.states["a"].consecutive_health_failures, 2)

    def test_a_SHARED_container_is_not_health_checked(self):
        manager = self._manager({
            "owner": definition(),
            "sharer": definition(shared_with="owner"),
        })
        self._running(manager, "owner", idle_seconds=0)
        self._running(manager, "sharer", idle_seconds=0)
        self._monitor(manager, boom=True, ticks=1)
        self.assertEqual(manager.states["sharer"].consecutive_health_failures, 0)


class ListingAndConfigTests(_ManagerFixture, unittest.TestCase):
    def test_the_listing_carries_what_a_dashboard_needs_to_place_work(self):
        manager = self._manager({"a": definition(group="models", idle_timeout_seconds=60)})
        self._running(manager, "a", idle_seconds=5)
        manager.states["a"].started_at = datetime.now(timezone.utc) - timedelta(seconds=300)
        entry = manager.list_containers()["a"]
        self.assertEqual(entry["status"], "running")
        self.assertEqual(entry["group"], "models")
        self.assertEqual(entry["internal_url"], "http://aify-a:8080")
        # The VALUES, not just the keys. A hardcoded 0 keeps both fields present and tells an
        # operator every container was started this instant — asserting presence alone missed it.
        self.assertGreaterEqual(entry["uptime_seconds"], 299)
        self.assertGreaterEqual(entry["idle_seconds"], 4)

    def test_a_stopped_container_reports_no_uptime_or_idle_time(self):
        """Reporting them for a stopped container would show an idle clock ticking on something
        that is not running."""
        manager = self._manager({"a": definition()})
        entry = manager.list_containers()["a"]
        self.assertNotIn("uptime_seconds", entry)
        self.assertNotIn("idle_seconds", entry)

    def test_a_sharer_reports_the_url_it_RESOLVES_to(self):
        manager = self._manager({
            "owner": definition(internal_port=9000),
            "sharer": definition(shared_with="owner"),
        })
        self._running(manager, "owner", idle_seconds=0)
        entry = manager.list_containers()["sharer"]
        self.assertEqual(entry["shared_with"], "owner")
        self.assertEqual(entry["resolved_url"], "http://aify-owner:9000")

    def test_an_error_message_survives_into_the_listing(self):
        """It is the only place an operator sees WHY a container failed."""
        manager = self._manager({"a": definition()})
        manager.states["a"].status = ContainerStatus.FAILED
        manager.states["a"].error_message = "Image not found: example/image:latest"
        self.assertIn("Image not found", manager.list_containers()["a"]["error"])

    def test_groups_default_when_none_is_named(self):
        manager = self._manager({
            "a": definition(group="models"),
            "b": definition(group="models"),
            "c": definition(),
        })
        self.assertEqual(manager.get_groups(), {"models": ["a", "b"], "default": ["c"]})

    # ── definitions parsed from service.json ─────────────────────────────────────────────────

    def test_defaults_merge_into_every_definition(self):
        definitions, defaults = load_container_definitions({"containers": {
            "defaults": {"image": "base:latest", "idle_timeout_seconds": 42},
            "definitions": {"a": {}, "b": {"idle_timeout_seconds": 7}},
        }})
        self.assertEqual(definitions["a"].image, "base:latest")
        self.assertEqual(definitions["a"].idle_timeout_seconds, 42)
        self.assertEqual(definitions["b"].idle_timeout_seconds, 7, "a definition must win over defaults")
        self.assertEqual(defaults["idle_timeout_seconds"], 42)

    def test_a_nested_block_is_MERGED_not_replaced(self):
        """`{"gpu": {"exclusive": true}}` must not silently drop the default device ids — that is a
        container that quietly stops requesting a GPU at all."""
        definitions, _ = load_container_definitions({"containers": {
            "defaults": {"image": "base:latest", "gpu": {"device_ids": ["0"], "memory_fraction": 0.5}},
            "definitions": {"a": {"gpu": {"exclusive": True}}},
        }})
        gpu = definitions["a"].gpu
        self.assertEqual(gpu.device_ids, ["0"])
        self.assertEqual(gpu.memory_fraction, 0.5)
        self.assertTrue(gpu.exclusive)

    def test_a_shared_with_pointing_at_nothing_is_refused_AT_LOAD(self):
        """Caught at config load, where the operator can see it, rather than as a ValueError on the
        first request months later."""
        with self.assertRaises(ValueError) as caught:
            load_container_definitions({"containers": {"definitions": {
                "a": {"image": "x:1", "shared_with": "ghost"},
            }}})
        self.assertIn("ghost", str(caught.exception))
        self.assertIn("Available", str(caught.exception), "the message must list what IS defined")

    def test_no_containers_configured_is_not_an_error(self):
        definitions, defaults = load_container_definitions({})
        self.assertEqual(definitions, {})
        self.assertEqual(defaults, {})


# `ContainerDefinition` is imported for the type it validates in the loader tests above.
assert ContainerDefinition is not None
