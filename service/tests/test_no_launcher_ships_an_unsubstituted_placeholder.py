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
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "install.sh"

# Every client install.sh can render. Derived below rather than trusted, so a new one cannot arrive
# ungoverned the way hermes and pi did.
PLACEHOLDER = re.compile(r"@@[A-Z0-9_]+@@")


def emittable_clients() -> list[str]:
    """The clients --emit-wrappers can render, read out of install.sh's own template mapping."""
    source = INSTALL.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"([a-z]+)-aify\.sh\.in", source)))
    assert names, "no wrapper templates named in install.sh; this test would render nothing"
    return names


def _bash() -> str:
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def render(client: str, *extra: str) -> str:
    with tempfile.TemporaryDirectory() as out:
        # THE RESOLVED bash, not the literal name. On Windows the bare word finds System32's WSL
        # bash first, which cannot open a C: path at all and fails 127 on every form -- measured
        # here: `shutil.which` returns Git Bash and both path forms work, the literal works with
        # neither. aify-env shipped this same shadowing bug and could not launch anything.
        result = subprocess.run(
            [_bash(), INSTALL.as_posix(), "--client", client, "http://127.0.0.2:1",
             *extra, "--emit-wrappers", Path(out).as_posix()],
            capture_output=True, text=True, cwd=ROOT, timeout=600,
        )
        rendered = Path(out) / f"{client}-aify"
        assert rendered.exists(), (
            f"{client}: nothing rendered (exit {result.returncode})\n{result.stdout}{result.stderr}"
        )
        return rendered.read_text(encoding="utf-8")


def test_the_template_scan_finds_every_client():
    """Positive control. A regex that found two names would silently halve this test's coverage."""
    clients = emittable_clients()
    assert {"claude", "codex", "hermes", "pi"} <= set(clients), clients


def test_the_placeholder_pattern_can_actually_match_one():
    """Negative control: the detector must be able to say PRESENT, or its absences mean nothing."""
    assert PLACEHOLDER.findall("if [ \"@@MCP_TRANSPORT@@\" = \"sse\" ]") == ["@@MCP_TRANSPORT@@"]
    assert PLACEHOLDER.findall('if [ "stdio" = "sse" ]') == []


def test_no_client_renders_a_literal_placeholder():
    offences = {}
    for client in emittable_clients():
        leftover = sorted(set(PLACEHOLDER.findall(render(client))))
        if leftover:
            offences[client] = leftover
    assert not offences, f"launchers rendered with literal placeholders: {offences}"


def test_the_sse_transport_renders_clean_too():
    """The non-default arm. A placeholder reachable only under a flag is still shipped text."""
    leftover = sorted(set(PLACEHOLDER.findall(render("claude", "--mcp-transport", "sse"))))
    assert leftover == [], leftover


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
