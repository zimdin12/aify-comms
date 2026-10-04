"""Run from anywhere: python docs/superpowers/plans/evidence/2026-10-05-result-report/report-mutants.py
Each mutant undoes one decision of the result-report repair; the named test must go red. Restores from saved bytes."""
import os, re, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
ROUTE = os.path.join(ROOT, "service", "routers", "terminal_controls.py")
TEST = "service/tests/test_a_control_report_proves_its_machine.py"

BEGIN = '        await db.execute("BEGIN IMMEDIATE")\n'
JUDGE = ('        proof = await judge_host_proof(db, [owner["machine_id"] or ""], presented_proof(request))\n'
         '        if proof.refusal:\n            raise HTTPException(403, proof.refusal)\n')
OWNER_READ = ('        owner = await (await db.execute(\n'
              '            "SELECT machine_id FROM environments WHERE id = ?", (control["environment_id"],))).fetchone()\n')
MISSING = ('        if not owner:\n            # Not the empty-machine branch below: a control whose environment row is gone has no owner to judge.\n'
           '            raise HTTPException(404, f\'Environment "{control["environment_id"]}" of terminal control "{control_id}" not found\')\n')


def m_no_judge(s):
    assert s.count(JUDGE) == 1
    return s.replace(JUDGE, "")


def m_lock_after_judge(s):
    assert s.count(BEGIN) == 1 and s.count(JUDGE) == 1
    return s.replace(BEGIN, "").replace(JUDGE, JUDGE + BEGIN)


def m_owner_before_lock(s):
    # The control and its owner are read BEFORE the reservation; the judge still runs under it, on the stale read.
    control_read = '        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()\n'
    assert s.count(BEGIN) == 1 and s.count(OWNER_READ) == 1 and s.count(control_read) == 1
    s = s.replace(BEGIN, "")
    s = s.replace(OWNER_READ, "")
    s = s.replace(control_read, control_read + "        if not control:\n            raise HTTPException(404, 'x')\n" + OWNER_READ + BEGIN)
    return s


def m_missing_is_empty(s):
    assert s.count(MISSING) == 1
    return s.replace(MISSING, "        owner = owner or {'machine_id': ''}\n")


COMMIT = "        await db.commit()\n"


def m_commit_after_judge(s):
    # The reservation is released once judged; the writes run in a new implicit transaction (independent review).
    assert s.count(JUDGE) == 1
    return s.replace(JUDGE, JUDGE + COMMIT)


def m_owner_from_terminal(s):
    # The owner read through the terminal's environment instead of the control's (independent review).
    assert s.count(OWNER_READ) == 1
    return s.replace(OWNER_READ, OWNER_READ.replace(
        '"SELECT machine_id FROM environments WHERE id = ?", (control["environment_id"],)',
        '"SELECT machine_id FROM environments WHERE id = (SELECT environment_id FROM terminal_sessions WHERE id = ?)", '
        '(control["terminal_id"],)'))


def m_judge_after_writes(s):
    # Refused only after every write and effect, just before the commit (independent review).
    assert s.count(JUDGE) == 1 and s.count(COMMIT) == 1
    return s.replace(JUDGE, "").replace(COMMIT, JUDGE + COMMIT)


def m_slow_owner_before_lock(s):
    # The independent review's delay of 0.7 s before the first read defeated the earlier clock-based schedule.
    s = m_owner_before_lock(s)
    control_read = '        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()\n'
    s = s.replace(control_read, "        import asyncio\n        await asyncio.sleep(0.7)\n" + control_read, 1)
    return s


MUTANTS = [
    ("judge deleted", m_no_judge, r"test_a_report_without_the_owners_proof_changes_nothing"),
    ("BEGIN IMMEDIATE after the judge", m_lock_after_judge, r"test_no_enrollment_lands_between_the_reports_judge_and_its_commit"),
    ("owner read before the reservation", m_owner_before_lock, r"test_a_machine_filled_and_enrolled_before_the_reservation_is_the_one_judged"),
    ("the same, 0.7 s late", m_slow_owner_before_lock, r"test_a_machine_filled_and_enrolled_before_the_reservation_is_the_one_judged"),
    ("missing environment treated as empty machine", m_missing_is_empty, r"test_a_control_whose_environment_is_gone_is_refused_not_trusted"),
    ("reservation released after the judge", m_commit_after_judge, r"test_no_enrollment_lands_between_the_reports_judge_and_its_commit"),
    ("owner read through the terminal", m_owner_from_terminal, r"test_the_owner_is_the_controls_environment_not_the_terminals"),
    ("judged after the writes", m_judge_after_writes, r"test_a_report_without_the_owners_proof_changes_nothing"),
]

with open(ROUTE, newline="") as f:
    good = f.read()
env = {k: v for k, v in os.environ.items() if k not in ("AIFY_AGENT_ID", "AIFY_AGENT_LEASE")}
try:
    for label, mutate, expected in MUTANTS:
        with open(ROUTE, "w", newline="") as f:
            f.write(mutate(good))
        r = subprocess.run([sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:randomly", "-rf"], cwd=ROOT,
                           capture_output=True, text=True, env=env)
        failed = sorted(set(re.findall(r"^(?:SUB)?FAILED(?:\[[^\]]*\])? \S+::(\w+)", r.stdout, re.M)))
        print(f"{label}: {'KILLED at the named test' if expected in failed else 'SURVIVED' if not failed else 'RED elsewhere only'}"
              f"  failed={failed}")
        with open(ROUTE, "w", newline="") as f:
            f.write(good)
finally:
    with open(ROUTE, "w", newline="") as f:
        f.write(good)
r = subprocess.run([sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:randomly"], cwd=ROOT, capture_output=True, text=True, env=env)
print("restored:", r.stdout.strip().splitlines()[-1])
