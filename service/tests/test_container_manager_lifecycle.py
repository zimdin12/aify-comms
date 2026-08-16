"""The container manager's start/stop lifecycle, driven against a fake Docker.

Seventeen of its methods were among the 71 service functions the suite never entered. The container
ROUTES are covered by a fake manager, which proves the routes call it and nothing about what it
does; this is the other half.

WHAT IS ACTUALLY AT RISK. Every failure path in `start_container` has to undo two things it may
already have taken: a GPU allocation and a running container. Leak the first and the device is
permanently short by that fraction with no tenant to blame; leak the second and a container runs
unmanaged, outside every reaper, until an operator finds it by hand. That is not hypothetical — the
`asyncio.CancelledError` branch exists because a cancel during the health wait did exactly that
(bughunt 2026-07-03): `CancelledError` is a BaseException, so `except Exception` never saw it, and
the container sat in STARTING which the background loops skip.

THREE MUTATIONS SURVIVE AND ARE RECORDED HERE RATHER THAN PAPERED OVER, because each says something
about the code rather than about the tests:

  * removing the GPU release from the health-timeout branch changes nothing — the outer
    `except Exception` releases again on the way past, so that inner release is a second defence;
  * removing it from the `ImageNotFound` branch changes nothing either, for a different reason:
    the allocation happens AFTER `containers.run`, so a missing image cannot have allocated
    anything. That release is defensive for a state that cannot occur;
  * making the "target already running" shortcut fall through changes nothing, because the
    fall-through calls `start_container(target)` and that returns early for a RUNNING container.
    The shortcut saves a lock acquisition, not a duplicate container.

THE FAKE IS A DOCKER CLIENT, NOT A DOCKER. It records what it was asked to run and can be told to
fail in the specific ways the real one does — `ImageNotFound`, `NotFound` on a lookup. Health is
injected separately, because `_wait_for_health` is an HTTP poll against a container that does not
exist here and its own behaviour is a different subject.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import docker.errors

from service.containers.manager import ContainerManager
from service.containers.models import ContainerDefinition, ContainerStatus


class FakeContainer:
    def __init__(self, name: str, container_id: str = "cid-1"):
        self.name = name
        self.id = container_id
        self.status = "running"
        self.labels: dict[str, str] = {}
        self.stopped = False
        self.removed = False

    def stop(self, timeout=None):
        self.stopped = True

    def remove(self, force=False):
        self.removed = True

    def logs(self, tail=100):
        return b"log line"


class FakeContainers:
    def __init__(self, owner: "FakeDocker"):
        self.owner = owner

    def list(self, filters=None, all=False):
        return list(self.owner.existing)

    def get(self, key):
        for container in self.owner.created + self.owner.existing:
            if key in (container.name, container.id):
                return container
        raise docker.errors.NotFound(f"no such container: {key}")

    def run(self, **kwargs):
        self.owner.run_calls.append(kwargs)
        if self.owner.run_error is not None:
            raise self.owner.run_error
        container = FakeContainer(kwargs["name"], f"cid-{len(self.owner.created) + 1}")
        self.owner.created.append(container)
        return container


class FakeVolumes:
    def __init__(self, owner=None):
        self.known: set[str] = set()

    def get(self, name):
        if name in self.known:
            return object()
        raise docker.errors.NotFound(f"no such volume: {name}")

    def create(self, name):
        self.known.add(name)
        return object()


class FakeDocker:
    def __init__(self, *, existing=(), run_error=None):
        self.containers = FakeContainers(self)
        self.volumes = FakeVolumes()
        self.created: list[FakeContainer] = []
        self.existing = list(existing)
        self.run_calls: list[dict] = []
        self.run_error = run_error

    def ping(self):
        return True


def definition(**kwargs) -> ContainerDefinition:
    base = {"image": "example/image:latest", "internal_port": 8080}
    base.update(kwargs)
    return ContainerDefinition(**base)


def run(coro):
    return asyncio.run(coro)


class ContainerManagerLifecycleTests(unittest.TestCase):
    def _manager(self, definitions: dict, *, docker_client=None) -> ContainerManager:
        client = FakeDocker() if docker_client is None else docker_client
        with mock.patch("docker.from_env", return_value=client):
            manager = ContainerManager(definitions, defaults={})
        manager.fake = client
        return manager

    def _healthy(self, manager, healthy: bool = True):
        async def _wait(*args, **kwargs):
            return healthy

        manager._wait_for_health = _wait

    # ── resolve_url ──────────────────────────────────────────────────────────────────────────

    def test_resolve_url_is_none_for_an_unknown_container(self):
        manager = self._manager({"a": definition()})
        self.assertIsNone(manager.resolve_url("nope"))

    def test_resolve_url_is_none_until_the_container_has_a_hostname(self):
        """A URL for a container that is not running would send a proxied request at nothing."""
        manager = self._manager({"a": definition()})
        self.assertIsNone(manager.resolve_url("a"))

    def test_resolve_url_follows_shared_with_to_the_TARGETS_url(self):
        """The whole point of sharing: two services, one process. Returning the sharer's own
        (absent) hostname would start a second copy of an expensive model server."""
        manager = self._manager({
            "owner": definition(internal_port=9000),
            "sharer": definition(shared_with="owner"),
        })
        manager.states["owner"].container_hostname = "aify-owner"
        self.assertEqual(manager.resolve_url("sharer"), "http://aify-owner:9000")

    # ── start: the refusals ──────────────────────────────────────────────────────────────────

    def test_starting_an_unknown_container_raises_rather_than_inventing_one(self):
        manager = self._manager({"a": definition()})
        with self.assertRaises(ValueError):
            run(manager.start_container("nope"))

    def test_with_no_docker_the_state_says_WHY_and_nothing_is_allocated(self):
        manager = self._manager({"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})})
        manager.docker = None
        with self.assertRaises(RuntimeError):
            run(manager.start_container("a"))
        self.assertEqual(manager.states["a"].status, ContainerStatus.FAILED)
        self.assertIn("Docker", manager.states["a"].error_message)
        self.assertEqual(manager.gpu.get_status(), {}, "a GPU was reserved for a start that never ran")

    def test_a_GPU_refusal_stops_the_start_before_docker_is_touched(self):
        manager = self._manager({
            "hog": definition(gpu={"device_ids": ["0"], "memory_fraction": 1.0}),
            "b": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5}),
        })
        manager.gpu.allocate("hog", manager.definitions["hog"].gpu)
        with self.assertRaises(RuntimeError):
            run(manager.start_container("b"))
        self.assertEqual(manager.states["b"].status, ContainerStatus.FAILED)
        self.assertIn("GPU", manager.states["b"].error_message)
        self.assertEqual(manager.fake.run_calls, [], "docker was asked to run a container it could not host")

    def test_starting_something_already_RUNNING_is_a_no_op(self):
        manager = self._manager({"a": definition()})
        manager.states["a"].status = ContainerStatus.RUNNING
        run(manager.start_container("a"))
        self.assertEqual(manager.fake.run_calls, [], "a second container was started for one already up")

    # ── start: the happy path ────────────────────────────────────────────────────────────────

    def test_a_started_container_is_labelled_so_it_can_be_reconciled_later(self):
        """`aify.managed` / `aify.name` are how a restarted hub finds containers it already owns.
        Without them the containers keep running and the hub starts duplicates."""
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        labels = manager.fake.run_calls[0]["labels"]
        self.assertEqual(labels["aify.managed"], "true")
        self.assertEqual(labels["aify.name"], "a")

    def test_a_started_container_lands_on_the_hubs_network_with_a_derived_name(self):
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        call = manager.fake.run_calls[0]
        self.assertEqual(call["network"], manager.network_name)
        self.assertEqual(call["name"], f"{manager.project_name}-a")
        self.assertEqual(manager.states["a"].container_hostname, f"{manager.project_name}-a")
        self.assertEqual(manager.states["a"].status, ContainerStatus.RUNNING)

    def test_a_GPU_container_allocates_only_AFTER_docker_accepted_it(self):
        manager = self._manager({"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})})
        self._healthy(manager)
        run(manager.start_container("a"))
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {"a": 0.5})

    def test_missing_volumes_are_created_and_existing_ones_are_reused(self):
        manager = self._manager({"a": definition(volumes={"vol-a": "/data"})})
        self._healthy(manager)
        run(manager.start_container("a"))
        self.assertIn("vol-a", manager.fake.volumes.known)
        self.assertEqual(manager.fake.run_calls[0]["volumes"], {"vol-a": {"bind": "/data", "mode": "rw"}})

    # ── start: every failure path must undo what it took ─────────────────────────────────────

    def test_a_health_TIMEOUT_tears_the_container_down_and_frees_the_GPU(self):
        """The container is running by then. Left behind it is outside every reaper — the loops
        skip anything not RUNNING — and its GPU fraction is held by a tenant that no longer exists."""
        manager = self._manager({"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})})
        self._healthy(manager, healthy=False)
        with self.assertRaises(RuntimeError):
            run(manager.start_container("a"))
        created = manager.fake.created[0]
        self.assertTrue(created.stopped and created.removed, "the unhealthy container was left running")
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {})
        self.assertEqual(manager.states["a"].status, ContainerStatus.FAILED)

    def test_an_IMAGE_NOT_FOUND_frees_the_GPU_and_says_how_to_fix_it(self):
        manager = self._manager(
            {"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})},
            docker_client=FakeDocker(run_error=docker.errors.ImageNotFound("nope")),
        )
        self._healthy(manager)
        with self.assertRaises(docker.errors.ImageNotFound):
            run(manager.start_container("a"))
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {})
        self.assertIn("docker pull", manager.states["a"].error_message)

    def test_a_CANCELLED_start_releases_the_GPU_and_removes_the_container(self):
        """THE 2026-07-03 BUGHUNT. `CancelledError` is a BaseException, so `except Exception` never
        saw it: the container kept running, the GPU stayed allocated, and the state sat in STARTING
        — which the idle reaper and health monitor both skip, so nothing ever cleaned it up."""
        manager = self._manager({"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})})

        async def _cancel(*args, **kwargs):
            raise asyncio.CancelledError()

        manager._wait_for_health = _cancel
        with self.assertRaises(asyncio.CancelledError):
            run(manager.start_container("a"))
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {},
                         "a cancelled start held its GPU allocation forever")
        created = manager.fake.created[0]
        self.assertTrue(created.stopped and created.removed,
                        "a cancelled start left a container running outside every reaper")
        self.assertEqual(manager.states["a"].status, ContainerStatus.FAILED)

    # ── sharing ──────────────────────────────────────────────────────────────────────────────

    def test_starting_a_SHARER_starts_the_target_once_and_mirrors_its_address(self):
        manager = self._manager({
            "owner": definition(internal_port=9000),
            "sharer": definition(shared_with="owner"),
        })
        self._healthy(manager)
        run(manager.start_container("sharer"))
        self.assertEqual(len(manager.fake.run_calls), 1, "sharing started a second container")
        self.assertEqual(manager.fake.run_calls[0]["name"], f"{manager.project_name}-owner")
        self.assertEqual(manager.states["sharer"].container_hostname,
                         manager.states["owner"].container_hostname)
        self.assertEqual(manager.states["sharer"].internal_port, 9000)

    def test_a_sharer_whose_target_is_already_running_starts_nothing(self):
        manager = self._manager({
            "owner": definition(internal_port=9000),
            "sharer": definition(shared_with="owner"),
        })
        manager.states["owner"].status = ContainerStatus.RUNNING
        manager.states["owner"].container_hostname = "aify-owner"
        run(manager.start_container("sharer"))
        self.assertEqual(manager.fake.run_calls, [])
        self.assertEqual(manager.states["sharer"].container_hostname, "aify-owner")

    def test_sharing_with_an_unknown_target_is_refused(self):
        manager = self._manager({"sharer": definition(shared_with="ghost")})
        with self.assertRaises(ValueError):
            run(manager.start_container("sharer"))

    # ── stop ─────────────────────────────────────────────────────────────────────────────────

    def test_stopping_removes_the_container_frees_the_GPU_and_clears_the_address(self):
        manager = self._manager({"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})})
        self._healthy(manager)
        run(manager.start_container("a"))
        run(manager.stop_container("a"))
        created = manager.fake.created[0]
        self.assertTrue(created.stopped and created.removed)
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {})
        state = manager.states["a"]
        self.assertEqual(state.status, ContainerStatus.STOPPED)
        self.assertIsNone(state.container_id)
        self.assertIsNone(state.container_hostname,
                          "a stale hostname would keep resolve_url pointing at a dead container")

    def test_stopping_marks_everything_SHARING_it_as_stopped_too(self):
        """The sharer has no container of its own; if it kept reading RUNNING, the proxy would keep
        routing to a hostname that no longer resolves."""
        manager = self._manager({
            "owner": definition(),
            "sharer": definition(shared_with="owner"),
        })
        self._healthy(manager)
        run(manager.start_container("sharer"))
        run(manager.stop_container("owner"))
        self.assertEqual(manager.states["sharer"].status, ContainerStatus.STOPPED)

    def test_stopping_something_that_was_never_started_does_nothing(self):
        manager = self._manager({"a": definition()})
        run(manager.stop_container("a"))
        self.assertEqual(manager.states["a"].status, ContainerStatus.DEFINED)

    def test_stopping_an_unknown_container_raises(self):
        manager = self._manager({"a": definition()})
        with self.assertRaises(ValueError):
            run(manager.stop_container("nope"))

    def test_a_container_that_vanished_from_docker_still_stops_cleanly(self):
        """Docker can be restarted under the hub. A NotFound on stop must leave the state correct
        rather than raising and stranding it as RUNNING forever."""
        manager = self._manager({"a": definition()})
        self._healthy(manager)
        run(manager.start_container("a"))
        manager.fake.created.clear()  # docker no longer knows about it
        run(manager.stop_container("a"))
        self.assertEqual(manager.states["a"].status, ContainerStatus.STOPPED)

    # ── reconciliation ───────────────────────────────────────────────────────────────────────

    def test_a_container_already_running_is_ADOPTED_rather_than_duplicated(self):
        """The hub restarts far more often than the model servers it manages. Not adopting them
        means starting a second copy of each — on the same GPU."""
        existing = FakeContainer("aify-a", "cid-existing")
        existing.labels = {"aify.managed": "true", "aify.name": "a"}
        client = FakeDocker(existing=[existing])
        manager = self._manager(
            {"a": definition(gpu={"device_ids": ["0"], "memory_fraction": 0.5})},
            docker_client=client,
        )
        state = manager.states["a"]
        self.assertEqual(state.status, ContainerStatus.RUNNING)
        self.assertEqual(state.container_id, "cid-existing")
        self.assertEqual(state.container_hostname, "aify-a")
        self.assertEqual(manager.gpu.get_status()["0"]["active_containers"], {"a": 0.5},
                         "an adopted container's GPU was not accounted for")

    def test_an_existing_but_STOPPED_container_is_not_adopted_as_running(self):
        existing = FakeContainer("aify-a", "cid-existing")
        existing.status = "exited"
        existing.labels = {"aify.managed": "true", "aify.name": "a"}
        manager = self._manager({"a": definition()}, docker_client=FakeDocker(existing=[existing]))
        self.assertEqual(manager.states["a"].status, ContainerStatus.STOPPED)

    def test_a_foreign_container_is_ignored(self):
        """The label filter is what keeps the hub out of other people's containers."""
        existing = FakeContainer("someone-elses", "cid-x")
        existing.labels = {"aify.managed": "true", "aify.name": "not-ours"}
        manager = self._manager({"a": definition()}, docker_client=FakeDocker(existing=[existing]))
        self.assertEqual(manager.states["a"].status, ContainerStatus.DEFINED)
