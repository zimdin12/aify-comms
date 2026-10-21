"""Runs scripts/tests/gate_attempt_window.mjs: a gate attempt runs inside the runner's primary window, so a command
running when the attempt budget passes is cut, cleanup still runs in full, and a verdict reached after the budget is
refused (runner review R2, carried into scripts/sandbox/gate/gate.mjs). Node's own output is kept in the failure
message, so a failing case is named."""
from pathlib import Path
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GateAttemptWindowTests(unittest.TestCase):
    def test_a_gate_attempt_runs_inside_the_primary_window(self):
        env = {k: v for k, v in os.environ.items() if k != 'AIFY_AGENT_ID'}
        done = subprocess.run(['node', '--test', str(ROOT / 'scripts' / 'tests' / 'gate_attempt_window.mjs')], cwd=ROOT,
                              capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
