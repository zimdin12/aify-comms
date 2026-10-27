"""The service's comms tools do what the JS tools do, case by case (0.9.11 slice 2).

Each case in `mcp/stdio/tests/tool-replies/cases/` was recorded from the JS tools agents use today
(`mcp/stdio/tests/tool-replies/replay.mjs` explains the format). Here every case runs against the Python
copy over the real `/mcp/http` mount, called as the case's caller by header, with a loopback service that
answers from the case's routes. The Python tool must make the same requests and render the same text.

A case whose tool the Python copy does not serve yet is skipped and names the tool, so the port's progress
is the skip count.
"""

import asyncio
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from service.sse import api_client

REPO = Path(__file__).resolve().parents[2]
CASES_DIR = REPO / "mcp" / "stdio" / "tests" / "tool-replies" / "cases"
SSE_SERVER = REPO / "mcp" / "sse_server.py"
CALLER = "agent-a"  # replay.mjs CALLER


def normalize_path(raw: str) -> str:
    """replay.mjs normalizePath: no /api/v1 prefix, query parameters sorted."""
    parts = urlsplit(raw)
    path = parts.path[len("/api/v1"):] if parts.path.startswith("/api/v1") else parts.path
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return f"{path}?{query}" if query else path


def load_cases():
    cases = []
    for file in sorted(CASES_DIR.glob("*.json")):
        for case in json.loads(file.read_text(encoding="utf-8")):
            cases.append(pytest.param(case, id=f"{file.name}: {case['name']}"))
    return cases


class FakeService:
    def __init__(self):
        self.routes, self.requests = {}, []
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                path = normalize_path(self.path)
                service.requests.append({"method": self.command, "path": path, "body": json.loads(raw) if raw else None})
                key = f"{self.command} {path}"
                answer = service.routes.get(key, {"status": 404, "body": {"detail": f"no fixture route for {key}"}})
                body = json.dumps(answer.get("body", {})).encode("utf-8")
                self.send_response(answer.get("status", 200))
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = do_PATCH = do_PUT = _answer

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture(scope="module")
def service():
    fake = FakeService()
    previous = api_client._BASE_URL
    api_client._BASE_URL = f"{fake.url}/api/v1"
    yield fake
    api_client._BASE_URL = previous
    fake.server.shutdown()


@pytest.fixture
def transport():
    """Loaded per case: FastMCP's session manager runs once per instance, and the service runs it once."""
    spec = importlib.util.spec_from_file_location("sse_server_tool_replies", SSE_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _call(transport, tool, args):
    app = FastAPI()
    async with transport.setup_mcp_server(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8800") as client:
            response = await client.post(
                "/mcp/http",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}},
                headers={"accept": "application/json, text/event-stream", "x-aify-agent-id": CALLER},
            )
    assert response.status_code == 200, response.text
    return json.loads(response.text)["result"]


def test_there_are_cases_to_replay():
    assert load_cases(), f"no fixture cases under {CASES_DIR}"


@pytest.mark.parametrize("case", load_cases())
def test_the_python_tool_matches_the_js(case, service, transport):
    if case["tool"] not in transport.mcp_server._tool_manager._tools:
        pytest.skip(f"{case['tool']} is not served by the Python copy yet")
    service.routes, service.requests = case.get("routes") or {}, []
    result = asyncio.run(_call(transport, case["tool"], case.get("args") or {}))
    text = "\n".join(part.get("text", "") for part in result.get("content", []))
    assert {"requests": service.requests, "text": text, "isError": bool(result.get("isError"))} == {
        "requests": case["requests"], "text": case["text"], "isError": case["isError"],
    }
