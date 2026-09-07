"""O5 (2026-06-06): the .claude and .agents skill trees MUST stay byte-identical.

install.sh deploys `.claude/skills/aify-comms*` to Claude and `.agents/skills/aify-comms*`
to Hermes/Codex from SEPARATE source trees, so both must exist and match — but they're
maintained by hand (CLAUDE.md warns "keep them in sync"). This test fails CI on any drift
(content OR the references/ subtree), replacing the manual diff -r reminder.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
def _skill_names() -> list[str]:
    """Every skill in EITHER tree, derived rather than listed.

    THE LIST WAS TWO AND THE TREE WAS THREE. `aify-comms-install/` arrived on 2026-08-30 and this
    gate never looked at it -- so an always-loaded `SKILL.md` shipped to both Claude and Codex was
    covered by no content-parity check at all. Two other gates then cited this one's guarantee:
    `skill-size-ratchet.test.js` says "the .agents mirror is byte-identical by
    test_skill_mirror_parity.py, so measuring one side measures both", and
    `test_every_skill_in_the_tree_gets_installed.py` says "the mirror gate checks the FILES match".
    Both were true of two skills out of three.

    A HARDCODED POPULATION IS SATISFIED BY THE SET THAT HAPPENS TO AGREE TODAY, which is this
    repo's own "gate granularity" defect. The union of both trees means a skill added to one side
    and forgotten on the other fails here rather than being silently out of scope.
    """
    names = set()
    for base in (_REPO_ROOT / ".claude" / "skills", _REPO_ROOT / ".agents" / "skills"):
        if base.exists():
            names.update(p.name for p in base.iterdir() if p.is_dir())
    return sorted(names)


_SKILLS = _skill_names()


def _tree(base: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(base)).replace("\\", "/")] = p.read_bytes()
    return out


def test_claude_and_agents_skill_mirrors_are_identical():
    for skill in _SKILLS:
        claude = _tree(_REPO_ROOT / ".claude" / "skills" / skill)
        agents = _tree(_REPO_ROOT / ".agents" / "skills" / skill)
        assert claude, f".claude/skills/{skill} is missing or empty"
        assert agents, f".agents/skills/{skill} is missing or empty"
        assert set(claude) == set(agents), (
            f"{skill}: file set differs between mirrors\n"
            f"  only in .claude: {sorted(set(claude) - set(agents))}\n"
            f"  only in .agents: {sorted(set(agents) - set(claude))}"
        )
        drifted = [rel for rel in claude if claude[rel] != agents[rel]]
        assert not drifted, (
            f"{skill}: mirror drift in {drifted} — .claude and .agents skill copies must be "
            f"byte-identical (edit both, or regenerate .agents from .claude)."
        )


def test_the_population_is_derived_and_not_empty():
    """POSITIVE CONTROL. Every assertion above loops over `_SKILLS`; an empty list satisfies each of
    them perfectly and reports green -- which is exactly how a narrower population hid a whole skill
    for eight days."""
    assert len(_SKILLS) >= 3, f"the skill scan found only {_SKILLS}"
    assert "aify-comms-install" in _SKILLS, (
        "the skill that was outside the old hardcoded population is still outside the derived one"
    )
