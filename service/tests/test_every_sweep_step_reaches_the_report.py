r"""A reconciler step whose result never reaches the sweep report is a silent healer.

`_run_dispatch_reconcile_once` runs 25 repair steps and returns a dict of counters. `service/main.py`
logs the truthy entries of that dict, once a minute and once at startup, and that log line is the ONLY
window anyone has into what the reconciler did. A step whose result is bound and then left out of the
dict does its work invisibly: nobody sees it working, and nobody sees it stop.

THE ONE THAT WAS MISSING, found 2026-08-29 by asking which locals a function computes and never reads:
`mirrored_failed_handoffs`. It counts sender notices mailed for require_reply runs that a REAPER
failed -- the sweep that exists because "a run failed by the orphan-closer never told the sender"
(review must-fix, 2026-06-10). MEASURED on the operator's live database: 49 such notices have been
mirrored, 17 of them on 2026-08-25 and 8 on 2026-08-26, and not one produced a log line. 24 of the 25
steps reported; this was the only hole, which is why the ceiling below is ZERO rather than a ratchet.

The dict itself already carries the rule, twice, in comments written by whoever added those keys:
"Reported in the sweep log so a repair is VISIBLE. A silent healer is indistinguishable from one that
never ran", and "Reported ALONGSIDE the reap count, not folded into it." Both were right and neither
was checkable. This makes them checkable.

DERIVED, NEVER LISTED. The step names come from the module's own syntax tree, so a step added
tomorrow is covered the day it lands rather than the day somebody remembers to extend a list.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SWEEP = REPO / "service" / "reconcilers" / "sweep.py"
SWEEP_FUNCTION = "_run_dispatch_reconcile_once"


def steps_and_report(source: str, function_name: str) -> tuple[dict[str, int], set[str]]:
    """(name -> line for every result bound from `_commit_step`, every name the return dict uses).

    A PURE function taking source text, so the scan can be driven with a known-bad input below. A
    scanner that has only ever been pointed at code that passes is a scanner nobody has watched work.
    """
    produced: dict[str, int] = {}
    reported: set[str] = set()
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or func.name != function_name:
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                for sub in ast.walk(node.value):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                            and sub.func.id == "_commit_step"):
                        produced.setdefault(node.targets[0].id, node.targets[0].lineno)
            elif isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Name):
                        reported.add(sub.id)
    return produced, reported


class EverySweepStepReachesTheReport(unittest.TestCase):
    def setUp(self) -> None:
        self.produced, self.reported = steps_and_report(
            SWEEP.read_text(encoding="utf-8"), SWEEP_FUNCTION,
        )

    def test_no_step_result_is_dropped(self):
        dropped = sorted((line, name) for name, line in self.produced.items() if name not in self.reported)
        self.assertEqual(dropped, [], (
            "these reconciler steps run and report nothing:\n"
            + "\n".join(f"  sweep.py:{line}  {name}" for line, name in dropped)
            + "\nAdd the counter to the returned dict. `service/main.py` logs that dict and nothing "
              "else, so a step missing from it heals silently and fails silently."
        ))

    def test_THE_SCAN_FINDS_THE_STEPS(self):
        """POSITIVE CONTROL. An empty scan passes the assertion above for the wrong reason, and this
        file's whole value is that its zero means something. If the sweep is refactored so steps stop
        being bound from `_commit_step`, this is what says so."""
        self.assertGreaterEqual(len(self.produced), 20, (
            f"only {len(self.produced)} step(s) found in {SWEEP_FUNCTION}; the scan has stopped "
            "seeing the steps it governs and its 'nothing dropped' verdict is empty"
        ))

    def test_THE_SCAN_CAN_SAY_NO(self):
        """NEGATIVE CONTROL, on source written to fail. `swept` reaches the report, `unswept` does
        not, and a scanner that cannot tell them apart cannot certify the real file either."""
        produced, reported = steps_and_report(
            "async def _run_dispatch_reconcile_once():\n"
            "    swept = await _commit_step(await _a(db))\n"
            "    unswept = await _commit_step(await _b(db))\n"
            "    return {'swept': swept}\n",
            SWEEP_FUNCTION,
        )
        self.assertEqual(sorted(produced), ["swept", "unswept"])
        self.assertEqual(sorted(name for name in produced if name not in reported), ["unswept"])

    def test_a_step_wrapped_in_len_still_counts_as_reported(self):
        """Most counters reach the dict as `len(x)` or `x.get(...)`, not as a bare name, so the
        reported set is every Name anywhere inside the dict rather than its values alone. Getting
        this wrong would report two dozen false drops and train the next person to ignore the test."""
        produced, reported = steps_and_report(
            "async def _run_dispatch_reconcile_once():\n"
            "    rows = await _commit_step(await _a(db))\n"
            "    bag = await _commit_step(await _b(db))\n"
            "    return {'n': len(rows), 'm': bag.get('m', 0)}\n",
            SWEEP_FUNCTION,
        )
        self.assertEqual(sorted(name for name in produced if name not in reported), [])


if __name__ == "__main__":
    unittest.main()
