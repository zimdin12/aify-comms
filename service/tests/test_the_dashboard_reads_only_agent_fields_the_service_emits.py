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


class UnresolvedSpread(Exception):
    """A `**something` in a payload whose keys this scan cannot name.

    Raised rather than skipped. A spread it cannot resolve makes the emitted set SMALLER, which turns
    a legitimate read into a reported defect -- and silently under-counting is how the first version
    of this scan reported `contract.state` and `contract.overdue` as never emitted when both arrive
    through `**_contract_state(...)`.
    """


def _keys_a_function_emits(func: ast.AST, resolve_call, depth: int = 0) -> set[str]:
    """Keys of RETURNED dict literals, `payload["x"] = ...` assignments, and resolved spreads.

    Assignment TARGETS only. A subscript on the right-hand side is a read from the database row and
    counting it invents fields the payload does not have.
    """
    if depth > 4:
        raise UnresolvedSpread("spread resolution nested deeper than 4 calls")
    keys: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values):
                if key is None:
                    # `**something` — the key list carries a None in its place.
                    keys |= _spread_keys(value, resolve_call, depth, func)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
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


def _binding_of(name: str, func: ast.AST):
    """What `name` was last assigned inside `func`, or None."""
    found = None
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    found = node.value
    return found


def _spread_keys(value: ast.AST, resolve_call, depth: int, enclosing: ast.AST) -> set[str]:
    """The keys a `**x` contributes, or a loud failure naming what could not be resolved."""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        target = resolve_call(value.func.id)
        if target is None:
            raise UnresolvedSpread(f"**{value.func.id}(...) — that function was not found")
        return _keys_a_function_emits(target, resolve_call, depth + 1)
    if isinstance(value, ast.Dict):
        return {k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if isinstance(value, ast.Name):
        # `state = _contract_state(...)` then `**state`. The binding is local, so the assignment is
        # in the same function; resolving the call it holds is the same problem one step removed.
        bound = _binding_of(value.id, enclosing)
        if bound is None:
            raise UnresolvedSpread(
                "**" + value.id + " -- nothing in this function assigns that name a dict"
            )
        return _spread_keys(bound, resolve_call, depth, enclosing)
    raise UnresolvedSpread(f"a spread of {type(value).__name__}, which this scan cannot name")


def _all_service_functions() -> dict[str, ast.AST]:
    """Every top-level function in the service, by name, so a spread can be followed across files.

    `_contract_row_to_dict` lives in routers/ and spreads a dict built in api_core/, so a scan
    confined to one file cannot resolve it.
    """
    index: dict[str, ast.AST] = {}
    for path in SERVICE.rglob("*.py"):
        if "tests" in path.parts or "new_dashboard" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(node.name, node)
    return index


def emitted_fields(assembly: frozenset[str] = None) -> tuple[set[str], set[str]]:
    """(fields the payload emits, names of the assembly functions actually found)."""
    assembly = ASSEMBLY if assembly is None else assembly
    index = _all_service_functions()
    fields: set[str] = set()
    found: set[str] = set()
    for name in assembly:
        node = index.get(name)
        if node is None:
            continue
        found.add(name)
        fields |= _keys_a_function_emits(node, index.get)
    return fields, found


#: `agent.x` AND `agent?.x`. The optional form is not a variant to be thorough about -- it is the
#: dominant idiom in the modules that hold the field readers, and this pattern matched only the plain
#: form until 2026-08-29. Counted that day across the dashboard's product modules: agent 46 plain /
#: 23 optional, env 42 / 9, contract 40 / 3, session 28 / 30. A third of the agent reads and MOST of
#: the session reads sat outside a gate whose whole purpose is catching a rename that JavaScript
#: reports as `undefined`, with no error and nothing logged.
_READ_TEMPLATE = r"\b{}\??\.([a-zA-Z_]\w*)\b"
_AGENT_READ = re.compile(_READ_TEMPLATE.format("agent"))


def _reads_through(variable: str) -> dict[str, list[str]]:
    """`<variable>.X` and `<variable>?.X` reads in dashboard product modules, excluding comments."""
    return _scan_reads(re.compile(_READ_TEMPLATE.format(re.escape(variable))))


def dashboard_reads() -> dict[str, list[str]]:
    """`agent.X` reads in dashboard product modules, excluding comment lines."""
    return _scan_reads(_AGENT_READ)


def dashboard_sources() -> list[Path]:
    """Every product module in the dashboard directory, derived rather than listed.

    This was `*.mjs` plus `app.js` BY NAME, which excluded the seven other `.js` modules beside it --
    `analytics.js`, `chat.js`, `console-chooser.js`, `status.js`, `theme.js`, `ui.js`, `util.js`.
    Measured 2026-08-29: 7 agent reads lived in them, unwatched, including the two in
    `console-chooser.js` that decide which console widget to mount. `app.js` was named because it was
    the file being sliced; its siblings were never considered.
    """
    return sorted(
        path for path in list(DASH.glob("*.mjs")) + list(DASH.glob("*.js"))
        if ".test." not in path.name
    )


def _scan_reads(pattern: "re.Pattern") -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in dashboard_sources():
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("//"):
                continue
            for match in pattern.finditer(line):
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


#: The other payloads the dashboard reads by name, each with the variable that holds it. Only
#: unambiguous variable names are listed: `r.` and `s.` name several different record types in the
#: dashboard, so a diff on them would report noise rather than defects.
OTHER_PAYLOADS = (
    ("env", frozenset({"_environment_record_to_dict"})),
    ("contract", frozenset({"_contract_row_to_dict"})),
    # SESSIONS, added 2026-08-29. The same join, unwatched until now: ONE producer and ONE
    # assignment on the dashboard side (`refresh-cycle.mjs`, from `/sessions`), and 16 reads that
    # named nothing the producer emits. `_agent_session_dict_live` is deliberately NOT listed -- it
    # calls `_agent_session_to_dict` and then overwrites `status`, so it adds no key, and naming it
    # here would make the assembly set claim a second producer that contributes nothing.
    ("session", frozenset({"_agent_session_to_dict"})),
)


class OtherPayloadContractsTests(unittest.TestCase):
    """The same join for environments and contracts.

    MEASURED 2026-08-28: environments emit 18 keys against 12 reads, contracts 20-plus against 11,
    and every mismatch was a dead `||` alternate beside the name the service does emit --
    `env.last_seen`, `env.machine_id`, `env.environmentId`. Removed, so this can be strict.
    """

    def test_the_contract_payload_resolves_its_spread(self) -> None:
        """The control THIS class exists for.

        `_contract_row_to_dict` ends in `**_contract_state(...)`, and a scan blind to that reports
        `contract.state` and `contract.overdue` -- 11 read sites between them -- as fields the
        service never sends. Both arrive through the spread, and `_contract_state` lives in a
        different file, so resolution has to cross files as well as follow the call.
        """
        fields, found = emitted_fields(frozenset({"_contract_row_to_dict"}))
        self.assertEqual(found, {"_contract_row_to_dict"})
        self.assertIn("state", fields, "the spread was not followed")
        self.assertIn("overdue", fields, "the spread was not followed")
        self.assertIn("id", fields, "a plain literal key is missing, so the scan is broken outright")

    def test_an_unresolvable_spread_is_raised_not_skipped(self) -> None:
        """No evidence is not a pass. A spread this cannot name must stop the scan, because silently
        under-counting turns a legitimate read into a reported defect."""
        module = ast.parse("def f(row):" + chr(10) + "    return {'a': 1, **row.extras}" + chr(10))
        with self.assertRaises(UnresolvedSpread):
            _keys_a_function_emits(module.body[0], lambda name: None)

    def test_no_dashboard_read_is_a_field_these_payloads_never_send(self) -> None:
        for variable, assembly in OTHER_PAYLOADS:
            with self.subTest(payload=variable):
                fields, found = emitted_fields(assembly)
                self.assertEqual(found, set(assembly), "producer not found for " + variable)
                self.assertGreaterEqual(len(fields), 10, "implausibly few fields for " + variable)
                reads = _reads_through(variable)
                self.assertGreaterEqual(len(reads), 5, "implausibly few " + variable + ".X reads")
                missing = {
                    name: sites for name, sites in reads.items()
                    if name not in fields and name not in JS_BUILTINS
                }
                self.assertEqual(
                    missing, {},
                    "the dashboard reads " + variable + " fields the service never emits: "
                    + "; ".join(
                        variable + "." + n + " at " + ", ".join(s[:3])
                        for n, s in sorted(missing.items())
                    ),
                )


if __name__ == "__main__":
    unittest.main()
