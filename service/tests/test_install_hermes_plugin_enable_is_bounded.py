"""install.sh must not let one hermes CLI call hang the whole install.

THE INCIDENT, 2026-09-12. A routine `install.sh --client hermes` sat for twelve minutes with no
output. `hermes plugins enable aify-comms` was alive at 0% CPU the whole time, blocked on nothing
the installer could see, and the run looked like a slow install rather than a stuck one because the
call's output is redirected to /dev/null by design.

WHAT MAKES IT A DEFECT RATHER THAN A HERMES QUIRK. The installer ALREADY handles a hermes CLI it
cannot use: `_enable_hermes_plugin_in_config` patches config.yaml directly, and it is the `else` of
that very `if`. A CLI that never returns is unavailable in every sense that matters, and the fallback
could not run because the condition never finished evaluating. The hang was not the surprising part;
an unbounded external call in front of a working fallback was.

WHAT THESE TESTS PROVE, and the limit of it. They drive the SHELL FUNCTION, not a full install: a
stub that sleeps is called through `_bounded_hermes_call` and must return within the deadline. They
do not prove the install completes — that is measured by running it, which is how this was found.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"


def _bash() -> str:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    return bash


def _call(script: str) -> subprocess.CompletedProcess:
    """Source install.sh's function definitions and run `script` against them.

    install.sh is sourced with a guard env var so it defines its functions and exits before doing
    anything: this test must never touch the operator's wrappers, MCP configs or hermes install.
    """
    body = (
        "set -u\n"
        # Pull in ONLY the two function definitions under test, by name, rather than sourcing a
        # 2,900-line installer that would start doing real work.
        f"eval \"$(sed -n '/^_bounded_hermes_call() {{/,/^}}/p' {INSTALL_SH.as_posix()})\"\n"
        f"{script}\n"
    )
    return subprocess.run([_bash(), "-c", body], capture_output=True, text=True, timeout=120)


def test_the_installer_defines_a_bounded_call_and_uses_it_for_plugins_enable() -> None:
    source = INSTALL_SH.read_text(encoding="utf-8")
    assert "_bounded_hermes_call() {" in source, "the bound was removed"
    # THE CALL SITE, not just the helper. A helper proven in isolation leaves its caller unproven,
    # which is the defect shape this repo keeps meeting.
    assert "_bounded_hermes_call 30 \"$hermes_bin\" plugins enable aify-comms" in source
    assert "\"$hermes_bin\" plugins enable aify-comms >/dev/null 2>&1; then" not in source, (
        "the unbounded call is back"
    )


def test_a_hermes_that_hangs_is_abandoned_rather_than_waited_on() -> None:
    started = time.monotonic()
    result = _call("_bounded_hermes_call 2 sleep 60; echo \"exit=$?\"")
    elapsed = time.monotonic() - started
    assert "exit=0" not in result.stdout, "a hung call reported success"
    assert elapsed < 30, f"the call was not bounded: {elapsed:.1f}s"


def test_POSITIVE_CONTROL_a_hermes_that_answers_still_succeeds() -> None:
    # Without this the test above passes for a helper that refuses everything, and the installer
    # would silently stop using the CLI it prefers.
    result = _call("_bounded_hermes_call 30 true; echo \"exit=$?\"")
    assert "exit=0" in result.stdout, result.stderr


def test_a_failing_hermes_is_a_failure_and_not_a_timeout() -> None:
    # The fallback must run for an ordinary non-zero exit exactly as it did before the bound.
    result = _call("_bounded_hermes_call 30 false; echo \"exit=$?\"")
    assert "exit=0" not in result.stdout
