"""An update must not silently drop a hook the operator opted into.

`--with-hook` is opt-in, and `redeploy.sh` -- the documented one-command update -- does not pass it.
So every update printed "[4/4] Notification hook skipped (use --with-hook to enable)" and left the
hook's REGISTRATION at whatever an older install wrote.

The hook's code was never the exposure: notify-check.js lives in the bridge directory, which is
mirrored on every install. What could go stale is the line that points at it -- the command shape in
~/.claude/settings.json, codex's hooks.json, or hermes' config.yaml. A changed command shape would keep
the old one, forever, with the installer reporting success.

Opting in once means opting in. The flag decides whether to install a hook that is not there; it does
not decide whether to maintain one that is.

These exercise the detector by running it, against fixture config roots. The alternative -- letting it
read the operator's live ~/.claude/settings.json -- is a test that reports whatever this machine
happens to be configured with.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DETECTOR = REPO / "scripts" / "hook-installed.sh"

# What a registered hook looks like in each client's config: a command naming the bridge script.
REGISTERED = {
    "claude": ("settings.json", '{"hooks":{"Notification":[{"hooks":[{"command":"node ~/.aify-comms/mcp/stdio/notify-check.js"}]}]}}'),
    "codex": ("hooks.json", '{"notify":["node","~/.aify-comms/mcp/stdio/notify-check.js"]}'),
    "hermes": ("config.yaml", 'agent_hooks:\n  - command: node ~/.aify-comms/mcp/stdio/notify-check.js\n'),
}

# The same files, configured with somebody else's hooks. Answering "yes" here would make every host
# with any hook at all look like an aify install.
UNRELATED = {
    "claude": ("settings.json", '{"hooks":{"Notification":[{"hooks":[{"command":"say done"}]}]}}'),
    "codex": ("hooks.json", '{"notify":["afplay","/System/Sounds/Glass.aiff"]}'),
    "hermes": ("config.yaml", 'agent_hooks:\n  - command: echo hi\n'),
}

CLIENTS = sorted(REGISTERED)


def _bash():
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _ask(client: str, root: Path) -> int:
    return subprocess.run(
        [_bash(), str(DETECTOR), client, str(root).replace("\\", "/")],
        capture_output=True, text=True,
    ).returncode


@pytest.mark.parametrize("client", CLIENTS)
def test_a_registered_hook_is_found(client, tmp_path):
    name, body = REGISTERED[client]
    (tmp_path / name).write_text(body, encoding="utf-8")
    assert _ask(client, tmp_path) == 0


@pytest.mark.parametrize("client", CLIENTS)
def test_an_absent_config_is_no_hook(client, tmp_path):
    assert _ask(client, tmp_path) == 1


@pytest.mark.parametrize("client", CLIENTS)
def test_somebody_elses_hook_is_not_ours(client, tmp_path):
    name, body = UNRELATED[client]
    (tmp_path / name).write_text(body, encoding="utf-8")
    assert _ask(client, tmp_path) == 1


def test_an_unknown_client_is_refused_rather_than_answered(tmp_path):
    """Absence of a lookup is not absence of a hook. A confident 'no' for a client nobody looked at is
    how one runtime keeps the silent skip while the others are fixed -- the hardest kind to notice."""
    assert _ask("opencode", tmp_path) == 2


def test_every_client_install_sh_can_hook_is_covered_here():
    """Derived from install.sh rather than listed, so a fourth hookable client cannot arrive without
    either a detector branch or a red test."""
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    hookable = set(re.findall(r"install_([a-z]+)_hook\(\)", text))
    assert hookable, "found no install_<client>_hook functions; the derivation is broken"
    assert hookable == set(CLIENTS), f"install.sh hooks {hookable}, the detector covers {set(CLIENTS)}"


def _ask_with_env(client: str, env_overrides: dict) -> int:
    """Run the detector with no root argument, under a controlled environment."""
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [_bash(), str(DETECTOR), client], capture_output=True, text=True, env=env
    ).returncode


def test_claude_and_codex_roots_are_derived_and_match_what_the_installers_write():
    """Behaviour, not a source grep. install.sh passes no root for these two, so the detector's own
    default has to be the same directory install_claude_hook / install_codex_hook write into --
    `$HOME/.claude` and `${CODEX_HOME:-$HOME/.codex}`. Driven by moving those variables and checking
    the answer follows.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aify-hookenv-") as tmp:
        home = Path(tmp)
        (home / ".claude").mkdir()
        name, body = REGISTERED["claude"]
        (home / ".claude" / name).write_text(body, encoding="utf-8")
        assert _ask_with_env("claude", {"HOME": str(home).replace("\\", "/")}) == 0

        codex = home / "elsewhere"
        codex.mkdir()
        name, body = REGISTERED["codex"]
        (codex / name).write_text(body, encoding="utf-8")
        assert _ask_with_env("codex", {"CODEX_HOME": str(codex).replace("\\", "/")}) == 0

        # And the negative: same env, no file, must be a clean "no" rather than an error.
        empty = home / "empty"
        empty.mkdir()
        assert _ask_with_env("codex", {"CODEX_HOME": str(empty).replace("\\", "/")}) == 1


def test_hermes_refuses_to_guess_because_its_root_is_not_derivable():
    """hermes' config lives wherever `hermes_config_root` resolves. Defaulting to ~/.hermes would
    answer "no hook" for the one client whose path cannot be derived -- keeping the silent skip for a
    single runtime while the other two looked fixed. Unanswerable must not read as "no"."""
    assert _ask_with_env("hermes", {}) == 2, "no root passed must be unanswerable, not a clean no"


def test_install_sh_passes_hermes_its_resolved_root():
    """The one root install.sh must supply, since the detector refuses to guess it. An agreement test:
    the two sites live in one shell script and cannot import each other."""
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    assert '_hook_root="$(hermes_config_root' in text, "hermes must be asked, not assumed"
    assert 'local config_root="$(hermes_config_root)"' in text, (
        "and install_hermes_hook must write to that same root"
    )
