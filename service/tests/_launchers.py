r"""Render install.sh's launchers ONCE per (client, flags), for the whole pytest session.

WHY. Twelve test files each render launchers, each with a private copy of the same two helpers, and
each paying a full `bash install.sh --emit-wrappers` run per test. Measured 2026-08-29 on this host:
one render is 8.5--10s, and the slowest 25 tests in a 609s python suite were almost all renders --
`test_no_client_renders_a_literal_placeholder` alone was 42.5s for four of them.

The render is a FIXTURE, not the subject. What each of those tests asserts is a different property of
one artifact, so rendering it 60 times proves the same thing 59 extra times. `--emit-wrappers` is
deterministic given the same arguments: it writes the launchers and EXITS before npm, MCP
registration, hook install or any environment mutation.

THE CACHE RETURNS TEXT, NEVER A DIRECTORY. That is deliberate and it is the whole safety argument.
The bridge suite made the other choice first -- it shared the rendered DIRECTORY -- and
`claude-wrapper-contract.test.js` went red on the first run: `runWrapper` kept its stub runtime and
its sealed HOME beside the wrapper, so a case asserting a launcher REFUSES to start found a stub an
earlier case had installed, and started. A case that passes because an earlier case prepared state is
the exact risk of consolidating an expensive fixture, and handing back immutable strings removes it by
construction. A test that needs a directory to inspect, seed or delete renders its own.

Keyed by the FULL argument list, so `--mcp-transport sse` and `--delegate-spawns` are different
artifacts and never answer for each other.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

#: Reachable by nothing. A render pointed at the operator's real 8800 would bake a live endpoint into
#: launchers a test then runs, and this suite runs on a box with a working fleet.
NOWHERE_URL = "http://127.0.0.2:1"

_CACHE: dict[tuple[str, ...], Mapping[str, str]] = {}


def bash() -> str:
    """The RESOLVED bash, never the bare name.

    On Windows the bare word finds System32's WSL bash first, which cannot open a C: path at all and
    exits 127 on every form. aify-env shipped this same shadowing bug and could not launch anything.
    """
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not on PATH")
    return found


def render(client: str, *extra: str, url: str = NOWHERE_URL) -> Mapping[str, str]:
    """Every file this client renders, by name, as immutable text.

    Not just `{client}-aify`: a client renders its own launcher, the strict variant where there is
    one, and the `aify-comms` environment-bridge launcher that carries the delegation setting. Reading
    one of them is how a regression in exactly that setting reached a live host.
    """
    key = (client, url, *extra)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with tempfile.TemporaryDirectory(prefix="aify-render-") as out:
        result = subprocess.run(
            [bash(), INSTALL_SH.as_posix(), "--client", client, url, *extra,
             "--emit-wrappers", Path(out).as_posix()],
            capture_output=True, text=True, cwd=REPO, timeout=600,
        )
        files = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(Path(out).iterdir()) if path.is_file()
        }
    assert f"{client}-aify" in files, (
        f"{client}: nothing rendered (exit {result.returncode})\n{result.stdout}{result.stderr}"
    )
    frozen = MappingProxyType(files)
    _CACHE[key] = frozen
    return frozen


def launcher(client: str, *extra: str, name: str | None = None, url: str = NOWHERE_URL) -> str:
    """One rendered launcher's text. `name` defaults to the client's own launcher."""
    return render(client, *extra, url=url)[name or f"{client}-aify"]


def emittable_clients() -> list[str]:
    """The clients `--emit-wrappers` can render, read out of install.sh's own template mapping.

    DERIVED, never listed. A hand-kept list left hermes and pi ungoverned by the placeholder scan for
    as long as it existed, and an unguarded population reports green exactly like a guarded one.
    """
    names = sorted(set(
        line.split("-aify.sh.in")[0].rsplit('"', 1)[-1].rsplit("/", 1)[-1]
        for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
        if "-aify.sh.in" in line
    ))
    names = [n for n in names if n.isalpha()]
    assert names, "no wrapper templates named in install.sh; a caller would render nothing"
    return names
