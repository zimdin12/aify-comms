"""The `[4/4]` branch that decides whether a notification hook gets refreshed, executed.

`--with-hook` is opt-in and `redeploy.sh` — the documented one-command update — does not pass it. Until
today that meant every update printed "hook skipped" and left the hook's registration wherever an older
install put it. The fix was a branch: opted in once means opted in.

The DETECTOR behind it is tested by running it (test_reinstall_keeps_the_hook.py). The branch itself was
asserted only by reading source, which proves a line was written. It sits at step 4 of 4, so no test can
reach it without a full install — and a full install writes into ~/.claude, runs npm, and registers MCP
servers on a machine with a live fleet.

So the block is LIFTED from install.sh verbatim and run with the hook installers stubbed. Lifted rather
than restated: a copy here would pass while install.sh did something else, which is the whole failure
this file guards against.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

REGISTERED_CLAUDE = '{"hooks":{"Notification":[{"hooks":[{"command":"node notify-check.js"}]}]}}'


def _bash() -> str:
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _posix(p) -> str:
    return str(p).replace("\\", "/")


def hook_branch() -> str:
    """install.sh's own hook decision, lifted verbatim."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('_hook_root=""')
    end = text.index('echo "[4/4] Notification hook skipped', start)
    block = text[start:text.index("\nfi", end) + 3]
    assert "hook-installed.sh" in block, "install.sh no longer consults the detector; extraction stale"
    assert 'install_${CLIENT}_hook' in block, "the derived dispatch is gone; extraction stale"
    return block


def _run(client: str, with_hook: bool, home: Path, config: str | None) -> str:
    """Run the lifted branch with the real detector and a stubbed installer."""
    if config is not None:
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        (home / ".claude" / "settings.json").write_text(config, encoding="utf-8")

    script = home / "branch.sh"
    script.write_text(
        f'SCRIPT_DIR="{_posix(REPO)}"\n'
        f'CLIENT="{client}"\n'
        f'WITH_HOOK={"true" if with_hook else "false"}\n'
        'hermes_config_root() { printf "%s" "$HOME/.hermes"; }\n'
        'install_claude_hook() { echo "STUB-RAN claude"; }\n'
        'install_codex_hook() { echo "STUB-RAN codex"; }\n'
        + hook_branch() + "\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": _posix(home)}
    result = subprocess.run([_bash(), _posix(script)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"branch exited {result.returncode}: {result.stderr}"
    return result.stdout


def test_with_hook_installs_when_none_is_there():
    with tempfile.TemporaryDirectory(prefix="aify-hookbranch-") as tmp:
        out = _run("claude", True, Path(tmp), config=None)
        assert "STUB-RAN claude" in out, "--with-hook must install"
        assert "Installing" in out


def test_an_already_installed_hook_is_refreshed_without_the_flag():
    """The claim: an update that does not pass --with-hook still maintains the hook."""
    with tempfile.TemporaryDirectory(prefix="aify-hookbranch2-") as tmp:
        out = _run("claude", False, Path(tmp), config=REGISTERED_CLAUDE)
        assert "STUB-RAN claude" in out, "an installed hook must be refreshed on a plain reinstall"
        assert "Refreshing" in out, "and it must say which of the two it did"


def test_no_flag_and_no_hook_stays_skipped():
    """The opt-in still means something: this must not start installing hooks nobody asked for."""
    with tempfile.TemporaryDirectory(prefix="aify-hookbranch3-") as tmp:
        out = _run("claude", False, Path(tmp), config=None)
        assert "STUB-RAN" not in out
        assert "skipped" in out


def test_somebody_elses_hook_does_not_count_as_ours():
    """A host with an unrelated Notification hook has not opted into aify's."""
    with tempfile.TemporaryDirectory(prefix="aify-hookbranch4-") as tmp:
        out = _run("claude", False, Path(tmp), config='{"hooks":{"Notification":[{"hooks":[{"command":"say done"}]}]}}')
        assert "STUB-RAN" not in out
        assert "skipped" in out


def test_a_client_with_no_installer_says_so_instead_of_failing():
    """The dispatch is derived from which install_<client>_hook exists. A client without one must
    report it, not crash a run that has already copied the bridge."""
    with tempfile.TemporaryDirectory(prefix="aify-hookbranch5-") as tmp:
        out = _run("hermes", True, Path(tmp), config=None)
        assert "not implemented for hermes" in out, out
        assert "STUB-RAN" not in out


def test_a_config_root_containing_a_space_still_finds_the_hook():
    """The argument install.sh passes must survive a path with a space in it.

    It was written unquoted so an EMPTY root would disappear rather than become an empty argument.
    That works and word-splits: a hermes root under a user folder named `Foo Bar` -- ordinary
    on Windows — arrives as two arguments, the detector reads the wrong path, answers "no hook", and
    hermes silently stops being refreshed on update. That is the exact silent skip this branch exists
    to remove, reintroduced for the one client whose root is passed rather than derived.

    Quoting is safe for the empty case too: hermes refuses an empty root outright, and claude and codex
    fall back to their own defaults via `${root:-...}`.
    """
    with tempfile.TemporaryDirectory(prefix="aify-hookspace-") as tmp:
        home = Path(tmp) / "home with a space"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(REGISTERED_CLAUDE, encoding="utf-8")
        out = _run("claude", False, home, config=None)
        assert "STUB-RAN claude" in out, "a space in the path must not read as 'no hook installed'"
        assert "Refreshing" in out


def test_a_hermes_root_containing_a_space_is_passed_whole():
    """hermes is the case that matters: its root is PASSED, not derived, so it is the only one that
    travels through that argument at all."""
    with tempfile.TemporaryDirectory(prefix="aify-hermesspace-") as tmp:
        home = Path(tmp)
        root = home / "hermes config"
        root.mkdir(parents=True)
        (root / "config.yaml").write_text("agent_hooks:\n  - command: node notify-check.js\n", encoding="utf-8")

        script = home / "branch.sh"
        script.write_text(
            f'SCRIPT_DIR="{_posix(REPO)}"\n'
            'CLIENT="hermes"\n'
            "WITH_HOOK=false\n"
            f'hermes_config_root() {{ printf "%s" "{_posix(root)}"; }}\n'
            'install_hermes_hook() { echo "STUB-RAN hermes"; }\n'
            + hook_branch() + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [_bash(), _posix(script)], capture_output=True, text=True,
            env={**os.environ, "HOME": _posix(home)},
        )
        assert result.returncode == 0, result.stderr
        assert "STUB-RAN hermes" in result.stdout, (
            f"a hermes root with a space must be passed whole: {result.stdout}"
        )
