"""The install gives Claude no machine-wide comms entry; claude-aify adds one per launch.

0.9.11, option A: the service's tools need the caller's agent id in a header, which nothing machine-wide
can hold, so the installer removes the `aify-comms` entry at every scope and adds none. A plain `claude` then
has no comms tools. Codex keeps its local tool process until docs/CODEX_CATCHUP.md, so it still gets the
bridge: that is the control. Executes the installer's own `register_stdio_server` against a recording CLI.
"""
import subprocess
from pathlib import Path

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]


def calls_of(tmp_path, cli):
    source = (REPO / "install.sh").read_text(encoding="utf-8")
    start = source.index("register_stdio_server() {")
    body = source[start:source.index("\n}\n", start) + 3]
    log = tmp_path / f"{cli}.log"
    script = tmp_path / f"run-{cli}.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'{cli}() {{ echo "$*" >> "{log.as_posix()}"; }}\n'
        "aify_api_key() { echo fixture-key; }\n"
        'path_for_node() { printf "%s" "$1"; }\n'
        "install_codex_mcp_env_vars() { :; }\n"
        + body + "SERVER_URL=http://fixture.invalid:8800 AIFY_BRIDGE_DIR=/bridge\n"
        f"register_stdio_server {cli}\n",
        encoding="utf-8", newline="\n",
    )
    result = subprocess.run([bash(), script.as_posix()], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return log.read_text(encoding="utf-8").splitlines()


def test_claude_loses_the_user_scope_entry_and_gets_none(tmp_path):
    calls = calls_of(tmp_path, "claude")
    assert "mcp remove --scope user aify-comms" in calls, calls
    assert not [c for c in calls if c.startswith("mcp add")], calls


def test_control_codex_still_gets_the_bridge(tmp_path):
    adds = [c for c in calls_of(tmp_path, "codex") if c.startswith("mcp add aify-comms")]
    assert len(adds) == 1 and adds[0].endswith("-- node /bridge/server.js"), adds
