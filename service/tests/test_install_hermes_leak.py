"""hermes-aify wrapper — process-leak reap clauses (fix/hermes-leak).

Static-text smoke checks against install.sh's emitted hermes-aify (bash) and
hermes-aify.ps1 (PowerShell) wrappers. Same pattern as
test_install_hermes_session_rediscover.py — we pin the emitted code shape
because a real hermes gateway can't be spun up in CI.

Covers:
  P1 — kill-prior must ALSO reap the prior `hermes --tui --resume <pinned>`
       visible TUI, scoped to the EXACT pinned resume handle, PRE-spawn ONLY.
  P3 — the resident branch must tear down its api_server daemon when the
       resident TUI exits (no bare `exec`/Invoke that skips the teardown).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


# --- P1: kill-prior reaps the prior resume-TUI -----------------------------


def test_bash_kill_prior_reaps_prior_resume_tui_pre_spawn_only():
    """bash aify_hermes_kill_prior must reap a prior `hermes --tui --resume
    <pinnedSession>` matched on the EXACT pinned handle, gated to the pre-spawn
    call (exclude_pid empty)."""
    text = _read_install_sh()
    fn_idx = text.find("aify_hermes_kill_prior() {")
    assert fn_idx > 0, "aify_hermes_kill_prior helper not found"
    fn = text[fn_idx : fn_idx + 3600]
    # Computes the pinned session (aify-<sanitized agent>) for the match.
    assert "aify-" in fn and "tr -c 'a-zA-Z0-9_-'" in fn, (
        "kill-prior must compute the pinned resume handle to match the TUI"
    )
    # Matches the resume-TUI on the EXACT pinned handle (not a broad --tui).
    assert "--tui --resume" in fn, "kill-prior must match `--tui --resume <pinned>`"
    # PRE-spawn only: the resume-TUI reap is gated behind the empty-exclude_pid
    # guard so the post-spawn call never kills the TUI we just exec'd.
    assert 'if [ -z "\\$exclude_pid" ]' in fn, (
        "the resume-TUI reap must be gated to the pre-spawn call (exclude_pid empty)"
    )
    # Scoped: never a blanket `pkill -f 'hermes --tui'` with no handle.
    assert "pkill -f \"hermes --tui\"" not in fn
    assert "pkill -f 'hermes --tui'" not in fn


def test_powershell_kill_prior_reaps_prior_resume_tui_pre_spawn_only():
    """PowerShell Invoke-AifyHermesKillPrior must reap a prior `hermes(.exe)?
    --tui --resume <pinnedSession>` matched on the EXACT pinned handle, gated to
    the pre-spawn call ($ExcludeLoopPid -le 0)."""
    text = _read_install_sh()
    fn_idx = text.find("function Invoke-AifyHermesKillPrior {")
    assert fn_idx > 0, "Invoke-AifyHermesKillPrior helper not found"
    fn = text[fn_idx : fn_idx + 4200]
    # Computes the pinned session for the match.
    assert "aify-" in fn and "-replace '[^a-zA-Z0-9_-]+'" in fn, (
        "kill-prior must compute the pinned resume handle to match the TUI"
    )
    # Matches a hermes(.exe) process whose command line carries the exact
    # `--tui --resume <pinned>`.
    assert "--tui --resume" in fn, "kill-prior must match `--tui --resume <pinned>`"
    # Hermes is a python entrypoint launched as hermes / hermes.exe — match the
    # resume handle on the command line regardless of host exe name.
    assert "[regex]::Escape(\\$pinned" in fn, (
        "the resume-TUI match must escape the exact pinned handle (agent-scoped)"
    )
    # PRE-spawn only: gated behind the $ExcludeLoopPid -le 0 guard.
    assert "if (\\$ExcludeLoopPid -le 0)" in fn, (
        "the resume-TUI reap must be gated to the pre-spawn call ($ExcludeLoopPid -le 0)"
    )


# --- P3: resident branch tears down its daemon on TUI exit ------------------


def test_bash_resident_branch_tears_down_daemon_on_tui_exit():
    """The bash resident hermes branch must run-then-teardown (no bare `exec`
    that skips the daemon stop)."""
    text = _read_install_sh()
    # Locate the bash resident branch (ensure_daemon + pinned + run TUI).
    idx = text.find('aify_hermes_ensure_daemon "\\$HERMES_AIFY_AGENT_ID"')
    assert idx > 0, "bash resident ensure_daemon branch not found"
    branch = text[idx : idx + 1400]
    # It must NOT bare-exec the TUI (exec replaces the shell and skips teardown).
    assert 'exec "\\$HERMES_RUNTIME_COMMAND" --tui "\\${HERMES_PERMISSION_FLAGS[@]}" --resume "\\$AIFY_HERMES_PINNED_SESSION"' not in branch, (
        "resident branch must not bare-exec the TUI; it must teardown the daemon on exit"
    )
    # It must stop the per-agent daemon after the TUI exits.
    assert 'node "\\$AIFY_HERMES_DAEMON_CLI" stop "\\$HERMES_AIFY_AGENT_ID"' in branch, (
        "resident branch must stop the per-agent daemon on TUI exit"
    )


def test_powershell_resident_branch_tears_down_daemon_on_tui_exit():
    """The PowerShell resident hermes branch must stop its daemon after the TUI
    Invoke returns (Invoke-HermesRuntime already runs then-returns, so add the
    daemon stop before exit)."""
    text = _read_install_sh()
    idx = text.find("Invoke-AifyHermesEnsureDaemon \\$HermesAifyAgentId | Out-Null")
    assert idx > 0, "PowerShell resident ensure-daemon branch not found"
    branch = text[idx : idx + 1400]
    assert "Invoke-HermesRuntime @('--tui', '--resume', \\$pinnedSession)" in branch
    # It must stop the per-agent daemon after the TUI returns.
    assert "node \\$AifyHermesDaemonCli stop \\$HermesAifyAgentId" in branch, (
        "resident branch must stop the per-agent daemon on TUI exit"
    )
