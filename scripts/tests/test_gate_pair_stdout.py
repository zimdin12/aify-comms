"""Runs scripts/tests/gate_pair_stdout.mjs: the gate's Pair parses Docker's stdout alone, so a benign stderr line can
no longer corrupt a value it reads (runner review R1, carried into scripts/sandbox/gate/pair.mjs). Node's own output is
kept in the failure message, so a failing case is named."""
from pathlib import Path
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PairReadsStdoutTests(unittest.TestCase):
    def test_every_pair_data_read_takes_stdout_only(self):
        env = {k: v for k, v in os.environ.items() if k != 'AIFY_AGENT_ID'}
        done = subprocess.run(['node', '--test', str(ROOT / 'scripts' / 'tests' / 'gate_pair_stdout.mjs')], cwd=ROOT,
                              capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
