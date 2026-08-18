"""The recorded baseline must name tests that EXIST, or Phase 5's comparison proves nothing.

WHAT THE BASELINE IS FOR. `baseline.json` records the e2e pass set at the START of v0.6, so the same
suite re-run at the END can be compared against it. The comparison is the only mechanical answer to
"did a release's worth of restructuring break delivery?" — and it is worth exactly as much as the
baseline's honesty.

THE FAILURE MODE THIS GUARDS. A baseline is a file, and a file rots quietly: rename a test and the
recorded id points at nothing, delete a file and the count still reads reassuringly high. Phase 5 would
then compare against names rather than behaviour and report a clean sweep. So the baseline is checked
on every run, not only at the end — a stale one fails NOW, while somebody remembers what changed.

WHAT IT DELIBERATELY DOES NOT DO. It does not assert that the baselined tests still PASS: that is what
running the suite does, and duplicating it here would mean a single failure reported twice with the
second report less informative than the first. This asserts the baseline still DESCRIBES the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
REPO_ROOT = HERE.parents[2]


def _recorded() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_baseline_exists_and_is_not_a_stub():
    """ANTI-VACUITY: every assertion below passes trivially against an empty test list."""
    assert BASELINE.exists(), (
        "baseline.json is missing. Phase 5 has nothing to compare against, so the end-to-end proof "
        "at the end of v0.6 would be an assertion that the suite passes today — which says nothing "
        "about what changed."
    )
    recorded = _recorded()
    assert recorded.get("passing", 0) >= 3, (
        f"a baseline of {recorded.get('passing')} tests is not a baseline"
    )
    assert len(recorded.get("tests", [])) == recorded.get("passing"), (
        "the recorded count and the recorded test list disagree, so one of them was hand-edited"
    )


def test_every_baselined_test_file_still_exists():
    for node_id in _recorded()["tests"]:
        path = REPO_ROOT / node_id.split("::")[0]
        assert path.exists(), (
            f"the baseline names a test file that no longer exists: {node_id}. Either restore it or "
            f"re-record the baseline in the same change — and if a test was deliberately removed, "
            f"say so in the release notes, because Phase 5 can no longer prove that behaviour."
        )


def test_the_suite_has_not_SHRUNK_below_the_baseline():
    """Growth is expected and fine; shrinkage means a behaviour stopped being proven. Counted from the
    files on disk rather than from a pytest run, so this stays cheap and cannot deadlock on itself."""
    import re

    on_disk = 0
    for path in sorted(HERE.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        on_disk += len(re.findall(r"^def (test_\w+)", source, flags=re.M))
    recorded = _recorded()["passing"]
    assert on_disk >= recorded, (
        f"the e2e suite has {on_disk} tests but the baseline recorded {recorded}. A test that "
        f"disappeared is a property nobody is proving any more; if that was deliberate, re-record the "
        f"baseline in the same commit so the loss is visible in the diff."
    )
