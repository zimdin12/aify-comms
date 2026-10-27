"""`install.sh --mcp-transport http` gives Claude no machine-wide comms entry; claude-aify adds one per launch.

0.9.11 slice 4, option A: the service's tools need the caller's agent id in a header, which nothing machine-wide
can hold, so the installer removes the user-scope `aify-comms` entry and adds none. A plain `claude` then has no
comms tools. Executes the installer's own `register_stdio_server` against a recording `claude`.
"""
import subprocess
from pathlib import Path

import pytest

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]


def claude_calls(tmp_path, transport):
    source = (REPO / "install.sh").read_text(encoding="utf-8")
    start = source.index("register_stdio_server() {")
    body = source[start:source.index("\n}\n", start) + 3]
    log = tmp_path / f"claude-{transport}.log"
    script = tmp_path / f"run-{transport}.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'claude() {{ echo "$*" >> "{log.as_posix()}"; }}\n'
        "aify_api_key() { echo fixture-key; }\n"
        'path_for_node() { printf "%s" "$1"; }\n'
        + body + f'MCP_TRANSPORT={transport} SERVER_URL=http://fixture.invalid:8800 AIFY_BRIDGE_DIR=/bridge\n'
        "register_stdio_server claude\n",
        encoding="utf-8", newline="\n",
    )
    result = subprocess.run([bash(), script.as_posix()], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return log.read_text(encoding="utf-8").splitlines()


def test_http_removes_the_user_scope_entry_and_adds_none(tmp_path):
    calls = claude_calls(tmp_path, "http")
    assert "mcp remove --scope user aify-comms" in calls, calls
    assert not [c for c in calls if c.startswith("mcp add")], calls


def test_control_stdio_still_adds_the_bridge(tmp_path):
    adds = [c for c in claude_calls(tmp_path, "stdio") if c.startswith("mcp add aify-comms --scope user")]
    assert len(adds) == 1 and adds[0].endswith("-- node /bridge/server.js"), adds
