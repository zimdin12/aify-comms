"""The comms tool list every connected agent reads is held at its measured size (0.9.11 slice 2).

`tools/list` goes into the context of every agent on every session, so its bytes are paid by the whole
fleet (operator, 2026-10-26: the stack must not put excess context in front of agents). A ratchet, as the
skills have: a list that grows fails, and one that shrinks must lower CEILING in the same commit, so the
saving is kept rather than spent by the next change. Raising it is a decision argued in the commit.

Measured as compact JSON of what FastMCP lists, the way `mcp/stdio/tests/tool-replies/replay.mjs` measures
the JS tools (34 tools, 24,525 bytes on 2026-10-27).
"""

import asyncio
import importlib.util
import json
from pathlib import Path

SSE_SERVER = Path(__file__).resolve().parents[2] / "mcp" / "sse_server.py"

#: Bytes, measured. Lower it when the list shrinks; the failure message gives the number.
CEILING = 18695
#: How far below CEILING the list may sit before the ceiling must come down to keep the saving.
SLACK = 100


def _tool_list_bytes() -> tuple[int, int]:
    spec = importlib.util.spec_from_file_location("sse_server_size_check", SSE_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = asyncio.run(module.mcp_server.list_tools())
    listed = [tool.model_dump(mode="json", exclude_none=True, by_alias=True) for tool in tools]
    return len(listed), len(json.dumps(listed, separators=(",", ":"), ensure_ascii=False))


def test_the_tool_list_is_held_at_its_size():
    count, size = _tool_list_bytes()
    assert count >= 30, f"only {count} tools listed; the measurement would be about the wrong thing"
    assert size <= CEILING, f"tools/list grew to {size} bytes over its ceiling of {CEILING}: pay for new bytes elsewhere"
    assert size > CEILING - SLACK, f"tools/list shrank to {size} bytes: lower CEILING to {size} to keep the saving"


def test_the_published_schema_drops_only_what_an_agent_cannot_use():
    """compact_schema: titles and `anyOf: [T, null]` / `default: null` go; a real union and a real default stay."""
    from service.sse.registration import compact_schema

    schema = {"title": "Args", "type": "object", "required": ["a"], "properties": {
        "a": {"title": "A", "type": "string", "description": "kept"},
        "b": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "default": None, "title": "B"},
        "c": {"anyOf": [{"type": "string"}, {"type": "integer"}], "title": "C"},
        "d": {"type": "boolean", "default": True, "title": "D"},
        "title": {"type": "string", "title": "Title"},
    }}
    assert compact_schema(schema) == {"type": "object", "required": ["a"], "properties": {
        "title": {"type": "string"},
        "a": {"type": "string", "description": "kept"},
        "b": {"type": "integer", "minimum": 1},
        "c": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        "d": {"type": "boolean", "default": True},
    }}
