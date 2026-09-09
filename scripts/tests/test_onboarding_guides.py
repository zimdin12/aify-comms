"""Installer instructions must preserve choice and repository ownership."""
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[2]


class OnboardingGuideTests(unittest.TestCase):
    def setUp(self):
        self.skill = (REPO / ".claude/skills/aify-comms-install/SKILL.md").read_text(encoding="utf-8")
        self.guide_path = REPO / "docs/INSTALL_ONBOARDING.md"
        self.guide = self.guide_path.read_text(encoding="utf-8") if self.guide_path.exists() else ""

    def test_entry_skill_requires_optional_choice_and_unknown_review(self):
        for term in ("herdr", "optional", "unknown", "verify-only", "plan-only", "outdated"):
            self.assertIn(term, self.skill.lower())
        self.assertIn("INSTALL_ONBOARDING.md", self.skill)
        self.assertRegex(self.skill.lower(), r"ask.*herdr")

    def test_environment_install_uses_owner_credential_aware_installer(self):
        self.assertNotRegex(self.skill, r"npm install -g .*aify-env")
        self.assertIn("https://github.com/zimdin12/aify-env", self.skill)
        self.assertIn("--plan-only", self.guide)
        self.assertIn("--no-prompt", self.guide)
        self.assertIn("credential", self.guide)
        self.assertIn("install.sh", self.guide)

    def test_official_herdr_paths_and_support_gate_are_named(self):
        for term in ("https://github.com/herdrdev/herdr", "distribution/install.ps1",
                     "herdr-windows-x86_64.zip", "SHA-256", "aify-env herdr",
                     "--version", "probe", "not guaranteed"):
            self.assertIn(term, self.guide)
        self.assertNotRegex(self.guide, r"curl[^\n]+\|\s*(sh|bash)")

    def test_restart_is_separate_from_install_and_verify(self):
        self.assertIn("separate", self.skill.lower())
        self.assertIn("restarts", self.skill.lower())
        self.assertIn("running", self.skill.lower())
        self.assertIn("identity", self.skill.lower())
        self.assertNotRegex(self.skill, r"(?m)^aify-env\s*(#.*)?$")

    def test_skill_mirrors_match_and_stay_under_existing_budget(self):
        other = (REPO / ".agents/skills/aify-comms-install/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(self.skill, other)
        self.assertLessEqual(len(self.skill.replace("\r\n", "\n").encode()), 4846)

    def test_readme_exposes_agent_workflow_and_optional_herdr(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        quick_start = readme.split("## Quick start", 1)[1].split("## Agent playbooks", 1)[0]
        for term in ("herdr", "INSTALL_ONBOARDING.md", "verify-only", "plan-only"):
            self.assertIn(term, quick_start)


if __name__ == "__main__":
    unittest.main(verbosity=2)
