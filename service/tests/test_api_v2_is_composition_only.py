"""`service/routers/api_v2.py` composes routers and does nothing else — including no re-exports.

This file was 20,545 lines at its peak and by the end of the domain extraction declared ZERO routes:
a helper library living at a router's address. v0.5.3 moved the helpers out (first to a
control-plane module that v0.7.0 deleted once it had emptied, then to their owners) and left only
the composition. CLAUDE.md records the deliberate part: there is NO compatibility
re-export, "so a stale `from service.routers.api_v2 import <helper>` fails loudly instead of
resolving."

NOTHING ENFORCED THAT. A single convenience re-export — one `from service.api_core.x import *`, or
a handful of names added "so the old imports keep working" — would silently restore every stale
import path the move existed to break, and it would look like a kindness while doing it. The failure
is not a wrong answer, it is the loss of an alarm: stale imports resolve again, and the next
relocation has no signal that anything was left behind.

The rule is asserted three ways because each catches a different way of breaking it: no declarations
here, no import of anything but routers, and — the one that actually states the contract — a helper that used
to live here must still raise ImportError.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
API_V2 = REPO / "service" / "routers" / "api_v2.py"


def _tree() -> ast.Module:
    return ast.parse(API_V2.read_text(encoding="utf-8"))


def test_it_imports_nothing_but_routers():
    """Importing a helper here is how a re-export starts: the name becomes an attribute of this
    module, and `from service.routers.api_v2 import <helper>` resolves again. So every import must
    bind a domain's `router`, or the router factory this module builds its own router with."""
    unexpected = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            unexpected.append(ast.unparse(node))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            if node.module == "service.api_core.routing" and [a.name for a in node.names] == ["domain_router"]:
                continue
            if (node.module or "").startswith("service.routers.") and [a.name for a in node.names] == ["router"]:
                continue
            unexpected.append(ast.unparse(node))
    assert unexpected == [], f"api_v2.py imports something other than a router: {unexpected}"


def test_every_top_level_statement_is_composition():
    """Docstring, imports, the router, and include_router calls. Nothing else — an assignment or a
    call that is not composition is the beginning of a second life for this module."""
    unexpected = []
    for node in _tree().body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the module docstring
        # `router = domain_router(...)` in either binding form. An annotated
        # `router: APIRouter = domain_router()` is the same composition and must not read as a
        # non-composition statement — the inverse of the `__all__` hole below, and the same
        # Assign-only blind spot.
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["router"]:
            continue
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "router":
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func).endswith("include_router")
        ):
            continue
        unexpected.append(ast.unparse(node).splitlines()[0][:80])
    assert unexpected == [], f"api_v2.py has non-composition statements: {unexpected}"


def test_a_relocated_helper_still_fails_loudly_when_imported_from_here():
    """THE CONTRACT ITSELF. These three lived in this file and now live in api_core leaves. If any
    becomes importable from here again, every stale import in the tree silently starts resolving and
    the next relocation loses its only signal that something was left behind."""
    # The real `from X import Y` STATEMENT is what raises ImportError; `__import__` with a fromlist
    # returns the module and leaves a missing name as an AttributeError, which is a different alarm
    # and not the one a stale import would hit. Exercise the statement.
    for name in ("_format_dispatch_state", "_dispatch_buffer_full_hint", "_contract_state"):
        with pytest.raises(ImportError):
            exec(f"from service.routers.api_v2 import {name}", {})


def test_the_router_it_does_publish_is_importable():
    """Anti-vacuity: the test above must be failing on the HELPER, not because the module is broken
    or unimportable in this environment."""
    from service.routers.api_v2 import router

    assert router is not None
    assert hasattr(router, "include_router")


def test_the_scan_is_reading_the_real_file():
    """A path that stopped resolving would make every assertion above pass over an empty tree."""
    assert API_V2.is_file(), f"{API_V2} does not exist"
    body = _tree().body
    assert len(body) > 10, f"only {len(body)} top-level statements — is this the composition file?"
    includes = sum(
        1
        for node in body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func).endswith("include_router")
    )
    assert includes >= 10, f"only {includes} include_router calls found — the composition is not here"
