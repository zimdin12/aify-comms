"""The container's MCP transport must actually mount.

`mcp/sse_server.py` ships, `service/main.py` mounts it, and for some unmeasured stretch it did not
load: `service/requirements.txt` asked for `mcp[cli]>=1.3.0`, pip took 2.0.0, and the 2.x package has
no `mcp.server.fastmcp` at all. The mount is wrapped in try/except and logs at INFO —
"MCP SSE server not available" — which reads exactly like "not configured". A whole transport
disappeared and nothing said so.

Nothing tested it either. The three files that mention sse_server read it as TEXT: the size gate, the
unreachable-statement scan, and the runtime-boundary check.

This matters beyond tidiness. Reaching MCP over HTTP is what lets a host stop carrying aify-comms'
own runtime — 92 MB in ~/.aify-comms today — and that is the split between the backend install and the
client install.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO / "service" / "requirements.txt"
SSE_SERVER = REPO / "mcp" / "sse_server.py"


def test_the_sse_server_module_is_still_shipped():
    """Anti-vacuity: if the file were gone, every assertion below would be about nothing."""
    assert SSE_SERVER.exists(), "mcp/sse_server.py is the transport; it must be in the image"
    assert "FastMCP" in SSE_SERVER.read_text(encoding="utf-8")


def test_the_mcp_dependency_is_bounded_above():
    """`>=1.3.0` let a major version through and deleted the API this file imports.

    A lower bound with no ceiling is not a pin: it is a promise to accept whatever upstream does next.
    """
    text = REQUIREMENTS.read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines() if l.strip().lower().startswith("mcp")]
    assert lines, "mcp is not declared in service/requirements.txt"
    for line in lines:
        assert "<" in line, (
            f"{line!r} has no upper bound. mcp 2.0.0 removed mcp.server.fastmcp, which sse_server.py "
            "imports, and the mount fails into an INFO log rather than an error."
        )


@pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="mcp not installed in this environment — the container is where this must hold",
)
def test_the_api_sse_server_imports_is_present_in_the_installed_mcp():
    """The check that would have caught it. Imports what the module imports, in the environment that
    has to run it."""
    assert importlib.util.find_spec("mcp.server.fastmcp") is not None, (
        "the installed mcp package has no mcp.server.fastmcp — sse_server.py cannot load, and "
        "/mcp/sse will 404 while startup logs only an INFO line"
    )
