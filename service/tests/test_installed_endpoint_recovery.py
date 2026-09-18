"""Recovering the endpoint an operator already chose, out of an installed launcher.

Two update paths depend on this and both had their own copy of the same regex:

  * `redeploy.sh` -- the documented one-command update -- re-renders every wrapper, and picks the URL
    by reading one that is already installed.
  * `install.sh`'s interactive prompt, which offers that URL as the pre-filled default.

Both greped `AIFY_SERVER_URL:-http://`, the shape wrappers had before the v0.6 harness contract. A
current launcher carries `HARNESS_ENDPOINT="${HARNESS_ENDPOINT-${AIFY_COMMS_URL:-<url>}}"` instead, so
the grep matched nothing, and nothing announced that: redeploy fell through to its loopback default and
would have rewritten every wrapper on the host to point at 127.0.0.1. On a fleet reaching a LAN address
that is the whole fleet, silently, during an update whose entire promise is that it changes nothing but
the code.

One reader now, so the two paths cannot drift apart again.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
READER = REPO / "scripts" / "installed-endpoint.sh"

LEGACY = (
    '#!/bin/bash\n'
    'export AIFY_SERVER_URL="${AIFY_SERVER_URL:-http://192.168.1.9:8800}"\n'
)


def _bash():
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def _ask(directory: Path) -> str:
    """What the reader says is installed in `directory`."""
    result = subprocess.run(
        [_bash(), str(READER), str(directory).replace("\\", "/")],
        capture_output=True, text=True,
    )
    # Exit 1 is the documented "nothing to recover", not a malfunction. Anything else is.
    assert result.returncode in (0, 1), f"reader failed: {result.returncode} {result.stderr}"
    if result.returncode == 1:
        assert result.stdout == "", "an empty answer must not also print something"
    return result.stdout.strip()


def test_still_reads_a_pre_contract_launcher():
    """An operator updating from an older install has exactly these on disk. Losing them would move
    the failure rather than fix it."""
    with tempfile.TemporaryDirectory(prefix="aify-endpoint-") as tmp:
        directory = Path(tmp)
        (directory / "claude-aify").write_text(LEGACY, encoding="utf-8")
        assert _ask(directory) == "http://192.168.1.9:8800"


def test_neither_update_path_carries_its_own_copy_of_the_regex():
    """The two paths drifted because each had its own. A grep for the pre-contract pattern in either
    script means somebody added a second reader back."""
    for script in ("redeploy.sh", "install.sh"):
        text = (REPO / script).read_text(encoding="utf-8")
        assert "AIFY_SERVER_URL:-http://" not in text, (
            f"{script} greps for the pre-contract wrapper shape again; use scripts/installed-endpoint.sh"
        )
