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
    """The bash resident/managed hermes branch must run-then-teardown (no bare
    `exec` that skips reaping the background delivery loop + gateway host).

    Updated 2026-06-03 (native-session-id model): the api_server `ensure_daemon`
    path was retired. The unified gateway-host branch now spawns a detached
    delivery loop (`hermes-managed-host.js run <agent>`), captures its PID, and
    installs an EXIT/INT/TERM trap that SIGTERMs the loop when the TUI ends — the
    loop's own teardown then reaps the hidden gateway host it owns. The TUI runs
    as a CHILD (not `exec`) so the trap can fire. Preserve the intent: the branch
    reaps its background process on TUI exit and never bare-execs the TUI.
    """
    text = _read_install_sh()
    # Locate the unified gateway-host branch (agent id present, no passthrough args).
    idx = text.find(
        'if [ -n "\\$HERMES_AIFY_AGENT_ID" ] && [ \\${#HERMES_ARGS[@]} -eq 0 ]; then'
    )
    assert idx > 0, "bash gateway-host (resident/managed) branch not found"
    # Bound the branch by the stable sentinel comment that immediately follows its closing
    # `fi`, NOT a fixed char window — the branch grows (hermes-resume DB-validate etc.) and a
    # fixed +N window silently dropped the teardown out of view (the 2026-06-05 stale-test).
    end = text.find(
        "# RESIDENT agent-id launch: handled by the unified GATEWAY-HOST branch above", idx
    )
    assert end > idx, "could not bound the bash gateway-host branch (sentinel comment missing)"
    branch = text[idx:end]
    # It must NOT bare-exec the TUI (exec replaces the shell and skips teardown).
    assert 'exec "\\$HERMES_RUNTIME_COMMAND" --tui' not in branch, (
        "resident branch must not bare-exec the TUI; it must reap the loop on exit"
    )
    # It captures the detached delivery-loop PID and installs an exit trap.
    assert 'HERMES_LOOP_PID="\\$!"' in branch, (
        "resident branch must capture the detached delivery-loop PID to reap it"
    )
    assert '_aify_hermes_on_exit() { kill "\\$HERMES_LOOP_PID"' in branch, (
        "resident branch must define a teardown that kills the delivery loop"
    )
    assert "trap _aify_hermes_on_exit EXIT INT TERM" in branch, (
        "resident branch must trap EXIT/INT/TERM so closing the TUI reaps the loop"
    )
    # And it must invoke that teardown after the (child) TUI returns (the TUI
    # runs as a child, then the wrapper calls _aify_hermes_on_exit + exits).
    assert "_aify_hermes_on_exit\n    exit \\$?" in branch or "_aify_hermes_on_exit\n  exit \\$?" in branch, (
        "resident branch must reap the loop (its gateway host) after the TUI exits"
    )


def test_powershell_resident_branch_tears_down_daemon_on_tui_exit():
    """The PowerShell resident/managed hermes branch must reap its background
    delivery loop (and the gateway host it owns) after the TUI Invoke returns.

    Updated 2026-06-03 (native-session-id model): the api_server `ensure-daemon`
    path was retired in favor of the unified gateway-host branch, which spawns a
    detached delivery loop (`hermes-managed-host.js run <agent>`) and captures its
    PID (`$hermesLoopPid`). Since `Invoke-HermesRuntime` runs-then-returns, the TUI
    is wrapped in try/finally so closing it ALWAYS Stop-Process's the loop — the
    PowerShell parity of the bash EXIT/INT/TERM trap. Without this the loop + its
    hidden gateway host orphan and the agent stays falsely online.
    """
    text = _read_install_sh()
    idx = text.find("if (\\$HermesAifyAgentId -and \\$HermesArgs.Count -eq 0) {")
    assert idx > 0, "PowerShell gateway-host (resident/managed) branch not found"
    branch = text[idx : idx + 12000]
    # Native-session-id model (2026-06-03): resume the resolved real session id,
    # not the retired synthetic `aify-<agentId>` pin.
    assert "Invoke-HermesRuntime (@('--tui', '--resume', \\$hermesResumeRealId) + \\$HermesPermissionFlags)" in branch
    # The TUI Invoke must be wrapped so its exit always reaps the loop.
    assert "} finally {" in branch, (
        "resident branch must wrap the TUI Invoke in try/finally so exit reaps the loop"
    )
    # It must Stop-Process the captured delivery-loop PID after the TUI returns.
    assert "Stop-Process -Id \\$hermesLoopPid -Force" in branch, (
        "resident branch must reap the delivery loop (its gateway host) on TUI exit"
    )
