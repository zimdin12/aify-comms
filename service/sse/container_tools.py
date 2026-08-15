"""The five Docker sub-container tools the SSE transport exposes, and the app reference they read.

Extracted from `mcp/sse_server.py` in v0.5.4. A whole SUBJECT, not a layer: these five are about
managed sub-containers — start, stop, list, logs, GPU allocation — and share nothing with the
`comms_*` tools around them except the server they were registered on. They were the only readers of
the module-global `_app` besides the two service tools, so that state comes with them and now has an
owner instead of sitting at the top of a 730-line registry.

REGISTRATION IS SEPARATE FROM DECLARATION, and that is what makes this move provable. The functions
are declared bare here and `register(mcp_server)` applies `mcp_server.tool()` to each, so every body
below sits at the same indentation it had in the transport and is byte-identical to it. Wrapping
them in a registrar function instead — the shape that first suggests itself — would have re-indented
five bodies, and a diff where every line moved is a diff nobody can check.

Importing `mcp_server` from the transport was the other option and it is not available: this module
is imported BY the transport, so reaching back would be a cycle, and `mcp/` is not importable by name
anyway (see this package's `__init__.py`).

`bind_app` is called by `setup_mcp_server`. Until it is, `get_manager()` returns None and every tool
answers "No container manager configured" — the same behaviour as before, when `_app` was None.
"""

from __future__ import annotations

_app = None


def bind_app(app) -> None:
    """Hand this module the FastAPI app whose state carries the container manager."""
    global _app
    _app = app


def get_manager():
    """Get container manager from app state."""
    if _app is None:
        return None
    return getattr(_app.state, "container_manager", None)


async def list_containers() -> dict:
    """List all managed sub-containers, their status, GPU allocation, and URLs."""
    manager = get_manager()
    if not manager:
        return {"error": "No container manager configured"}
    return {
        "containers": manager.list_containers(),
        "groups": manager.get_groups(),
    }


async def start_container(name: str) -> dict:
    """
    Start a managed sub-container by name. If already running, returns current state.
    If the container is shared with another, starts the target container instead.
    """
    manager = get_manager()
    if not manager:
        return {"error": "No container manager configured"}
    if name not in manager.definitions:
        return {"error": f"Unknown container: {name}", "available": list(manager.definitions.keys())}
    try:
        state = await manager.start_container(name)
        return {"status": state.status.value, "url": state.internal_url}
    except Exception as e:
        return {"error": str(e)}


async def stop_container(name: str) -> dict:
    """Stop a running sub-container by name."""
    manager = get_manager()
    if not manager:
        return {"error": "No container manager configured"}
    if name not in manager.definitions:
        return {"error": f"Unknown container: {name}"}
    try:
        await manager.stop_container(name)
        return {"status": "stopped", "name": name}
    except Exception as e:
        return {"error": str(e)}


async def gpu_status() -> dict:
    """Get GPU device allocation status showing which containers are using which GPUs."""
    manager = get_manager()
    if not manager:
        return {"error": "No container manager configured"}
    return manager.gpu.get_status()


async def container_logs(name: str, tail: int = 50) -> str:
    """Get recent logs from a managed sub-container."""
    manager = get_manager()
    if not manager:
        return "No container manager configured"
    if name not in manager.definitions:
        return f"Unknown container: {name}"
    return manager.get_container_logs(name, tail=tail)


#: The tools this module registers, in the order they were declared in the transport. Named
#: explicitly rather than swept out of `globals()`: a sweep would silently pick up any future helper
#: that happens to be a coroutine, and registering an internal function as an agent-callable tool is
#: not a mistake anything downstream would report.
TOOLS = (list_containers, start_container, stop_container, gpu_status, container_logs)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool. Called once, where the declarations used to be."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
