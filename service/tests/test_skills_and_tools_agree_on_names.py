"""Every tool a skill names is registered, and every registered tool is named somewhere.

WHY THIS ONE IS WORTH A GATE, when most documentation drift is not: a SKILL.md is not read on demand.
The files load into every agent's context every session, so a tool name that has been renamed is not a
stale doc somebody finds later — it is an instruction, in front of every agent, on every turn, to call
something that does not exist. The agent tries it, gets an error, and improvises.

Measured 2026-08-25 before this existed: 36 tools registered across the bridge, 36 comms_* names
mentioned across 34 skill files, and zero mismatches in either direction. So this is not fixing a
break; it is holding a state that is currently correct and has nothing else keeping it that way.
`test_skills_name_real_things.py` checks the FILES a skill points at, not the tool names it teaches.

BOTH DIRECTIONS, because they fail differently:

  * a skill naming an unregistered tool sends every agent at a dead call;
  * a registered tool named in no skill is invisible — agents cannot use what they were never told
    about, and nothing anywhere reports that as a fault.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "mcp" / "stdio"

TOOL_NAME = re.compile(r"""server\.tool\(\s*["']([a-z0-9_]+)["']""")
SKILL_MENTION = re.compile(r"\b(comms_[a-z0-9_]+)\b")


def registered_tools() -> set[str]:
    """What the bridge actually hands to an MCP client."""
    names: set[str] = set()
    for path in list(BRIDGE.glob("*.mjs")) + list(BRIDGE.glob("*.js")):
        names |= set(TOOL_NAME.findall(path.read_text(encoding="utf-8", errors="replace")))
    return names


def skill_files() -> list[Path]:
    return sorted(
        list(ROOT.glob(".claude/skills/**/*.md")) + list(ROOT.glob(".agents/skills/**/*.md")),
    )


def mentioned_tools() -> dict[str, set[str]]:
    """Every comms_* name any skill teaches, and which files teach it."""
    found: dict[str, set[str]] = {}
    for path in skill_files():
        for name in set(SKILL_MENTION.findall(path.read_text(encoding="utf-8", errors="replace"))):
            found.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return found


class SkillsAndToolsAgreeOnNames(unittest.TestCase):
    def test_the_scan_finds_both_sides(self):
        """The control. Two empty sets agree perfectly, and an assertion over them proves nothing —
        which is exactly how a renamed registration pattern would turn this file green and blind."""
        self.assertGreater(len(registered_tools()), 20, "the tool scan found almost nothing")
        self.assertGreater(len(skill_files()), 10, "the skill scan found almost no files")
        self.assertIn("comms_send", registered_tools(), "the scan missed a tool that certainly exists")
        self.assertIn("comms_send", mentioned_tools(), "the scan missed a skill mention that exists")

    def test_the_scan_can_say_no(self):
        """The other control. A matcher that matched everything would also pass the two tests below."""
        self.assertNotIn("comms_zzz_not_a_tool", registered_tools())
        self.assertNotIn("comms_zzz_not_a_tool", mentioned_tools())

    def test_no_skill_teaches_a_tool_that_does_not_exist(self):
        registered = registered_tools()
        ghosts = {
            name: sorted(files) for name, files in mentioned_tools().items()
            if name not in registered
        }
        self.assertEqual(
            ghosts, {},
            "these tool names are taught to every agent on every turn and are not registered: "
            + "; ".join(f"{n} (in {', '.join(f)})" for n, f in sorted(ghosts.items())),
        )

    def test_no_registered_tool_is_left_undocumented(self):
        """A tool no skill mentions is one agents were never told about.

        If a tool is deliberately internal, that is a decision — say so in a skill, or say so here.
        Adding a name to a silent exemption list to clear this test is the move the gate exists to
        catch, which is why there is no exemption list to add it to."""
        undocumented = sorted(registered_tools() - set(mentioned_tools()))
        self.assertEqual(
            undocumented, [],
            "registered but taught nowhere, so no agent knows it exists: " + ", ".join(undocumented),
        )


if __name__ == "__main__":
    unittest.main()
