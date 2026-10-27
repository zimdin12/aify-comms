"""`comms_share_file` and `comms_save_file` in hermes (0.9.11).

On an HTTP-tools launch (`AIFY_COMMS_MCP=http`, exported by hermes-aify) hermes takes its comms tools from the
service, which cannot read or write a file on the agent's disk, and no comms process runs inside hermes. These two
run on the agent's machine through the bridge's `shared-file-cli.mjs`: the code the Claude channel serves the same
two tools with, its endpoint and its key. Hermes agents shared 49 zip files by path in the 14 days before the switch.

A stdio launch has `comms_share` with a file path from its own bridge, so these are not registered there.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

#: The bridge copy beside this package: <root>/integrations/hermes-aify-plugin/aify_hermes_plugin -> <root>/mcp/stdio.
CLI = Path(__file__).resolve().parents[3] / "mcp" / "stdio" / "shared-file-cli.mjs"
TOOLSET = "aify-comms-files"
#: An upload of a large file over loopback; the service bounds the size.
TIMEOUT_S = 300

_schemas: list[dict] | None = None


def holds_file_tools(env=os.environ) -> bool:
    return env.get("AIFY_COMMS_MCP", "").strip().lower() == "http"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not on PATH")
    return subprocess.run([node, str(CLI), *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=TIMEOUT_S)


def _handler(name: str):
    def handle(args: dict, **_kw) -> str:
        try:
            result = _run([name, json.dumps(args or {})])
        except Exception as exc:  # a tool reply, never a crash of the agent's turn
            return f"{name} could not run: {exc}"
        return (result.stdout or result.stderr).strip()
    return handle


def register(ctx) -> None:  # noqa: ANN001 - hermes PluginContext
    """Register both tools on `ctx`, on every context as hermes keeps tools per plugin manager."""
    global _schemas
    if ctx is None or not holds_file_tools():
        return
    if _schemas is None:
        _schemas = json.loads(_run(["--list"]).stdout)
    for schema in _schemas:
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=_handler(schema["name"]),
                          description=schema["description"])
