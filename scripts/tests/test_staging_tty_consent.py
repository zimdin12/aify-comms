"""The E1 tty driver answers displayed prompts only and preserves refusal/failure."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "scripts/staging/fixtures/tty-consent.py"


@unittest.skipIf(os.name == "nt", "PTY qualification runs on POSIX")
class TtyConsent(unittest.TestCase):
    def run_child(self, prompts, code):
        return subprocess.run([sys.executable, "-I", str(DRIVER), json.dumps(prompts),
                               sys.executable, "-c", code], input="", text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)

    def test_displayed_prompts_are_answered_in_the_same_tty(self):
        result = self.run_child([["artifact approval", "yes\n"], ["policy preparation", "y\n"]],
                                "import os; assert os.isatty(0); "
                                "assert input('artifact approval') == 'yes'; "
                                "assert input('policy preparation') == 'y'")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("TTY_CONSENT answered=2 expected=2", result.stdout)

    def test_a_prompt_that_never_arrives_cannot_manufacture_consent(self):
        result = self.run_child([["expected approval", "y\n"]], "print('another prompt, not the selected one')")
        self.assertEqual(result.returncode, 78, result.stdout)
        self.assertIn("TTY_CONSENT answered=0 expected=1", result.stdout)

    def test_alarm_captures_the_actual_child_owner_and_tty_before_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / 'child-pid'
            result = self.run_child([], "import os, signal, time; "
                                    f"open({str(identity)!r}, 'w').write(str(os.getpid())); "
                                    "os.kill(os.getppid(), signal.SIGALRM); time.sleep(2)")
            pid = identity.read_text()
        self.assertEqual(result.returncode, 124, result.stdout)
        rows = [line.split(None, 5) for line in result.stdout.splitlines()]
        child = next((row for row in rows if len(row) == 6 and row[0] == pid), None)
        self.assertIsNotNone(child, result.stdout)
        self.assertNotEqual(child[3], '?', result.stdout)
        self.assertTrue(child[2], result.stdout)

    def test_answering_a_prompt_does_not_hide_child_failure(self):
        result = self.run_child([["approval", "y\n"]], "import sys; input('approval'); sys.exit(7)")
        self.assertEqual(result.returncode, 7, result.stdout)
        self.assertIn("TTY_CONSENT answered=1 expected=1", result.stdout)
