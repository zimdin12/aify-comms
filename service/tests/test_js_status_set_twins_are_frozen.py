"""The canonical status sets are copied into JavaScript too, and nothing looked across the boundary.

`test_status_set_literal_twins_are_frozen.py` freezes the SQL copies of five canonical Python sets:
"Add a status to a canonical set and every hardcoded twin silently keeps the old meaning". It scans
`service/**/*.py`. **The JavaScript copies were outside every scan it makes.**

There are eleven of them, in two runtimes that cannot import Python at all — the dashboard, which
runs in a browser, and the MCP bridge, which runs on the host. Each spells out a set whose owner is a
Python constant:

    service/new_dashboard/notify.mjs         NOTIFIABLE_EVENTS         <- ntfy.NOTIFIABLE_EVENTS
    service/new_dashboard/run-inspector-...  terminal                  <- _DISPATCH_TERMINAL_STATUSES
    service/new_dashboard/status.js          AGENT_STATUSES            <- status_engine.VALID_STATUSES
    service/new_dashboard/sessions-list.mjs  LIVE_SESSION_ROW_STATUSES <- _LIVE_SESSION_STATUSES (+)
    service/new_dashboard/console-chooser.js sessionDead               <- (four candidates; see below)
    mcp/stdio/dispatch-execution.js          NATIVE_MANAGED_RUNTIMES   <- _NATIVE_MANAGED_RUNTIMES
    mcp/stdio/lifecycle-tools.mjs            live                      <- _LIVE_SESSION_STATUSES
    mcp/stdio/runtimes-pi.js                 PI_MODEL_PLACEHOLDER_...  <- runtimes.base.MODEL_PLACEHOLDERS
    mcp/stdio/virtual-terminals.mjs          VIRTUAL_RPC_RUNTIMES      <- _NATIVE_MANAGED_RUNTIMES
    mcp/stdio/adapters/base.js               MODEL_PLACEHOLDERS        <- runtimes.base.MODEL_PLACEHOLDERS
    mcp/stdio/adapters/base.js               HANDLE_PLACEHOLDERS       <- runtimes.base.HANDLE_PLACEHOLDERS
    mcp/stdio/doctor-predicates.js           ENV_KNOWN_STATES          <- env_status.ENVIRONMENT_STATUSES

THE LAST ONE ARRIVED BY THIS GATE WORKING. `ENV_KNOWN_STATES` was unattributable when the ledger was
first written, because the `environments.status` vocabulary had NO Python owner — it lived as prose
in one docstring and as literals at three write sites, and the only complete statement of it in the
repo was that JavaScript set. Declaring `env_status.ENVIRONMENT_STATUSES` made it bindable, and the
census demanded the declaration on the same run that introduced the constant.

THE LAST TWO ARE WHY THE CENSUS WALKS THE WHOLE REPO. `mcp/stdio/adapters/base.js` is a direct port
of `service/runtimes/base.py` — same class, same two constants, same normalization — and it sits one
directory deeper than every other bridge module. A scan listing `mcp/stdio/*.js` reads eleven twins
and misses these two while looking exactly as thorough. The Python side of the 1000-line gate made
that mistake for real, leaving fifteen files ungoverned including one that ships in the container.

Exactly ONE was bound to its owner: `AGENT_STATUSES`, by `test_status_vocabulary_binding.py`, written
for H1 of the 2026-07-31 audit. Its reasoning applies unchanged to the other ten and was never
extended to them — and that test's own words say why the drift would not announce itself: "a seventh
server-side state does not throw — it renders as a muted grey 'unknown' chip and filters into
nothing. The dashboard keeps working and quietly stops telling the truth."

WHAT DRIFT WOULD ACTUALLY DO, per twin, because "they should match" is not a reason:

  * `_LIVE_SESSION_STATUSES` gains a member -> `sessions-list.mjs` stops counting a live row as live.
    That list collapses each agent to one entry and keeps SEVERAL rows only when several are live,
    deliberately: "that is not clutter, it is a duplicate-worker leak — a class this repo has been
    bitten by — and hiding it would be the dashboard lying about a real fault." The drift hides
    exactly the fault the design refuses to hide. In `lifecycle-tools.mjs` the same set gates a
    bridge tool's view of which sessions are running.
  * `_NATIVE_MANAGED_RUNTIMES` gains a runtime -> the bridge does not dispatch to it natively and
    does not give it a virtual terminal. A new runtime that looks installed and never receives work.
  * `_DISPATCH_TERMINAL_STATUSES` gains a member -> the run inspector offers controls on a finished
    run.
  * `NOTIFIABLE_EVENTS` gains an event -> the dashboard silently declines to notify for it.

This gate does NOT rule that the copies should become imports. They cannot be: neither runtime can
read Python, and the vocabulary is not served by the API, "so there was no runtime path by which the
client could learn it either". Freezing is the available remedy, and it is the same one the SQL gate
chose for the same reason.

SCOPE, stated so an empty result is not confused with a clean one: the census below walks the WHOLE
repo for non-test `.js`/`.mjs`, not a hand-listed pair of roots — the 1000-line gate's Python half
read `service/**` only until 2026-08-15 and left fifteen files ungoverned, including one that ships
in the container. It matches only sets of 2-14 plain string literals, and only where the value set
belongs to exactly ONE Python constant name; a value set held by several differently-named constants
cannot be attributed to an owner and is recorded as ambiguous rather than bound to a guess. That is
the same scope rule the SQL gate states, and it is why `console-chooser.js`'s six dead-session
statuses are listed here as ambiguous: four Python constants hold those exact six values.
"""

from __future__ import annotations

import pathlib
import re
import unittest

from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES
from service.api_core.liveness import _LIVE_SESSION_STATUSES
from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.env_status import ENVIRONMENT_STATUSES
from service.ntfy import NOTIFIABLE_EVENTS
from service.runtimes.base import HANDLE_PLACEHOLDERS, MODEL_PLACEHOLDERS
from service.status_engine import VALID_STATUSES

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

#: The Python constants whose values reach JavaScript. Imported, not re-parsed: the owner's runtime
#: value is the contract, and a test that re-reads the source could agree with a file while
#: disagreeing with the object the service actually uses.
OWNERS: dict[str, frozenset] = {
    "NOTIFIABLE_EVENTS": frozenset(NOTIFIABLE_EVENTS),
    "VALID_STATUSES": frozenset(VALID_STATUSES),
    "_DISPATCH_TERMINAL_STATUSES": frozenset(_DISPATCH_TERMINAL_STATUSES),
    "_LIVE_SESSION_STATUSES": frozenset(_LIVE_SESSION_STATUSES),
    "_NATIVE_MANAGED_RUNTIMES": frozenset(_NATIVE_MANAGED_RUNTIMES),
    "_TERMINAL_ACTIVE_STATUSES": frozenset(_TERMINAL_ACTIVE_STATUSES),
    "ENVIRONMENT_STATUSES": frozenset(ENVIRONMENT_STATUSES),
    "HANDLE_PLACEHOLDERS": frozenset(HANDLE_PLACEHOLDERS),
    "MODEL_PLACEHOLDERS": frozenset(MODEL_PLACEHOLDERS),
}

#: JS declaration -> the Python constant name(s) holding the identical value set.
#:
#: `owners` with more than one name means the value set is AMBIGUOUS — several differently-named
#: Python constants hold it, so no single owner can be asserted. Those entries are declared to keep
#: the census complete, not to bind anything; picking one owner for them is a reviewer's call.
EXACT_TWINS: dict[tuple[str, str], list[str]] = {
    # A direct port of `service/runtimes/base.py`, down to the constant names. The Python side
    # already freezes both value sets (`service/tests/runtimes/test_base.py`); nothing was watching
    # the port.
    ("mcp/stdio/adapters/base.js", "HANDLE_PLACEHOLDERS"): ["HANDLE_PLACEHOLDERS"],
    ("mcp/stdio/adapters/base.js", "MODEL_PLACEHOLDERS"): ["MODEL_PLACEHOLDERS"],
    # THIS ENTRY EXISTS BECAUSE THE PYTHON SIDE GAINED AN OWNER, not because the JS changed.
    # `ENV_KNOWN_STATES` held the only complete statement of the `environments.status` vocabulary
    # anywhere in the repo — Python had it as prose plus three scattered write sites — so the census
    # could not attribute it to anything. Declaring `ENVIRONMENT_STATUSES` made it bindable, and this
    # gate demanded the declaration on the same run.
    ("mcp/stdio/doctor-predicates.js", "ENV_KNOWN_STATES"): ["ENVIRONMENT_STATUSES"],
    ("mcp/stdio/dispatch-execution.js", "NATIVE_MANAGED_RUNTIMES"): ["_NATIVE_MANAGED_RUNTIMES"],
    ("mcp/stdio/lifecycle-tools.mjs", "live"): ["_LIVE_SESSION_STATUSES"],
    ("mcp/stdio/runtimes-pi.js", "PI_MODEL_PLACEHOLDER_VALUES"): ["MODEL_PLACEHOLDERS"],
    ("mcp/stdio/virtual-terminals.mjs", "VIRTUAL_RPC_RUNTIMES"): ["_NATIVE_MANAGED_RUNTIMES"],
    ("service/new_dashboard/notify.mjs", "NOTIFIABLE_EVENTS"): ["NOTIFIABLE_EVENTS"],
    ("service/new_dashboard/run-inspector-controls.mjs", "terminal"): ["_DISPATCH_TERMINAL_STATUSES"],
    ("service/new_dashboard/status.js", "AGENT_STATUSES"): ["VALID_STATUSES"],
    # AMBIGUOUS. `ENDED_AGENT_SESSION_STATUSES`, `_TERMINAL_END_STATUSES`,
    # `_SESSION_DELETE_ALLOWED_STATUSES` and `_TERMINAL_DELETE_ALLOWED_STATUSES` all hold these six.
    # Which one the console chooser is copying is not derivable from the values.
    ("service/new_dashboard/console-chooser.js", "sessionDead"): [
        "ENDED_AGENT_SESSION_STATUSES",
        "_SESSION_DELETE_ALLOWED_STATUSES",
        "_TERMINAL_DELETE_ALLOWED_STATUSES",
        "_TERMINAL_END_STATUSES",
    ],
}

#: JS sets that are DELIBERATELY WIDER than a Python owner. The census cannot find these — it matches
#: on equality — so they are declared by hand, and the assertion is containment in the direction that
#: matters: every Python member must be present. A JS-only extra is the design; a Python-only member
#: is the silent failure.
WIDER_TWINS: dict[tuple[str, str], dict] = {
    ("service/new_dashboard/sessions-list.mjs", "LIVE_SESSION_ROW_STATUSES"): {
        "contains": "_LIVE_SESSION_STATUSES",
        # The file says it mirrors `_LIVE_SESSION_STATUSES` "plus the worker-detail statuses the
        # sessions list also treats as live". Those extras equal `_TERMINAL_ACTIVE_STATUSES` today —
        # but so does the union with `tuning.LIVE_SESSION_STATUSES`, so which constant the extras
        # came from is not decidable from the values. Only the containment is asserted; the extras
        # are recorded here so a future reader does not have to re-derive them.
        "extras_today": ["active", "attached", "idle"],
    },
}

#: `const NAME = [...]` / `const NAME = new Set([...])`, both quote styles, any indentation.
#: Located by NAME rather than by position: a test that matches leading whitespace asserts the file's
#: formatting, which is what made thirteen source-reading tests break on moves with their invariants
#: fully intact.
DECL = re.compile(
    r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:new\s+Set\s*\(\s*)?\[([^\]]*)\]",
    re.S,
)
STRING = re.compile(r"['\"]([A-Za-z0-9_.:-]+)['\"]")


def _js_sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted(REPO.rglob("*.*js")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts) or path.suffix not in (".js", ".mjs"):
            continue
        if ".test." in path.name:
            continue
        out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def _js_string_sets(sources) -> dict[tuple[str, str], frozenset]:
    """Every JS declaration whose initialiser is a list of 2-14 plain string literals."""
    found: dict[tuple[str, str], frozenset] = {}
    for rel, src in sources:
        for match in DECL.finditer(src):
            body = match.group(2)
            values = STRING.findall(body)
            # Reject anything that is not PURELY string literals: a list holding an identifier, a
            # call or a spread is not a value set and must not be attributed to a Python constant.
            residue = re.sub(r"['\"][^'\"]*['\"]|[\s,]", "", body)
            if residue or not 2 <= len(values) <= 14:
                continue
            found[(rel, match.group(1))] = frozenset(values)
    return found


def _census(sources) -> dict[tuple[str, str], list[str]]:
    """JS sets whose value set is held by at least one Python constant, mapped to those names."""
    by_values: dict[frozenset, list[str]] = {}
    for name, values in _python_named_sets().items():
        by_values.setdefault(values, []).append(name)
    return {
        key: sorted(by_values[values])
        for key, values in _js_string_sets(sources).items()
        if values in by_values
    }


def _python_named_sets() -> dict[str, frozenset]:
    """Module-level `NAME = {"a", "b"}` sets across `service/`, keyed by name.

    A name declared twice with DIFFERENT values is dropped: it cannot stand for one value set.
    `_NATIVE_MANAGED_RUNTIMES` is declared in two modules with the same four runtimes, which is
    fine — that is one constant with two homes, and the SQL twin gate has the same situation.
    """
    import ast

    seen: dict[str, set[frozenset]] = {}
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", "") in ("set", "frozenset")
                and value.args
            ):
                value = value.args[0]
            if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
                continue
            items = [e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(items) != len(value.elts) or not 2 <= len(items) <= 14:
                continue
            seen.setdefault(target.id, set()).add(frozenset(items))
    return {name: next(iter(vs)) for name, vs in seen.items() if len(vs) == 1}


def _declared_set(rel: str, name: str) -> frozenset:
    src = (REPO / rel).read_text(encoding="utf-8")
    for match in DECL.finditer(src):
        if match.group(1) == name:
            return frozenset(STRING.findall(match.group(2)))
    raise AssertionError(
        f"{rel} no longer declares `{name}` as a list of string literals. If it moved or changed "
        f"shape, update this ledger WITH the change — do not delete the entry to go green."
    )


class JsStatusSetTwinsTests(unittest.TestCase):
    def test_each_bound_twin_still_equals_its_python_owner(self):
        """THE ONE THAT MATTERS. Change a Python constant and the JS copy keeps the old meaning."""
        for (rel, name), owners in EXACT_TWINS.items():
            if len(owners) != 1:
                continue  # ambiguous; see the ledger comment
            owner = owners[0]
            with self.subTest(js=f"{rel}:{name}", owner=owner):
                self.assertIn(owner, OWNERS, f"{owner} is not imported by this test any more")
                self.assertEqual(
                    sorted(_declared_set(rel, name)),
                    sorted(OWNERS[owner]),
                    f"{rel} declares `{name}` with values that no longer match Python's {owner}. "
                    f"Neither the dashboard nor the bridge can import Python, so nothing else will "
                    f"notice: the copy simply keeps the old meaning.",
                )

    def test_the_wider_twins_still_contain_every_python_member(self):
        for (rel, name), entry in WIDER_TWINS.items():
            owner = entry["contains"]
            with self.subTest(js=f"{rel}:{name}", owner=owner):
                js = _declared_set(rel, name)
                missing = sorted(OWNERS[owner] - js)
                self.assertEqual(
                    missing, [],
                    f"{rel}'s `{name}` is missing {missing}, which Python's {owner} now treats as "
                    f"live. The sessions list collapses each agent to one row and keeps several only "
                    f"when several are LIVE — a status it does not recognise as live makes it hide a "
                    f"duplicate worker, the exact fault that display refuses to hide.",
                )
                self.assertEqual(
                    sorted(js - OWNERS[owner]), entry["extras_today"],
                    "the deliberate extras changed; that is a design change, so update the ledger",
                )

    def test_no_new_javascript_file_starts_spelling_a_python_set_out(self):
        actual = _census(_js_sources())
        self.assertEqual(
            {f"{rel}:{name}": owners for (rel, name), owners in sorted(actual.items())},
            {f"{rel}:{name}": owners for (rel, name), owners in sorted(EXACT_TWINS.items())},
            "the JavaScript copies of Python status sets changed. FEWER is good — a copy became "
            "derived or was deleted. MORE means a new hand-typed copy that will not move when the "
            "Python constant does; declare it here with its owner, or with every candidate owner if "
            "the value set is ambiguous.",
        )

    def test_the_scan_is_not_silently_matching_nothing(self):
        """Anti-vacuity in BOTH populations: a broken JS walk and a broken Python walk each report a
        clean repo, and so does a correct scan over a repo with no twins."""
        sources = _js_sources()
        self.assertGreater(len(sources), 60, "the JS walk found almost no files")
        self.assertTrue(
            any(rel.startswith("mcp/stdio/") for rel, _ in sources)
            and any(rel.startswith("service/new_dashboard/") for rel, _ in sources),
            "the walk must reach BOTH JS runtimes — hand-listed roots covered the tree only by "
            "coincidence once already",
        )
        self.assertGreater(len(_js_string_sets(sources)), 20, "no JS string sets parsed at all")
        self.assertGreater(len(_python_named_sets()), 10, "no Python named sets parsed at all")

    def test_the_detector_reads_both_quote_styles_and_both_container_shapes(self):
        """The four shapes these eleven twins are actually written in — and one that is not a set.

        This is not hypothetical caution. The bridge's dead-import detector assumed double quotes
        everywhere and therefore collected NOTHING from the single-quoted dashboard, reporting all 59
        of its modules clean; the same blindness hid nine dead imports in `server.js`. A parser that
        reads nothing reports the same green as one that found nothing.
        """
        def parse(src):
            return _js_string_sets([("x.js", src)])

        self.assertEqual(
            parse('const A = ["a", "b"];'), {("x.js", "A"): frozenset({"a", "b"})},
            "double-quoted array",
        )
        self.assertEqual(
            parse("const A = ['a', 'b'];"), {("x.js", "A"): frozenset({"a", "b"})},
            "single-quoted array — the dashboard's style",
        )
        self.assertEqual(
            parse('export const A = new Set(["a", "b"]);'), {("x.js", "A"): frozenset({"a", "b"})},
            "exported Set — the bridge's style",
        )
        self.assertEqual(
            parse('    const A = new Set(["a", "b"]);'), {("x.js", "A"): frozenset({"a", "b"})},
            "indented, function-local — three of the eleven twins are declared inside a function",
        )
        self.assertEqual(
            parse('const A = ["a", B, "c"];'), {},
            "a list holding an identifier is not a value set and must not be bound to a constant",
        )
        self.assertEqual(parse('const A = ["only"];'), {}, "a one-member list is not a set")

    def test_the_census_attributes_only_unambiguous_value_sets(self):
        """A value set held by two differently-named constants has no derivable owner. The census
        must still REPORT it — silently dropping it would let a new copy of an ambiguous set land
        undeclared, which is the hole this gate exists to close — but it reports both names so the
        ledger has to say so out loud."""
        census = _census(_js_sources())
        ambiguous = {k: v for k, v in census.items() if len(v) > 1}
        self.assertTrue(ambiguous, "no ambiguous twin found; console-chooser.js should be one")
        for key, owners in ambiguous.items():
            self.assertEqual(
                owners, EXACT_TWINS[key],
                f"the constants holding {key}'s value set changed",
            )
