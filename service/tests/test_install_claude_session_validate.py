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

from service.tests._launchers import launcher


def _rendered_wrapper() -> str:
    """The RENDERED claude-aify wrapper, the artifact an operator installs."""
    return launcher("claude")


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
