r"""Every flag and script the install skill tells an agent to run is one that exists.

THE FAILURE THIS PREVENTS is the one the sibling gate was written for, one level along: four documents
told an operator to run `npm install -g aify-env`, which 404s, and it read as correct on the machine it
was written on. A flag is the same shape and cheaper to get wrong -- `install.sh` has grown and lost
options, and a skill naming `--with-key` instead of `--with-api-key` sends an agent into a usage error
it will then try to work around.

IT MATTERS MORE FOR A SKILL THAN FOR A DOC. A person reading a stale flag in a README tries it, sees
the usage line, and adapts. An agent following a skill has been told this is the procedure; when the
flag is refused it is as likely to invent a way past it as to stop.

DERIVED FROM `install.sh` ITSELF, never a second list. The flags come out of its argument parser, so a
flag removed there fails here rather than living on in prose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / ".claude" / "skills" / "aify-comms-install" / "SKILL.md"
INSTALL_SH = REPO / "install.sh"


def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _install_flags() -> set[str]:
    """The long options `install.sh` actually accepts, read from its own `case` arms."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    # Each arm is `    --name)` at the start of a line, optionally with alternatives.
    return set(re.findall(r"^\s*(--[a-z][a-z0-9-]*)\)", source, re.MULTILINE))


def test_the_parser_is_readable_at_all():
    """POSITIVE CONTROL. Every assertion below is "no unknown flags found", which a regex that matched
    nothing would satisfy for ever."""
    flags = _install_flags()
    assert len(flags) >= 4, f"only found {flags}; the argument parser is not being read"
    assert "--client" in flags


def test_every_install_flag_the_skill_names_is_real():
    named = set(re.findall(r"(--[a-z][a-z0-9-]+)", _skill_text()))
    # `--json` belongs to the state script and `-d`/`--build` to docker compose; only the ones on an
    # `install.sh` line are this parser's to answer for.
    on_install_lines = {
        flag
        for line in _skill_text().splitlines() if "install.sh" in line
        for flag in re.findall(r"(--[a-z][a-z0-9-]+)", line)
    }
    unknown = sorted(on_install_lines - _install_flags())
    assert unknown == [], (
        f"the install skill tells an agent to pass {unknown}, which install.sh does not accept. "
        "An agent told this is the procedure is as likely to work around the refusal as to stop."
    )
    assert named, "the skill names no flags at all; it has stopped being a procedure"


def test_every_script_the_skill_runs_exists():
    scripts = set(re.findall(r"(?:bash|\./)\s*([\w./-]+\.sh)", _skill_text()))
    missing = sorted(name for name in scripts if not (REPO / name.lstrip("./")).exists())
    assert missing == [], f"the skill runs {missing}, which are not in this repo"
    assert "scripts/install-state.sh" in scripts, (
        "the skill no longer starts by reading what the machine already has, which is the step that "
        "keeps the rest of it short"
    )


def test_the_state_script_is_executable_and_reads_only():
    """It runs before anything is installed, so it must not need anything installed. And it must not
    print the key: a state report that leaks a credential into a terminal, a log and an agent's
    context is a worse problem than the one it solves."""
    source = (REPO / "scripts" / "install-state.sh").read_text(encoding="utf-8")
    assert 'printf "%s\\n" "$key"' not in source
    assert "api-key.sh" in source, "it should ask the one key reader rather than re-deriving"
    # The whole point is that it does not start anything.
    for dangerous in ("aify-comms\n", "npm install", "docker compose up", "install.sh"):
        assert f"\n{dangerous}" not in source, f"the state script runs `{dangerous.strip()}`"


def test_the_skill_keeps_the_bare_command_warning():
    """`aify-comms` with no arguments supersedes the bridge already serving the host, and nine managed
    agents were reaped by a four-second run meant only to check a launcher. An install skill is
    exactly where someone reaches for a smoke test."""
    text = _skill_text()
    assert "--check" in text, "the skill does not offer the safe alternative"
    assert re.search(r"[Nn]ever run a bare `aify-comms`", text), (
        "the skill lost the one warning that an install-time smoke test must not be `aify-comms`"
    )
