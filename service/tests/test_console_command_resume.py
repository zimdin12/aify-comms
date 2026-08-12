"""Pin that _default_console_command emits `--resume <handle>` for all runtimes
that support it once the handle is stored. The codex carve-out (removed in
Plan 1 of the RuntimeAdapter refactor) is the primary regression target."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.control_plane import _default_console_command


def _session(*, agent_id, handle, runtime):
    return {"agent_id": agent_id, "session_handle": handle, "runtime": runtime}


def test_claude_managed_includes_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="h1", runtime="claude-code"),
        "/tmp",
        interactive=False,
    )
    assert "claude-aify" in cmd
    assert "--aify-agent a" in cmd
    assert "--auto" in cmd
    assert "--resume h1" in cmd


def test_claude_interactive_includes_resume_when_handle_known():
    cmd = _default_console_command(
        _session(agent_id="a", handle="h1", runtime="claude-code"),
        "/tmp",
        interactive=True,
    )
    assert "claude-aify --aify-agent a" in cmd
    assert "--auto" not in cmd
    assert "--resume h1" in cmd


def test_codex_managed_includes_resume():
    """Regression for Plan 1: drop the codex carve-out; managed launches now resume."""
    cmd = _default_console_command(
        _session(agent_id="a", handle="thread-uuid", runtime="codex"),
        "/tmp",
        interactive=False,
    )
    assert "codex-aify" in cmd
    assert "--aify-agent a" in cmd
    assert "--resume thread-uuid" in cmd


def test_codex_interactive_includes_resume_when_handle_known():
    """Operator-driven Plan 1 decision: interactive Console resumes if we have a
    handle. codex-aify wrapper handles stale handles gracefully."""
    cmd = _default_console_command(
        _session(agent_id="a", handle="thread-uuid", runtime="codex"),
        "/tmp",
        interactive=True,
    )
    assert "codex-aify --aify-agent a" in cmd
    assert "--resume thread-uuid" in cmd


def test_codex_no_handle_no_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="", runtime="codex"),
        "/tmp",
        interactive=False,
    )
    assert "codex-aify --aify-agent a" in cmd
    assert "--resume" not in cmd


def test_hermes_managed_includes_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="hh", runtime="hermes"),
        "/tmp",
        interactive=False,
    )
    assert "hermes-aify --aify-agent a" in cmd
    assert "--resume hh" in cmd


def test_pi_managed_includes_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="omp-uuid", runtime="pi"),
        "/tmp",
        interactive=False,
    )
    assert "pi-aify --aify-agent a" in cmd
    assert "--resume omp-uuid" in cmd


def test_pi_interactive_no_resume():
    cmd = _default_console_command(
        _session(agent_id="a", handle="omp-uuid", runtime="pi"),
        "/tmp",
        interactive=True,
    )
    # Pi interactive intentionally stays fresh — comments in api_v2 explain the
    # 026H control-sequence trap. Plan 1 preserves this behavior.
    assert "pi-aify --aify-agent a" in cmd
    assert "--resume" not in cmd
