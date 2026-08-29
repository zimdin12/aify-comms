r"""Re-running install.sh keeps the delegation setting the host already chose.

THIS BIT ON A LIVE HOST, 2026-08-29, while deploying. `install.sh --client claude` re-rendered the
environment-bridge launcher with `AIFY_COMMS_DELEGATE_SPAWNS=""`, and `aify-comms doctor` said so
immediately: "Managed spawns are hosted by the aify-comms bridge itself. aify-env is not in the spawn
path." Had the bridge been relaunched in between, the whole fleet would have gone back to
bridge-hosted spawns with nothing announcing it.

THE READER ALREADY EXISTED AND ALREADY NAMED THIS BUG. `scripts/installed-delegation.sh` opens with:
"The third setting an update can silently discard, after the endpoint and the notification hook.
`redeploy.sh` re-renders the launcher by calling install.sh, and install.sh bakes delegation only when
asked -- so an update whose entire promise is 'nothing changes but the code' moved managed spawns back
off aify-env."

`redeploy.sh` was fixed. install.sh was not, and install.sh is the command the DOCTOR tells you to run
after a bridge edit: "Re-run `bash install.sh --client <runtime>` AND relaunch the wrappers". So
following the tool's own advice turned delegation off.

TWO CHANGES, AND THE SECOND IS WHY NOTHING CAUGHT THE FIRST. install.sh now reads the installed
launcher back through the same reader `redeploy.sh` uses, and `install_bridge_launcher` honours
`EMIT_WRAPPERS_DIR` like every other wrapper does. Before that it wrote only to `~/.local/bin`, so the
ONE launcher carrying this setting was the one no test could render -- a render-only hook that skipped
exactly the file worth checking.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
URL = "http://127.0.0.2:1"

#: A previously-installed launcher, reduced to the two lines the reader parses. The real file is ~180
#: lines of bash; the reader takes these two, so a fixture carrying them is the same input.
INSTALLED = """#!/bin/bash
export AIFY_COMMS_DELEGATE_SPAWNS="{on}"
export AIFY_ENV_ENDPOINT="{endpoint}"
"""


def _bash() -> str:
    # `shutil.which`, never the bare name: on Windows a plain "bash" resolves to WSL's, which cannot
    # read a C:\ path and exits 127. Every install test here resolves it this way.
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _render(directory: Path, *extra: str) -> str:
    """Render the bridge launcher into `directory` and return its text.

    `--emit-wrappers` writes and EXITS before npm, MCP registration, hook install or any env mutation,
    so this cannot touch a live host's bin.
    """
    result = subprocess.run(
        [_bash(), str(INSTALL_SH), "--client", "claude", URL, "--emit-wrappers",
         directory.as_posix(), *extra],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    launcher = directory / "aify-comms"
    assert launcher.exists(), (
        "--emit-wrappers did not render the bridge launcher. It is the one that carries the "
        "delegation setting, so leaving it out of the render-only hook is what let a regression in "
        "exactly that setting reach a live host."
    )
    return launcher.read_text(encoding="utf-8")


def test_a_plain_reinstall_keeps_delegation_on():
    """THE CASE THAT BIT. No flag, an installed launcher that delegates, and the setting survives."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        directory = Path(tmp)
        (directory / "aify-comms").write_text(
            INSTALLED.format(on="1", endpoint="http://127.0.0.1:8802"), encoding="utf-8")
        text = _render(directory)
    assert 'export AIFY_COMMS_DELEGATE_SPAWNS="1"' in text, (
        "a re-install turned delegation OFF. The next bridge start would host spawns itself and "
        "aify-env would leave the spawn path, silently."
    )
    assert 'export AIFY_ENV_ENDPOINT="http://127.0.0.1:8802"' in text


def test_a_non_default_endpoint_is_carried_not_defaulted():
    """The endpoint travels with the setting. Defaulting it would point spawns at a daemon nobody
    chose, which is the reason the reader refuses to invent one."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        directory = Path(tmp)
        (directory / "aify-comms").write_text(
            INSTALLED.format(on="1", endpoint="http://127.0.0.1:9999"), encoding="utf-8")
        text = _render(directory)
    assert 'export AIFY_ENV_ENDPOINT="http://127.0.0.1:9999"' in text


def test_no_installed_launcher_means_no_delegation_invented():
    """A fresh host stays local. Carrying forward is recovering a CHOICE, never manufacturing one."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        text = _render(Path(tmp))
    assert 'export AIFY_COMMS_DELEGATE_SPAWNS=""' in text


def test_a_launcher_that_says_off_stays_off():
    """`"0"` is the obvious way to write off, and the reader treats it as off -- the same four words
    the spawn path accepts. A reader asking merely "is it non-blank" would carry a setting that was
    never in effect."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        directory = Path(tmp)
        (directory / "aify-comms").write_text(
            INSTALLED.format(on="0", endpoint="http://127.0.0.1:8802"), encoding="utf-8")
        text = _render(directory)
    assert 'export AIFY_COMMS_DELEGATE_SPAWNS=""' in text


def test_delegation_on_with_no_endpoint_is_not_reproduced():
    """A launcher in that state is one install.sh should not copy: it would default the endpoint, and
    defaulting silently is what the reader exists to prevent."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        directory = Path(tmp)
        (directory / "aify-comms").write_text(
            INSTALLED.format(on="1", endpoint=""), encoding="utf-8")
        text = _render(directory)
    assert 'export AIFY_COMMS_DELEGATE_SPAWNS=""' in text


def test_the_flag_still_wins_over_what_is_installed():
    """An operator changing the endpoint must not be overruled by the old one."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        directory = Path(tmp)
        (directory / "aify-comms").write_text(
            INSTALLED.format(on="1", endpoint="http://127.0.0.1:8802"), encoding="utf-8")
        text = _render(directory, "--delegate-spawns", "http://127.0.0.1:7777")
    assert 'export AIFY_ENV_ENDPOINT="http://127.0.0.1:7777"' in text


def test_there_is_a_way_back():
    """A sticky default with no off switch is its own trap. `--no-delegate-spawns` is the way out, and
    without it the carry-forward would mean one choice survives rather than the host's choice."""
    with tempfile.TemporaryDirectory(prefix="aify-deleg-") as tmp:
        directory = Path(tmp)
        (directory / "aify-comms").write_text(
            INSTALLED.format(on="1", endpoint="http://127.0.0.1:8802"), encoding="utf-8")
        text = _render(directory, "--no-delegate-spawns")
    assert 'export AIFY_COMMS_DELEGATE_SPAWNS=""' in text
    assert 'export AIFY_ENV_ENDPOINT=""' in text


def test_the_reader_is_shared_with_redeploy_rather_than_copied():
    """One parse, two callers. The endpoint reader's own history is why: `redeploy.sh` and install.sh
    each held a copy of that regex, both still matching the PRE-CONTRACT shape, and both silently
    stopped finding anything."""
    install_text = INSTALL_SH.read_text(encoding="utf-8")
    redeploy_text = (REPO / "redeploy.sh").read_text(encoding="utf-8")
    for name, text in (("install.sh", install_text), ("redeploy.sh", redeploy_text)):
        assert "scripts/installed-delegation.sh" in text, (
            f"{name} no longer uses the shared delegation reader; a second copy of that parse is how "
            "the endpoint readers came to disagree"
        )
