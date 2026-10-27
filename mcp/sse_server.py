"""The comms tools over MCP, inside the service container: Streamable HTTP at `/mcp/http`, and the deprecated
SSE at `/mcp/sse` until 0.9.11 slice 5 deletes it.

Every tool is declared in a `service/sse/*_tools.py` module and registered here. They live under `service/`
because `mcp/` is not this repo's package: `import mcp` resolves to the PyPI distribution FastMCP comes from,
which is why `service/main.py` loads this file by path (`service/sse/__init__.py`).

The list is the comms tools only. It is what every connected agent reads into its context, so nothing that
is not a comms tool belongs in it: the container tools a service template carried were removed in 0.9.11,
and the HTTP container API (`service/routers/containers.py`) is unaffected.
"""

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from service.config import get_config
from service.sse import (
    agent_tools, channel_tools, compact_tools, console_tools, environment_tools, inbox_tools, lifecycle_tools,
    management_tools, run_tools, self_record_tools, send_tools, shared_file_tools, usage_tools,
)

logger = logging.getLogger(__name__)

config = get_config()
# STATELESS, JSON (0.9.11): each `/mcp/http` call stands alone, so a service rebuild costs a connected agent
# nothing; a stateful session would answer 404 to every agent after each deploy.
# NO SECOND HOST ALLOW-LIST: FastMCP's default refuses any Host but localhost (421), which locks out an agent
# that reaches this service by another name (WSL, another PC). The Host/Origin guard is the service's own
# CrossSiteBrowserMiddleware (service/main.py, browser_origin.py), which covers /mcp like every route,
# whether or not an API key is set.
mcp_server = FastMCP(
    config.name,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/http",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

#: In the order the tools are listed to an agent. Each module registers through `registration.register_tools`.
TOOL_MODULES = (
    agent_tools, send_tools, run_tools, console_tools, inbox_tools, channel_tools, shared_file_tools,
    management_tools, usage_tools, self_record_tools, lifecycle_tools, environment_tools, compact_tools,
)
for _module in TOOL_MODULES:
    _module.register(mcp_server)


def setup_mcp_server(app):
    """Mount both MCP transports onto the FastAPI app, and return the Streamable HTTP lifetime.

    One mount for both: a mount owns its whole prefix, so a second one under `/mcp` would never be reached.
    The caller enters the returned context for the app's lifetime; Streamable HTTP refuses requests without it.
    """
    cfg = get_config()
    http_app = mcp_server.streamable_http_app()
    app.mount(cfg.mcp_path_prefix, Starlette(routes=[*mcp_server.sse_app().routes, *http_app.routes]))
    logger.info(f"MCP mounted at {cfg.mcp_path_prefix}/http (Streamable HTTP) and {cfg.mcp_path_prefix}/sse")
    return mcp_server.session_manager.run()
