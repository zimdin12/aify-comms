"""No launcher this installer renders carries a literal `@@TOKEN@@`.

A placeholder the renderer does not know survives into the installed launcher as literal text, and
install.sh still exits 0 and `bash -n` still passes -- it is valid shell, just wrong. `@@MCP_TRANSPORT@@`
did exactly this: the templates gained a transport branch, aify-wrapper's installer substituted it,
aify-comms' did not, and every launcher rendered here compared the literal string `@@MCP_TRANSPORT@@`
to "sse". False, so it took the stdio arm and worked by accident, for a value nobody had chosen.

THE HOLE WAS THE SCOPE, not the absence of a check. A claude-only version of this test caught it,
because claude happened to be the template that changed. Hermes and pi had no such test at all, so the
same mistake in either would have rendered literally with nothing to notice -- the same shape as the
1000-line gate reading `service/**` only, where an unguarded population reports green exactly like a
guarded one.

Rendering uses `--emit-wrappers`, which exits before npm, MCP registration and any environment
mutation, so this runs on a machine with a live fleet without touching it.
"""
from __future__ import annotations

import re
import subprocess

from _launchers import INSTALL_SH as INSTALL, REPO as ROOT, bash as _bash, emittable_clients, render

# Every client install.sh can render is DERIVED in `_launchers`, not listed here, so a new one
# cannot arrive ungoverned the way hermes and pi did. The render is cached for the session there
# too: this file's four-client scan was 42.5 seconds of the suite, all of it install.sh.
PLACEHOLDER = re.compile(r"@@[A-Z0-9_]+@@")


def test_the_template_scan_finds_every_client():
    """Positive control. A regex that found two names would silently halve this test's coverage."""
    clients = emittable_clients()
    assert {"claude", "codex", "hermes", "pi"} <= set(clients), clients


def test_the_placeholder_pattern_can_actually_match_one():
    """Negative control: the detector must be able to say PRESENT, or its absences mean nothing."""
    assert PLACEHOLDER.findall('if [ "@@MCP_TRANSPORT@@" = "sse" ]') == ["@@MCP_TRANSPORT@@"]
    assert PLACEHOLDER.findall('if [ "stdio" = "sse" ]') == []


def _leftover(files: dict[str, str]) -> dict[str, list[str]]:
    """Which rendered files kept a placeholder, and which one. Named, because the remedy differs
    per parameter and a bare boolean sends the reader back to render it themselves."""
    return {
        name: sorted(set(PLACEHOLDER.findall(text)))
        for name, text in files.items() if PLACEHOLDER.search(text)
    }


def test_no_client_renders_a_literal_placeholder():
    offences = {}
    for client in emittable_clients():
        leftover = _leftover(render(client))
        if leftover:
            offences[client] = leftover
    assert not offences, (
        f"launchers rendered with literal placeholders: {offences}. A template parameter reached "
        "a rendered launcher as a literal. That is how @@MCP_TRANSPORT@@ forced every launcher "
        "onto the stdio arm for a value nobody chose, and how @@SERVICE_NAME@@ later made the "
        "rendered MCP config name a server called `@@SERVICE_NAME@@` and hung "
        "claude-wrapper-behaviour.test.js at 200 seconds. Substitute it in "
        "render_wrapper_template, or ask why aify-wrapper added a parameter this repo does not "
        "fill."
    )


def test_the_sse_transport_renders_clean_too():
    """The non-default arm. A placeholder reachable only under a flag is still shipped text."""
    assert _leftover(render("claude", "--mcp-transport", "sse")) == {}


def test_help_runs_clean_and_says_nothing_on_stderr():
    """`bash -n` parses; it does not run. Stray text above `set -euo pipefail` is valid shell that
    executes as a command, so a documentation line pasted into the file header becomes a
    "command not found" on every invocation -- and because it lands before `set -e`, the script
    carries on and exits 0. That is what happened while adding --mcp-transport to the usage text:
    the anchor matched the commented copy in the header instead of the usage function, `bash -n`
    passed, and `--help` still printed usage. The only visible difference was on stderr.
    """
    result = subprocess.run(
        [_bash(), INSTALL.as_posix(), "--help"], capture_output=True, text=True, cwd=ROOT, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "", f"--help wrote to stderr: {result.stderr!r}"
    assert "--mcp-transport" in result.stdout, "the transport flag is undocumented in --help"
