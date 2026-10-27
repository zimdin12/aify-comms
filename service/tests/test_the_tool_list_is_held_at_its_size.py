"""The comms tool list every connected agent reads is held at its measured size (0.9.11 slice 2).

`tools/list` goes into the context of every agent on every session, so its bytes are paid by the whole
fleet (operator, 2026-10-26: the stack must not put excess context in front of agents). A ratchet, as the
skills have: a list that grows fails, and one that shrinks must lower CEILING in the same commit, so the
saving is kept rather than spent by the next change. Raising it is a decision argued in the commit.

Measured as the UTF-8 bytes of compact JSON of what FastMCP lists. The JS tools, listed through
`mcp/stdio/tests/tool-replies/replay.mjs` and measured the same way, were 34 tools in 24,533 bytes on
2026-10-27.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

SSE_SERVER = Path(__file__).resolve().parents[2] / "mcp" / "sse_server.py"

#: Bytes, measured. Lower it when the list shrinks; the failure message gives the number.
CEILING = 18580
#: How far below CEILING the list may sit before the ceiling must come down to keep the saving.
SLACK = 100


def _fresh_server():
    spec = importlib.util.spec_from_file_location("sse_server_size_check", SSE_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.mcp_server


def _tool_list_bytes() -> tuple[int, int]:
    tools = asyncio.run(_fresh_server().list_tools())
    listed = [tool.model_dump(mode="json", exclude_none=True, by_alias=True) for tool in tools]
    return len(listed), len(json.dumps(listed, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def test_the_tool_list_is_held_at_its_size():
    count, size = _tool_list_bytes()
    assert count >= 30, f"only {count} tools listed; the measurement would be about the wrong thing"
    assert size <= CEILING, f"tools/list grew to {size} bytes over its ceiling of {CEILING}: pay for new bytes elsewhere"
    assert size > CEILING - SLACK, f"tools/list shrank to {size} bytes: lower CEILING to {size} to keep the saving"


def test_an_argument_published_without_null_is_one_an_agent_can_leave_out():
    """The published schema refuses an explicit null the service accepts. That is safe only where null means
    "not given": the argument is optional, and null is its default. A validating client then omits it, with
    the same result. The JS tools publish the same schema and refuse null outright (zod `.optional()`)."""
    folded = []
    for tool in _fresh_server()._tool_manager.list_tools():
        accepted = tool.fn_metadata.arg_model.model_json_schema(by_alias=True)
        for name, prop in accepted.get("properties", {}).items():
            if _NULL_TYPE in prop.get("anyOf", []) and _NULL_TYPE not in tool.parameters["properties"][name].get("anyOf", []):
                folded.append(name)
                assert name not in accepted.get("required", []), f"{tool.name}.{name}: null folded away from a required argument"
                assert "default" in prop and prop["default"] is None, f"{tool.name}.{name}: null is not its default, so omitting it differs"
    assert folded, "no nullable argument was folded; the check would pass on anything"


_NULL_TYPE = {"type": "null"}


def test_the_published_schema_drops_titles_and_the_null_branch_and_keeps_the_rest():
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
