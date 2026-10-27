"""An installed hermes entry is replaced whatever its key spelling, as a YAML reader sees the result.

Review of switch 1ed207d2: the writer matched only a plain `  aify-comms:`. A quoted `"aify-comms":` was left
beside the new block, and PyYAML kept the old stdio `command: node`, so that hermes stayed on a local bridge.
Each case runs the real `configWithAifyEntry` under node and reads its output with PyYAML.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
MODULE = (REPO / "scripts" / "hermes-mcp-config.mjs").as_uri()
STDIO = "    command: node\n    args: [legacy/server.js]\n"


def patched(text: str) -> dict:
    script = (f"const m = await import({json.dumps(MODULE)});"
              f"process.stdout.write(m.configWithAifyEntry({json.dumps(text)},"
              ' {serverUrl: "http://h:8800", apiKey: "k", transport: "http"}));')
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=True).stdout
    return yaml.safe_load(out)


@pytest.mark.parametrize("key", ['aify-comms:', '"aify-comms":', "'aify-comms':", "aify-comms:  # ours"])
@pytest.mark.parametrize("section", ["mcp_servers:", '"mcp_servers":'])
def test_the_old_entry_is_replaced_not_kept_beside_the_new_one(key, section):
    servers = patched(f"{section}\n  {key}\n{STDIO}  other:\n    url: u\n")["mcp_servers"]
    assert servers["aify-comms"] == {"url": "http://h:8800/mcp/http",
                                     "headers": {"X-Aify-Agent-Id": "${AIFY_AGENT_ID}", "X-API-Key": "k"}}
    assert servers["other"] == {"url": "u"}, "a neighbour must survive"


def test_a_quoted_leftover_under_plugins_is_removed_and_the_plugin_setting_kept():
    config = patched('plugins:\n  entries:\n    aify-comms:\n      allow_tool_override: false\n'
                     f'  "aify-comms":\n{STDIO}mcp_servers:\n  other:\n    url: u\n')
    assert "aify-comms" not in config["plugins"], "the stdio leftover under plugins must go"
    assert config["plugins"]["entries"]["aify-comms"] == {"allow_tool_override": False}
    assert config["mcp_servers"]["aify-comms"]["url"] == "http://h:8800/mcp/http"
