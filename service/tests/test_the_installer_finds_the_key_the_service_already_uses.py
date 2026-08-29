r"""The installer never read `.env`, so setting an API key would have 401'd the whole fleet.

THE TRAP, in order. `service/main.py` installs its auth middleware only `if config.api_key`, so with
nothing set -- today's state, measured against the running container on 2026-08-30: `GET
/api/v1/agents` returns 200 with no key AND with a wrong key -- everything works keyless. The moment
an operator sets `API_KEY` in `.env` and restarts, the service starts refusing unauthenticated calls.
Every installed client holds no key. And re-running `install.sh` does not fix them: it resolved the
key as `${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}` in four hand-typed places, none of which looks at
`.env`, so it would find nothing and write the same keyless config again.

The remedy that obviously should work makes no difference, which is the worst shape a failure can
have. Those four call sites now share one script, `scripts/api-key.sh`, beside the two others that
read what the host already chose before an update overwrites it.

NEVER ROTATED. An existing key is reused verbatim wherever it is found: minting a fresh one would
leave every already-installed bridge holding the old value, which is the same outage caused by the fix
for it.

These RUN the real script rather than asserting on its text: a resolver that is never executed is a
claim about a file, not about an installer. It is copied into a scratch repo root first, because it
resolves `.env` relative to its own location -- so these can never read the operator's real key.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
API_KEY_SH = REPO / "scripts" / "api-key.sh"


def _bash() -> str:
    # `shutil.which`, never the bare name: on Windows a plain "bash" resolves to WSL's, which cannot
    # read a C:\ path and exits 127. Every install test here resolves it this way.
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _run(root: Path, env: dict[str, str] | None, generate: bool) -> subprocess.CompletedProcess:
    # A HOSTILE environment by default: the operator's own key must not leak in and make an "it found
    # nothing" case quietly pass by finding theirs.
    sealed = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    sealed.update(env or {})
    command = [_bash(), str(root / "scripts" / "api-key.sh")] + (["--generate"] if generate else [])
    return subprocess.run(command, capture_output=True, text=True, env=sealed)


def _scratch_repo(directory: Path, env_text: str | None) -> Path:
    (directory / "scripts").mkdir()
    shutil.copy2(API_KEY_SH, directory / "scripts" / "api-key.sh")
    if env_text is not None:
        (directory / ".env").write_text(env_text, encoding="utf-8")
    return directory


def _ask(env_text: str | None, env: dict[str, str] | None = None, generate: bool = False) -> str:
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), env_text)
        result = _run(root, env, generate)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()


def test_the_key_the_service_is_configured_with_is_found():
    """The whole point: `.env` is where the SERVICE reads its key, so it is where the installer must
    end up looking. This case returned empty before, and every client was configured keyless."""
    assert _ask("API_KEY=abc123\n") == "abc123"


def test_an_explicit_key_in_the_shell_still_wins():
    # Both names, in the bridge's own precedence. An operator exporting one is making a choice, and a
    # file cannot overrule it.
    assert _ask("API_KEY=from-file\n", {"CLAUDE_MCP_API_KEY": "from-shell"}) == "from-shell"
    assert _ask("API_KEY=from-file\n", {"AIFY_API_KEY": "from-shell"}) == "from-shell"
    assert _ask("API_KEY=from-file\n",
                {"CLAUDE_MCP_API_KEY": "first", "AIFY_API_KEY": "second"}) == "first"


def test_no_key_anywhere_is_an_ANSWER_and_not_an_error():
    """Running without a key is a supported configuration -- it is what this host does today -- so an
    empty result must be a clean empty string rather than a failure or an invented value."""
    assert _ask(None) == ""
    assert _ask("") == ""
    assert _ask("SOMETHING_ELSE=1\n") == ""


def test_the_env_file_is_read_and_never_sourced():
    """`.env` is operator-edited. Sourcing it would EXECUTE whatever is in it, as the operator, during
    an install -- so a value that looks like a command must come back as literal text."""
    assert _ask("API_KEY=$(echo pwned)\n") == "$(echo pwned)"
    backtick = chr(96)
    assert _ask(f"API_KEY={backtick}echo pwned{backtick}\n") == f"{backtick}echo pwned{backtick}"


def test_the_usual_env_file_spellings_all_parse():
    # Quoted and spaced forms are both common, and a key read with its quotes attached is a key that
    # matches nothing -- a 401 whose cause is invisible in every log on both sides.
    assert _ask('API_KEY="quoted"\n') == "quoted"
    assert _ask("API_KEY='single'\n") == "single"
    assert _ask("API_KEY = spaced\n") == "spaced"
    assert _ask("API_KEY=trailing\r\n") == "trailing", "a CRLF .env left a carriage return on the key"


def test_a_declared_but_EMPTY_key_is_no_key_and_still_exits_cleanly():
    """The shape an operator produces by starting to set one and stopping, and the one case where a
    stray non-zero exit would abort the whole install: the script runs under `set -euo pipefail`, and
    the branch that finds a line but no value is the only one that reaches the end without returning
    early. Verified by exit status, not just by the value returned."""
    assert _ask("API_KEY=\n") == ""
    assert _ask('API_KEY=""\n') == ""
    assert _ask("API_KEY=   \n") == ""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), "API_KEY=\n")
        result = _run(root, None, generate=False)
        assert result.returncode == 0, f"a blank key aborted the installer: {result.stderr}"


def test_a_commented_out_key_is_not_a_key():
    assert _ask("#API_KEY=disabled\n") == ""
    assert _ask("# API_KEY=disabled\nAPI_KEY=real\n") == "real"


def test_the_FIRST_definition_wins_the_way_a_dotenv_reader_reads_it():
    assert _ask("API_KEY=first\nAPI_KEY=second\n") == "first"


def test_generating_writes_the_key_where_the_SERVICE_will_read_it():
    """Both halves have to end up with the same value, and the service only reads `.env`."""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), None)
        result = _run(root, None, generate=True)
        assert result.returncode == 0, result.stderr
        key = result.stdout.strip()
        assert len(key) >= 32, f"a short key is worse than none: {key!r}"
        assert (root / ".env").read_text(encoding="utf-8").strip().endswith(key), \
            "the key was printed but not persisted where the service reads it"


def test_generating_twice_returns_the_SAME_key():
    """The failure the fix could cause, in its most direct form: a second install must not rotate."""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), None)
        first = _run(root, None, generate=True).stdout.strip()
        second = _run(root, None, generate=True).stdout.strip()
        assert first == second, "a second run minted a new key and orphaned every installed bridge"
        assert (root / ".env").read_text(encoding="utf-8").count("API_KEY=") == 1


def test_generating_REUSES_an_existing_key_rather_than_rotating():
    """A fresh key leaves every already-installed bridge holding the old one -- the same fleet-wide
    401 this whole change exists to prevent."""
    assert _ask("API_KEY=already-here-and-long-enough-to-be-real\n", generate=True) \
        == "already-here-and-long-enough-to-be-real"
    assert _ask("API_KEY=from-file\n", {"CLAUDE_MCP_API_KEY": "from-shell"}, generate=True) == "from-shell"


def test_a_generated_key_is_not_predictable():
    """Two independent repos must not produce the same key. A weak one reads as protection while being
    guessable, and every caller would then be configured to trust it."""
    keys = set()
    for _ in range(3):
        with tempfile.TemporaryDirectory() as scratch:
            root = _scratch_repo(Path(scratch), None)
            keys.add(_run(root, None, generate=True).stdout.strip())
    assert len(keys) == 3, f"generation is not random: {keys}"
    assert all(len(key) >= 32 for key in keys)


def test_install_sh_delegates_rather_than_carrying_its_own_copy():
    """The four call sites collapsed into one. A fifth hand-typed precedence is the drift this closes,
    and it is the shape the bridge had too -- five modules each spelling the same two names."""
    install_sh = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "scripts/api-key.sh" in install_sh, "install.sh no longer delegates to the one reader"
    assert "${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}" not in install_sh, \
        "install.sh re-typed the key precedence instead of calling the script"
