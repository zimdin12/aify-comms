"""The two readers of a launcher's endpoint must return the same answer.

There are two, and the duplication is forced rather than sloppy:

  * `scripts/installed-endpoint.sh` (bash) -- used by `redeploy.sh` and by install.sh's prompt, which
    runs BEFORE [1/4] does `npm install`, so it cannot depend on a node module being present.
  * aify-wrapper's `mcp/stdio/node_modules/aify-wrapper/lib/installed-endpoint.mjs` (JS) -- used by that package's own installer to make
    a reinstall stop asking for an endpoint the host already has.

Both parse the same assignment line out of the same rendered launcher. A template change moves that
line and each reader goes quietly blind on its own schedule -- which is exactly how `redeploy.sh` came
to hold a regex matching the pre-contract shape while everything around it looked fine.

An agreement test rather than a shared implementation, because the sharing is what is impossible here.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
BASH_READER = REPO / "scripts" / "installed-endpoint.sh"
JS_READER = REPO / "mcp" / "stdio" / "node_modules" / "aify-wrapper" / "lib" / "installed-endpoint-cli.mjs"

URL = "http://10.20.30.40:8800"


def _posix(p) -> str:
    return str(p).replace("\\", "/")


def _tools():
    bash, node = shutil.which("bash"), shutil.which("node")
    if not bash or not node:
        pytest.skip("bash and node are both required to compare the two readers")
    if not JS_READER.exists():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")
    return bash, node


def _bash_says(directory: Path) -> str:
    bash, _ = _tools()
    r = subprocess.run([bash, str(BASH_READER), _posix(directory)], capture_output=True, text=True)
    assert r.returncode in (0, 1), f"bash reader failed: {r.returncode} {r.stderr}"
    return r.stdout.strip()


def _js_says(directory: Path) -> str:
    _, node = _tools()
    r = subprocess.run([node, _posix(JS_READER), _posix(directory)], capture_output=True, text=True)
    assert r.returncode == 0, f"js reader failed: {r.returncode} {r.stderr}"
    return r.stdout.strip()


def _render(directory: Path) -> None:
    bash, _ = _tools()
    subprocess.run(
        [bash, str(INSTALL_SH), "--client", "claude", URL, "--emit-wrappers", _posix(directory)],
        check=True, capture_output=True, env={**os.environ, "AIFY_NO_PROMPT": "1"},
    )


def test_both_readers_find_the_endpoint_in_a_launcher_install_sh_writes_today():
    with tempfile.TemporaryDirectory(prefix="aify-agree-") as tmp:
        out = Path(tmp)
        _render(out)
        assert (out / "claude-aify").exists(), "nothing rendered, so nothing was compared"
        assert _bash_says(out) == URL, "the bash reader lost the current shape"
        assert _js_says(out) == URL, "the js reader lost the current shape"


def test_both_readers_say_nothing_for_an_empty_directory():
    """Agreement on absence matters as much as agreement on a value: a reader that invents a default
    here is how a fleet gets repointed at loopback."""
    with tempfile.TemporaryDirectory(prefix="aify-agree2-") as tmp:
        empty = Path(tmp)
        assert _bash_says(empty) == ""
        assert _js_says(empty) == ""


def test_both_readers_refuse_an_unrendered_template():
    """Neither may hand back `@@ENDPOINT@@` as though it were an address."""
    template = REPO / "mcp" / "stdio" / "node_modules" / "aify-wrapper" / "wrappers" / "claude-aify.sh.in"
    if not template.exists():
        pytest.skip("aify-wrapper package not installed — run 'npm install' in mcp/stdio")
    with tempfile.TemporaryDirectory(prefix="aify-agree3-") as tmp:
        out = Path(tmp)
        shutil.copyfile(template, out / "claude-aify")
        assert _bash_says(out) == ""
        assert _js_says(out) == ""


# Every client install.sh can render. Derived from the installer's own emit flags rather than listed,
# so a fifth client cannot arrive with an unread wrapper shape.
def _emittable_clients() -> list[str]:
    text = INSTALL_SH.read_text(encoding="utf-8")
    flags = re.findall(r"--emit-([a-z]+)-wrappers", text)
    clients = sorted(set(flags))
    assert clients, "found no --emit-<client>-wrappers flags; this derivation is broken"
    return clients


@pytest.mark.parametrize("client", _emittable_clients())
def test_both_readers_find_the_endpoint_for_every_client_the_installer_renders(client, tmp_path):
    """The Claude-only version of this test is why a real blocker shipped.

    hermes' template nests the fallback TWO levels --
    `${HARNESS_ENDPOINT-${AIFY_SERVER_URL:-${AIFY_COMMS_URL:-<url>}}}` -- and a reader written against
    claude's one level returned nothing for it. redeploy.sh then falls back to loopback, so a
    hermes-only host would have had every wrapper repointed at 127.0.0.1 by the command that exists to
    preserve its endpoint. Found by comms-senior-dev in pre-deploy review; the tests were green.
    """
    bash, _ = _tools()
    out = tmp_path / client
    subprocess.run(
        [bash, str(INSTALL_SH), "--client", client, URL, "--emit-wrappers", _posix(out)],
        check=True, capture_output=True, env={**os.environ, "AIFY_NO_PROMPT": "1"},
    )
    rendered = sorted(p.name for p in out.iterdir())
    assert rendered, f"nothing rendered for {client}, so nothing was read"

    assert _bash_says(out) == URL, f"the bash reader lost {client}'s shape: {rendered}"
    assert _js_says(out) == URL, f"the js reader lost {client}'s shape: {rendered}"
