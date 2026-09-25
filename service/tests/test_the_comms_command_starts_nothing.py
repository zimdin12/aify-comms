"""The `aify-comms` command cannot start an environment bridge, because there is no longer one to be.

WHAT WAS REMOVED, v0.6.1. A bare `aify-comms` exec'd `server.js --environment-bridge`: a real
environment bridge that BY DESIGN superseded whichever one was already serving this environment, so
the older one exited and its managed workers were reaped. It took the whole managed fleet down twice
-- on 2026-08-11 from a four-second run meant only to confirm the launcher still started, and on
2026-08-20 when a backtick inside an unquoted heredoc executed the name. Both incidents are in this
repo's memory, and the mitigation was a rule: "never run a bare `aify-comms`". A rule everybody must
remember is a defect with a delay on it.

WHY IT COULD GO. aify-env is the host tier: it owns processes and PTYs, claims spawn requests, runs
the launchers and streams the consoles -- proven on real hardware on 2026-09-03, six lanes up with no
bridge running at all. `docs/TARGET_ARCHITECTURE.md` named exactly that as the condition
("install, restart, and one spawn with no bridge running"), and there is no longer a second spawner
for this command to be.

WHAT THESE PIN, in both directions. That the bridge is gone is the easy half and would be satisfied
by an empty file; most of what follows is that the command still WORKS -- `doctor` is what every
agent and roughly forty documents reach for, and `--check` and `--version` are the two answers an
operator gets before reinstalling anything. A removal that took the verifier with it would pass a
test written only about the removal.

AND THE INSTALL RECORD SURVIVES: the `AIFY_ENV_ENDPOINT` export, which nothing in the file consumes,
is read back by doctor's `spawn-delegation` row to know which aify-env to ask.
"""

from __future__ import annotations

import subprocess

from service.tests._launchers import bash, launcher

COMMS = "aify-comms"


def _run(text: str, tmp_path, *args: str) -> subprocess.CompletedProcess:
    """The rendered command, EXECUTED. Reading it can only prove what it says."""
    path = tmp_path / COMMS
    path.write_text(text, encoding="utf-8", newline="\n")
    return subprocess.run(
        [bash(), path.as_posix(), *args], capture_output=True, text=True, timeout=120,
    )


def _code(text: str) -> str:
    """The launcher's EXECUTABLE lines. Its comments record what was removed and why, in the words
    of the incidents -- so a scan of the whole file finds the flag it is asserting the absence of,
    and reads the history as the behaviour. Measured: that is exactly how this test first failed."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_THE_BRIDGE_EXEC_IS_GONE():
    """THE REMOVAL. `--environment-bridge` is the argv that made a process the bridge."""
    code = _code(launcher("claude", name=COMMS))
    assert "--environment-bridge" not in code, "the launcher can still start an environment bridge"
    # server.js IS still named, three times, and all three are the `--check` validation: an echo, a
    # file test and a `node --check` parse. None of them RUNS it, which is the actual property --
    # asserting the name were absent would fail on a launcher that is behaving correctly.
    runs = [
        line for line in code.splitlines()
        if "server.js" in line and not any(m in line for m in ("echo ", "[ -f ", "--check"))
    ]
    assert runs == [], f"the launcher still runs the MCP bridge script: {runs}"
    # POSITIVE CONTROL for the stripper: it must not have eaten the file. A scan of "" passes every
    # assertion above, which is the failure mode of measuring an emptied string.
    assert "exit 2" in code and "doctor.js" in code


def test_a_bare_run_and_an_unknown_option_both_refuse_and_say_where_the_host_tier_is(tmp_path):
    """THE INCIDENT, closed. Exit is non-zero so a script cannot mistake the refusal for a start.

    An unknown option used to be a separate branch that exited 2 before the bridge started. With no
    bridge left the two cases are one, and the second argv pins that the merge did not make a stray
    flag START something."""
    for args in ((), ("--nonsense",)):
        done = _run(launcher("claude", name=COMMS), tmp_path, *args)
        assert done.returncode == 2, f"{args} exited {done.returncode}: {done.stdout}{done.stderr}"
        said = done.stdout + done.stderr
        assert "aify-env" in said, f"{args}: the refusal does not say where managed agents are hosted now"
        assert "starts nothing" in said, args


def test_the_doctor_subcommand_still_execs_the_doctor():
    """CONTROL, and the one that matters most. Every agent and about forty documents reach for
    `aify-comms doctor`; a removal that took it along would satisfy every assertion above."""
    text = launcher("claude", name=COMMS)
    assert 'exec node "' in text and "doctor.js" in text, "the doctor subcommand is gone"
    assert text.index("doctor.js") < text.index("starts nothing"), (
        "the doctor branch is below the refusal, so `aify-comms doctor` would refuse instead of run"
    )


def test_check_and_help_still_answer(tmp_path):
    """The two read-only branches an operator uses before reinstalling anything."""
    text = launcher("claude", name=COMMS)
    checked = _run(text, tmp_path, "--check")
    assert checked.returncode in (0, 1), f"--check crashed: {checked.stdout}{checked.stderr}"
    assert "launcher check" in checked.stdout

    helped = _run(text, tmp_path, "--help")
    assert helped.returncode == 0
    assert "doctor" in helped.stdout and "aify-env" in helped.stdout.lower(), (
        "the help text does not point at the tier that hosts managed agents"
    )


def test_doctor_asks_the_service_this_host_was_installed_against(tmp_path):
    """v0.7 (B3). The launcher computed the installed endpoint and never passed it on, so `doctor.js`
    fell back to localhost:8800 and every service row on a host pointed at a LAN service asked the
    wrong machine. EXECUTED: doctor.js is swapped for a probe that prints the endpoint it was given,
    so nothing here reaches a real service."""
    import os
    import re

    text = launcher("claude", name=COMMS)
    baked = re.search(r'^SERVER_URL="\$\{AIFY_SERVER_URL:-([^}]*)\}"', text, re.M)
    assert baked, "control: the launcher bakes an endpoint"
    doctor = re.search(r'exec node "([^"]*doctor\.js)"', text)
    assert doctor, "control: the doctor branch names doctor.js"
    probe = tmp_path / "probe.js"
    probe.write_text("process.stdout.write(process.env.AIFY_SERVER_URL || '(none)');\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("AIFY_SERVER_URL", "AIFY_COMMS_URL")}
    path = tmp_path / COMMS
    path.write_text(text.replace(doctor.group(1), probe.as_posix()), encoding="utf-8", newline="\n")
    done = subprocess.run([bash(), path.as_posix(), "doctor"], capture_output=True, text=True, timeout=120, env=env)
    assert done.stdout == baked.group(1), f"doctor was given {done.stdout!r}, not {baked.group(1)!r}: {done.stderr}"
