"""A launcher installed by aify-comms must carry the registry fingerprint, not the word "unknown".

The Phase 6 gate says a wrapper built against a stale registry reports itself stale rather than
silently launching against one service. `aify-wrapper-check` is the consumer that answers that, by
comparing the fingerprint baked into a launcher against the registry as it is now.

aify-comms baked the literal string "unknown". Its own installer is the PRIMARY path -- the one every
install guide tells an operator to run -- so every launcher on every host reported `??` and none could
ever report `current`. The check refused to call them fine, which is the right failure direction, and
it was uninformative for the only path that matters.

It baked "unknown" because aify-comms read no registry. It writes one now: v0.6 Task 6a registers this
service into ~/.aify/services.json, and the aify-wrapper package it depends on ships the fingerprint
tool. Both halves were present; nothing joined them.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
REGISTRY_CLI = REPO / "mcp" / "stdio" / "node_modules" / "aify-wrapper" / "lib" / "registry-cli.mjs"
MARKER = 'HARNESS_REGISTRY_FINGERPRINT="'
RENDER_URL = "http://127.0.0.2:1"

REGISTRY = {
    "version": 1,
    "services": {
        "aify-comms": {"endpoint": "http://127.0.0.2:1", "endpointEnv": ["AIFY_SERVER_URL"]},
    },
}


def _bash():
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _posix(p) -> str:
    return str(p).replace("\\", "/")


def _fingerprint_of(registry_file: Path) -> str:
    if not REGISTRY_CLI.exists():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    out = subprocess.run(
        [node, _posix(REGISTRY_CLI), "fingerprint", _posix(registry_file)],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _render_with_registry(tmp: Path, registry_file: Path) -> str:
    out = tmp / "out"
    env = {**os.environ, "AIFY_SERVICE_REGISTRY": _posix(registry_file)}
    subprocess.run(
        [_bash(), str(INSTALL_SH), "--client", "claude", RENDER_URL, "--emit-wrappers", _posix(out)],
        check=True, capture_output=True, env=env,
    )
    text = (out / "claude-aify").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(MARKER):
            return line[len(MARKER):].rstrip('"')
    raise AssertionError("the rendered launcher carries no fingerprint marker at all")


def test_a_rendered_launcher_carries_the_fingerprint_of_the_registry_it_was_built_against():
    with tempfile.TemporaryDirectory(prefix="aify-fp-") as tmp:
        tmp = Path(tmp)
        registry = tmp / "services.json"
        registry.write_text(json.dumps(REGISTRY), encoding="utf-8")

        baked = _render_with_registry(tmp, registry)
        assert baked != "unknown", "the primary install path must not bake a placeholder"
        assert baked == _fingerprint_of(registry)


def test_a_different_registry_produces_a_different_fingerprint():
    """The discriminating case. A constant would satisfy the test above just as well, and a
    fingerprint that never changes cannot report staleness."""
    with tempfile.TemporaryDirectory(prefix="aify-fp2-") as tmp:
        tmp = Path(tmp)
        one = tmp / "one.json"
        one.write_text(json.dumps(REGISTRY), encoding="utf-8")

        other = dict(REGISTRY)
        other["services"] = dict(REGISTRY["services"])
        other["services"]["something-else"] = {"endpoint": "http://127.0.0.2:2", "endpointEnv": []}
        two = tmp / "two.json"
        two.write_text(json.dumps(other), encoding="utf-8")

        assert _render_with_registry(tmp, one) != _render_with_registry(tmp, two)


def test_an_absent_registry_still_renders_and_says_so():
    """A host with no registry yet is a legitimate state, not a failure: the empty-registry
    fingerprint is a real answer, and the install must not stop for it."""
    with tempfile.TemporaryDirectory(prefix="aify-fp3-") as tmp:
        tmp = Path(tmp)
        missing = tmp / "nothing-here.json"
        baked = _render_with_registry(tmp, missing)
        assert baked == _fingerprint_of(missing), "absent must fingerprint as the empty registry"


def test_the_service_registers_before_any_launcher_is_written():
    """Execution ORDER, which is a real property of a shell script and not observable from a render.

    A launcher bakes the fingerprint of the registry as it stands when it renders. Registration used to
    run at the very end, after the wrappers, so every FIRST install produced a launcher that was stale
    the moment it existed -- by exactly the entry the installer had just added. A reinstall corrected
    it, which is the worst shape for a staleness check: right on the second try, and quietly wrong on
    the first.
    """
    lines = INSTALL_SH.read_text(encoding="utf-8").splitlines()

    def line_of(needle: str) -> int:
        hits = [i for i, l in enumerate(lines) if needle in l and not l.lstrip().startswith("#")]
        assert hits, f"{needle} no longer appears as code in install.sh"
        return hits[0]

    registers = line_of("register-service-cli.mjs")
    first_wrapper = min(
        line_of("  install_claude_wrapper"),
        line_of("  install_codex_wrapper"),
        line_of("  install_hermes_wrapper"),
    )
    assert registers < first_wrapper, (
        f"registration runs at line {registers + 1}, after the first wrapper install at "
        f"{first_wrapper + 1}; the launcher would be fingerprinted against a registry missing this service"
    )
