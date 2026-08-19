"""hermes-aify wrapper session-handle contract.

Fresh `hermes-aify` launches must not bind themselves to `session.most_recent`
from the dashboard gateway. That method reports historical DB state before the
visible TUI has attached, so using it as the resident handle registers the
agent against a session that cannot be visibly woken. Explicit `--resume <id>`
remains authoritative. Fresh launches rely on the TUI-written active-session
file once the visible session exists.

These are install.sh static-text smoke checks (no bash invocation) — same
pattern as test_install_hermes_prebuild.py's family. We can't easily spin
up a real hermes gateway in tests, so we pin the wrapper's emitted code
shape; the failure path is non-fatal and exercised live by the operator.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import lru_cache

import pytest

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
# Visible-session bind, the single-active-session fallback, the gateway-URL
# publication, and the wrapper-owned active-session-file preservation moved out
# of install.sh source-patches (removed in Plan 1.4, 2026-05-30 — see
# install.sh's `AIFY_HERMES_LEGACY_SOURCE_PATCH` gate) and now live in the
# durable hermes-aify plugin loaded at runtime. Tests that used to assert these
# as install.sh-emitted source patches now assert them against the plugin.
#: EVERY patch module, not one file. `patch_gateway_server` — which holds most of what the
#: assertions below look for — moved to `gateway_patch.py` in v0.5.4, and reading only `patches.py`
#: then finds NONE of these strings. That failure reads as "the installer stopped emitting the
#: visible-session patch", which is the regression these tests exist to catch, so the scan follows
#: the CODE rather than the filename: any new patch module in the package is picked up.
HERMES_PLUGIN_DIR = REPO / "integrations" / "hermes-aify-plugin" / "aify_hermes_plugin"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


# ── the RENDERED hermes-aify wrapper ────────────────────────────────────────────────────────────
#
# Added 2026-08-19 (v0.6 Phase 2), when the wrapper body moved out of install.sh into
# wrappers/hermes-aify.sh.in. Tests that assert on the WRAPPER read this; tests that assert on the
# INSTALLER (the .ps1 shim, plugin patches, config rewrites) keep reading install.sh, because that is
# still where those live. A location pin breaks on a move and stays green on a defect — asking the
# artifact an operator installs is immune to both.
@lru_cache(maxsize=1)
def _read_hermes_wrapper() -> str:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH — hermes wrapper render skipped")
    with tempfile.TemporaryDirectory(prefix="aify-hermes-render-") as tmp:
        subprocess.run(
            [bash, str(INSTALL_SH), "--client", "hermes", "http://127.0.0.1:8899",
             "--emit-wrappers", tmp],
            check=True,
            capture_output=True,
        )
        wrapper = Path(tmp) / "hermes-aify"
        assert wrapper.exists(), "--emit-wrappers must produce hermes-aify"
        return wrapper.read_text(encoding="utf-8")


# The rendered PowerShell TUI shim. Hermes is the only runtime needing a `.ps1` at all — it carries
# the visible-TUI requirement on Windows — and `--emit-wrappers` writes it beside the bash wrapper.
@lru_cache(maxsize=1)
def _read_hermes_ps1() -> str:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH — hermes shim render skipped")
    with tempfile.TemporaryDirectory(prefix="aify-hermes-ps1-") as tmp:
        subprocess.run(
            [bash, str(INSTALL_SH), "--client", "hermes", "http://127.0.0.1:8899",
             "--emit-wrappers", tmp],
            check=True,
            capture_output=True,
        )
        shim = Path(tmp) / "hermes-aify.ps1"
        assert shim.exists(), "--emit-wrappers must produce hermes-aify.ps1"
        return shim.read_text(encoding="utf-8")


def _defines(text: str, name: str) -> bool:
    """`name() {` — the shell function DEFINITION."""
    return re.search(rf"^{re.escape(name)}\(\)\s*\{{", text, re.MULTILINE) is not None


def _calls(text: str, name: str) -> bool:
    """`name` at the start of a line (after indent) NOT followed by `()` — an invocation.

    Asserting the bare token instead cannot tell these apart, because the definition contains it.
    That is how a hook check stayed green after its call sites were deleted (b3cdcd46). Invocations
    here take arguments, so the shape is "name, then anything that is not the definition's `()`".
    """
    return re.search(rf"^[ \t]*{re.escape(name)}(?!\s*\(\))(\s|$)", text, re.MULTILINE) is not None


def _read_plugin_patches() -> str:
    sources = sorted(
        path.read_text(encoding="utf-8")
        for path in HERMES_PLUGIN_DIR.glob("*.py")
        if path.name not in {"__init__.py", "bootstrap.py"}
    )
    assert sources, f"no patch modules found under {HERMES_PLUGIN_DIR}"
    return "\n".join(sources)


def test_hermes_wrapper_does_not_rediscover_from_gateway_history():
    """Fresh hermes-aify must not export gateway session.most_recent as current."""
    text = _read_install_sh()
    assert "rediscover_hermes_session_id" not in text
    assert "HERMES_REDISCOVERED_SESSION_ID" not in text
    assert "[hermes-aify] session id rediscovered" not in text


def test_hermes_wrapper_exports_only_explicit_resume_handle_before_launch():
    """Only explicit --resume/--session-id should seed HERMES_SESSION_ID."""
    text = _read_hermes_wrapper()
    assert 'HERMES_EXPLICIT_SESSION_HANDLE="false"' in text
    idx = text.find('if [ "$HERMES_EXPLICIT_SESSION_HANDLE" = "true" ]')
    assert idx > 0
    window = text[idx : idx + 350]
    assert 'export HERMES_SESSION_ID="$HERMES_SESSION_HANDLE"' in window
    assert 'export AIFY_SESSION_HANDLE="$HERMES_SESSION_HANDLE"' in window
    assert 'export AIFY_EXPLICIT_SESSION_HANDLE="true"' in window
    assert 'if [ -n "$HERMES_SESSION_HANDLE" ]; then' not in text


def test_hermes_wrapper_consumes_resume_args_for_tui_default():
    """`hermes-aify --resume id` must consume the resume arg and exec a
    default `--tui ... --resume <handle>`.

    Updated 2026-05-31 for the visible-TUI wrapper rework: the resume exec now
    lives in the `aify_hermes_exec_plain_or_tui` helper and places the bypass
    `HERMES_PERMISSION_FLAGS` between `--tui` and `--resume`. The old
    `aify_hermes_run_foreground` helper no longer exists.
    """
    text = _read_hermes_wrapper()
    assert 'if [ "$PREV_ARG" = "--resume" ] || [ "$PREV_ARG" = "--session-id" ] || [ "$PREV_ARG" = "-r" ]; then' in text
    assert 'HERMES_ARGS+=("$ARG")\n  if [ "$PREV_ARG" = "--resume" ]' not in text
    assert 'exec "$HERMES_RUNTIME_COMMAND" --tui "${HERMES_PERMISSION_FLAGS[@]}" --resume "$HERMES_SESSION_HANDLE"' in text


def test_hermes_wrapper_fallback_preserves_explicit_resume_handle():
    """The terminal (non-managed / passthrough) launch path must still resume
    the explicit Hermes session when one was given.

    Updated 2026-05-31 for the visible-TUI wrapper rework: the separate
    `aify_hermes_fallback()` helper is gone. `aify_hermes_exec_plain_or_tui()`
    is now both the default-TUI helper and the terminal fallback (it is the
    last statement in the wrapper). It resumes the explicit handle when set and
    only execs the raw `${HERMES_ARGS[@]}` passthrough when the operator
    actually supplied subcommand args.
    """
    text = _read_hermes_wrapper()
    helper_idx = text.find("aify_hermes_exec_plain_or_tui()")
    assert helper_idx > 0
    helper = text[helper_idx : helper_idx + 750]
    # Explicit --resume handle is preserved in the default-TUI branch.
    assert 'exec "$HERMES_RUNTIME_COMMAND" --tui "${HERMES_PERMISSION_FLAGS[@]}" --resume "$HERMES_SESSION_HANDLE"' in helper
    # The raw passthrough exec is gated behind a non-empty HERMES_ARGS check, so
    # an explicit handle is never dropped by an unconditional argv passthrough.
    assert 'if [ ${#HERMES_ARGS[@]} -eq 0 ]; then' in helper

    # The helper is wired in as the wrapper's terminal launch path.
    assert "\naify_hermes_exec_plain_or_tui\n" in text
    # The removed standalone fallback helper must not reappear.
    assert "aify_hermes_fallback()" not in text


def test_hermes_wrapper_forces_utf8_python_io():
    """Windows non-UTF-8 consoles must not crash Hermes subprocess readers."""
    text = _read_hermes_wrapper()
    assert 'export PYTHONUTF8="${PYTHONUTF8:-1}"' in text
    assert 'export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"' in text


def test_hermes_windows_shim_uses_powershell_not_git_bash_for_tui():
    """Windows PowerShell launches must keep native Hermes attached to console."""
    text = _read_install_sh()
    # DEFINED *and* CALLED. The bare token is satisfied by `install_hermes_windows_tui_shim() {`
    # alone, so deleting the invocation would leave this green while Windows hermes fell back to the
    # git-bash launch this test exists to prevent. Same shape as the claude turn-hook check fixed in
    # b3cdcd46; a call here takes arguments, so it is "the name at line start followed by something
    # other than `()`".
    assert _defines(text, "install_hermes_windows_tui_shim")
    assert _calls(text, "install_hermes_windows_tui_shim"), (
        "the shim installer is defined but never invoked — the wrapper would not be written"
    )
    assert "hermes-aify.ps1" in text
    assert 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0hermes-aify.ps1" %*' in text
    # The PS fallback launches the native Hermes TUI resuming the explicit handle.
    # The permission-bypass flags are appended via `(@(...) + $HermesPermissionFlags)`,
    # so match the resume invocation up to the array close.
    assert "Invoke-HermesRuntime (@('--tui', '--resume', \\$HermesSessionHandle) + \\$HermesPermissionFlags)" in text
    assert "exit (Invoke-HermesRuntime" not in text


def test_hermes_installer_patches_visible_session_bind():
    """Hermes managed/resident delivery must bind to the open TUI session, not
    resume a hidden sid.

    Updated 2026-05-31: the `patch_hermes_gateway_visible_bind` install.sh
    source patch was removed (Plan 1.4) and the behavior moved into the durable
    hermes-aify plugin. Assert the bind method + TeeTransport mirroring there.
    """
    text = _read_plugin_patches()
    assert "aify.session.bind_transport" in text
    # TeeTransport mirrors the visible-session transport with the bridge
    # transport; the plugin binds it via a `tee_transport` alias and imports
    # TeeTransport from tui_gateway.transport.
    assert "tee_transport(primary, bridge_transport)" in text
    assert "from tui_gateway.transport import TeeTransport as tee_transport" in text


def test_hermes_visible_bind_falls_back_to_single_active_session():
    """If the saved handle is stale but this wrapper gateway has exactly one
    visible session, bind to that session instead of failing or forking hidden.

    Updated 2026-05-31: this fallback moved from the removed install.sh source
    patch into the hermes-aify plugin's resolve_visible_session().
    """
    text = _read_plugin_patches()
    assert "visible session fallback: saved handle not active; using sole active session" in text
    assert "active_candidates" in text
    # The single-candidate guard is what gates the fallback.
    assert "len(active_candidates) == 1" in text


def test_hermes_wrapper_pins_stable_resume_session():
    """Session continuity is now DETERMINISTIC, not discovered.

    Updated 2026-06-03 (native-session-id model): the synthetic per-agent
    `aify-<agentId>` pinned key (`AIFY_HERMES_PINNED_SESSION`) was retired on the
    bash side. The bash wrapper now resumes the agent's REAL native hermes session
    id resolved up-front — from the agent-keyed marker (`readSessionIdMarker`),
    converged against the live gateway's `resolve-session` ground truth
    (`HERMES_RESUME_REAL_ID`) — and passes it as `hermes --tui --resume <id>`, so
    a relaunch reuses the SAME transcript with no duplication and the session id is
    known before launch (superseding active-session-file discovery). The PowerShell
    wrapper was brought to PARITY on 2026-06-03: the synthetic `aify-<agentId>`
    pin (`$env:HERMES_TUI_RESUME = $pinnedSession` + `--resume $pinnedSession`) was
    retired. The PS1 managed branch now reads the agent's real native id from the
    marker (`readSessionIdMarker`), converges it against the gateway's
    `resolve-session` ground truth (`$hermesResumeRealId`), and resumes that real
    id (explicit operator `--resume` still wins, fresh session otherwise). Either
    way the resume target is deterministic, not discovered, and is honored via an
    explicit `--resume` flag (the env var alone is stripped).
    """
    text = _read_hermes_wrapper()
    # Bash: deterministic real-native-session-id resume, resolved up-front.
    assert "HERMES_RESUME_REAL_ID" in text, "bash wrapper must resolve a deterministic real session id up-front"
    assert 'node "$AIFY_HERMES_MANAGED_HOST_JS" resolve-session' in text, (
        "bash wrapper must converge the resume id against the live gateway (resolve-session)"
    )
    assert '--tui --resume "$HERMES_RESUME_REAL_ID"' in text, (
        "bash wrapper must resume the resolved real session id via an explicit --resume"
    )
    ps = _read_hermes_ps1()
    # PowerShell: native-session-id model parity (2026-06-03). The synthetic
    # pinned-session pin is GONE; the managed branch resumes the resolved real id.
    assert '$env:HERMES_TUI_RESUME = $pinnedSession' not in ps, (
        "PowerShell wrapper must NOT pin a synthetic HERMES_TUI_RESUME session anymore"
    )
    assert "$pinnedSession = 'aify-' +" not in ps, (
        "PowerShell wrapper must NOT build a synthetic 'aify-<agentId>' resume handle anymore"
    )
    assert "node $AifyHermesManagedHostJs resolve-session $HermesAifyAgentId" in ps, (
        "PowerShell wrapper must converge the resume id against the live gateway (resolve-session)"
    )
    assert "Invoke-HermesRuntime (@('--tui', '--resume', $hermesResumeRealId) + $HermesPermissionFlags)" in ps, (
        "PowerShell wrapper must resume the resolved real session id via an explicit --resume"
    )
    # PowerShell: re-export the gateway URL so the MCP child registers a real
    # ws:// gatewayUrl (parity with bash AIFY_HERMES_GATEWAY_URL fix).
    assert "$env:AIFY_HERMES_GATEWAY_URL = $hermesHost.wsUrl" in ps, (
        "PowerShell wrapper must re-export AIFY_HERMES_GATEWAY_URL for resident-run registration"
    )
    assert "--resume" in ps, "wrapper must pass --resume so the session id is honored (env var alone is stripped)"


def test_hermes_installer_preserves_wrapper_active_session_file():
    """Hermes main.py must not discard the wrapper-provided active-session file.

    Updated 2026-05-31: the install.sh `patch_hermes_tui_active_session_file`
    source patch was removed (commit aab3cd7) and the behavior moved into the
    hermes-aify plugin's `patch_hermes_cli_main`, which wraps `_launch_tui` so
    the mkstemp/unlink dance does not throw away the wrapper-provided
    `HERMES_TUI_ACTIVE_SESSION_FILE`.
    """
    text = _read_plugin_patches()
    assert "def patch_hermes_cli_main(" in text
    # It wraps the real _launch_tui (idempotently) rather than the old source
    # patch's standalone helper.
    assert 'getattr(module, "_launch_tui", None)' in text
    assert 'setattr(module, "_launch_tui", launch_tui_with_active_file)' in text
    # It keys off the wrapper-provided active-session-file env var.
    assert 'os.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE", "").strip()' in text


def test_hermes_installer_patches_codex_stream_nonetype_fallback():
    """Hermes openai-codex stream bugs should fall back to raw create stream."""
    text = _read_install_sh()
    # Same reason as the shim above: the patch must be APPLIED, not merely defined. A definition
    # with no call means the NoneType stream bug returns and this test still passes.
    assert _defines(text, "patch_hermes_codex_stream_none_fallback")
    assert _calls(text, "patch_hermes_codex_stream_none_fallback"), (
        "the codex-stream patch is defined but never applied to the hermes install"
    )
    assert "Responses stream hit SDK NoneType iterable bug" in text
    assert "agent._run_codex_create_stream_fallback(api_kwargs, client=active_client)" in text
    assert "if not isinstance(_out, list) or not _out:" in text


def test_hermes_wrapper_loads_aify_plugin_by_default():
    """hermes-aify should load the durable aify plugin unless explicitly disabled."""
    text = _read_install_sh()
    assert "AIFY_HERMES_PLUGIN" in text
    assert "integrations/hermes-aify-plugin" in text
    assert "AIFY_HERMES_DISABLE_PLUGIN" in text
    assert "PYTHONPATH" in text
