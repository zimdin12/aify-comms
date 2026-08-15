"""Every `comms_*` tool the SSE transport DECLARES must actually be on the server.

WHY THIS IS A SEPARATE CLAIM NOW. Until v0.5.4 declaring an SSE tool and registering it were one act
— `@mcp_server.tool()` sat directly above `async def`, so reading the source WAS reading the
inventory, and `transport-parity.test.js` scanning that source was sound. Extracted tool groups
cannot decorate: the module is imported BY the transport, so importing the server back would be a
cycle. They declare bare and a `register(mcp_server)` call applies the decorator. Declaration and
registration are now two steps, and only the first is visible to a source scan.

The gap that opens is silent in the worst way. A group that is declared and never registered reads
as present to every source-scanning test, ships, and simply does not exist for any SSE client — no
error, no log line, nothing that fails. The tool is just absent, and the file that should contain it
looks complete.

JS cannot close it, so this does: the transport is loaded and FastMCP is asked what it holds. That
is the same question an MCP client asks over the wire, answered by the same object.

Loading the transport by path is not a workaround — it is how `service/main.py` mounts it, because
`mcp/` is the PyPI package rather than this repo's directory. See `service/sse/__init__.py`.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRANSPORT = REPO / "mcp" / "sse_server.py"
SSE_PACKAGE = REPO / "service" / "sse"


def _load_transport():
    spec = importlib.util.spec_from_file_location("sse_server_registration_check", TRANSPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registered_names() -> set[str]:
    return {tool.name for tool in asyncio.run(_load_transport().mcp_server.list_tools())}


def _declared_names() -> set[str]:
    """Top-level `async def comms_*` across the transport and its extracted tool modules."""
    names: set[str] = set()
    for path in [TRANSPORT, *sorted(SSE_PACKAGE.glob("*.py"))]:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("comms_"):
                names.add(node.name)
    return names


class SseToolsAreRegisteredTests(unittest.TestCase):
    def test_every_declared_comms_tool_reaches_the_server(self):
        declared, registered = _declared_names(), _registered_names()
        missing = sorted(declared - registered)
        self.assertEqual(
            [],
            missing,
            "declared in an SSE module and NOT registered on the server. An extracted group needs "
            "its `register(mcp_server)` called from mcp/sse_server.py, and the tool added to that "
            f"module's TOOLS tuple. Invisible to every source scan: {missing}",
        )

    def test_the_server_holds_nothing_that_no_module_declares(self):
        """The reverse: a name reaching clients that no file in the tree accounts for."""
        registered = {n for n in _registered_names() if n.startswith("comms_")}
        self.assertEqual([], sorted(registered - _declared_names()))

    def test_the_two_scans_are_not_both_empty(self):
        """Either side returning nothing would make the comparison above pass on no evidence."""
        self.assertGreaterEqual(len(_declared_names()), 15)
        self.assertGreaterEqual(len(_registered_names()), 20)

    def test_an_extracted_group_is_genuinely_covered(self):
        """Anti-vacuity with teeth: name a tool that is declared OUTSIDE the transport.

        If every tool moved back into `sse_server.py`, the assertions above would still pass while
        testing nothing this file was written for. `comms_channel_send` is declared in
        `service/sse/channel_tools.py` and registered by its registrar, which is exactly the
        two-step path that has no source-visible proof.
        """
        transport_declared = {
            node.name for node in ast.parse(TRANSPORT.read_text(encoding="utf-8")).body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        self.assertNotIn("comms_channel_send", transport_declared, "it moved back; re-aim this test")
        self.assertIn("comms_channel_send", _registered_names())

    def test_the_tool_keeps_its_schema_and_description_through_the_registrar(self):
        """A registered name is not enough — an agent picks a tool by its description and arguments.

        `mcp_server.tool()` builds the schema from the signature and docstring at REGISTRATION time,
        so applying it later than the declaration is where that could quietly change.
        """
        tool = next(t for t in asyncio.run(_load_transport().mcp_server.list_tools())
                    if t.name == "comms_channel_send")
        self.assertEqual(
            ["body", "channel", "from_agent", "priority", "queueIfBusy", "silent", "steer", "type"],
            sorted(tool.inputSchema.get("properties", {})),
        )
        self.assertIn("live-gated message to a channel", tool.description or "")


if __name__ == "__main__":
    unittest.main()
