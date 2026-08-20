"""redeploy.sh must rebuild every wrapper against the endpoint already installed, never a default.

`redeploy.sh` is the documented one-command update, and it re-renders EVERY launcher on the host. The
URL it picks is therefore fleet-wide: pick wrong and every agent is repointed in one command, silently,
during an update whose whole promise is that only the code changes.

Its reader was fixed earlier -- it held a private copy of a regex matching the PRE-CONTRACT wrapper
shape, so it had quietly stopped finding anything and fell through to loopback. But fixing the reader
did not assert the WIRING: `${AIFY_DEFAULT_SERVER_URL:-$(detect || echo loopback)}` is where a
recovered value gets thrown away, and nothing executed that expression.

Checking it on this host proves nothing, because the installed launcher carries loopback and so does
the fallback -- one input read twice. So the resolution block is extracted from redeploy.sh and run
against a HOME that carries a launcher with a distinctive address.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REDEPLOY = REPO / "redeploy.sh"
INSTALL_SH = REPO / "install.sh"
DISTINCT = "http://10.99.99.99:8800"
LOOPBACK = "http://127.0.0.1:8800"


def _bash() -> str:
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _posix(p) -> str:
    return str(p).replace("\\", "/")


def resolution_block() -> str:
    """redeploy.sh's own resolution logic, lifted verbatim.

    Lifted rather than restated: a copy here would pass while redeploy.sh did something else, which is
    the failure this whole file is about.
    """
    text = REDEPLOY.read_text(encoding="utf-8")
    start = text.index("detect_installed_server_url() {")
    end = text.index("SERVER_URL=", start)
    block = text[start:text.index("\n", end) + 1]
    assert "installed-endpoint.sh" in block, (
        "redeploy.sh no longer resolves through the shared reader; this extraction is stale"
    )
    return block


def _resolve_with_home(home: Path) -> str:
    script = home / "probe.sh"
    script.write_text(
        f'REPO_ROOT="{_posix(REPO)}"\n' + resolution_block() + 'printf "%s" "$SERVER_URL"\n',
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "AIFY_DEFAULT_SERVER_URL"}
    env["HOME"] = _posix(home)
    return subprocess.run(
        [_bash(), _posix(script)], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def _install_launcher(home: Path, endpoint: str) -> None:
    """A launcher in the shape install.sh writes today, at the path redeploy.sh looks in."""
    dest = home / ".local" / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_bash(), str(INSTALL_SH), "--client", "claude", endpoint, "--emit-wrappers", _posix(dest)],
        check=True, capture_output=True,
    )
    assert (dest / "claude-aify").exists(), "nothing rendered, so nothing was resolved against"


def test_it_rebuilds_against_the_endpoint_the_installed_launcher_carries():
    with tempfile.TemporaryDirectory(prefix="aify-redeploy-") as tmp:
        home = Path(tmp)
        _install_launcher(home, DISTINCT)
        assert _resolve_with_home(home) == DISTINCT


def test_a_host_with_no_launcher_falls_back_rather_than_inventing():
    """The fallback is correct when there is nothing to recover; it is only wrong when it overrides
    something real."""
    with tempfile.TemporaryDirectory(prefix="aify-redeploy2-") as tmp:
        home = Path(tmp)
        (home / ".local" / "bin").mkdir(parents=True)
        assert _resolve_with_home(home) == LOOPBACK


def test_the_extraction_really_came_from_redeploy():
    """Anti-vacuity: if the block stopped naming the reader, every result above would be about a
    snippet this test wrote for itself."""
    block = resolution_block()
    assert "detect_installed_server_url" in block
    assert re.search(r"AIFY_DEFAULT_SERVER_URL:-", block), "the override arm is missing from the block"
    assert LOOPBACK in block, "the fallback this test asserts on must be redeploy's own"
