"""The plugin manifest declares the same identity the installer registers.

`.claude-plugin/plugin.json` called this project "forked from aify-comms", pointed its homepage at
https://github.com/zimdin12/aify-agents-bridge (which returns 404), and `.claude-plugin/.mcp.json`
registered the MCP server under that same dead name. install.sh registers it as `aify-comms`, so the
two install paths produced DIFFERENT tool prefixes for the same tools -- `mcp__aify-agents-bridge__
comms_send` from the plugin, `mcp__aify-comms__comms_send` from the installer -- while every skill in
the repo documents only the second. Nothing failed; a plugin user simply had tools no instruction
mentioned.

BOTH SIDES ARE DERIVED. The expected name comes from install.sh's own registration, so this test
tracks the installer rather than a constant typed here; if the installer's name ever changes, this
fails and names both, which is the conversation worth having.

THE INSTALLER REGISTERS TWO SERVERS, not one, and asserting otherwise was this test's first bug:
`aify-comms` carries the comms_* tools and `aify-comms-channel` is the resident dispatch channel the
bridge runs. Only the first has a plugin counterpart, so the derivation picks the one written through
`data.mcpServers[...]` -- the tools registration -- and asserts the other is present rather than
pretending it is not there.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
PLUGIN_MCP = ROOT / ".claude-plugin" / ".mcp.json"
INSTALL = ROOT / "install.sh"


def installer_server_name() -> str:
    """The name install.sh writes the comms_* TOOLS server under, in a client's MCP config."""
    source = INSTALL.read_text(encoding="utf-8")
    names = set(re.findall(r"data\.mcpServers\['([^']+)'\]", source))
    assert names, "install.sh no longer registers a named MCP server; this test is measuring nothing"
    assert len(names) == 1, f"more than one tools server registered: {sorted(names)}"
    return names.pop()


def test_the_installer_registers_the_tools_server_and_the_channel_separately():
    """Positive control, and it names both so a silent merge of the two would show up here."""
    source = INSTALL.read_text(encoding="utf-8")
    assert installer_server_name() == "aify-comms"
    assert "aify-comms-channel" in source, "the resident dispatch channel registration is gone"


def test_the_plugin_registers_the_same_mcp_server_name_as_the_installer():
    servers = json.loads(PLUGIN_MCP.read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == [installer_server_name()], (
        f"the plugin registers {list(servers)} but install.sh registers "
        f"{installer_server_name()!r}; the two paths would give agents different tool prefixes"
    )


def test_the_plugin_names_the_repository_it_actually_lives_in():
    manifest = json.loads(PLUGIN.read_text(encoding="utf-8"))
    assert manifest["name"] == installer_server_name()
    assert manifest["homepage"].endswith("/aify-comms"), manifest["homepage"]


def test_the_manifest_does_not_describe_this_project_as_a_fork_of_itself():
    text = PLUGIN.read_text(encoding="utf-8")
    assert "forked from aify-comms" not in text
    assert "aify-agents-bridge" not in text, "the retired identity is back somewhere in the manifest"
