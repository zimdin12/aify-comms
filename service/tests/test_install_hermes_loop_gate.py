"""WS1 Task 1.5 — wrapper health-gates the delivery loop before the TUI.

The managed-hermes wrapper (`hermes-aify` / `hermes-aify.ps1`) must NOT exec a
visible TUI until the background delivery loop has become a live claimer. The
loop signals that by writing `aify-hermes-loop-ready-<agent>` into os.tmpdir()
(see mcp/stdio/hermes-loop-ready.js). The wrapper therefore:

  1. spawns the delivery loop, captures its PID;
  2. polls (bounded, ~30s) for a fresh `aify-hermes-loop-ready-<agent>` marker
     in the temp dir BEFORE exec'ing `hermes --tui` / Invoke-HermesRuntime;
  3. on timeout prints a LOUD failure and exits non-zero — never showing a TUI
     that cannot receive work (visible-TUI HARD requirement).

It also fixes the self-reap race: `aify_hermes_kill_prior` /
`Invoke-AifyHermesKillPrior` must EXCLUDE the PID of the loop the current
wrapper just spawned, so a concurrent same-agent relaunch can't kill the new
loop.

These tests assert on the GENERATED wrapper text (no live hermes / loop launch).
install.sh exposes a side-effect-free `--emit-hermes-wrappers <dir>` test hook
that writes both wrappers into <dir> and exits, mirroring `--prebuild-dry-run`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"


def _emit_wrappers(tmp_path: Path) -> tuple[str, str]:
    """Generate the bash + PowerShell hermes wrappers into tmp_path; return text.

    Uses the `--emit-hermes-wrappers` test hook so nothing in the operator's
    environment is touched and no npm/hermes/loop is launched.
    """
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH — install.sh wrapper-gen smoke skipped")

    out_dir = tmp_path / "bin"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ}
    result = subprocess.run(
        [
            bash,
            str(INSTALL_SH),
            "--client",
            "hermes",
            "--emit-hermes-wrappers",
            str(out_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"--emit-hermes-wrappers should exit 0; rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    bash_wrapper = out_dir / "hermes-aify"
    ps_wrapper = out_dir / "hermes-aify.ps1"
    assert bash_wrapper.is_file(), f"bash wrapper not generated; stderr:\n{result.stderr}"
    assert ps_wrapper.is_file(), f"ps wrapper not generated; stderr:\n{result.stderr}"
    return (
        bash_wrapper.read_text(encoding="utf-8", errors="replace"),
        ps_wrapper.read_text(encoding="utf-8-sig", errors="replace"),
    )


# --------------------------------------------------------------------------
# (a) bounded wait-for-ready-then-exec ordering in BOTH branches
# --------------------------------------------------------------------------


def test_bash_wrapper_waits_for_ready_marker_before_exec(tmp_path):
    bash_text, _ = _emit_wrappers(tmp_path)

    # The marker basename the loop writes (hermes-loop-ready.js).
    assert "aify-hermes-loop-ready-" in bash_text, (
        "bash wrapper must reference the loop ready marker basename"
    )
    # The gate must appear before the managed `exec ... --tui` so a TUI is never
    # shown ahead of a live claimer. Find positions of the gate and the exec.
    gate_idx = bash_text.find("aify-hermes-loop-ready-")
    exec_idx = bash_text.find("exec \"$HERMES_RUNTIME_COMMAND\" --tui --resume")
    assert gate_idx != -1 and exec_idx != -1, (
        "expected both the ready-marker gate and the managed exec line"
    )
    assert gate_idx < exec_idx, (
        "ready-marker gate must precede the managed `exec hermes --tui`"
    )
    # Bounded poll: a loop bounded by a ~30s budget (token '30' present in the
    # gate region) rather than an unbounded wait.
    gate_region = bash_text[gate_idx - 600 : exec_idx]
    assert "30" in gate_region, "expected a bounded (~30s) wait budget in the gate"


def test_ps_wrapper_waits_for_ready_marker_before_runtime(tmp_path):
    _, ps_text = _emit_wrappers(tmp_path)

    assert "aify-hermes-loop-ready-" in ps_text, (
        "PowerShell wrapper must reference the loop ready marker basename"
    )
    gate_idx = ps_text.find("aify-hermes-loop-ready-")
    run_idx = ps_text.find("Invoke-HermesRuntime @('--tui', '--resume'")
    assert gate_idx != -1 and run_idx != -1, (
        "expected both the ready-marker gate and the managed Invoke-HermesRuntime"
    )
    assert gate_idx < run_idx, (
        "ready-marker gate must precede the managed Invoke-HermesRuntime"
    )
    gate_region = ps_text[gate_idx - 600 : run_idx]
    assert "30" in gate_region, "expected a bounded (~30s) wait budget in the gate"


# --------------------------------------------------------------------------
# (b) NON-FATAL on timeout in BOTH branches (2026-06-02 hotfix): a slow/transient
#     loop must NOT take the team down. On the gate timeout the wrapper WARNs and
#     starts the TUI anyway (the loop keeps retrying); it must NOT exit non-zero.
# --------------------------------------------------------------------------


def test_bash_wrapper_warns_and_starts_tui_on_timeout(tmp_path):
    bash_text, _ = _emit_wrappers(tmp_path)

    assert "not yet a live claimer" in bash_text, (
        "bash wrapper must emit the non-fatal WARN when the loop is slow to ready"
    )
    warn_idx = bash_text.find("not yet a live claimer")
    after = bash_text[warn_idx : warn_idx + 400]
    assert "starting TUI anyway" in after, (
        "bash wrapper must start the TUI anyway (non-fatal) when the loop is slow"
    )
    assert "exit 1" not in after, (
        "bash wrapper must NOT exit non-zero on a slow loop (no team-down)"
    )


def test_ps_wrapper_warns_and_starts_tui_on_timeout(tmp_path):
    _, ps_text = _emit_wrappers(tmp_path)

    assert "not yet a live claimer" in ps_text, (
        "PowerShell wrapper must emit the non-fatal WARN when the loop is slow"
    )
    warn_idx = ps_text.find("not yet a live claimer")
    after = ps_text[warn_idx : warn_idx + 400]
    assert "starting TUI anyway" in after, (
        "PowerShell wrapper must start the TUI anyway (non-fatal) when the loop is slow"
    )
    assert "exit 1" not in after, (
        "PowerShell wrapper must NOT exit non-zero on a slow loop (no team-down)"
    )


# --------------------------------------------------------------------------
# (c) kill-prior excludes the just-spawned loop PID in BOTH branches
# --------------------------------------------------------------------------


def test_bash_kill_prior_excludes_spawned_loop_pid(tmp_path):
    bash_text, _ = _emit_wrappers(tmp_path)

    # kill-prior must accept/known an exclude-PID and skip it when matching the
    # `hermes-managed-host.js run <agent>` loop. The captured PID comes from `$!`.
    assert "$!" in bash_text, "wrapper must capture the spawned loop PID via $!"
    # kill-prior region: from its definition to the end of the managed-host kill.
    kp_idx = bash_text.find("aify_hermes_kill_prior() {")
    assert kp_idx != -1, "kill-prior function must exist"
    kp_region = bash_text[kp_idx : kp_idx + 1600]
    # An exclude-pid local/param threaded into the managed-host kill.
    assert "exclude" in kp_region.lower(), (
        "kill-prior must take an exclude-PID parameter for the just-spawned loop"
    )


def test_ps_kill_prior_excludes_spawned_loop_pid(tmp_path):
    _, ps_text = _emit_wrappers(tmp_path)

    # Start-Process -PassThru gives the spawned loop's .Id.
    assert "-PassThru" in ps_text, (
        "PowerShell wrapper must capture the spawned loop PID via Start-Process -PassThru"
    )
    kp_idx = ps_text.find("function Invoke-AifyHermesKillPrior")
    assert kp_idx != -1, "PowerShell kill-prior function must exist"
    kp_region = ps_text[kp_idx : kp_idx + 1800]
    assert "exclude" in kp_region.lower(), (
        "PowerShell kill-prior must take an exclude-PID parameter for the loop"
    )
