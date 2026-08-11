"""`aify-comms doctor` — the verifier under the name people already know.

The doctor shipped as a standalone `aify-doctor` binary and the operator's objection to that was
fair: one product should not need two command names remembered. It is now reachable as a
subcommand of the wrapper that already exists, with the old binary kept as an alias because ~40
references in docs, skills and agent habits point at it.

Static-text checks against the wrapper install.sh EMITS, the same pattern as the other
test_install_*.py files — the wrapper is generated bash, so the shape is what can be pinned.

The thing that would actually break here is subtle and worth naming: MCP clients launch this
wrapper with NO arguments to get the stdio bridge. The subcommand is only safe because a first
argument of "doctor" can never come from that path, and because the dispatch happens BEFORE the
bridge is started. A future edit that moves the branch below the bridge exec, or that starts
consuming positional arguments for the bridge, would break either MCP startup or the subcommand —
and every MCP client on the host fails at once if it is the first.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _launcher_body() -> str:
    """The heredoc that install_bridge_launcher writes to ~/.local/bin/aify-comms."""
    text = _install_sh()
    start = text.index("install_bridge_launcher()")
    return text[start : start + 8000]


def test_the_wrapper_dispatches_a_doctor_subcommand():
    body = _launcher_body()
    assert '= "doctor" ]' in body, "aify-comms doctor must be routed by the wrapper"
    assert "doctor.js" in body, "the subcommand must exec the same script the alias does"


def test_doctor_forwards_its_arguments():
    """`--json` and `--strict` are the whole point for scripted and agent callers."""
    body = _launcher_body()
    at = body.index('= "doctor" ]')
    branch = body[at : at + 400]
    assert "shift" in branch, "the subcommand word must be consumed before forwarding"
    assert '"\\$@"' in branch, "remaining arguments must reach doctor.js"


def test_the_subcommand_is_dispatched_BEFORE_anything_else_runs():
    """If this lands after the bridge starts, the subcommand never runs — and if the bridge stops
    starting on a bare invocation, every MCP client on the host breaks at once."""
    body = _launcher_body()
    assert body.index('= "doctor" ]') < body.index('= "--version" ]'), (
        "the doctor branch must precede the other argument handling"
    )


def test_the_standalone_alias_is_still_installed():
    """Removing it would break docs, skills and agent habits that name `aify-doctor`."""
    text = _install_sh()
    assert 'DOCTOR_PATH="$DOCTOR_BIN_DIR/aify-doctor"' in text
    assert 'exec node \\"$AIFY_BRIDGE_DIR/doctor.js\\"' in text


def test_the_installer_tells_the_operator_the_preferred_name_first():
    text = _install_sh()
    at = text.index("Verifier installed:")
    announcement = text[at : at + 300]
    assert "aify-comms doctor" in announcement
    assert announcement.index("aify-comms doctor") < announcement.index("aify-doctor  ")
