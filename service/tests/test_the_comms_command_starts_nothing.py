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

AND THE INSTALL RECORD SURVIVES. Two `export` lines nothing in the file consumes any more are read
back by `scripts/installed-delegation.sh` and by doctor's `spawn-delegation`, so that a redeploy
carries the host's own choice forward. Losing them moved managed spawns off aify-env once already,
minutes after the flip on 2026-08-25.
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


def test_a_bare_run_refuses_and_says_where_the_host_tier_is(tmp_path):
    """THE INCIDENT, closed. Exit is non-zero so a script cannot mistake the refusal for a start."""
    done = _run(launcher("claude", name=COMMS), tmp_path)
    assert done.returncode == 2, f"a bare run exited {done.returncode}: {done.stdout}{done.stderr}"
    said = done.stdout + done.stderr
    assert "aify-env" in said, "the refusal does not say where managed agents are hosted now"
    assert "starts nothing" in said


def test_an_unknown_option_refuses_the_same_way(tmp_path):
    """It used to be a separate branch that exited 2 before the bridge started. With no bridge left
    the two cases are one, and this pins that the merge did not make a stray flag START something."""
    done = _run(launcher("claude", name=COMMS), tmp_path, "--nonsense")
    assert done.returncode == 2
    assert "aify-env" in done.stdout + done.stderr


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


def test_no_api_key_is_baked_into_a_command_that_starts_nothing():
    """The key was there because the BRIDGE could not reach its own service without one. Every
    surviving branch reaches the service on its own terms -- `doctor` resolves the key itself, and
    `--version` curls the unauthenticated `/version`. A secret copied into a file that no longer
    needs it is a copy to leak for nothing."""
    text = launcher("claude", name=COMMS)
    assert "AIFY_API_KEY" not in text, "an API key is still baked into the verifier"


def test_THE_INSTALL_RECORD_IS_STILL_READABLE():
    """`scripts/installed-delegation.sh` greps these two lines out of THIS file so a redeploy carries
    the host's own choice forward. Its own regex is the assertion, not a paraphrase of it."""
    text = launcher("claude", "--delegate-spawns", name=COMMS)
    assert 'export AIFY_COMMS_DELEGATE_SPAWNS="1"' in text, "the delegation record was lost"
    assert 'export AIFY_ENV_ENDPOINT="' in text, "the aify-env endpoint record was lost"


def test_the_reader_actually_recovers_the_endpoint(tmp_path):
    """END TO END, through the real script rather than through its shape. A record that matches a
    regex in a test and not the one in the reader is a record nothing reads."""
    (tmp_path / COMMS).write_text(
        launcher("claude", "--delegate-spawns", name=COMMS), encoding="utf-8", newline="\n",
    )
    done = subprocess.run(
        [bash(), "scripts/installed-delegation.sh", tmp_path.as_posix()],
        capture_output=True, text=True, timeout=120,
        cwd=(__import__("pathlib").Path(__file__).resolve().parents[2]).as_posix(),
    )
    assert done.returncode == 0, f"the reader found no delegation: {done.stdout}{done.stderr}"
    assert done.stdout.strip().startswith("http"), done.stdout


def test_the_reader_says_no_when_delegation_is_off(tmp_path):
    """NEGATIVE CONTROL for the pair above. Without it, a reader that printed an endpoint for every
    input would satisfy the positive case and prove nothing."""
    (tmp_path / COMMS).write_text(launcher("claude", name=COMMS), encoding="utf-8", newline="\n")
    done = subprocess.run(
        [bash(), "scripts/installed-delegation.sh", tmp_path.as_posix()],
        capture_output=True, text=True, timeout=120,
        cwd=(__import__("pathlib").Path(__file__).resolve().parents[2]).as_posix(),
    )
    assert done.returncode == 1, f"the reader invented a delegation: {done.stdout}"
