"""M2 (2026-06-06): the bridge's NATIVE_MANAGED_RUNTIMES (mcp/stdio/dispatch-execution.js)
MUST stay in sync with the service's _NATIVE_MANAGED_RUNTIMES (service/routers/api_v2.py).

If they drift, a runtime added on one side but not the other silently no-ops: the dispatch
loop skips it (queued forever) or the service routes a managed run nobody claims. This test
extracts the JS set textually and asserts it equals the Python set, so the drift fails CI
instead of in production.
"""

import re
from pathlib import Path

from service.control_plane import _NATIVE_MANAGED_RUNTIMES

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
        f"  service (api_v2.py _NATIVE_MANAGED_RUNTIMES): {sorted(py)}"
    )
