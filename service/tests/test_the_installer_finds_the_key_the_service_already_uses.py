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
    """An operator exporting a key is making a choice, and a file with NO key cannot overrule it.

    This test used to pit the shell against a file holding a DIFFERENT key and assert the shell won.
    That is the case where clients get one value and the restarted service runs on the other, so it
    is now a refusal rather than a preference -- see the conflict test below. What survives here is
    the part that was always right: precedence between the two SHELL names, and the shell answering
    when the file says nothing."""
    assert _ask("OTHER=1\n", {"CLAUDE_MCP_API_KEY": "from-shell"}) == "from-shell"
    assert _ask("OTHER=1\n", {"AIFY_API_KEY": "from-shell"}) == "from-shell"
    assert _ask("OTHER=1\n",
                {"CLAUDE_MCP_API_KEY": "first", "AIFY_API_KEY": "second"}) == "first"
    # And a file naming the SAME key is agreement, not a conflict, so the shell still answers.
    assert _ask("API_KEY=same\n", {"CLAUDE_MCP_API_KEY": "same"}) == "same"


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


def test_the_LAST_definition_wins_THE_WAY_COMPOSE_READS_IT():
    """This test asserted `first` and was named for "the way a dotenv reader reads it". Some dotenv
    libraries do read first. The consumer here is not one of them: `docker-compose.yml` passes `.env`
    as `env_file`, and Compose parses it into a map where a later line overwrites an earlier one.

    MEASURED against real Compose, both ways, in a throwaway project: on `API_KEY=FIRST_aaa` then
    `API_KEY=LAST_bbb`, `docker compose config` renders `LAST_bbb`; swapping the two lines swaps the
    answer, so the reading is positional and not a property of those strings. The script took the
    first (`grep -m1`). So a duplicated key handed the SERVICE one value and every CLIENT the other:
    a 401 on every call, with both halves looking correctly configured."""
    assert _ask("API_KEY=first\nAPI_KEY=second\n") == "second"


def test_GENERATING_CANNOT_AUTHOR_THE_DUPLICATE_IT_USED_TO_MISREAD():
    """The old `--generate` appended. An operator's `.env` commonly carries a blank `API_KEY=` from a
    template, which read as absent, so generating appended a SECOND definition -- and then the next
    run read the blank first one and appended a third. The file the two readers disagree about was
    written by this script."""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), "API_KEY=\nOTHER=kept\n")
        result = _run(root, None, generate=True)
        assert result.returncode == 0, result.stderr
        text = (root / ".env").read_text(encoding="utf-8")
        definitions = [line for line in text.splitlines() if line.strip().startswith("API_KEY=")]
        assert len(definitions) == 1, f"generate authored a duplicate definition: {definitions!r}"
        assert definitions[0].split("=", 1)[1] == result.stdout.strip()
        assert "OTHER=kept" in text, "rewriting the key dropped an unrelated line"


def test_A_SHELL_KEY_AND_A_DIFFERENT_FILE_KEY_IS_REFUSED_NOT_PREFERRED():
    """The case that configures every client with a value the service will refuse. The shell key used
    to win silently: clients got it, the service restarted onto the file's key, and every call 401'd
    with both halves looking correctly installed. There is a right action available, so this refuses
    and names it rather than picking one."""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), "API_KEY=" + ("f" * 40) + "\n")
        result = _run(root, {"AIFY_API_KEY": "s" * 40}, generate=False)
        assert result.returncode == 3, f"a shell/file conflict was not refused: {result!r}"
        assert result.stdout.strip() == "", "it printed a key it had just called ambiguous"
        assert "DIFFERENT API keys" in result.stderr
        # POSITIVE CONTROL: the SAME key in both places is not a conflict, so the refusal above is a
        # real disagreement rather than a guard that fires whenever the shell is set at all.
        agreed = _run(root, {"AIFY_API_KEY": "f" * 40}, generate=False)
        assert agreed.returncode == 0, agreed.stderr
        assert agreed.stdout.strip() == "f" * 40


def test_A_SHELL_ONLY_KEY_IS_PERSISTED_WHERE_THE_SERVICE_WILL_READ_IT():
    """It was exported into every client and never written to `.env`, so the next service restart came
    up keyless while every client presented a key. Nothing 401s in that direction -- a keyless service
    accepts anything -- which is exactly why it could sit there unnoticed until the operator set a
    key for real."""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), "OTHER=kept\n")
        result = _run(root, {"AIFY_API_KEY": "s" * 40}, generate=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "s" * 40, "generate rotated a key it was handed"
        text = (root / ".env").read_text(encoding="utf-8")
        assert "API_KEY=" + ("s" * 40) in text, "the key handed to clients was never persisted"


def test_A_WEAK_KEY_FROM_ANY_SOURCE_IS_REPORTED():
    """Validation applied only to newly generated bytes, so a weak key that arrived any other way was
    reused as though it had been vetted. It is REPORTED and not refused: the service is already
    running on it and every installed bridge holds it, so aborting neither rotates it nor helps."""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), "API_KEY=short\n")
        result = _run(root, None, generate=False)
        assert result.returncode == 0, "a weak existing key must not abort the install"
        assert result.stdout.strip() == "short", "it warned and then withheld the key in use"
        assert "guessable" in result.stderr
        # NEGATIVE CONTROL: a key at the floor draws no warning, so the warning tracks length rather
        # than firing on every key that reaches the check.
        fine = _run(_scratch_repo(Path(tempfile.mkdtemp()), "API_KEY=" + ("k" * 32) + "\n"), None, False)
        assert fine.returncode == 0 and fine.stderr.strip() == "", fine.stderr


def test_ABSENT_AND_ERROR_ARE_DIFFERENT_ANSWERS():
    """`aify_api_key() { ...; || true; }` in install.sh collapsed a real failure into "no key", so a
    keyless config got written after an error. They are distinguishable now: absent exits 0 with
    empty stdout, a conflict exits 3."""
    with tempfile.TemporaryDirectory() as scratch:
        absent = _run(_scratch_repo(Path(scratch), "OTHER=x\n"), None, generate=False)
        assert absent.returncode == 0 and absent.stdout.strip() == ""
    with tempfile.TemporaryDirectory() as scratch:
        root = _scratch_repo(Path(scratch), "API_KEY=" + ("f" * 40) + "\n")
        conflict = _run(root, {"AIFY_API_KEY": "s" * 40}, generate=False)
        assert conflict.returncode != 0, "an error is indistinguishable from finding no key"
        assert conflict.returncode != absent.returncode


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
    # A shell key over a file that names NO key is still handed back unrotated. (This line used to
    # put a DIFFERENT key in the file and assert the shell won; that pairing is a refusal now.)
    assert _ask("OTHER=1\n", {"CLAUDE_MCP_API_KEY": "from-shell"}, generate=True) == "from-shell"


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
