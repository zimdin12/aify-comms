"""The aify Hermes plugin package is copied beside the bridge, and the shim hermes loads imports it from there.

THE INCIDENT, 2026-10-23 to 10-26. Since the guided install chain, aify-wrapper renders hermes-aify with
AIFY_HERMES_PLUGIN_PATH=<install root>/integrations/hermes-aify-plugin, and nothing put the package there. Every
hermes gateway log began "[aify-comms-plugin] shim load failed: No module named 'aify_hermes_plugin'", so turn-start,
file activity and the herdr state plugin registered in no hermes process.

This drives `install_hermes_plugin` alone, extracted from install.sh, with the hermes-side helpers stubbed into
temp directories: it must never touch the operator's hermes config. Then a real Python loads the shim it wrote,
with no plugin path in the environment, the way the gateway process sees it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"


def _install_plugin(tmp: Path) -> tuple[Path, Path]:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    native_base = tmp / "native"
    hermes_home = tmp / "hermes-home"
    script = (
        "set -u\n"
        f"eval \"$(sed -n '/^install_hermes_plugin() {{/,/^}}/p' '{INSTALL_SH.as_posix()}')\"\n"
        f"hermes_config_root() {{ printf %s '{hermes_home.as_posix()}'; }}\n"
        "hermes_runtime_is_native_windows() { return 1; }\n"
        "hermes_cmd() { return 1; }\n"
        "_enable_hermes_plugin_in_config() { :; }\n"
        f"SCRIPT_DIR='{ROOT.as_posix()}'\n"
        # The drive form on Windows, so the native Python below can read the baked path as written.
        f"AIFY_NATIVE_BASE='{native_base.as_posix()}'\n"
        f"HOME='{tmp.as_posix()}'\n"
        "install_hermes_plugin\n"
    )
    result = subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return native_base / "integrations" / "hermes-aify-plugin", hermes_home / "plugins" / "aify-comms" / "__init__.py"


def _load_shim(shim: Path) -> subprocess.CompletedProcess:
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('aify_comms_shim', r'{shim}')\n"
        "shim = importlib.util.module_from_spec(spec); spec.loader.exec_module(shim)\n"
        "shim.register(None)\n"
        "module = sys.modules.get('aify_hermes_plugin')\n"
        "print('LOADED-FROM', getattr(module, '__file__', '') if module else '')\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "PYTHON"))}
    env["AIFY_HERMES_PLUGIN"] = "1"
    return subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=60, env=env)


def test_the_shim_imports_the_package_from_the_copy_beside_the_bridge(tmp_path: Path) -> None:
    copy, shim = _install_plugin(tmp_path)
    assert (copy / "aify_hermes_plugin" / "bootstrap.py").is_file(), "the package was not copied"
    loaded = _load_shim(shim)
    assert "shim load failed" not in loaded.stderr, loaded.stderr
    origin = loaded.stdout.split("LOADED-FROM", 1)[1].strip()
    assert Path(origin).resolve().is_relative_to(copy.resolve()), f"loaded from {origin}, not the copy"


def test_CONTROL_without_the_copy_the_shim_says_it_failed(tmp_path: Path) -> None:
    # The probe above can say no: with the copy gone, the same shim reports what every gateway log reported.
    copy, shim = _install_plugin(tmp_path)
    shutil.rmtree(copy)
    loaded = _load_shim(shim)
    assert "shim load failed: No module named 'aify_hermes_plugin'" in loaded.stderr, loaded.stderr
