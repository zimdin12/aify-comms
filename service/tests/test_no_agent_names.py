"""Operational identities do not belong in maintained source or teaching examples; recognize roles, not a roster."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("manager", "senior-dev", "tech-lead", "lead", "coder", "tester", "critic", "reviewer", "architect")
IDENTITY = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+)*-(?:" + "|".join(ROLES) + r")(?:-[a-z0-9]+)*\b", re.I)
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".in", ".ps1", ".cmd", ".bat",
                   ".json", ".toml", ".yaml", ".yml", ".md", ".html", ".css", ".svg", ".example", ""}
RAW_SUFFIXES = {".txt", ".out", ".log", ".tap", ".patch", ".diff"}


def identities(text):
    return [match[0] for match in IDENTITY.finditer(text) if match[0].lower() not in ROLES
            and not re.match(r"\.(?:m?js|py)\b", text[match.end():])]


def raw_capture(relative):
    # Captured outputs and documentation JSON receipts retain original bytes. Maintained JSON fixtures are scanned.
    return relative.suffix.lower() in RAW_SUFFIXES or (relative.parts[0] == "docs" and relative.suffix.lower() == ".json")


def maintained_files():
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    return [Path(name) for name in tracked if name and not raw_capture(Path(name))
            and Path(name).suffix.lower() in SOURCE_SUFFIXES]


def violations(files=None, root=ROOT):
    found = []
    for relative in maintained_files() if files is None else files:
        content = (root / relative).read_bytes()
        for number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            names = identities(line)
            if names:
                found.append(f"{relative.as_posix()}:{number}: {', '.join(names)}")
    return found


class NoAgentNames(unittest.TestCase):
    def test_role_derived_recognition_has_positive_and_negative_controls(self):
        for role in ROLES:
            with self.subTest(role=role):
                self.assertEqual(identities(role), [])
                name = "-".join(("fixture", role))
                for value in (name, name + "-2", name.upper()):
                    self.assertEqual(identities(value), [value])
        for neutral in ("probe-agent", "source-agent", "target-agent", "fixture.session", "agent-2"):
            self.assertEqual(identities(neutral), [])
        self.assertEqual(identities("terminal" + "-manager.mjs"), [], "a code-file citation is not an agent ID")
        self.assertEqual(identities("\0" + name + "\0"), [name], "embedded NUL in a valid UTF-8 fixture does not hide text")
        self.assertTrue(raw_capture(Path("docs/evidence/receipt.json")))
        self.assertFalse(raw_capture(Path("service/contracts/cases.json")))
        self.assertFalse(raw_capture(Path("docs/evidence/driver.mjs")))

    def test_the_scan_reaches_source_tests_and_both_teaching_mirrors(self):
        files = [name.as_posix() for name in maintained_files()]
        self.assertGreater(len(files), 1000)
        for prefix in ("service/", "mcp/stdio/", ".claude/skills/", ".agents/skills/"):
            self.assertTrue(any(name.startswith(prefix) for name in files), prefix)

    def test_maintained_source_contains_no_operational_agent_identities(self):
        found = violations()
        self.assertEqual(found, [], "replace operational names with neutral fixtures or generic roles:\n" + "\n".join(found))
