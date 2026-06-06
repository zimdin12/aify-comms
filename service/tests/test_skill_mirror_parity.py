"""O5 (2026-06-06): the .claude and .agents skill trees MUST stay byte-identical.

install.sh deploys `.claude/skills/aify-comms*` to Claude and `.agents/skills/aify-comms*`
to Hermes/Codex from SEPARATE source trees, so both must exist and match — but they're
maintained by hand (CLAUDE.md warns "keep them in sync"). This test fails CI on any drift
(content OR the references/ subtree), replacing the manual diff -r reminder.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS = ["aify-comms", "aify-comms-debug"]


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
