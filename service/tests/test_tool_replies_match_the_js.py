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
import re
import threading
from datetime import datetime, timedelta, timezone
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


def normalize_body(raw: str, content_type: str = ""):
    """replay.mjs normalizeBody: JSON, or a form as {"form": {...}}; a send's random `clientNonce` is
    recorded as present, not by value."""
    if not raw:
        return None
    if "x-www-form-urlencoded" in content_type.lower():
        return {"form": dict(parse_qsl(raw, keep_blank_values=True))}
    body = json.loads(raw)
    if isinstance(body, dict) and isinstance(body.get("clientNonce"), str) and body["clientNonce"]:
        body["clientNonce"] = "<nonce>"
    return body


def with_times(value, now: datetime | None = None):
    """replay.mjs withTimes: `<now-3h>`-style strings in a route body become times."""
    now = now or datetime.now(timezone.utc)
    if isinstance(value, str):
        match = re.fullmatch(r"<now(?:-(\d+)([smh]))?>", value)
        if not match:
            return value
        ago = timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours"}[match.group(2)]: int(match.group(1))}) if match.group(1) else timedelta()
        return (now - ago).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, list):
        return [with_times(item, now) for item in value]
    if isinstance(value, dict):
        return {k: with_times(v, now) for k, v in value.items()}
    return value


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
                service.requests.append({"method": self.command, "path": path,
                                         "body": normalize_body(raw, self.headers.get("content-type") or "")})
                key = f"{self.command} {path}"
                answer = service.routes.get(key, {"status": 404, "body": {"detail": f"no fixture route for {key}"}})
                # Compact, as FastAPI and replay.mjs serialise it: an error's text carries the body verbatim.
                body = json.dumps(with_times(answer.get("body", {})), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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


def _enum_values(node) -> list:
    """replay.mjs enumValues: every value a schema allows, wherever pydantic put the `enum`."""
    if isinstance(node, dict):
        return list(node.get("enum") or []) + [v for child in node.values() for v in _enum_values(child)]
    if isinstance(node, list):
        return [v for child in node for v in _enum_values(child)]
    return []


def test_the_public_schemas_promise_what_the_js_promises(transport):
    """Required arguments and allowed values, per tool the Python copy serves, the caller's argument aside.

    Argument NAMES are compared by test_both_transports_declare_the_same_tool_surface.py.
    """
    js = json.loads((CASES_DIR.parent / "schemas.json").read_text(encoding="utf-8"))
    caller_args: dict[str, set[str]] = {}
    for param in load_cases():
        case = param.values[0]
        caller_args.setdefault(case["tool"], set()).update(case.get("callerArgs") or [])
    tools = asyncio.run(transport.mcp_server.list_tools())
    compared = []
    for tool in tools:
        if tool.name not in js or tool.name not in caller_args:  # ported tools are the ones with cases
            continue
        dropped = caller_args.get(tool.name, set())
        expected = js[tool.name]
        enums = {name: sorted(set(_enum_values(schema))) for name, schema in (tool.inputSchema.get("properties") or {}).items()}
        actual = {"required": sorted(tool.inputSchema.get("required") or []), "enums": {k: v for k, v in enums.items() if v}}
        assert actual == {"required": sorted(set(expected["required"]) - dropped),
                          "enums": {k: v for k, v in expected["enums"].items() if k not in dropped}}, tool.name
        compared.append(tool.name)
    assert len(compared) >= 13, compared


def test_there_are_cases_to_replay():
    assert load_cases(), f"no fixture cases under {CASES_DIR}"


@pytest.mark.parametrize("case", load_cases())
def test_the_python_tool_matches_the_js(case, service, transport):
    if case["tool"] not in transport.mcp_server._tool_manager._tools:
        pytest.skip(f"{case['tool']} is not served by the Python copy yet")
    # `callerArgs` name the JS arguments that carried the caller; the service reads the caller from the header.
    args = dict(case.get("args") or {})
    for name in case.get("callerArgs") or []:
        assert args.pop(name) == CALLER, f"{name} must be the caller in a case that declares it a caller argument"
    service.routes, service.requests = case.get("routes") or {}, []
    result = asyncio.run(_call(transport, case["tool"], args))
    text = "\n".join(part.get("text", "") for part in result.get("content", []))
    assert {"requests": service.requests, "text": text, "isError": bool(result.get("isError"))} == {
        "requests": case["requests"], "text": case["text"], "isError": case["isError"],
    }
