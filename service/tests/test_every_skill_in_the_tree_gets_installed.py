r"""The installer copies every skill in the tree, not the ones somebody remembered to name.

FOUND BY DEPLOYING, not by reading. `install.sh` named `aify-comms` and `aify-comms-debug` at each of
its two call sites -- the Claude tree and the Codex/Hermes mirror. A third skill was added, the
installer ran, printed its success banner, and did not copy it. Nothing failed. The list was simply one
shorter than the directory, and the skill whose entire purpose is to be found when an agent is asked
to install aify-comms existed only inside the checkout.

That is the shape this repo's own allowlist header warns about: "a list you must remember to update is
a defect with a delay on it". The delay here was hours.

WHY A TEST AND NOT JUST THE FIX. Both call sites now walk the directory, and a walk is only correct
while it stays a walk. A future edit that reintroduces a name -- to skip one, to order them, to handle
a special case -- restores the defect silently, because the installer's output looks identical either
way.

IT READS `install.sh`, NOT THE INSTALLED TREE. Asserting on `~/.claude/skills` would pass or fail on
whether somebody happened to run the installer on this machine, which is a fact about the developer
rather than about the code.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
CLAUDE_SKILLS = REPO / ".claude" / "skills"
AGENT_SKILLS = REPO / ".agents" / "skills"


def _skill_names(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if p.is_dir()}


def test_there_are_skills_to_install():
    """POSITIVE CONTROL. Every assertion below is about a set of names; an empty tree would satisfy
    them all."""
    assert len(_skill_names(CLAUDE_SKILLS)) >= 3, "the skills tree is not being read"


def test_the_two_trees_hold_the_same_skills():
    """The mirror gate checks the FILES match; this checks neither tree has gained a skill the other
    lacks, which would install for one runtime's agents and not the other's."""
    assert _skill_names(CLAUDE_SKILLS) == _skill_names(AGENT_SKILLS)


def test_the_installer_names_no_individual_skill():
    """The defect, stated as its shape. A path ending in a specific skill directory means somebody has
    gone back to listing them, and the next skill added will be missed exactly as the last one was."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    named = sorted(set(re.findall(r"skills/(aify-comms[a-z-]*)[\"/\s]", source)))
    assert named == [], (
        f"install.sh names {named} instead of walking the directory. A skill added after that line "
        "was written will not be installed, and the installer will report success anyway."
    )


def test_both_trees_are_walked():
    source = INSTALL_SH.read_text(encoding="utf-8")
    assert "install_skill_tree" in source, "the shared walker is gone"
    for tree in (".claude/skills", ".agents/skills"):
        assert re.search(rf'install_skill_tree "\$SCRIPT_DIR/{re.escape(tree)}"', source), (
            f"{tree} is no longer installed by the walker"
        )


def test_the_walker_removes_a_stale_copy_before_writing():
    """A skill is a DIRECTORY, so copying over an old one leaves whatever the new version deleted.
    A reference file removed upstream would keep being read by every agent on the host."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    body = source[source.index("install_skill_tree() {"):]
    body = body[:body.index("\n}\n")]
    assert "rm -rf" in body, "the walker copies over a stale skill instead of replacing it"
    assert "cp -R" in body
