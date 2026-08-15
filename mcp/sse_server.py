"""
MCP Server - SSE Transport (runs inside the Docker container)

This MCP server is mounted into the FastAPI app and accessible via SSE at:
  http://<host>:<port>/mcp/sse

AI agents building on this template should:
1. Register tools that expose the service's core functionality
2. Each tool should be self-documenting with clear descriptions
3. Tools should handle errors gracefully and return helpful messages

The tools registered here become available to any MCP-compatible client
(Claude Code, OpenClaw, Cursor, etc.)
"""

import logging
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP

from service.config import get_config
# Every comms_* tool now lives in one of these modules and is REGISTERED here rather than declared
# here. The transport keeps the server object, the two service tools that read container state, and
# the mount — nothing that renders a message.
#
# They live under `service/` rather than beside this file because `mcp/` is NOT this repo's package:
# `import mcp` resolves to the PyPI distribution the line above imports FastMCP from, which is why
# `service/main.py` loads THIS file by path instead of importing it. See `service/sse/__init__.py`.
#
# `_api` was imported here until the last tool that called it left. It is gone rather than kept "for
# convenience": an unused re-export is what makes a stale patch target look valid, and a test that
# swapped THIS module's `_api` would have intercepted nothing while appearing to work.
from service.sse.agent_tools import register as _register_agent_tools
from service.sse.channel_tools import register as _register_channel_tools
from service.sse.console_tools import register as _register_console_tools
from service.sse.container_tools import (
    bind_app as _bind_container_app,
    get_manager as _get_manager,
    register as _register_container_tools,
)
from service.sse.inbox_tools import register as _register_inbox_tools
from service.sse.management_tools import register as _register_management_tools
from service.sse.run_tools import register as _register_run_tools
from service.sse.send_tools import register as _register_send_tools
from service.sse.shared_file_tools import register as _register_shared_file_tools

logger = logging.getLogger(__name__)

# Context variables for per-request user/client tracking
user_id_var: ContextVar[str] = ContextVar("user_id", default="default")
client_name_var: ContextVar[str] = ContextVar("client_name", default="unknown")

# Create MCP server instance
config = get_config()
mcp_server = FastMCP(config.name)

# ---------------------------------------------------------------------------
# Service Tools
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def service_info() -> dict:
    """Get information about this service, its capabilities, managed containers, and available tools."""
    cfg = get_config()
    result = {
        "name": cfg.name,
        "version": cfg.version,
        "description": cfg.description,
        "status": "running",
    }
    manager = _get_manager()
    if manager:
        result["containers"] = manager.list_containers()
        result["groups"] = manager.get_groups()
    return result


@mcp_server.tool()
async def service_health() -> dict:
    """Check if the service and its dependencies are healthy."""
    checks = {}
    manager = _get_manager()
    if manager:
        checks["docker"] = "connected" if manager.docker else "unavailable"
        checks["containers"] = {
            name: state.status.value
            for name, state in manager.states.items()
        }
    return {"status": "healthy", "checks": checks}


# ---------------------------------------------------------------------------
# Container Management Tools — declared in service/sse/container_tools.py, registered here so they
# land on this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_container_tools(mcp_server)


# ---------------------------------------------------------------------------
# Registration + presence — declared in service/sse/agent_tools.py.
# ---------------------------------------------------------------------------

_register_agent_tools(mcp_server)


# ---------------------------------------------------------------------------
# Send + dispatch — declared in service/sse/send_tools.py, registered here so they land on this
# server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_send_tools(mcp_server)

# NOTE (2026-05-31): comms_run_steer was REMOVED here to match the canonical
# stdio bridge (mcp/stdio/server.js), which retired it — ordinary comms_send to
# the target steers automatically when the target is busy and steer-capable.
# This SSE transport is intentionally a REDUCED tool surface vs stdio (it omits
# lifecycle/contract/inbox-management tools like comms_spawn / comms_compact /
# comms_contracts / comms_agent_info / comms_remove_agent / comms_delete_session);
# use the stdio bridge for the full tool set.


# ---------------------------------------------------------------------------
# Run status + interrupt — declared in service/sse/run_tools.py. They were never adjacent here:
# comms_run_status stood above the console pair and comms_run_interrupt below it, which is how one
# subject reads as two unrelated tools.
# ---------------------------------------------------------------------------

_register_run_tools(mcp_server)


# ---------------------------------------------------------------------------
# Console read + input — declared in service/sse/console_tools.py, registered here so they land on
# this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_console_tools(mcp_server)


# ---------------------------------------------------------------------------
# Inbox + Search — declared in service/sse/inbox_tools.py, registered here so they land on
# this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_inbox_tools(mcp_server)


# ---------------------------------------------------------------------------
# Channel Tools — declared in service/sse/channel_tools.py, registered here so they land on this
# server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_channel_tools(mcp_server)


# ---------------------------------------------------------------------------
# File Sharing Tools — declared in service/sse/shared_file_tools.py, registered here so they
# land on this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_shared_file_tools(mcp_server)


# ---------------------------------------------------------------------------
# Management Tools — declared in service/sse/management_tools.py, registered here so they
# land on this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_management_tools(mcp_server)


def setup_mcp_server(app):
    """Mount the MCP server onto the FastAPI app."""
    _bind_container_app(app)
    cfg = get_config()

    # Get the SSE app from FastMCP
    sse_app = mcp_server.sse_app()

    # Mount under the configured prefix
    app.mount(cfg.mcp_path_prefix, sse_app)

    logger.info(
        f"MCP SSE server mounted at {cfg.mcp_path_prefix}/ "
        f"- Connect at {cfg.mcp_path_prefix}/sse"
    )
