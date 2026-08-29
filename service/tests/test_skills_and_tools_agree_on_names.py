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

#: A skill writes a call as `comms_send(to="x", type="info")`. Only the keyword names matter here.
SKILL_CALL = re.compile(r"\b(comms_[a-z0-9_]+)\(([^)]*)\)")
KWARG = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=")
#: A top-level key of the zod schema object, e.g. `  to: z.string()`.
SCHEMA_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")


def registered_tools() -> set[str]:
    """What the bridge actually hands to an MCP client."""
    names: set[str] = set()
    for path in list(BRIDGE.glob("*.mjs")) + list(BRIDGE.glob("*.js")):
        names |= set(TOOL_NAME.findall(path.read_text(encoding="utf-8", errors="replace")))
    return names


def tool_parameters() -> dict[str, set[str]]:
    """tool name -> the parameter names its zod schema declares.

    The schema is `server.tool`\'s THIRD argument, so this brace-matches from the first `{` after the
    registration rather than trying to parse JavaScript. Nested objects are excluded by depth: a
    `z.object({ ... })` inside a parameter must not contribute its own keys as if they were the
    tool\'s.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(list(BRIDGE.glob("*.mjs")) + list(BRIDGE.glob("*.js"))):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in TOOL_NAME.finditer(text):
            window = text[match.end():match.end() + 2000]
            if "{" not in window:
                continue
            start = text.index("{", match.end())
            depth = 0
            end = start
            while end < len(text):
                if text[end] == "{":
                    depth += 1
                elif text[end] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            keys: set[str] = set()
            level = 0
            for line in text[start + 1:end].splitlines():
                key = SCHEMA_KEY.match(line.strip())
                if level == 0 and key:
                    keys.add(key.group(1))
                level += line.count("{") + line.count("(") - line.count("}") - line.count(")")
            found[match.group(1)] = keys
    return found


def skill_parameters() -> dict[str, dict[str, set[str]]]:
    """tool name -> parameter -> the skill files that write it."""
    found: dict[str, dict[str, set[str]]] = {}
    for path in skill_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for call in SKILL_CALL.finditer(line):
                for kwarg in KWARG.findall(call.group(2)):
                    where = f"{path.relative_to(ROOT)}:{number}"
                    found.setdefault(call.group(1), {}).setdefault(kwarg, set()).add(where)
    return found


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

    def test_the_parameter_scans_find_both_sides(self):
        """The control for the parameter half. A schema reader that parsed nothing, or a call reader
        that matched nothing, would make the comparison below pass having read one side or neither."""
        schemas = tool_parameters()
        self.assertGreater(len(schemas), 20, "no tool schema was parsed")
        self.assertIn("to", schemas.get("comms_send", set()), "comms_send\'s own parameters are missing")
        self.assertEqual(schemas.get("comms_agents"), set(),
                         "comms_agents takes no parameters; a reader claiming otherwise is over-matching")
        written = skill_parameters()
        self.assertGreaterEqual(len(written), 5, "no parameterised call was found in any skill")
        self.assertIn("to", written.get("comms_send", {}), "a call every skill writes was not read")

    def test_the_parameter_scan_can_say_no(self):
        """The other control: an invented parameter must be absent from both sides."""
        self.assertNotIn("aifyNotAParam", tool_parameters().get("comms_send", set()))
        self.assertNotIn("aifyNotAParam", skill_parameters().get("comms_send", {}))

    def test_no_skill_teaches_a_parameter_the_tool_does_not_take(self):
        """The gate itself.

        A tool whose schema could not be parsed is SKIPPED rather than treated as taking nothing --
        an unparsed schema is no evidence, and reporting every one of its parameters as wrong would
        bury a real finding in noise. `test_the_parameter_scans_find_both_sides` is what stops that
        skip from swallowing the whole gate.
        """
        schemas = tool_parameters()
        wrong: dict[str, set[str]] = {}
        for tool, params in skill_parameters().items():
            declared = schemas.get(tool)
            if declared is None:
                continue
            for param, where in params.items():
                if param not in declared:
                    wrong.setdefault(f"{tool}({param}=...)", set()).update(where)
        self.assertEqual(
            wrong, {},
            "these parameters are taught to every agent on every turn and the tool does not take "
            "them: " + "; ".join(
                f"{call} (in {', '.join(sorted(files))})" for call, files in sorted(wrong.items())
            ),
        )

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
