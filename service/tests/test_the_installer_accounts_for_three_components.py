"""Installing aify-comms installs one third of the system, and the installer used to say so nowhere.

THE OPERATOR'S ASK, 2026-09-02: *"install stuff has to be good also, it should have changed almost
totally, because installing is now including 3 components and each repo has its own install
instructions"*. Until 2026-09-03 `install.sh` finished by describing only itself -- on a host where a
missing aify-env means managed spawns cannot run AT ALL, since v0.6.1 removed the command that used
to host them. A component nobody is told about is one nobody installs.

IT NEVER RUNS WHAT IT MEASURES, and that is the design constraint rather than a nicety. A bare
`aify-env` STARTS the host tier: it supersedes whichever instance is serving the machine, and the
predecessor reaps its managed workers on the way out. `aify-comms` had exactly that property until
v0.6.1 and took the operator's fleet down twice from it. So presence is `command -v`, which resolves
a name without executing it, and the version is READ out of the installed package's own
`package.json`. A version check that runs the thing it is checking is an outage waiting for a cron.

WHY A SEPARATE SCRIPT rather than eight more lines in `install.sh`. That file is 2,977 lines against
a ratchet that may only go DOWN, and the rendering belongs beside the reader anyway -- two copies of
"what does missing look like" is how they come to disagree. The installer carries ONE line, and the
duplicated "Verifier installed" block it replaced paid for it: the file came out at exactly its
ceiling rather than over it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "components.sh"

#: The three the operator names. DERIVED from the script's own table rather than typed again here --
#: a second list agrees until one of them is corrected.
EXPECTED = ("aify-comms", "aify-env", "aify-wrapper")


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash(), SCRIPT.as_posix(), *args],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, **(env or {})},
    )


def _rows(out: str) -> dict[str, tuple[str, str, str]]:
    parsed = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        name, state, version, howto = (line.split("\t") + ["", "", ""])[:4]
        parsed[name] = (state, version, howto)
    return parsed


def test_ALL_THREE_COMPONENTS_ARE_REPORTED():
    """THE ASK. Not "the one you just installed" -- all three, every time."""
    done = _run()
    rows = _rows(done.stdout)
    assert tuple(rows) == EXPECTED, f"reported {tuple(rows)}: {done.stdout}{done.stderr}"


def test_every_row_says_installed_or_missing_and_nothing_else():
    """A third state would be a state no caller handles. `--missing` and the renderer both branch on
    exactly these two."""
    for name, (state, _v, _h) in _rows(_run().stdout).items():
        assert state in {"installed", "missing"}, f"{name} reported {state!r}"


def test_A_MISSING_COMPONENT_CARRIES_ITS_OWN_INSTALLER():
    """"Each repo has its own install instructions" is the operator's phrasing, and a MISSING row that
    does not carry them sends the reader looking. Driven by emptying PATH, which makes every probe
    fail -- the only way to see the missing branch on a host that has all three."""
    done = _run(env={"PATH": ""})
    rows = _rows(done.stdout)
    assert tuple(rows) == EXPECTED, f"an empty PATH changed which components are reported: {rows}"
    for name, (state, version, howto) in rows.items():
        assert state == "missing", f"{name} resolved with no PATH: {state}"
        assert howto.strip(), f"{name} is missing and says nothing about how to get it"
        assert version == "", f"{name} is missing but reported version {version!r}"
    assert "aify-env" in rows and "install.sh" in rows["aify-env"][2]


def test_the_probe_can_say_both_yes_and_no():
    """POSITIVE CONTROL FOR THE ONE ABOVE. If the empty-PATH run reported `missing` because the script
    errored rather than because the probes failed, the test above would pass on a broken instrument.
    So the same script, on the real PATH, must find at least one component."""
    found = [n for n, (s, _v, _h) in _rows(_run().stdout).items() if s == "installed"]
    assert found, "no component resolved on the real PATH, so `missing` above proves nothing"


def test_a_missing_component_is_a_NONZERO_exit():
    """So a caller can gate on it without parsing. Both directions, in one test, because an exit
    status that is always 0 and one that is always 1 are equally useless."""
    assert _run(env={"PATH": ""}).returncode != 0
    assert _run().returncode == 0, "a host with all three components reported a failure"


def test_missing_only_lists_nothing_when_everything_is_present():
    """`--missing` is the form a caller uses to ask "is anything absent". On a complete host it must
    be silent rather than repeating the full listing."""
    assert _run("--missing").stdout.strip() == ""
    assert _run("--missing", env={"PATH": ""}).stdout.strip() != ""


def test_the_render_form_is_human_and_names_the_missing_ones():
    """The installer prints THIS, so what it prints is what an operator acts on."""
    rendered = _run("--render").stdout
    assert "Components on this host:" in rendered
    for name in EXPECTED:
        assert name in rendered, f"{name} is absent from the rendered form"

    absent = _run("--render", env={"PATH": ""}).stdout
    assert "MISSING" in absent, "a missing component is not called out in the human form"
    assert "install.sh" in absent, "and it does not say how to get one"


def test_IT_NEVER_EXECUTES_WHAT_IT_MEASURES():
    """THE SAFETY PROPERTY, and the one worth a source assertion. Running `aify-env` to ask its
    version starts the host tier, supersedes whichever instance is serving the machine, and reaps
    that instance's managed workers -- the incident aify-comms had twice before v0.6.1 removed its
    own version of this command.

    Asserted on the script's CODE, comments stripped, because the whole point is that no test can
    safely demonstrate the failure: proving it by running the thing is the accident."""
    code = "\n".join(
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    for probe in ("aify-env", "aify-wrapper-check"):
        for line in code.splitlines():
            if probe not in line:
                continue
            assert "command -v" in line or "|" in line, (
                f"a line names {probe} outside the probe table and the presence check, which is how "
                f"a component gets EXECUTED to measure it: {line.strip()}"
            )
    # And the version comes from a file, never from a process.
    assert "package.json" in code
    assert "--version" not in code, "it asks a component its version by running it"


def test_the_installer_calls_it_rather_than_re_deriving_it():
    """The rendering lives in ONE place. `install.sh` carrying its own copy is how the two come to
    disagree about what `missing` looks like -- and it is 2,977 lines against a ratchet, so a second
    copy costs twice."""
    install = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "scripts/components.sh" in install, "the installer does not report the three components"
    assert install.count("scripts/components.sh") == 1, "more than one call site to keep in step"


@pytest.mark.parametrize("flag", ["--missing", "--render", ""])
def test_no_mode_writes_anything(flag, tmp_path):
    """A reader that mutates is not a reader. Nothing here may create, move or delete a file --
    especially not on a host where the things being measured own live processes."""
    before = sorted(p.name for p in tmp_path.iterdir())
    _run(*( [flag] if flag else [] ), env={"HOME": tmp_path.as_posix()})
    assert sorted(p.name for p in tmp_path.iterdir()) == before
