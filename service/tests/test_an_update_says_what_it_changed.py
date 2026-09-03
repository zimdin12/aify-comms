"""An update that cannot say whether it worked is a reinstall with a better name.

THE OPERATOR'S ASK, 2026-09-02: *"install should be also used for updating"*. `redeploy.sh` is that
path and it ended by printing "wrappers refreshed" -- a claim about what it ATTEMPTED. CLAUDE.md
opens with why that is not enough: *every deploy path in this repo fails silently*. No error,
everything looks installed, and what you changed is not what is running. Measured on 2026-08-30: the
aify-wrapper pin was bumped in both package files, `npm install` reported success, and
`node_modules/aify-wrapper` still held the previous code.

WHAT IT DOES NOW. The verifier's verdicts are captured before the update and again after, and the
difference is reported in three categories that must not be collapsed: broken by this update, fixed
by this update, and already failing. The last one matters as much as the first -- misattributing a
pre-existing failure to the update sends the next reader somewhere else entirely, which is the
mistake the spawn reaper's error text made and had to be corrected for.

NOTHING NAMES A CHECK, deliberately. The obvious design picks the four or five doctor checks that
"answer whether a deploy took", and a hand-kept list of those is a defect with a delay on it: this
repo has already had four scanners hardcode the doctor's filename, so moving one check reddened three
while the fourth stayed green by no longer looking. Comparing the whole verdict set needs no list,
cannot go stale, and catches an update breaking something nobody thought to associate with it.

THE BUG THIS NEARLY SHIPPED WITH, and the reason `test_CRLF_AND_LF_AGREE` exists. On Windows the
verifier's output arrives CRLF, so the parsed state was `ok` with a trailing carriage return and
matched neither "ok" nor "fail". Every comparison fell through all three branches and printed
NOTHING, with exit 0 -- a tool whose entire job is to say what changed, reporting "no change" for
every update, silently. It looked correct in a first hand-check because `sed -i` had normalised the
fixture between one case and the next. That is the same shape as the night's other defects: an
instrument that cannot fail is not evidence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "deploy-delta.sh"

#: Both line endings, because the whole point is that they must produce the same answer.
LF = "\n"
CRLF = "\r\n"


def _write(path: Path, rows: list[str], ending: str) -> Path:
    path.write_bytes(ending.join(rows).encode() + ending.encode())
    return path


def _compare(before: Path, after: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash(), SCRIPT.as_posix(), "compare", before.as_posix(), after.as_posix()],
        capture_output=True, text=True, timeout=120,
    )


def test_A_REGRESSION_IS_NAMED_AND_FAILS(tmp_path):
    """THE POINT. A check that passed before the update and fails after is what the update did."""
    before = _write(tmp_path / "b", ["service ok", "bridge-current fail"], LF)
    after = _write(tmp_path / "a", ["service fail", "bridge-current fail"], LF)
    done = _compare(before, after)
    assert "BROKEN BY THIS UPDATE: service" in done.stdout, done.stdout + done.stderr
    assert done.returncode == 1, "a regression did not fail the comparison"


def test_a_pre_existing_failure_is_NOT_attributed_to_the_update(tmp_path):
    """The category that protects the next reader. `bridge-current` has been failing on this host all
    night for reasons unrelated to any deploy; reporting it as caused by an update sends whoever
    reads it to the wrong place."""
    before = _write(tmp_path / "b", ["service ok", "bridge-current fail"], LF)
    after = _write(tmp_path / "a", ["service ok", "bridge-current fail"], LF)
    done = _compare(before, after)
    assert "still failing (was already): bridge-current" in done.stdout
    assert "BROKEN BY THIS UPDATE" not in done.stdout
    assert done.returncode == 0, "a pre-existing failure was reported as an update failure"


def test_a_fix_is_reported_too(tmp_path):
    """An operator who is only ever told about damage stops believing the tool. An update that
    repairs something should say so -- it is also the evidence that the update landed."""
    before = _write(tmp_path / "b", ["bridge-installed fail"], LF)
    after = _write(tmp_path / "a", ["bridge-installed ok"], LF)
    done = _compare(before, after)
    assert "fixed by this update:  bridge-installed" in done.stdout
    assert done.returncode == 0


def test_CRLF_AND_LF_AGREE(tmp_path):
    """THE BUG IT NEARLY SHIPPED WITH. A CRLF capture matched no branch, so every comparison printed
    nothing and exited 0. Both encodings must produce byte-identical output, or the tool answers "no
    change" on the platform this fleet actually runs on."""
    rows_before = ["service ok", "bridge-current fail"]
    rows_after = ["service fail", "bridge-current fail"]
    lf = _compare(_write(tmp_path / "blf", rows_before, LF), _write(tmp_path / "alf", rows_after, LF))
    crlf = _compare(_write(tmp_path / "bcr", rows_before, CRLF), _write(tmp_path / "acr", rows_after, CRLF))
    assert crlf.stdout == lf.stdout, (
        f"CRLF and LF disagree.\nLF:\n{lf.stdout}\nCRLF:\n{crlf.stdout}"
    )
    assert crlf.returncode == lf.returncode == 1


def test_an_empty_baseline_says_so_rather_than_inventing_one(tmp_path):
    """A first install has no verifier yet. Absence stays absence: an operator handed an invented
    comparison cannot tell it from a real one."""
    before = tmp_path / "b"
    before.write_bytes(b"")
    after = _write(tmp_path / "a", ["service ok"], LF)
    done = _compare(before, after)
    assert "no baseline" in done.stdout
    assert done.returncode == 0


def test_an_unrunnable_verifier_reports_UNVERIFIED(tmp_path):
    """No evidence is not a pass. If the verifier could not run after the update, the update is
    unverified and must say so -- this repo has produced that exact false green twice."""
    before = _write(tmp_path / "b", ["service ok"], LF)
    after = tmp_path / "a"
    after.write_bytes(b"")
    done = _compare(before, after)
    assert "UNVERIFIED" in done.stdout
    assert done.returncode == 0


def test_the_comparison_can_say_both_yes_and_no(tmp_path):
    """ANTI-VACUITY. Every assertion above is about output; a script that printed nothing at all
    would satisfy the negative ones. This pins that identical input produces a clean, non-failing
    answer AND that differing input does not."""
    rows = ["service ok", "skills-installed ok"]
    same = _compare(_write(tmp_path / "b", rows, LF), _write(tmp_path / "a", rows, LF))
    assert same.returncode == 0
    assert "BROKEN BY THIS UPDATE" not in same.stdout

    differing = _compare(
        _write(tmp_path / "b2", rows, LF),
        _write(tmp_path / "a2", ["service fail", "skills-installed ok"], LF),
    )
    assert differing.returncode == 1


def test_it_names_no_individual_check(tmp_path):
    """THE DESIGN CONSTRAINT. A hand-kept list of "checks that answer whether a deploy took" is one
    more place to remember, and a check added later would be silently uncovered. Asserted on the
    source, comments stripped, because the failure is an omission -- there is no input that
    demonstrates a list which is merely INCOMPLETE."""
    code = "\n".join(
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    for check_id in ("bridge-installed", "skills-installed", "spawn-delegation", "env-bridge"):
        assert check_id not in code, (
            f"{check_id} is named in the comparison, so it is a list that will go stale"
        )
    # POSITIVE CONTROL for that scan: the doctor IS invoked, so "no check names" means the code does
    # not enumerate them rather than that it does nothing.
    assert "--json" in code and "doctor" in code


def test_redeploy_actually_uses_it():
    """A reader nothing calls changes nothing -- this repo's most-repeated defect. Both halves: the
    baseline must be captured BEFORE the wrappers are refreshed, or it records the state the update
    already produced and can never show a regression."""
    text = (REPO / "redeploy.sh").read_text(encoding="utf-8")
    assert "deploy-delta.sh" in text, "the update path does not verify itself"
    # The path is QUOTED in the script (`"$REPO_ROOT/scripts/deploy-delta.sh" capture`), so the
    # verb is what to search for. Matching the unquoted `deploy-delta.sh capture` finds nothing and
    # fails describing a missing call that is right there -- which is how this assertion first read.
    assert text.count("deploy-delta.sh\" capture") == 2, "a before and an after are both required"
    first_capture = text.index("deploy-delta.sh\" capture")
    refresh = text.index("install.sh\" --client")
    assert first_capture < refresh, (
        "the baseline is captured after the refresh, so it can only ever agree with the result"
    )
    assert text.index("deploy-delta.sh\" compare") > refresh
