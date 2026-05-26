"""Plan 5 Section A — install.sh hermes web_dist prebuild.

Operator's hermes-aify dashboard probe fails silently when hermes_cli/web_dist
is absent (`--skip-build` errors). This caused AIFY_HERMES_GATEWAY_URL to
never get exported and every resident hermes wake mode to be
'hermes-missing-handle' (observed 2026-05-25 — see
~/.local/state/aify-comms/hermes-aify-dashboard-*.log).

The fix: install.sh, on `--client hermes`, detects a missing web_dist under
the hermes install root and runs `npm install + npm run build` once. The
operator-visible log line is `prebuilding hermes web_dist`.

These tests exercise the prebuild-dry-run code path so we don't actually
invoke npm in CI — the dry-run flag short-circuits before npm, but still
emits the same log strings so callers can verify intent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"


def _run_install_sh(env_extra: dict, *args: str) -> subprocess.CompletedProcess:
    """Run install.sh with the given extra env. Returns the completed process.

    `bash` is required. The dry-run flag prevents npm + actual wrapper writes
    from firing; we only need to assert the prebuild branch's stderr output.
    """
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH — install.sh prebuild smoke skipped")
    import os

    env = {**os.environ, **env_extra}
    return subprocess.run(
        [bash, str(INSTALL_SH), "--client", "hermes", "--prebuild-dry-run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_install_hermes_prebuilds_web_dist_if_missing(tmp_path):
    """When web_dist is missing but web/ source exists, prebuild branch fires."""
    fake_root = tmp_path / "hermes-agent"
    (fake_root / "hermes_cli").mkdir(parents=True)
    (fake_root / "web").mkdir(parents=True)
    (fake_root / "web" / "package.json").write_text(
        '{"name":"hermes-web","scripts":{"build":"true"}}'
    )

    result = _run_install_sh({"AIFY_HERMES_INSTALL_ROOT": str(fake_root)})

    combined = (result.stdout or "") + (result.stderr or "")
    assert "prebuilding hermes web_dist" in combined.lower(), (
        f"Expected prebuild log line, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_install_hermes_skips_prebuild_when_web_dist_present(tmp_path):
    """When web_dist/index.html already exists, prebuild branch is a no-op."""
    fake_root = tmp_path / "hermes-agent"
    web_dist = fake_root / "hermes_cli" / "web_dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text("<!doctype html><title>x</title>")
    (fake_root / "web").mkdir(parents=True)

    result = _run_install_sh({"AIFY_HERMES_INSTALL_ROOT": str(fake_root)})

    combined = (result.stdout or "") + (result.stderr or "")
    assert "prebuilding hermes web_dist" not in combined.lower(), (
        f"Prebuild should not run when web_dist exists; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "web_dist already present" in combined.lower(), (
        f"Expected idempotency log line; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_install_hermes_skips_prebuild_when_web_source_missing(tmp_path):
    """When the install root has no web/ source dir, log + return cleanly."""
    fake_root = tmp_path / "hermes-agent"
    (fake_root / "hermes_cli").mkdir(parents=True)
    # No web/ subdir — we cannot prebuild anything.

    result = _run_install_sh({"AIFY_HERMES_INSTALL_ROOT": str(fake_root)})

    combined = (result.stdout or "") + (result.stderr or "")
    assert "hermes web source not found" in combined.lower(), (
        f"Expected 'web source not found' log; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_install_hermes_skips_prebuild_when_install_root_unset_and_undetectable(
    tmp_path, monkeypatch
):
    """No env, no detectable hermes on PATH — install.sh logs and continues."""
    # Point AIFY_HERMES_INSTALL_ROOT at a path that doesn't exist so the
    # auto-detect branch is exercised. The detect call may or may not find
    # hermes on this CI host; either way the prebuild should not crash.
    result = _run_install_sh(
        {"AIFY_HERMES_INSTALL_ROOT": str(tmp_path / "does-not-exist")}
    )

    combined = (result.stdout or "") + (result.stderr or "")
    # Either we hit the "install root not found" branch OR (less likely on
    # a CI host) a real hermes is detected and we hit one of the other
    # branches. The contract: install.sh exits cleanly either way.
    assert result.returncode == 0, (
        f"install.sh --prebuild-dry-run should exit 0 even when nothing to do; "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_install_hermes_detects_install_root_from_aify_hermes_command(tmp_path):
    """AIFY_HERMES_COMMAND is the supported path when hermes is not on PATH."""
    fake_root = tmp_path / "hermes-agent"
    config_file = fake_root / "hermes_cli" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("mcp_servers: {}\n")
    (fake_root / "web").mkdir(parents=True)
    fake_cmd = tmp_path / "hermes"
    fake_cmd.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = config ] && [ \"${2:-}\" = path ]; then\n"
        f"  printf '%s\\n' {config_file}\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n"
    )
    fake_cmd.chmod(0o755)

    result = _run_install_sh({"AIFY_HERMES_COMMAND": str(fake_cmd)})

    combined = (result.stdout or "") + (result.stderr or "")
    assert "prebuilding hermes web_dist" in combined.lower(), (
        "Expected install.sh to call AIFY_HERMES_COMMAND config path and "
        f"discover fake install root; got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
