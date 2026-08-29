r"""`pendingResidentTakeover` is retired, and the OTHER side of the join has to know.

THE SHAPE, which is worth more than the key. `e3c3ce8c` ("fix(sessions): make ownership switching
manual", 2026-05-26) deleted the mechanism that wrote `runtime_state.pendingResidentTakeover` and
returned `ownershipTransition="pending_resident_takeover"`. The service side of that deletion is
documented twice already -- it kept the ACTION and dropped the CONDITION, froze every managed agent's
`bridgeInstanceId`, and cost 19 of 24 agents a correct owner until it was found on 2026-08-29.

What nobody looked at was the CONSUMER. The bridge kept reading both inputs, in two places:

    auto-registration.mjs   const pendingTakeover =
                              r.ownershipTransition === "pending_resident_takeover" ||
                              (runtimeState?.pendingResidentTakeover && ... === BRIDGE_INSTANCE_ID);
                            ...
                            if (!pendingTakeover && ownership.claim) { ... }

    dispatch-loop.mjs       if (managed && runtimeState?.pendingResidentTakeover &&
                                ... === BRIDGE_INSTANCE_ID) continue;   // do not claim work

Neither could fire. A deletion on one side of a process boundary leaves the other side compiling,
passing its own suite, and reading a value that stopped arriving -- the same class as the dashboard's
dead field alternates, one component further out.

THIS FILE COVERS BOTH SIDES, AND THE BRIDGE SIDE IS A SOURCE SCAN ON PURPOSE. The service is the
authority on whether either input can arrive, so a bridge test asserting "the key never comes" would
only assert what its own fixture chose to send. Driving the real call site would mean executing
`autoRegisterConfiguredAgent`, which resolves its endpoint at module load and POSTs `/agents` -- a
suite that registers agents is how six of them once landed in the operator's production registry --
and it would prove nothing extra: the removed term was provably always false, so the reduced
condition is the same condition. What needs a gate is that the two inputs stay retired, and that is
what the two mutations below check.

THE BEHAVIOUR IS NOT LOST. A resident registration against a driving managed agent takes the
`manualResidentCandidate` path and is answered `manual_switch_required`; `registration.py` states in
as many words that it "never lets the resident drive; the operator switches in the dashboard". The
bridge's hold-back was the old half of a mechanism whose new half is server-side.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"
BRIDGE = ROOT / "mcp" / "stdio"

RETIRED_KEY = "pendingResidentTakeover"
RETIRED_TRANSITION = "pending_resident_takeover"


def _service_sources() -> list[Path]:
    return [
        path for path in SERVICE.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
        and "new_dashboard" not in path.parts
    ]


def _bridge_sources() -> list[Path]:
    return [
        path for path in list(BRIDGE.glob("*.js")) + list(BRIDGE.glob("*.mjs"))
        if ".test." not in path.name
    ]


class ARetiredKeyHasNoReadersLeftTests(unittest.TestCase):
    def test_the_scans_found_their_subjects(self) -> None:
        """The control. Two empty file lists agree perfectly and prove nothing."""
        self.assertGreater(len(_service_sources()), 100, "the service scan found almost nothing")
        self.assertGreater(len(_bridge_sources()), 20, "the bridge scan found almost nothing")
        self.assertTrue(
            any(RETIRED_KEY in p.read_text(encoding="utf-8") for p in _service_sources()),
            "the key is not mentioned anywhere in the service, so this file is testing a typo",
        )

    def test_nothing_in_the_service_WRITES_the_retired_key(self) -> None:
        """Every mention left is a `pop` or prose. A write would make the bridge's readers live
        again, and they are gone -- so a future writer must fail here rather than half-work."""
        writes: list[str] = []
        for path in _service_sources():
            source = path.read_text(encoding="utf-8")
            if RETIRED_KEY not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:  # pragma: no cover - the compile gate owns this
                continue
            for node in ast.walk(tree):
                # `x["pendingResidentTakeover"] = ...`
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Subscript)
                                and isinstance(target.slice, ast.Constant)
                                and target.slice.value == RETIRED_KEY):
                            writes.append(f"{path.name}:{node.lineno}")
                # `{"pendingResidentTakeover": ...}` in a dict literal
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and key.value == RETIRED_KEY:
                            writes.append(f"{path.name}:{node.lineno}")
                # `x.setdefault("pendingResidentTakeover", ...)` / `.update({...})` by name
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"setdefault"} and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == RETIRED_KEY):
                    writes.append(f"{path.name}:{node.lineno}")
        self.assertEqual(writes, [], (
            "something writes the retired key again, and the bridge no longer reads it: "
            + ", ".join(writes)
        ))

    def test_the_transition_vocabulary_does_not_contain_the_retired_value(self) -> None:
        """The other input the bridge used to key on. The service emits exactly two transitions."""
        emitted = set()
        for path in _service_sources():
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r'"ownershipTransition"\s*:\s*"([a-z_]+)"', source):
                emitted.add(match.group(1))
        self.assertTrue(emitted, "no ownershipTransition value was found at all, so this proves nothing")
        self.assertNotIn(RETIRED_TRANSITION, emitted, f"the retired transition is back: {sorted(emitted)}")

    def test_the_bridge_no_longer_reads_either_input(self) -> None:
        """The consumer, checked from here because this is the file that knows they are retired.

        Read as source rather than executed: importing the bridge's registration path starts a
        registration, which is exactly what must not happen in a suite.
        """
        offenders: list[str] = []
        for path in _bridge_sources():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                if RETIRED_KEY in line or RETIRED_TRANSITION in line:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], (
            "the bridge reads a value the service stopped sending in 2026-05: "
            + ", ".join(offenders)
        ))


if __name__ == "__main__":
    unittest.main()
