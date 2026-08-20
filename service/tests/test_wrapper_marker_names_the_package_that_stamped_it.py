"""The version baked into a launcher is the aify-wrapper package's, not aify-comms'.

Two installers render the SAME templates. aify-wrapper's bakes its own VERSION; aify-comms' baked the
repo-root VERSION instead. Both files read 0.5.7 on 2026-08-20, so one field carried two different
meanings and nothing could tell -- and `aify-comms doctor`'s `wrapper-current` compares that field
against the package, so the first independent release on either side turns every clean install STALE.

Separate release lines are the entire point of the three-repo split. This is not a risk, it is a date.
"""

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
WRAPPER_PACKAGE = REPO / "mcp" / "stdio" / "node_modules" / "aify-wrapper"
MARKER = 'HARNESS_WRAPPER_VERSION="'
RENDER_URL = "http://127.0.0.2:1"


def _read_version(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _rendered() -> str:
    """Render claude-aify. `--emit-wrappers` exits before npm, MCP registration or any env mutation,
    so this cannot touch a machine with a live fleet."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    if not (WRAPPER_PACKAGE / "VERSION").exists():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")
    with tempfile.TemporaryDirectory(prefix="aify-marker-") as tmp:
        subprocess.run(
            [bash, str(INSTALL_SH), "--client", "claude", RENDER_URL, "--emit-wrappers", tmp],
            check=True,
            capture_output=True,
        )
        return (Path(tmp) / "claude-aify").read_text(encoding="utf-8")


def _marker_value(text: str) -> str:
    for line in text.splitlines():
        if line.startswith(MARKER):
            return line[len(MARKER):].rstrip('"')
    raise AssertionError("the rendered launcher carries no HARNESS_WRAPPER_VERSION marker")


def test_the_marker_is_present_and_is_not_still_a_placeholder():
    """Anchors the two assertions below: an unrendered placeholder would satisfy neither honestly."""
    value = _marker_value(_rendered())
    assert value, "the marker rendered empty"
    assert not value.startswith("@@"), f"the placeholder survived the render: {value}"


def test_the_marker_carries_the_wrapper_package_version():
    assert _marker_value(_rendered()) == _read_version(WRAPPER_PACKAGE / "VERSION")


def test_the_marker_follows_the_package_and_not_aify_comms(tmp_path):
    """The discriminating case, and the only one that can fail today.

    While both VERSION files read the same string, equality proves nothing about which file was read.
    So this renders against a COPY of the package carrying a sentinel version and asserts the marker
    followed it. A copy, not the real package: mutating the file every install reads and putting it
    back is a race waiting for the day two things render at once.
    """
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    if not (WRAPPER_PACKAGE / "wrappers").is_dir():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")

    sentinel = "9.9.9-sentinel"
    package = tmp_path / "aify-wrapper"
    shutil.copytree(WRAPPER_PACKAGE / "wrappers", package / "wrappers")
    (package / "VERSION").write_text(sentinel + "\n", encoding="utf-8")

    out = tmp_path / "out"
    env = {**os.environ, "AIFY_WRAPPER_TEMPLATE_DIR": str(package / "wrappers").replace("\\", "/")}
    subprocess.run(
        [bash, str(INSTALL_SH), "--client", "claude", RENDER_URL, "--emit-wrappers", str(out)],
        check=True,
        capture_output=True,
        env=env,
    )
    rendered = _marker_value((out / "claude-aify").read_text(encoding="utf-8"))
    assert rendered == sentinel, "the marker must come from the package that supplied the template"
    assert rendered != _read_version(REPO / "VERSION"), "and never from aify-comms' release version"


def test_a_package_with_no_version_reports_unknown_rather_than_borrowing(tmp_path):
    """Fail closed. Borrowing aify-comms' number here is precisely the defect, so absence has to stay
    absent -- doctor reads 'unknown' as stale, which is the truth."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    if not (WRAPPER_PACKAGE / "wrappers").is_dir():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")

    package = tmp_path / "no-version"
    shutil.copytree(WRAPPER_PACKAGE / "wrappers", package / "wrappers")  # deliberately no VERSION

    out = tmp_path / "out"
    env = {**os.environ, "AIFY_WRAPPER_TEMPLATE_DIR": str(package / "wrappers").replace("\\", "/")}
    subprocess.run(
        [bash, str(INSTALL_SH), "--client", "claude", RENDER_URL, "--emit-wrappers", str(out)],
        check=True,
        capture_output=True,
        env=env,
    )
    assert _marker_value((out / "claude-aify").read_text(encoding="utf-8")) == "unknown"
