"""The two derivations of "which files are the doctor" must return the same files.

WHY TWO EXIST. The scanners that need this population live in both suites -- four of them, and every
one had hardcoded ``doctor.js`` as the answer until a check moved into its own module and reddened
three at once while the fourth kept scanning a file the check had left. A derivation fixes that; two
copies of one LIST would not, because they agree right up to the moment somebody edits one.

WHY THIS TEST EXISTS ANYWAY. Two implementations of one derivation can still drift -- one taught to
follow a new import form, the other not. This is the collision that makes the drift loud, and it is
the only place the two are compared. It is deliberately an AGREEMENT test, not a second assertion
about the right answer: neither side is the authority, so a disagreement names both.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from doctor_sources import ROOT, doctor_source_files

WALKER = ROOT / "mcp" / "stdio" / "tests" / "doctor-sources.mjs"


class BothDoctorSourceWalkersAgree(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("node is not on PATH")

    def _js_files(self) -> set[str]:
        script = (
            'import("file://" + process.argv[1].replace(/' + chr(92) + chr(92) + '/g, "/"))'
            ".then((m) => console.log(JSON.stringify(m.doctorSourceFiles())))"
        )
        out = subprocess.run(
            [self.node, "--input-type=module", "-e", script, str(WALKER)],
            capture_output=True, text=True, timeout=60, cwd=str(WALKER.parent),
        )
        self.assertEqual(out.returncode, 0, f"the JS walker did not run: {out.stderr}")
        return {Path(f).resolve().name for f in json.loads(out.stdout)}

    def test_the_python_walk_finds_the_doctor_at_all(self):
        """The control. Two empty sets agree perfectly, so the comparison below proves nothing until
        each side is known to have found something -- the wrong zero this repo keeps producing."""
        names = {p.name for p in doctor_source_files()}
        self.assertIn("doctor.js", names)
        self.assertGreaterEqual(len(names), 2, "the walk found only the entry point; imports missed")

    def test_the_js_walk_finds_the_doctor_at_all(self):
        js = self._js_files()
        self.assertIn("doctor.js", js)
        self.assertGreaterEqual(len(js), 2, "the JS walk found only the entry point; imports missed")

    def test_the_two_walks_return_the_same_files(self):
        py = {p.name for p in doctor_source_files()}
        js = self._js_files()
        self.assertEqual(
            py, js,
            "the two derivations disagree about which files are the doctor; "
            f"python-only={sorted(py - js)} js-only={sorted(js - py)}",
        )

    def test_a_file_the_doctor_does_not_import_is_not_a_doctor_source(self):
        """The negative control. A walk that returned every file in the directory would pass every
        assertion above -- a probe that cannot say ABSENT cannot say PRESENT."""
        names = {p.name for p in doctor_source_files()}
        self.assertNotIn("server.js", names, "the walk is not following imports, it is listing files")


if __name__ == "__main__":
    unittest.main()
