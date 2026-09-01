r"""The environment-bridge launcher must carry the API key, or securing the service kills the fleet.

WHAT THIS PINS. `~/.local/bin/aify-comms` starts the environment bridge -- the process that owns
managed spawns and the fleet. It authenticates with `CLAUDE_MCP_API_KEY` / `AIFY_API_KEY`, read once
at module load (`mcp/stdio/aify-service-endpoint.mjs:54`) and otherwise absent from every request.

MEASURED 2026-08-30, WITH CONTROLS. `API_KEY` appeared 0 times in the generated launcher block and 0
times in the INSTALLED launcher, against 4 and 3 respectively for `AIFY_SERVER_URL` (so the search
worked) and 0 for a string known to be absent (so it could say no). The consequence is the worst
shape a failure can have: the moment an operator set `API_KEY` -- which is exactly what README tells
them to do to close an open service -- the bridge would 401 on every call, managed spawns would stop,
and nothing anywhere would name the cause. The remedy for an exposed service took the fleet down.

WHY IT IS NOT A DUPLICATE. `test_the_installer_finds_the_key_the_service_already_uses.py` proves the
RESOLVER finds a key. Nothing proved the launcher CARRIES one, and those are different failures: the
resolver was correct throughout the window in which the bridge got nothing.

THE RUNTIME PRECEDENCE IS PART OF THE CONTRACT. The launcher emits `${AIFY_API_KEY:-<baked>}`, not a
bare assignment, so an operator who rotates the key in their shell is not overridden by whatever was
true at install time. A bare assignment would pass a naive "is the key present" test and silently
clobber a rotation, so the shape is asserted, not just the value.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

#: Hostile by construction: set, and pointing nowhere. A render must never reach the live service.
URL = "http://127.0.0.2:1"

#: The two names `scripts/api-key.sh` reads from the environment. Plain `API_KEY` is deliberately
#: NOT one of them -- that spelling belongs to `.env`, where the service reads it.
SHELL_KEY_NAMES = ("CLAUDE_MCP_API_KEY", "AIFY_API_KEY")


def _bash() -> str:
    # `shutil.which`, never the bare name: on Windows a plain "bash" resolves to WSL's, which cannot
    # read a C:\ path and exits 127. Every install test here resolves it this way.
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _render(directory: Path, key: str | None) -> str:
    """Render the bridge launcher with `key` in the environment, and return its text.

    `--emit-wrappers` writes and EXITS before npm, MCP registration, hook install or any env
    mutation, so this cannot touch a live host's bin or its installed clients.
    """
    env = dict(os.environ)
    # Seal BOTH names so the operator's own shell cannot decide this test's outcome. A run that
    # inherits a real key would pass the keyed cases for the wrong reason and never fail the bare one.
    for name in SHELL_KEY_NAMES:
        env.pop(name, None)
    # AND SEAL `.env`, WHICH IS THE OTHER HALF AND WAS NOT SEALED. `scripts/api-key.sh` resolves the
    # shell FIRST and the repo's `.env` SECOND, and refuses with exit 3 when the two name different
    # keys -- correctly, because that is the state where clients get one key and the service runs on
    # another. Sealing only the shell therefore left the operator's real file deciding the outcome:
    # the day `API_KEY` was set for real, all four tests here failed on a CONFLICT they had set up
    # without being able to see it. Pointing at an empty file in the temp directory makes "no key
    # configured anywhere" the baseline each case then varies from.
    env["AIFY_ENV_FILE"] = (directory / "sealed.env").as_posix()
    (directory / "sealed.env").write_text("", encoding="utf-8")
    if key is not None:
        env["AIFY_API_KEY"] = key

    result = subprocess.run(
        [_bash(), str(INSTALL_SH), "--client", "claude", URL,
         "--emit-wrappers", directory.as_posix()],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    launcher = directory / "aify-comms"
    assert launcher.exists(), "--emit-wrappers did not render the bridge launcher"
    return launcher.read_text(encoding="utf-8")


def test_a_configured_key_is_baked_into_the_launcher():
    """THE DEFECT. With a key configured, the bridge must be given it."""
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        text = _render(Path(tmp), "sk-fixture-abc123")
    assert 'export AIFY_API_KEY="${AIFY_API_KEY:-sk-fixture-abc123}"' in text, (
        "the environment bridge was rendered without the key the service is configured with. "
        "Setting API_KEY would 401 every spawn, with no error naming the cause."
    )


def test_an_inherited_key_wins_over_the_baked_one():
    """A rotation in the operator's shell must not be overridden by install-time state."""
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        text = _render(Path(tmp), "sk-fixture-abc123")
    # The `${VAR:-default}` shape is the whole contract. A bare `export AIFY_API_KEY="sk-..."`
    # contains the key and would satisfy a value-only assertion while clobbering a live rotation.
    assert 'export AIFY_API_KEY="sk-fixture-abc123"' not in text, (
        "the key was baked as a bare assignment, which overrides an operator's rotated key"
    )


def test_with_no_key_the_line_is_inert_rather_than_absent():
    """An unkeyed host is a supported configuration and must not be changed by this line.

    Empty is what `apiKeyFrom()` already treats as absent, so the export is a no-op rather than a
    credential. Emitting nothing at all would work too; emitting an EMPTY value must not.
    """
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        text = _render(Path(tmp), None)
    assert 'export AIFY_API_KEY="${AIFY_API_KEY:-}"' in text

    # POSITIVE CONTROL for that assertion: the same render definitely carries other baked settings,
    # so an empty key is a real absence and not a launcher that failed to render its env block.
    assert 'export AIFY_SERVER_URL=' in text
    assert URL in text


def test_the_launcher_never_carries_a_key_the_environment_did_not_supply():
    """Nothing invents a credential. With no key anywhere, no key-shaped value appears."""
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        text = _render(Path(tmp), None)
    for name in SHELL_KEY_NAMES:
        for line in text.splitlines():
            if line.startswith(f'export {name}='):
                assert line.endswith(':-}"'), f"{name} was given a value nobody configured: {line}"


def test_the_seal_this_file_depends_on_actually_seals():
    """`AIFY_ENV_FILE` is honoured, proven by making it name a key and reading it back.

    WITHOUT THIS, THE SEAL CAN ROT SILENTLY. Every case above sets `AIFY_ENV_FILE` and would keep
    passing if `scripts/api-key.sh` stopped honouring it -- they would simply be reading the
    operator's real `.env` again, which is the state that broke them in the first place and did so
    invisibly for as long as no key was set. A seal nothing verifies is a comment.

    Both directions in one test: the sealed file's key is returned, and a DIFFERENT sealed file with
    no key returns nothing. One without the other would pass for a script that ignored the variable
    and happened to find the same answer.
    """
    with tempfile.TemporaryDirectory(prefix="aify-seal-") as tmp:
        sealed = Path(tmp) / "sealed.env"
        env = dict(os.environ)
        for name in SHELL_KEY_NAMES:
            env.pop(name, None)
        env["AIFY_ENV_FILE"] = sealed.as_posix()

        sealed.write_text("API_KEY=sk-sealed-value-not-the-real-one\n", encoding="utf-8")
        found = subprocess.run(
            [_bash(), str(REPO / "scripts" / "api-key.sh")],
            capture_output=True, text=True, env=env,
        )
        assert found.returncode == 0, found.stderr
        assert found.stdout.strip() == "sk-sealed-value-not-the-real-one", (
            "scripts/api-key.sh ignored AIFY_ENV_FILE, so every test in this file is reading the "
            "operator's real .env and its result is decided by the host"
        )

        sealed.write_text("# nothing here\n", encoding="utf-8")
        empty = subprocess.run(
            [_bash(), str(REPO / "scripts" / "api-key.sh")],
            capture_output=True, text=True, env=env,
        )
        assert empty.returncode == 0, empty.stderr
        assert empty.stdout.strip() == "", (
            "a sealed file with no key still produced one, so the resolver reached past the seal"
        )
