import asyncio
"""
Container management and routing endpoints.

/api/v1/containers/* - Manage sub-containers (start, stop, status, logs)
/route/{name}/*      - Proxy requests to running sub-containers
/api/v1/gpu          - GPU allocation status
"""

from fastapi import APIRouter, Request, HTTPException

from service.containers.models import ContainerStatus
from service.containers.proxy import proxy_request

router = APIRouter(tags=["containers"])


def _get_manager(request: Request):
    manager = getattr(request.app.state, "container_manager", None)
    if manager is None:
        raise HTTPException(503, "Container manager not initialized")
    return manager


# ---------------------------------------------------------------------------
# Container Management
# ---------------------------------------------------------------------------

@router.get("/api/v1/containers")
async def list_containers(request: Request):
    """List all managed containers and their status."""
    manager = _get_manager(request)
    return {
        "containers": manager.list_containers(),
        "groups": manager.get_groups(),
    }


@router.get("/api/v1/containers/{name}")
async def get_container(name: str, request: Request):
    """Get status of a specific container."""
    manager = _get_manager(request)
    containers = manager.list_containers()
    if name not in containers:
        raise HTTPException(404, f"Container '{name}' not defined")
    return containers[name]


@router.post("/api/v1/containers/{name}/start")
async def start_container(name: str, request: Request):
    """Start a container. If shared, starts the target container."""
    manager = _get_manager(request)
    if name not in manager.definitions:
        raise HTTPException(404, f"Container '{name}' not defined")
    try:
        state = await manager.start_container(name)
        return {
            "status": state.status.value,
            "url": state.internal_url,
            "container_id": state.container_id,
        }
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@router.post("/api/v1/containers/{name}/stop")
async def stop_container(name: str, request: Request):
    """Stop a container gracefully."""
    manager = _get_manager(request)
    if name not in manager.definitions:
        raise HTTPException(404, f"Container '{name}' not defined")
    await manager.stop_container(name)
    return {"status": "stopped"}


@router.post("/api/v1/containers/{name}/restart")
async def restart_container(name: str, request: Request):
    """Restart a container."""
    manager = _get_manager(request)
    if name not in manager.definitions:
        raise HTTPException(404, f"Container '{name}' not defined")
    state = await manager.restart_container(name)
    return {"status": state.status.value, "url": state.internal_url}


@router.get("/api/v1/containers/{name}/logs")
async def get_logs(name: str, request: Request, tail: int = 100):
    """Get container logs."""
    manager = _get_manager(request)
    if name not in manager.definitions:
        raise HTTPException(404, f"Container '{name}' not defined")
    logs = await asyncio.to_thread(manager.get_container_logs, name, tail=tail)  # docker SDK blocks
    return {"logs": logs}


@router.post("/api/v1/containers/{name}/pull")
async def pull_image(name: str, request: Request):
    """Pre-pull the Docker image for a container."""
    manager = _get_manager(request)
    if name not in manager.definitions:
        raise HTTPException(404, f"Container '{name}' not defined")
    try:
        result = await manager.pull_image(name)
        return {"result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# GPU Status
# ---------------------------------------------------------------------------

@router.get("/api/v1/gpu")
async def gpu_status(request: Request):
    """Get GPU device allocation status."""
    manager = _get_manager(request)
    return {"devices": manager.gpu.get_status()}


# ---------------------------------------------------------------------------
# Request Proxy / Router
# ---------------------------------------------------------------------------

@router.api_route(
    "/route/{name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def route_request(name: str, path: str, request: Request):
    """
    Proxy request to a sub-container.
    Starts the container on first request if not running.
    Supports streaming responses (SSE, chunked) for LLM inference.
    """
    manager = _get_manager(request)

    if name not in manager.definitions:
        raise HTTPException(404, f"Container '{name}' not defined")

    defn = manager.definitions[name]
    state = manager.states[name]

    # Resolve shared containers
    actual_name = defn.shared_with if defn.shared_with else name
    actual_state = manager.states.get(actual_name, state)

    # Start on first request
    if actual_state.status in (ContainerStatus.DEFINED, ContainerStatus.STOPPED, ContainerStatus.FAILED):
        try:
            actual_state = await manager.start_container(actual_name)
        except RuntimeError as e:
            raise HTTPException(503, detail=str(e))

    if actual_state.status == ContainerStatus.STARTING:
        raise HTTPException(
            503,
            detail=f"Container '{actual_name}' is starting, retry shortly",
            headers={"Retry-After": "5"},
        )

    if actual_state.status != ContainerStatus.RUNNING:
        raise HTTPException(503, f"Container '{actual_name}' is {actual_state.status.value}")

    # Update idle tracker
    from datetime import datetime, timezone
    actual_state.last_request_at = datetime.now(timezone.utc)

    # Proxy the request
    target_url = f"{actual_state.internal_url}/{path}"
    return await proxy_request(request, target_url)
