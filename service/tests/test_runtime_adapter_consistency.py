"""Cross-language consistency: every per-runtime capability value must match
between the JS adapter (mcp/stdio/adapters/*.js) and the Python adapter
(service/runtimes/*.py). Catches drift before it ships.

Runs `node mcp/stdio/scripts/dump-capabilities.mjs` and compares to the
Python adapter values. Skips cleanly if Node isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _js_to_py_key(js_key: str) -> str:
    """Convert JS camelCase to Python snake_case."""
    out = []
    for ch in js_key:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def test_js_to_py_key_conversion():
    """Smoke test the key converter so the main test failures are clear."""
    assert _js_to_py_key("supportsResident") == "supports_resident"
    assert _js_to_py_key("preferredDeliveryMode") == "preferred_delivery_mode"
    assert _js_to_py_key("supportsMultiClient") == "supports_multi_client"


def test_js_and_python_adapters_agree_on_capabilities():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH — cross-language consistency check skipped")

    script = ROOT / "mcp" / "stdio" / "scripts" / "dump-capabilities.mjs"
    proc = subprocess.run(
        [node, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    js_caps = json.loads(proc.stdout)

    from service.runtimes import adapter_for, supported_runtimes

    assert sorted(js_caps.keys()) == sorted(supported_runtimes()), (
        f"JS and Python disagree on which runtimes are supported. "
        f"JS: {sorted(js_caps.keys())}, Py: {sorted(supported_runtimes())}"
    )

    drifts: list[str] = []
    for name in supported_runtimes():
        py = adapter_for(name)
        for js_key, js_value in js_caps[name].items():
            py_key = _js_to_py_key(js_key)
            py_value = getattr(py, py_key)
            if py_value != js_value:
                drifts.append(
                    f"{name}.{js_key} (py: {py_key}): JS={js_value!r}, Py={py_value!r}"
                )

    assert not drifts, "Capability drift between JS and Python adapters:\n  - " + "\n  - ".join(drifts)
