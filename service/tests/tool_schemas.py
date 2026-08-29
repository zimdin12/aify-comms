"""What each stdio tool DECLARES it accepts, read out of its zod schema.

Extracted from `test_skills_and_tools_agree_on_names.py` on 2026-08-29, when the
transport-parity gate needed the same answer. A second extractor of one fact is the shape these
gates keep catching: two producers agree until one of them is fixed.

Reading JavaScript with a parser would be better and is not available here, so this
brace-matches `server.tool`'s THIRD argument and takes only its top-level keys -- a
`z.object({...})` inside a parameter must not contribute its own keys as if they were the
tool's own.
"""
from __future__ import annotations

import re
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2] / "mcp" / "stdio"

#: `server.tool("name",` -- the stdio registration form.
TOOL_NAME = re.compile(r"""server\.tool\(\s*["']([a-z0-9_]+)["']""")
#: A top-level key of the zod schema object, e.g. `  to: z.string()`.
SCHEMA_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")


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
