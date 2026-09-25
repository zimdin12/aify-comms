"""install.sh's plugin refresh deletes `service/`, `mcp/` and friends in the plugin dir, then copies
them from the checkout. A plugin dir that IS the checkout lost those directories before they were
copied from (v0.7 docs review). The function is extracted from install.sh and run on scratch trees.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]


def _function_source() -> str:
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    start = text.index("refresh_plugin_snapshot() {")
    return text[start:text.index("\n}\n", start) + 3]


def _checkout(root: Path) -> Path:
    for part in (".claude-plugin", "service", "mcp"):
        (root / part).mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "service" / "keep.py").write_text("x", encoding="utf-8")
    (root / "VERSION").write_text("0.7.0\n", encoding="utf-8")
    return root


def _refresh(script_dir: Path, dst: Path) -> subprocess.CompletedProcess:
    program = _function_source() + f'\nSCRIPT_DIR="{script_dir.as_posix()}"\nrefresh_plugin_snapshot "{dst.as_posix()}" test\n'
    return subprocess.run([bash(), "-c", program], capture_output=True, text=True, timeout=60)


def test_a_plugin_dir_that_is_the_checkout_is_left_alone(tmp_path):
    checkout = _checkout(tmp_path / "repo")
    done = _refresh(checkout, checkout)
    assert done.returncode == 0, done.stderr
    assert "is this checkout" in done.stdout, done.stdout
    assert (checkout / "service" / "keep.py").exists(), "the refresh deleted the checkout it copies from"


def test_control_a_separate_plugin_dir_is_refreshed(tmp_path):
    checkout = _checkout(tmp_path / "repo")
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    done = _refresh(checkout, plugin)
    assert done.returncode == 0, done.stderr
    assert (plugin / "service" / "keep.py").exists(), done.stdout + done.stderr
