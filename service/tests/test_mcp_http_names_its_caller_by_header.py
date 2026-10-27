"""`/mcp/http` serves the comms tools, and a tool learns its caller from the `X-Aify-Agent-Id` header.

0.9.11 slice 2: one service process serves every agent's tools, so the caller can no longer come from the
process environment. These drive real MCP requests through the mount `setup_mcp_server` builds, with the
session-manager lifetime the service enters, rather than calling tool functions directly: what is under
test is the path a header takes from an HTTP request to the tool.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from mcp.server.fastmcp import Context

from service.sse.caller import caller_id

SSE_SERVER = Path(__file__).resolve().parents[2] / "mcp" / "sse_server.py"
ACCEPT = "application/json, text/event-stream"


def _load_transport():
    spec = importlib.util.spec_from_file_location("sse_server_http_check", SSE_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _call(module, payload, headers):
    app = FastAPI()
    async with module.setup_mcp_server(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8800") as client:
            response = await client.post("/mcp/http", json=payload, headers={"accept": ACCEPT, **headers})
    return response


def _tool_call(name):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}


@pytest.fixture
def transport():
    module = _load_transport()

    async def whoami(ctx: Context) -> str:
        return caller_id(ctx)

    module.mcp_server.tool(name="test_whoami")(whoami)
    return module


def _text(response):
    assert response.status_code == 200, response.text
    result = json.loads(response.text)["result"]
    return result.get("isError", False), result["content"][0]["text"]


def test_a_tool_reads_its_caller_from_the_header(transport):
    response = asyncio.run(_call(transport, _tool_call("test_whoami"), {"x-aify-agent-id": "agent-a"}))
    assert _text(response) == (False, "agent-a")


def test_a_call_without_the_header_is_refused_not_guessed(transport):
    is_error, text = _text(asyncio.run(_call(transport, _tool_call("test_whoami"), {})))
    assert is_error and "x-aify-agent-id" in text, text


@pytest.mark.parametrize("unexpanded", ["${AIFY_AGENT_ID}", "${env:AIFY_AGENT_ID}"])
def test_an_unexpanded_header_is_refused_not_taken_as_an_agent(transport, unexpanded):
    """A hermes no launcher gave an id sends its installed entry's `${AIFY_AGENT_ID}` as written."""
    is_error, text = _text(asyncio.run(_call(transport, _tool_call("test_whoami"), {"x-aify-agent-id": unexpanded})))
    assert is_error and "unexpanded" in text, text


def test_a_host_other_than_localhost_is_served(transport):
    """FastMCP's default answers 421 to any Host but localhost; an agent on WSL or another PC uses another name."""
    response = asyncio.run(_call(
        transport, _tool_call("test_whoami"), {"x-aify-agent-id": "agent-a", "host": "host.docker.internal:8800"},
    ))
    assert _text(response) == (False, "agent-a")


def test_the_comms_tools_are_listed(transport):
    response = asyncio.run(_call(transport, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {}))
    assert response.status_code == 200, response.text
    names = {tool["name"] for tool in json.loads(response.text)["result"]["tools"]}
    assert {"comms_send", "comms_inbox", "test_whoami"} <= names, sorted(names)
