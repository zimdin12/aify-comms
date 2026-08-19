"""Plan 6 B4 — claude-aify validates its session handle against the on-disk transcript.

Unlike hermes/codex/pi (which query a live runtime), claude has no probe endpoint — but its session
id maps 1:1 to a JSONL transcript at ~/.claude/projects/<encoded-cwd>/<id>.jsonl. If
CLAUDE_SESSION_ID is set but no matching file exists, the env value is stale (the prior session was
GC'd, or the operator cd'd into a different project). It must be unset before exec'ing claude so the
runtime creates a fresh session and the bridge's discover picks up the new id.

REWRITTEN 2026-08-19 (v0.6 Phase 2). These were "static-text smoke checks on install.sh — no bash
exec": they grepped the INSTALLER SOURCE for substrings. When the wrapper body moved out of its
heredoc into wrappers/claude-aify.sh.in, all five went red while the wrapper's behaviour was proven
byte-identical. That is the exact failure mode of a location pin — it asserts where text lives, so it
breaks on a move and stays green on a defect.

They now render the wrapper and assert on the ARTIFACT an operator installs, which survives the text
moving and would fail if the rendering broke. The behaviour itself — a stale id actually being
cleared, a valid one actually being kept — is proven by EXECUTING the wrapper in
mcp/stdio/tests/claude-wrapper-behaviour.test.js; these remain as cheap structural guards on the
rendered output.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

# A literal, never the operator's configured endpoint: the rendered text must be identical on every
# machine, and nothing here may reach a live service.
RENDER_URL = "http://127.0.0.1:8899"


@lru_cache(maxsize=1)
def _rendered_wrapper() -> str:
    """Render claude-aify into a throwaway dir and return its text.

    `--emit-wrappers` writes the wrapper and EXITS before npm, MCP registration, hook install or any
    env mutation, so this cannot touch ~/.local/bin on a machine with a live fleet.
    """
    # `shutil.which` and not the bare name: on Windows a plain "bash" resolves to
    # C:\Windows\System32\bash.exe (WSL), which cannot read a C:\ path and exits 127. The other
    # install tests here already resolve it this way.
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH — claude wrapper render skipped")
    with tempfile.TemporaryDirectory(prefix="aify-claude-render-") as tmp:
        subprocess.run(
            [bash, str(INSTALL_SH), "--client", "claude", RENDER_URL, "--emit-wrappers", tmp],
            check=True,
            capture_output=True,
        )
        wrapper = Path(tmp) / "claude-aify"
        assert wrapper.exists(), "--emit-wrappers must produce claude-aify"
        return wrapper.read_text(encoding="utf-8")


def test_rendered_wrapper_is_not_empty():
    """Anchors every assertion below: an empty render would satisfy none of them, but a truncated one
    could satisfy several by accident."""
    text = _rendered_wrapper()
    assert text.startswith("#!/bin/bash"), "the wrapper must render as an executable script"
    assert len(text.splitlines()) > 100, "a plausible wrapper is hundreds of lines, not a stub"


def test_claude_wrapper_defines_validate_helper():
    assert "validate_claude_session_id" in _rendered_wrapper(), (
        "Plan 6 B4: the installed wrapper must define validate_claude_session_id"
    )


def test_claude_wrapper_checks_projects_directory():
    assert ".claude/projects" in _rendered_wrapper(), (
        "Plan 6 B4: the validator must consult ~/.claude/projects/..."
    )


def test_claude_wrapper_unsets_stale_session_id():
    text = _rendered_wrapper()
    assert "unset CLAUDE_SESSION_ID" in text
    assert "unset CLAUDE_RESUME_ID" in text, (
        "both must be cleared — leaving CLAUDE_RESUME_ID would re-export the stale id below"
    )


def test_claude_wrapper_validate_is_non_fatal():
    """The wrapper runs under `set -e`, so an unguarded non-zero return would abort the launch.

    A stale session id must degrade to a fresh session, never to a wrapper that refuses to start.
    """
    text = _rendered_wrapper()
    assert "if [ -n \"${CLAUDE_RESUME_ID:-}\" ] && ! validate_claude_session_id" in text, (
        "the validator must be called inside a condition, where a non-zero return is not fatal"
    )


def test_claude_wrapper_strips_stale_explicit_resume_args():
    """An explicit `--resume <id>` is validated before it is forwarded.

    Otherwise a stale dashboard handle stays in argv after CLAUDE_SESSION_ID is cleared, and claude
    exits with "No conversation found" instead of creating a fresh, repairable session.
    """
    text = _rendered_wrapper()
    assert "CLAUDE_RESUME_FROM_ARG=false" in text
    assert 'CLAUDE_ARGS+=("${CLAUDE_RESUME_FLAG:---resume}" "$CLAUDE_RESUME_ID")' in text, (
        "the resume flag must be re-added from the VALIDATED id, not passed through from argv"
    )


def test_rendered_wrapper_has_no_unsubstituted_placeholders():
    """A template placeholder the renderer does not know becomes literal `@@TOKEN@@` text in the
    installed wrapper — with install.sh exiting 0 and bash -n passing."""
    text = _rendered_wrapper()
    import re

    leftover = re.findall(r"@@[A-Z0-9_]+@@", text)
    assert leftover == [], f"unsubstituted placeholders reached the wrapper: {sorted(set(leftover))}"
