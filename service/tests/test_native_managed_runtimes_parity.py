"""M2 (2026-06-06): every copy of NATIVE_MANAGED_RUNTIMES must agree.

If they drift, a runtime added on one side but not the other silently no-ops: the dispatch loop skips it
(queued forever) or the service routes a managed run nobody claims. This test extracts each copy and
asserts they are equal, so the drift fails CI instead of in production.

THERE ARE THREE COPIES, and until v0.5.4 this test guarded only two:

  service/api_core/runtime.py    the service's set (was service/control_plane.py; before that api_v2.py)
  mcp/stdio/dispatch-execution.js  the bridge's `new Set([...])`
  service/db.py                  its OWN tuple, unguarded until now

The db.py copy was found while relocating the service's set in v0.5.4 and it was genuinely ungoverned —
nothing compared it to anything. It is asserted here rather than deleted: three literals agreeing is a
weaker design than one, but converting db.py to an import is a change to a module this structural slice
was not opening, and an agreement test is the response a duplication finding earns. Note the type
differs (a tuple there, a set here); the comparison is by CONTENT because both are only ever membership-
tested.
"""

import re
from pathlib import Path

from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DISPATCH_JS = _REPO_ROOT / "mcp" / "stdio" / "dispatch-execution.js"


def _js_native_managed_runtimes() -> set[str]:
    text = _DISPATCH_JS.read_text(encoding="utf-8")
    m = re.search(r"NATIVE_MANAGED_RUNTIMES\s*=\s*new\s+Set\(\[([^\]]*)\]\)", text)
    assert m, "could not find NATIVE_MANAGED_RUNTIMES = new Set([...]) in dispatch-execution.js"
    return set(re.findall(r"""['"]([^'"]+)['"]""", m.group(1)))


def test_native_managed_runtimes_bridge_service_parity():
    js = _js_native_managed_runtimes()
    py = set(_NATIVE_MANAGED_RUNTIMES)
    assert js, "JS NATIVE_MANAGED_RUNTIMES parsed empty"
    assert js == py, (
        "NATIVE_MANAGED_RUNTIMES drift between bridge and service — a runtime in one but not "
        f"the other silently no-ops.\n  bridge (dispatch-execution.js): {sorted(js)}\n"
        f"  service (api_core/runtime.py _NATIVE_MANAGED_RUNTIMES): {sorted(py)}"
    )


def test_db_py_keeps_its_own_copy_in_agreement():
    """`service/db.py` declares the same runtimes as a TUPLE. Nothing compared it to anything before."""
    from service.db import _NATIVE_MANAGED_RUNTIMES as db_copy
    assert set(db_copy) == set(_NATIVE_MANAGED_RUNTIMES), (
        "service/db.py's _NATIVE_MANAGED_RUNTIMES has drifted from service/api_core/runtime.py's.\n"
        f"  db.py:               {sorted(db_copy)}\n"
        f"  api_core/runtime.py: {sorted(_NATIVE_MANAGED_RUNTIMES)}"
    )
