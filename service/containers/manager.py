"""
Container Manager - orchestrates sub-container lifecycle.

Manages Docker containers via the Docker SDK, with:
- On-demand start (start when first request arrives)
- Idle timeout (auto-stop after inactivity)
- Health monitoring with optional auto-restart
- GPU allocation tracking
- Shared containers (multiple services can share one container)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import docker
import docker.types
import httpx

from service.config import get_config
from .models import ContainerDefinition, ContainerState, ContainerStatus
from .gpu import GPUAllocator

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


class ContainerManager:
    def __init__(self, definitions: dict[str, ContainerDefinition], defaults: dict[str, Any]):
        self.definitions = definitions
        self.defaults = defaults
        self.states: dict[str, ContainerState] = {}
        self.gpu = GPUAllocator()
        self._locks: dict[str, asyncio.Lock] = {}
        self._reaper_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None

        config = get_config()
        self.project_name = config.custom.get(
            "compose_project_name",
            config.custom.get("compose_project_name", "aify"),
        )
        self.network_name = config.custom.get(
            "network_name", f"{self.project_name}-network"
        )

        # Initialize states and locks
        for name, defn in self.definitions.items():
            self.states[name] = ContainerState(name=name, internal_port=defn.internal_port)
            self._locks[name] = asyncio.Lock()

        # Connect to Docker
        try:
            self.docker = docker.from_env()
            self.docker.ping()
            logger.info("Docker client connected")
        except Exception as e:
            logger.error(f"Docker client failed: {e}")
            self.docker = None

        self._reconcile_existing()

    def _reconcile_existing(self):
        """Check Docker for containers we manage that might already be running."""
        if not self.docker:
            return
        try:
            containers = self.docker.containers.list(
                filters={"label": "aify.managed=true"}, all=True,
            )
            for container in containers:
                name = container.labels.get("aify.name", "")
                if name in self.states:
                    state = self.states[name]
                    if container.status == "running":
                        state.status = ContainerStatus.RUNNING
                        state.container_id = container.id
                        state.container_hostname = container.name
                        state.started_at = _now()
                        state.last_request_at = _now()
                        defn = self.definitions[name]
                        if defn.gpu.device_ids:
                            self.gpu.allocate(name, defn.gpu)
                        logger.info(f"Reconciled running container: {name}")
                    else:
                        state.status = ContainerStatus.STOPPED
        except Exception as e:
            logger.warning(f"Reconciliation failed: {e}")

    def resolve_url(self, name: str) -> str | None:
        """Get the URL for a container, resolving shared_with references."""
        if name not in self.definitions:
            return None
        defn = self.definitions[name]
        if defn.shared_with:
            target_state = self.states.get(defn.shared_with)
            return target_state.internal_url if target_state else None
        state = self.states.get(name)
        return state.internal_url if state else None

    async def start_container(self, name: str) -> ContainerState:
        """Start a container. Handles sharing, GPU allocation, Docker API, health checks."""
        if name not in self.definitions:
            raise ValueError(f"Unknown container: {name}")

        defn = self.definitions[name]

        # If shared, redirect to the target container
        if defn.shared_with:
            target = defn.shared_with
            if target not in self.definitions:
                raise ValueError(f"Container '{name}' shares with unknown '{target}'")
            target_state = self.states.get(target)
            if target_state and target_state.status == ContainerStatus.RUNNING:
                # Update our state to reflect we're "running" via the shared target
                self.states[name].status = ContainerStatus.RUNNING
                self.states[name].container_hostname = target_state.container_hostname
                self.states[name].internal_port = target_state.internal_port
                return self.states[name]
            # Target not running - start it
            result = await self.start_container(target)
            self.states[name].status = ContainerStatus.RUNNING
            self.states[name].container_hostname = result.container_hostname
            self.states[name].internal_port = result.internal_port
            return self.states[name]

        async with self._locks[name]:
            state = self.states[name]

            if state.status == ContainerStatus.RUNNING:
                return state

            if not self.docker:
                state.status = ContainerStatus.FAILED
                state.error_message = "Docker client not available"
                raise RuntimeError("Docker client not available")

            # Check GPU availability
            ok, reason = self.gpu.can_allocate(name, defn.gpu)
            if not ok:
                state.status = ContainerStatus.FAILED
                state.error_message = f"GPU: {reason}"
                raise RuntimeError(f"Cannot start {name}: {reason}")

            state.status = ContainerStatus.STARTING
            state.error_message = None
            container_name = f"{self.project_name}-{name}"

            try:
                # Remove existing stopped container with same name
                try:
                    old = self.docker.containers.get(container_name)
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass

                # Ensure volumes exist
                volumes = {}
                for vol_name, mount_path in defn.volumes.items():
                    try:
                        self.docker.volumes.get(vol_name)
                    except docker.errors.NotFound:
                        self.docker.volumes.create(vol_name)
                    volumes[vol_name] = {"bind": mount_path, "mode": "rw"}

                # GPU device requests
                device_requests = []
                if defn.gpu.device_ids:
                    device_requests.append(
                        docker.types.DeviceRequest(
                            device_ids=defn.gpu.device_ids,
                            capabilities=[["gpu"]],
                        )
                    )

                labels = {
                    "aify.managed": "true",
                    "aify.name": name,
                    "aify.group": defn.group,
                    # Docker Desktop compose grouping
                    "com.docker.compose.project": self.project_name,
                    "com.docker.compose.service": name,
                    "com.docker.compose.container-number": "1",
                    "com.docker.compose.oneoff": "False",
                    **defn.labels,
                }

                container = self.docker.containers.run(
                    image=defn.image,
                    name=container_name,
                    command=defn.command if defn.command else None,
                    detach=True,
                    network=self.network_name,
                    volumes=volumes,
                    environment=dict(defn.environment),
                    labels=labels,
                    device_requests=device_requests if device_requests else None,
                    mem_limit=defn.resources.memory_limit,
                    nano_cpus=int(float(defn.resources.cpu_limit) * 1e9),
                    restart_policy={"Name": "no"},
                )

                state.container_id = container.id
                state.container_hostname = container_name

                if defn.gpu.device_ids:
                    self.gpu.allocate(name, defn.gpu)

                # Wait for health
                healthy = await self._wait_for_health(
                    container_name, defn.internal_port,
                    defn.health_check.endpoint,
                    defn.startup_timeout_seconds,
                    defn.health_check.interval_seconds,
                )

                if healthy:
                    state.status = ContainerStatus.RUNNING
                    state.started_at = _now()
                    state.last_request_at = _now()
                    state.consecutive_health_failures = 0
                    logger.info(f"Container started: {name} -> {container_name}:{defn.internal_port}")
                else:
                    state.status = ContainerStatus.FAILED
                    state.error_message = "Health check timeout"
                    container.stop(timeout=10)
                    container.remove(force=True)
                    self.gpu.release_with_fraction(name, defn.gpu)
                    raise RuntimeError(f"Container {name} failed health check within {defn.startup_timeout_seconds}s")

                return state

            except docker.errors.ImageNotFound:
                state.status = ContainerStatus.FAILED
                state.error_message = f"Image not found: {defn.image}. Run: docker pull {defn.image}"
                self.gpu.release_with_fraction(name, defn.gpu)
                raise
            except asyncio.CancelledError:
                # CancelledError is a BaseException — NOT caught by `except Exception`
                # (bughunt 2026-07-03) — so a cancel during _wait_for_health leaked the
                # running container + GPU allocation, stuck in STARTING and never reaped
                # (the background loops skip non-RUNNING). Release + tear down, then re-raise.
                if state.status == ContainerStatus.STARTING:
                    state.status = ContainerStatus.FAILED
                    state.error_message = "start cancelled"
                self.gpu.release_with_fraction(name, defn.gpu)
                if self.docker and state.container_id:
                    try:
                        c = self.docker.containers.get(state.container_id)
                        c.stop(timeout=5)
                        c.remove(force=True)
                    except Exception:
                        pass
                raise
            except Exception as e:
                if state.status == ContainerStatus.STARTING:
                    state.status = ContainerStatus.FAILED
                    state.error_message = str(e)
                self.gpu.release_with_fraction(name, defn.gpu)
                raise

    async def stop_container(self, name: str, timeout: int = 30):
        """Stop and remove a container."""
        if name not in self.states:
            raise ValueError(f"Unknown container: {name}")

        defn = self.definitions[name]

        if defn.shared_with:
            self.states[name].status = ContainerStatus.STOPPED
            return

        async with self._locks[name]:
            state = self.states[name]
            if state.status not in (ContainerStatus.RUNNING, ContainerStatus.FAILED, ContainerStatus.STARTING):
                return

            state.status = ContainerStatus.STOPPING

            if self.docker and state.container_id:
                try:
                    container = self.docker.containers.get(state.container_id)
                    container.stop(timeout=timeout)
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass
                except Exception as e:
                    logger.error(f"Error stopping {name}: {e}")

            self.gpu.release_with_fraction(name, defn.gpu)
            state.status = ContainerStatus.STOPPED
            state.container_id = None
            state.container_hostname = None

            # Also mark containers sharing this one as stopped
            for other_name, other_defn in self.definitions.items():
                if other_defn.shared_with == name:
                    self.states[other_name].status = ContainerStatus.STOPPED

            logger.info(f"Container stopped: {name}")

    async def restart_container(self, name: str):
        await self.stop_container(name)
        return await self.start_container(name)

    def get_container_logs(self, name: str, tail: int = 100) -> str:
        state = self.states.get(name)
        if not state or not state.container_id or not self.docker:
            return ""
        try:
            container = self.docker.containers.get(state.container_id)
            return container.logs(tail=tail).decode("utf-8", errors="replace")
        except Exception as e:
            return f"Error: {e}"

    async def pull_image(self, name: str) -> str:
        if name not in self.definitions:
            raise ValueError(f"Unknown container: {name}")
        if not self.docker:
            raise RuntimeError("Docker client not available")
        defn = self.definitions[name]
        logger.info(f"Pulling image: {defn.image}")
        loop = asyncio.get_running_loop()
        image = await loop.run_in_executor(None, self.docker.images.pull, defn.image)
        return f"Pulled {image.tags}"

    def list_containers(self) -> dict:
        result = {}
        for name, state in self.states.items():
            defn = self.definitions[name]
            entry = {
                "status": state.status.value,
                "image": defn.image,
                "group": defn.group or "default",
                "gpu_devices": defn.gpu.device_ids,
                "gpu_memory_fraction": defn.gpu.memory_fraction,
                "idle_timeout": defn.idle_timeout_seconds,
                "auto_start": defn.auto_start,
                "internal_url": state.internal_url,
            }
            if defn.shared_with:
                entry["shared_with"] = defn.shared_with
                target_state = self.states.get(defn.shared_with)
                entry["resolved_url"] = target_state.internal_url if target_state else None
            if state.status == ContainerStatus.RUNNING:
                entry["uptime_seconds"] = (_now() - state.started_at).total_seconds() if state.started_at else 0
                entry["idle_seconds"] = state.idle_seconds
            if state.error_message:
                entry["error"] = state.error_message
            result[name] = entry
        return result

    def get_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for name, defn in self.definitions.items():
            groups.setdefault(defn.group or "default", []).append(name)
        return groups

    async def _wait_for_health(
        self, hostname: str, port: int, endpoint: str, timeout: int, interval: int,
    ) -> bool:
        url = f"http://{hostname}:{port}{endpoint}"
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(timeout=5.0) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(interval)
        return False

    async def start_background_tasks(self):
        self._reaper_task = asyncio.create_task(self._idle_reaper_loop())
        self._health_task = asyncio.create_task(self._health_monitor_loop())

        for name, defn in self.definitions.items():
            if defn.auto_start:
                try:
                    await self.start_container(name)
                except Exception as e:
                    logger.error(f"Auto-start failed for {name}: {e}")

    async def stop_background_tasks(self):
        for task in [self._reaper_task, self._health_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _idle_reaper_loop(self):
        while True:
            await asyncio.sleep(30)
            # Snapshot names to avoid dict mutation during iteration
            names = list(self.states.keys())
            for name in names:
                state = self.states.get(name)
                if not state or state.status != ContainerStatus.RUNNING:
                    continue
                defn = self.definitions[name]
                if defn.idle_timeout_seconds == 0 or defn.shared_with:
                    continue
                if state.idle_seconds > defn.idle_timeout_seconds:
                    logger.info(f"Idle reaper: stopping {name} (idle {state.idle_seconds:.0f}s)")
                    try:
                        await self.stop_container(name)
                    except Exception as e:
                        logger.error(f"Idle reaper error for {name}: {e}")

    async def _health_monitor_loop(self):
        while True:
            await asyncio.sleep(10)
            names = list(self.states.keys())
            for name in names:
                state = self.states.get(name)
                if not state or state.status != ContainerStatus.RUNNING:
                    continue
                defn = self.definitions[name]
                if defn.shared_with:
                    continue

                try:
                    url = f"http://{state.container_hostname}:{state.internal_port}{defn.health_check.endpoint}"
                    async with httpx.AsyncClient(timeout=float(defn.health_check.timeout_seconds)) as client:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            state.consecutive_health_failures = 0
                        else:
                            state.consecutive_health_failures += 1
                except Exception:
                    state.consecutive_health_failures += 1

                if state.consecutive_health_failures >= defn.health_check.retries:
                    logger.error(f"Health: {name} failed {state.consecutive_health_failures}x, restarting")
                    state.status = ContainerStatus.FAILED
                    try:
                        await self.restart_container(name)
                    except Exception as e:
                        logger.error(f"Auto-restart failed for {name}: {e}")

    async def shutdown(self):
        await self.stop_background_tasks()
        from .proxy import close_client
        await close_client()


def load_container_definitions(config_data: dict) -> tuple[dict[str, ContainerDefinition], dict]:
    """Parse container definitions from service.json, merging defaults."""
    containers_config = config_data.get("containers", {})
    defaults = containers_config.get("defaults", {})
    definitions_raw = containers_config.get("definitions", {})

    definitions = {}
    for name, raw in definitions_raw.items():
        merged = {**defaults}
        for key, value in raw.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        definitions[name] = ContainerDefinition(**merged)

    # Validate shared_with references
    for name, defn in definitions.items():
        if defn.shared_with and defn.shared_with not in definitions:
            raise ValueError(
                f"Container '{name}' has shared_with='{defn.shared_with}' "
                f"but '{defn.shared_with}' is not defined. "
                f"Available: {list(definitions.keys())}"
            )

    return definitions, defaults
