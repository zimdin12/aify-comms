r"""No launcher this installer renders carries the service API key. It used to, and had to.

WHAT THIS FILE USED TO SAY, AND WHY IT SAYS THE OPPOSITE NOW. It was
`test_the_environment_bridge_gets_the_key_the_service_uses.py`, and it pinned the reverse: the
`aify-comms` launcher STARTED the environment bridge -- the process that owned managed spawns and
the fleet -- and that bridge authenticates with `CLAUDE_MCP_API_KEY` / `AIFY_API_KEY`, read once at
module load (`mcp/stdio/aify-service-endpoint.mjs:54`) and otherwise absent from every request. So
the moment an operator set `API_KEY`, which is exactly what README tells them to do to close an open
service, the bridge 401'd on every call, managed spawns stopped, and nothing named the cause. The
remedy for an exposed service took the fleet down. Measured 2026-08-30 with controls: `API_KEY`
appeared 0 times in the generated block and 0 times in the installed launcher, against 4 and 3 for
`AIFY_SERVER_URL` (so the search worked) and 0 for a string known absent (so it could say no).

**v0.6.1 REMOVED THE BRIDGE, so it removed the reason.** aify-env is the host tier; `aify-comms` is
a verifier. Every branch that survives reaches the service on its own terms -- `doctor` execs
`doctor.js`, which resolves the key itself, and `--version` curls the unauthenticated `/version`.
PROVEN rather than assumed, and it was already true before the change: the `doctor` branch execs at
the top of the file, ABOVE where the export used to be, so the baked key was never in its
environment. `aify-comms doctor` reported the container build against a keyed service on 2026-09-03.

SO THE PROPERTY INVERTS AND STAYS WORTH A TEST. A secret copied into a file that no longer needs it
is a copy to leak for nothing, and this is the shape of leak nobody looks for: the launchers are
world-readable, they are copied by `redeploy.sh`, and they get pasted into issues.

MEASURED ACROSS EVERY RENDERED FILE, not just the one that used to carry it. A key added back to any
launcher would satisfy a test that only watched `aify-comms`.

THE SEAL IS THE OTHER HALF, and it has to be proven or every case here is vacuous. `api-key.sh`
resolves the shell FIRST and the repo's `.env` SECOND. Sealing only the shell left the operator's
real file deciding the outcome -- the day `API_KEY` was set for real, every case failed on a
conflict it had set up without being able to see it.
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

#: A value no real key could be, so finding it anywhere is unambiguous.
FIXTURE_KEY = "sk-fixture-should-never-be-written-abc123"

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


def _render(directory: Path, key: str | None) -> dict[str, str]:
    """Render every launcher with `key` configured, and return them all by name.

    `--emit-wrappers` writes and EXITS before npm, MCP registration, hook install or any env
    mutation, so this cannot touch a live host's bin or its installed clients.
    """
    env = dict(os.environ)
    for name in SHELL_KEY_NAMES:
        env.pop(name, None)
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
    rendered = {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "sealed.env"
    }
    assert "aify-comms" in rendered, "--emit-wrappers rendered no aify-comms command"
    assert "claude-aify" in rendered, "--emit-wrappers rendered no client launcher"
    return rendered


def test_A_CONFIGURED_KEY_REACHES_NO_LAUNCHER():
    """THE PROPERTY. With a key configured, no rendered file may contain it."""
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        rendered = _render(Path(tmp), FIXTURE_KEY)
    carrying = sorted(name for name, text in rendered.items() if FIXTURE_KEY in text)
    assert carrying == [], f"the service key was baked into: {carrying}"


def test_the_render_under_test_actually_saw_the_key():
    """POSITIVE CONTROL, and without it the assertion above is satisfied by a render that failed.

    An empty output directory, a crashed install.sh or a launcher that lost its whole env block all
    produce "the key is nowhere" -- the same answer as success. So this pins that the run rendered
    real launchers carrying the OTHER settings it was given.
    """
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        rendered = _render(Path(tmp), FIXTURE_KEY)
    assert URL in rendered["aify-comms"], "the render did not carry the server URL it was given"
    assert len(rendered) >= 2, f"implausibly few files rendered: {sorted(rendered)}"


def test_no_launcher_declares_a_key_variable_at_all():
    """Not merely "the value is absent": an `export AIFY_API_KEY=` line with any content is a
    destination, and a destination is where a value comes back."""
    with tempfile.TemporaryDirectory(prefix="aify-key-") as tmp:
        rendered = _render(Path(tmp), FIXTURE_KEY)
    offenders = [
        f"{name}: {line.strip()}"
        for name, text in rendered.items()
        for line in text.splitlines()
        if any(line.lstrip().startswith(f"export {n}=") for n in SHELL_KEY_NAMES)
    ]
    assert offenders == [], f"a launcher still declares a key variable: {offenders}"


def test_THE_SEAL_THIS_FILE_DEPENDS_ON_ACTUALLY_SEALS():
    """`AIFY_ENV_FILE` is honoured, proven by making it name a key and reading it back.

    WITHOUT THIS, THE SEAL CAN ROT SILENTLY. Every case above sets `AIFY_ENV_FILE` and would keep
    passing if `scripts/api-key.sh` stopped honouring it -- they would simply be reading the
    operator's real `.env` again, which is the state that broke this file's predecessor and did so
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
            "AIFY_ENV_FILE was ignored, so every case in this file is reading the operator's .env"
        )

        sealed.write_text("", encoding="utf-8")
        empty = subprocess.run(
            [_bash(), str(REPO / "scripts" / "api-key.sh")],
            capture_output=True, text=True, env=env,
        )
        assert empty.stdout.strip() == "", (
            f"an empty sealed .env still yielded a key: {empty.stdout!r}"
        )
