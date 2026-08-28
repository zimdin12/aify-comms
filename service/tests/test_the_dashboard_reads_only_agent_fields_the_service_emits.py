"""Every `agent.X` the dashboard reads is a field the agent payload actually emits.

THE JOIN, AND WHY NOTHING WATCHED IT. The service assembles an agent payload in three places --
`_agent_record_to_dict` plus the two `_enforce_*` gates that add keys afterwards -- and the dashboard
reads fields off it by name. A rename on either side is silent: JavaScript hands back `undefined`, the
template renders a blank or takes a falsy branch, and nothing is logged. Both suites stay green
because each side is self-consistent.

MEASURED 2026-08-28, first run: 32 keys emitted, 22 distinct `agent.X` reads, 3 reads not emitted --
`agent.last_seen`, `agent.unreadCount`, `agent.session_handle`. All three were snake_case alternates
sitting behind a `||` next to the camelCase name the service does emit, so all three were dead
branches and NOTHING was broken. They are gone now, which is what lets this test be strict: a dead
alternate is worse than nothing here, because it reads like coverage for the rename it cannot catch.

THE SCAN WAS WRONG TWICE BEFORE IT WAS RIGHT, which is why the controls below are not decoration:

  * collecting every `Subscript` with a string slice inside the serializer counted `row["runtime"]`
    -- a READ from the database row -- as an emitted key. 35 real keys became 47, and each phantom
    key would have masked a genuinely missing field by making the diff smaller.
  * matching `agent.X` OR `a.X` on the dashboard side dragged in every one-letter variable in the
    file, reporting `a.at` and `a.ts` as agent fields.

SCOPE, STATED RATHER THAN IMPLIED. This sees reads through a variable literally named `agent`, and
nothing else. Mutation-tested both ways: inventing `agent.lastHeardFrom` on the dashboard turns it
red, but renaming the service's `statusNote` does NOT, because that field is read as `a.statusNote`
and `item.statusNote`. Widening the pattern to short variable names was tried and made the scan
wrong -- `a.at` and `a.ts` are not agent fields. So this gate covers the 22 reads that name their
subject, and a rename of a field read through another variable name is a gap it does not close.

A BLANKET snake_case BAN WAS CONSIDERED AND IS WRONG. The dead alternates removed alongside this test
were all snake_case, which suggests "the API speaks camelCase, so a snake_case read is dead or a
bug". Measured before proposing it: 51 distinct snake_case property reads across 109 sites, and most
are correct -- `/stats` emits `input_tokens` and `dispatch_runs_by_status`, analytics emits
`left_pct` and `used_pct`, settings emits `dashboard_theme`. The rule would have been false.

A third false signal came from the control itself: the first version asserted the producer scan was
sound if it emitted both `status` and `agentId`. `agentId` is legitimately absent -- the id is the
DICT KEY in `result[aid] = payload`, not a field -- so a correct scan looked broken. Separating the
two conditions is what told a wrong instrument from a wrong assumption.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"
DASH = SERVICE / "new_dashboard"

#: Every function that contributes a key to the agent payload. Named rather than discovered because
#: "which functions build this payload" is a fact about the route, not something a scan can infer.
ASSEMBLY = {"_agent_record_to_dict", "_enforce_live_worker_gate", "_enforce_env_reachable_gate"}

#: Property names that are JavaScript, not payload fields.
JS_BUILTINS = {"length", "map", "filter", "forEach", "id", "name", "type", "value"}


def _keys_a_function_emits(func: ast.AST) -> set[str]:
    """Keys of RETURNED dict literals, plus `payload["x"] = ...` assignments.

    Assignment TARGETS only. A subscript on the right-hand side is a read from the database row and
    counting it invents fields the payload does not have.
    """
    keys: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    keys.add(target.slice.value)
    return keys


def emitted_fields() -> tuple[set[str], set[str]]:
    """(fields the payload emits, names of the assembly functions actually found)."""
    fields: set[str] = set()
    found: set[str] = set()
    for path in SERVICE.rglob("*.py"):
        if "tests" in path.parts or "new_dashboard" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ASSEMBLY:
                found.add(node.name)
                fields |= _keys_a_function_emits(node)
    return fields, found


_AGENT_READ = re.compile(r"\bagent\.([a-zA-Z_]\w*)\b")


def dashboard_reads() -> dict[str, list[str]]:
    """`agent.X` reads in dashboard product modules, excluding comment lines."""
    hits: dict[str, list[str]] = {}
    sources = sorted(DASH.glob("*.mjs")) + [DASH / "app.js"]
    for path in sources:
        if ".test." in path.name or not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("//"):
                continue
            for match in _AGENT_READ.finditer(line):
                hits.setdefault(match.group(1), []).append(f"{path.name}:{number}")
    return hits


class DashboardReadsOnlyAgentFieldsTheServiceEmitsTests(unittest.TestCase):
    def test_both_scans_found_their_subject(self) -> None:
        """The control. Two empty sets agree perfectly, and the comparison below would pass having
        read nothing at all -- the exact wrong zero this file's docstring records twice."""
        fields, found = emitted_fields()
        self.assertEqual(found, ASSEMBLY, f"an assembly function was not found: missing {ASSEMBLY - found}")
        self.assertGreaterEqual(len(fields), 20, f"implausibly few emitted fields: {sorted(fields)}")
        reads = dashboard_reads()
        self.assertGreaterEqual(len(reads), 10, f"implausibly few agent.X reads: {sorted(reads)}")

    def test_each_scan_can_say_no(self) -> None:
        """The negative control. A producer scan that returned every string, or a consumer scan that
        matched anything, satisfies every assertion here while proving nothing."""
        fields, _ = emitted_fields()
        self.assertNotIn("aify_not_a_real_field", fields)
        self.assertNotIn("row", fields, "a database-row read is being counted as an emitted field")
        self.assertNotIn("aify_not_a_real_field", dashboard_reads())

    def test_a_known_field_is_seen_on_both_sides(self) -> None:
        """Anchors the comparison to something checkable by hand, so a pass means the two scans are
        looking at the same thing rather than agreeing by both being empty of the same names."""
        fields, _ = emitted_fields()
        self.assertIn("status", fields)
        self.assertIn("sessionMode", fields)
        self.assertIn("status", dashboard_reads())

    def test_agent_id_is_the_dict_key_and_not_a_field(self) -> None:
        """Pins the fact that cost a debugging cycle: `agentId` is absent from the payload on
        purpose, because `list_agents` returns `{agent_id: payload}`. A future reader seeing it
        missing should find this rather than conclude the producer scan is broken."""
        fields, _ = emitted_fields()
        self.assertNotIn("agentId", fields)

    def test_no_dashboard_read_is_a_field_the_service_never_sends(self) -> None:
        reads = dashboard_reads()
        fields, _ = emitted_fields()
        missing = {
            name: sites for name, sites in reads.items()
            if name not in fields and name not in JS_BUILTINS
        }
        self.assertEqual(
            missing, {},
            "the dashboard reads agent fields the service never emits, so they are `undefined` at "
            "runtime with no error and no log: "
            + "; ".join(f"agent.{n} at {', '.join(s[:3])}" for n, s in sorted(missing.items())),
        )


if __name__ == "__main__":
    unittest.main()
