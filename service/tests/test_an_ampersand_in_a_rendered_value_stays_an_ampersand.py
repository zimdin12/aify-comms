"""An `&` in a value the installer renders into a launcher stays an `&` (2026-10-14 audit, X6).

bash 5.2 turned on `patsub_replacement`, which makes `&` in the replacement half of `${var//pat/rep}`
expand to the matched text. aify-wrapper's render.sh turns it off; this repo's own renderer did not, so
an endpoint carrying a query string rendered as `?a=1@@ENDPOINT@@b=2`. Reproduced on bash 5.3.9. Driven
through the real installer's `--emit-wrappers`, so it tests the renderer the installer actually runs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
PACKAGE = REPO / "mcp" / "stdio" / "node_modules" / "aify-wrapper"


def _render(tmp_path: Path, url: str) -> str:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    if not (PACKAGE / "render.sh").is_file():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")
    out = tmp_path / "out"
    done = subprocess.run([bash, str(INSTALL_SH), "--client", "claude", url, "--emit-wrappers", str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return (out / "claude-aify").read_text(encoding="utf-8")


def test_an_ampersand_in_the_endpoint_renders_literally(tmp_path):
    url = "http://127.0.0.2:1/p?a=1&b=2"
    text = _render(tmp_path, url)
    assert url in text, "the & was replaced by the matched placeholder"
    assert "@@" not in text, "a placeholder reached the launcher"


def test_a_plain_endpoint_renders_as_given(tmp_path):
    """Control: the same path with no & in the value."""
    assert "http://127.0.0.2:1/plain" in _render(tmp_path, "http://127.0.0.2:1/plain")


def test_a_refused_render_fails_the_install_with_its_status(tmp_path):
    """render.sh refuses a placeholder nobody supplied (78); the install must stop with that status and
    leave no launcher, through a template override, which the adapter stages beside the renderer."""
    bash = shutil.which("bash")
    if not bash or not (PACKAGE / "render.sh").is_file():
        pytest.skip("bash or the aify-wrapper package is missing")
    package = tmp_path / "override"
    shutil.copytree(PACKAGE / "wrappers", package / "wrappers")
    template = package / "wrappers" / "claude-aify.sh.in"
    template.write_text(template.read_text(encoding="utf-8") + "\n# @@NOBODY_SUPPLIES_THIS@@\n", encoding="utf-8")
    out = tmp_path / "out"
    env = {**__import__("os").environ, "AIFY_WRAPPER_TEMPLATE_DIR": str(package / "wrappers").replace("\\", "/")}
    done = subprocess.run([bash, str(INSTALL_SH), "--client", "claude", "http://127.0.0.2:1", "--emit-wrappers", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 78, (done.returncode, done.stderr[-1500:])
    assert "NOBODY_SUPPLIES_THIS" in done.stderr
    assert not (out / "claude-aify").exists(), "a refused render left a launcher behind"
