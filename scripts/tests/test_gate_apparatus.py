"""The gate's qualification apparatus (review section 6): what an attempt must prove about itself before its verdicts
count. Three obligations, one class each:

1. env-resumed's fixture must separate fresh-busy from idle, or the case is refused by name before any judge runs.
2. An attempt's output folder is acquired exclusively: of two acquisitions of one name, exactly one wins.
3. The mutation campaign fails closed: an arm that throws, hangs, survives or misses its anchor fails the campaign, and
   its file is restored and its receipt kept whatever happened.

The node checks run the real modules (case-judging.mjs, attempt-dir.mjs) in a child node; the campaign runs in-process
over a throwaway tree. No Docker, no network, no live service."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / 'scripts' / 'sandbox' / 'gate'
RETAINED = ROOT / 'docs' / 'evidence' / '2026-10-21-sw3a-gate' / 'decision2-red' / 'silenced' / 'evidence.json'


def node(script, *args):
    done = subprocess.run(['node', '--input-type=module', '-e', script, *args], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(f'node exited {done.returncode}: {done.stderr}')
    return json.loads(done.stdout)


def url(path):
    return Path(path).resolve().as_uri()


JUDGE_ENV_RESUMED = f"""
import fs from "node:fs";
import {{ judgeCase }} from "{url(GATE / 'case-judging.mjs')}";
import {{ JUDGES }} from "{url(GATE / 'judges.mjs')}";
const evidence = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const marks = JSON.parse(process.argv[2]);
evidence.marks = {{ ...evidence.marks, ...marks }};
const result = judgeCase({{ id: process.argv[3], error: null, evidence, judges: JUDGES }});
const direct = JUDGES[process.argv[3]](evidence);
console.log(JSON.stringify({{ failed: result.failed, pass: result.pass, direct: direct.failed }}));
"""


class ResumePhaseFixtureTests(unittest.TestCase):
    def judge(self, marks, case='env-resumed'):
        return node(JUDGE_ENV_RESUMED, str(RETAINED), json.dumps(marks), case)

    def retained(self):
        return json.loads(RETAINED.read_text(encoding='utf-8'))['marks']

    def test_the_retained_attempt_whose_fresh_and_idle_coincide_is_refused_by_name(self):
        m = self.retained()
        self.assertEqual(m['freshAccepted'], m['idleAccepted'], 'precondition: the retained marks coincide')
        result = self.judge({'freshBusyAccepted': m['freshAccepted'], 'idleRequestedAt': m['freshAccepted']['seenAt']})
        self.assertFalse(result['pass'])
        self.assertIn('fixture: the resume did not separate fresh-busy from idle', result['failed'])

    def test_the_retained_attempt_as_recorded_names_its_missing_fresh_busy_phase(self):
        result = self.judge({})
        self.assertEqual(result['failed'], ['fixture: no fresh busy record was accepted after the resume'])

    def test_a_fresh_busy_phase_shorter_than_one_claim_pass_is_refused(self):
        m = self.retained()
        fresh = {'at': m['idleAccepted']['at'] - 5000, 'seenAt': m['idleAccepted']['seenAt'] - 5000}
        result = self.judge({'freshBusyAccepted': fresh, 'idleRequestedAt': fresh['seenAt'] + 4000})
        self.assertIn('fixture: the fresh-busy phase was shorter than one claim pass', result['failed'])

    def test_a_late_idle_request_mark_does_not_stand_for_an_observed_claim_pass(self):
        # Review of 11d8f3ae: fresh busy seen 7000 ms before idle, the request mark 11800 ms after fresh.
        m = self.retained()
        fresh = {'at': m['idleAccepted']['at'] - 7000, 'seenAt': m['idleAccepted']['seenAt'] - 7000}
        result = self.judge({'freshBusyAccepted': fresh, 'idleRequestedAt': fresh['seenAt'] + 11800})
        self.assertIn('fixture: the fresh-busy phase was shorter than one claim pass', result['failed'])

    def test_a_claim_pass_on_only_one_clock_is_refused(self):
        m = self.retained()
        for at_gap, seen_gap in ((15000, 7000), (7000, 15000)):
            fresh = {'at': m['idleAccepted']['at'] - at_gap, 'seenAt': m['idleAccepted']['seenAt'] - seen_gap}
            result = self.judge({'freshBusyAccepted': fresh, 'idleRequestedAt': fresh['seenAt'] + 11000})
            self.assertIn('fixture: the fresh-busy phase was shorter than one claim pass', result['failed'], (at_gap, seen_gap))

    def test_CONTROL_a_separated_resume_reaches_the_judge_unchanged(self):
        m = self.retained()
        fresh = {'at': m['idleAccepted']['at'] - 15000, 'seenAt': m['idleAccepted']['seenAt'] - 15000}
        result = self.judge({'freshBusyAccepted': fresh, 'idleRequestedAt': fresh['seenAt'] + 11000})
        self.assertFalse([f for f in result['failed'] if f.startswith('fixture: the resume')
                          or f.startswith('fixture: no fresh') or f.startswith('fixture: the fresh-busy')])
        self.assertEqual(result['failed'], result['direct'], 'an admitted fixture is judged exactly as the judge does')

    def test_CONTROL_a_case_with_no_fixture_check_is_judged_directly(self):
        result = self.judge({}, case='env-paused')
        self.assertEqual(result['failed'], result['direct'])


ACQUIRE = f"""
import {{ AttemptDirTaken, acquireAttemptDir }} from "{url(GATE / 'attempt-dir.mjs')}";
try {{ acquireAttemptDir(process.argv[1]); console.log(JSON.stringify("won")); }}
catch (e) {{ console.log(JSON.stringify(e instanceof AttemptDirTaken ? "taken" : `error ${{e.message}}`)); }}
"""


class AttemptDirTests(unittest.TestCase):
    def test_a_second_acquisition_of_one_name_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / 'attempts' / 'a1')
            self.assertEqual(node(ACQUIRE, target), 'won')
            self.assertEqual(node(ACQUIRE, target), 'taken', 'the empty folder the first attempt made is still its own')

    def test_of_eight_concurrent_acquisitions_exactly_one_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / 'race')
            procs = [subprocess.Popen(['node', '--input-type=module', '-e', ACQUIRE, target], cwd=ROOT,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
            outcomes = [json.loads(p.communicate(timeout=120)[0]) for p in procs]
            self.assertEqual(sorted(outcomes), ['taken'] * 7 + ['won'], outcomes)

    def test_CONTROL_a_fresh_name_is_acquired_with_its_parent_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'deep' / 'er' / 'a2'
            self.assertEqual(node(ACQUIRE, str(target)), 'won')
            self.assertTrue(target.is_dir())


def load_campaign():
    spec = importlib.util.spec_from_file_location('gate_campaign', GATE / 'campaign.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOY_CHECK = ("import sys; t = open('scripts/sandbox/gate/toy.txt', encoding='utf-8').read(); "
             "bad = 'bad' in t; print('FAIL the toy is bad' if bad else 'ok'); sys.exit(1 if bad else 0)")


class CampaignFailsClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'tree'
        (self.root / 'scripts' / 'sandbox' / 'gate').mkdir(parents=True)
        self.toy = self.root / 'scripts' / 'sandbox' / 'gate' / 'toy.txt'
        self.toy.write_text('good\n', encoding='utf-8')
        self.original = self.toy.read_bytes()
        self.out = Path(self.tmp.name) / 'out'
        self.campaign = load_campaign()

    def tearDown(self):
        self.tmp.cleanup()

    def run_campaign(self, arms, checker=None):
        module = Path(self.tmp.name) / 'arms.py'
        checkers = {'*': checker or [sys.executable, '-c', TOY_CHECK]}
        module.write_text(f'ARMS = {arms!r}\nCHECKERS = {checkers!r}\n', encoding='utf-8')
        failures, receipts = self.campaign.campaign(self.root, self.out, module)
        self.assertEqual(self.toy.read_bytes(), self.original, 'the toy file is restored whatever happened')
        return failures, receipts

    def test_CONTROL_a_killed_arm_passes_the_campaign(self):
        failures, receipts = self.run_campaign([('k1', 'toy.txt', 'good', 'bad', 'FAIL the toy is bad', 'kill')])
        self.assertEqual(failures, [])
        self.assertTrue(receipts[0]['killed'])
        self.assertEqual(json.loads((self.out / 'k1.receipt.json').read_text(encoding='utf-8')), receipts[0])

    def test_an_arm_that_throws_mid_mutation_is_restored_recorded_and_fails_the_campaign(self):
        real = self.campaign.run_checker

        def checker(argv, root):
            if self.toy.read_text(encoding='utf-8') == 'bad\n':  # mid-mutation: the file is the mutant right now
                raise RuntimeError('the checker launcher broke')
            return real(argv, root)
        with mock.patch.object(self.campaign, 'run_checker', checker):
            failures, receipts = self.run_campaign([('t1', 'toy.txt', 'good', 'bad', 'FAIL the toy is bad', 'throw')])
        self.assertIn('t1: controller error: RuntimeError: the checker launcher broke', failures)
        receipt = json.loads((self.out / 't1.receipt.json').read_text(encoding='utf-8'))
        self.assertFalse(receipt['killed'])
        self.assertEqual(receipt['sha_restored'], receipt['sha_original'])

    def test_a_checker_that_cannot_start_fails_the_campaign(self):
        failures, _ = self.run_campaign([('e1', 'toy.txt', 'good', 'bad', 'FAIL the toy is bad', 'launch')],
                                        checker=['no-such-checker-binary-xyz'])
        self.assertTrue(any('baseline control' in f for f in failures), failures)

    def test_a_hung_checker_is_killed_and_fails_its_arm(self):
        hang = [sys.executable, '-c', "import sys, time\nif 'bad' in open('scripts/sandbox/gate/toy.txt').read(): time.sleep(30)\n"]
        with mock.patch.object(self.campaign, 'TIMEOUT_S', 2):
            failures, receipts = self.run_campaign([('h1', 'toy.txt', 'good', 'bad', 'never printed', 'hang')], checker=hang)
        self.assertIn('h1: checker error: timed out after 2 s', failures)
        self.assertFalse(receipts[0]['killed'])

    def test_a_surviving_arm_fails_the_campaign(self):
        failures, _ = self.run_campaign([('s1', 'toy.txt', 'good', 'fine', 'FAIL the toy is bad', 'survive')])
        self.assertIn('s1: survived: the checker passed or never named the witness', failures)

    def test_an_arm_killed_by_another_witness_is_not_killed(self):
        failures, receipts = self.run_campaign([('w1', 'toy.txt', 'good', 'bad', 'FAIL something else', 'wrong witness')])
        self.assertFalse(receipts[0]['killed'])
        self.assertTrue(failures)

    def test_a_witness_named_only_on_a_passing_line_is_not_a_kill(self):
        # The checker fails (exit 1) for its own reason, and names the witness only on a PASS line.
        check = ("import sys; t = open('scripts/sandbox/gate/toy.txt', encoding='utf-8').read(); "
                 "print('PASS the toy is bad'); print('FAIL something unrelated' if 'bad' in t else 'ok'); "
                 "sys.exit(1 if 'bad' in t else 0)")
        failures, receipts = self.run_campaign([('n1', 'toy.txt', 'good', 'bad', 'the toy is bad', 'passing line')],
                                               checker=[sys.executable, '-c', check])
        self.assertFalse(receipts[0]['killed'])
        self.assertIn('n1: survived: the checker passed or never named the witness', failures)

    def test_an_anchor_that_misses_fails_the_campaign(self):
        failures, _ = self.run_campaign([('m1', 'toy.txt', 'absent', 'bad', 'FAIL the toy is bad', 'miss')])
        self.assertIn('m1: anchor found 0 times', failures)

    def test_a_red_baseline_runs_no_arm_and_fails(self):
        self.toy.write_text('bad\n', encoding='utf-8')
        self.original = self.toy.read_bytes()
        failures, receipts = self.run_campaign([('b1', 'toy.txt', 'bad', 'good', 'x', 'baseline')])
        self.assertEqual(receipts, [])
        self.assertTrue(any(f.startswith('baseline control') for f in failures))

    def test_the_command_line_exits_nonzero_on_any_failure(self):
        module = Path(self.tmp.name) / 'arms.py'
        module.write_text(f"ARMS = {[('s1', 'toy.txt', 'good', 'fine', 'FAIL the toy is bad', 'survive')]!r}\n"
                          f"CHECKERS = {{'*': {[sys.executable, '-c', TOY_CHECK]!r}}}\n", encoding='utf-8')
        done = subprocess.run([sys.executable, str(GATE / 'campaign.py'), str(self.root), str(self.out), str(module)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('campaign FAIL', done.stdout)


if __name__ == '__main__':
    unittest.main()
