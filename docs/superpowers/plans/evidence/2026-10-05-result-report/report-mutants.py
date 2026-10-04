"""Each mutant undoes one decision of the result-report repair; its named test must fail FOR the named cause.

  python docs/superpowers/plans/evidence/2026-10-05-result-report/report-mutants.py

FAIL-CLOSED (review of bff298f1: the first version printed survivors, ignored exit codes and matched test names, not
causes). A mutant counts as killed only when pytest, run on that one test, exits 1 AND its output carries the cause
string named below. Anything else is a survivor, and any survivor makes this script exit 1. It rewrites
service/routers/terminal_controls.py in the checkout it sits in and restores it from saved bytes, checked by digest,
so run it in a checkout nobody else is using. AIFY_AGENT_ID and AIFY_AGENT_LEASE are dropped from the child's env.
"""
import hashlib, os, subprocess, sys

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
CONTROL_READ = '        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()\n'
COMMIT = "        await db.commit()\n"
FIRST_WRITE_END = '            (status, now, req.error or "", control_id),\n        )\n'
EVENT_APPEND = "        await _append_terminal_event(\n"


def once(s, *sites):
    for site in sites:
        assert s.count(site) == 1, f"mutation site not unique: {site[:60]!r}"
    return s


def m_no_judge(s):
    return once(s, JUDGE).replace(JUDGE, "")


def m_lock_after_judge(s):
    return once(s, BEGIN, JUDGE).replace(BEGIN, "").replace(JUDGE, JUDGE + BEGIN)


def m_owner_before_lock(s):
    # The control and its owner are read BEFORE the reservation; the judge still runs under it, on the stale read.
    s = once(s, BEGIN, OWNER_READ, CONTROL_READ).replace(BEGIN, "").replace(OWNER_READ, "")
    return s.replace(CONTROL_READ, CONTROL_READ + "        if not control:\n            raise HTTPException(404, 'x')\n" + OWNER_READ + BEGIN)


def m_slow_owner_before_lock(s):
    # The independent review's 0.7 s delay before the first read defeated the earlier clock-based schedule.
    return m_owner_before_lock(s).replace(CONTROL_READ, "        import asyncio\n        await asyncio.sleep(0.7)\n" + CONTROL_READ, 1)


def m_missing_is_empty(s):
    return once(s, MISSING).replace(MISSING, "        owner = owner or {'machine_id': ''}\n")


def m_commit_after_judge(s):
    return once(s, JUDGE).replace(JUDGE, JUDGE + COMMIT)


def m_commit_after_first_write(s):
    # Senior-dev's plant on bff298f1: the reservation released after the control UPDATE, effects still to come.
    return once(s, FIRST_WRITE_END).replace(FIRST_WRITE_END, FIRST_WRITE_END + COMMIT)


def m_commit_before_event(s):
    return once(s, EVENT_APPEND).replace(EVENT_APPEND, COMMIT + EVENT_APPEND)


def m_owner_from_terminal(s):
    return once(s, OWNER_READ).replace(OWNER_READ, OWNER_READ.replace(
        '"SELECT machine_id FROM environments WHERE id = ?", (control["environment_id"],)',
        '"SELECT machine_id FROM environments WHERE id = (SELECT environment_id FROM terminal_sessions WHERE id = ?)", '
        '(control["terminal_id"],)'))


def m_judge_after_writes(s):
    return once(s, JUDGE, COMMIT).replace(JUDGE, "").replace(COMMIT, JUDGE + COMMIT)


D2 = "test_no_enrollment_lands_between_the_reports_judge_and_its_commit"
NOT_STOPPED = "an enrollment was not stopped at its writer acquisition"
MUTANTS = [
    ("judge deleted", m_no_judge, "test_a_report_without_the_owners_proof_changes_nothing", "200 != 403"),
    ("BEGIN IMMEDIATE after the judge", m_lock_after_judge, D2, NOT_STOPPED),
    ("owner read before the reservation", m_owner_before_lock,
     "test_a_machine_filled_and_enrolled_before_the_reservation_is_the_one_judged", "200 != 403"),
    ("the same, 0.7 s late", m_slow_owner_before_lock,
     "test_a_machine_filled_and_enrolled_before_the_reservation_is_the_one_judged", "200 != 403"),
    ("missing environment treated as empty machine", m_missing_is_empty,
     "test_a_control_whose_environment_is_gone_is_refused_not_trusted", "200 != 404"),
    ("reservation released after the judge", m_commit_after_judge, D2, NOT_STOPPED),
    ("reservation released after the first write", m_commit_after_first_write, D2, NOT_STOPPED),
    ("reservation released before the event append", m_commit_before_event, D2, NOT_STOPPED),
    ("owner read through the terminal", m_owner_from_terminal,
     "test_the_owner_is_the_controls_environment_not_the_terminals", "200 != 403"),
    ("judged after the writes", m_judge_after_writes, "test_a_report_without_the_owners_proof_changes_nothing",
     "a refused report resized the live screen"),
]

env = {k: v for k, v in os.environ.items() if k not in ("AIFY_AGENT_ID", "AIFY_AGENT_LEASE")}


def pytest(*select):
    return subprocess.run([sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:randomly", *select], cwd=ROOT,
                          capture_output=True, text=True, env=env)


with open(ROUTE, "rb") as f:
    good = f.read()
digest = hashlib.sha256(good).hexdigest()
text = good.decode("utf-8")
print(f"route sha256 {digest[:16]}  test sha256 {hashlib.sha256(open(os.path.join(ROOT, TEST), 'rb').read()).hexdigest()[:16]}")
survivors = []
try:
    for label, mutate, test, cause in MUTANTS:
        with open(ROUTE, "wb") as f:
            f.write(mutate(text).encode("utf-8"))
        r = pytest("-k", test)
        killed = r.returncode == 1 and cause in r.stdout
        if not killed:
            survivors.append(label)
        print(f"{'KILLED  ' if killed else 'SURVIVED'} {label}: {test} exit={r.returncode} cause {'seen' if cause in r.stdout else 'NOT seen'} ({cause!r})")
        with open(ROUTE, "wb") as f:
            f.write(good)
finally:
    with open(ROUTE, "wb") as f:
        f.write(good)
with open(ROUTE, "rb") as f:
    assert hashlib.sha256(f.read()).hexdigest() == digest, "the route was not restored"
r = pytest()
print(f"restored: exit={r.returncode} {r.stdout.strip().splitlines()[-1]}")
if survivors or r.returncode != 0:
    print(f"FAILED: survivors={survivors}")
    sys.exit(1)
print(f"all {len(MUTANTS)} mutants killed for their named cause")
