"""The no-lock plant for the terminal claim (dc724538) against f5105858's retained test, run on the bytes in the tree.

  python docs/superpowers/plans/evidence/2026-10-04-claim-lock/claim-lock-plant.py [runs]

The plant deletes the claim's `BEGIN IMMEDIATE` and nothing else, so the second judge and the write run without the
write lock. `test_no_enrollment_lands_between_the_claims_final_judge_and_its_write` must go red on every run, at its
own assertion; the two earlier race tests release their competing enrollment at the FIRST judge, so they are timing
witnesses and are reported, not required. The module is restored from its saved bytes and checked by digest.
"""
import hashlib, os, re, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
MODULE = os.path.join(ROOT, "service", "api_core", "terminal_controls_io.py")
TEST = "service/tests/test_a_claim_judges_its_proof_under_the_write_lock.py"
WITNESS = "test_no_enrollment_lands_between_the_claims_final_judge_and_its_write"
LOCK = '            await db.execute("BEGIN IMMEDIATE")\n'
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

with open(MODULE, "rb") as f:
    good = f.read()
digest = hashlib.sha256(good).hexdigest()
text = good.decode("utf-8")
assert text.count(LOCK) == 1, "the plant site is not unique"
env = {k: v for k, v in os.environ.items() if k not in ("AIFY_AGENT_ID", "AIFY_AGENT_LEASE")}


def run():
    r = subprocess.run([sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:randomly", "-rf"], cwd=ROOT,
                       capture_output=True, text=True, env=env)
    failed = sorted(set(re.findall(r"^(?:SUB)?FAILED(?:\[[^\]]*\])? \S+::(\w+)", r.stdout, re.M)))
    return failed, r.stdout.strip().splitlines()[-1]


print(f"module sha256 {digest[:16]}  test sha256 {hashlib.sha256(open(os.path.join(ROOT, TEST), 'rb').read()).hexdigest()[:16]}")
print("unplanted:", run())
try:
    with open(MODULE, "wb") as f:
        f.write(text.replace(LOCK, "").encode("utf-8"))
    for n in range(1, RUNS + 1):
        failed, tail = run()
        print(f"planted run {n}: witness {'RED' if WITNESS in failed else 'GREEN (plant survived)'}; failed={failed}; {tail}")
finally:
    with open(MODULE, "wb") as f:
        f.write(good)
with open(MODULE, "rb") as f:
    assert hashlib.sha256(f.read()).hexdigest() == digest, "the module was not restored"
print("restored:", run())
