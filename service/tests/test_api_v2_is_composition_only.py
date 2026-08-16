"""`service/routers/api_v2.py` composes routers and does nothing else — including no re-exports.

This file was 20,545 lines at its peak and by the end of the domain extraction declared ZERO routes:
a helper library living at a router's address. v0.5.3 moved the helpers to `service/control_plane.py`
and left only the composition. CLAUDE.md records the deliberate part: there is NO compatibility
re-export, "so a stale `from service.routers.api_v2 import <helper>` fails loudly instead of
resolving."

NOTHING ENFORCED THAT. A single convenience re-export — one `from service.control_plane import *`, or
a handful of names added "so the old imports keep working" — would silently restore every stale
import path the move existed to break, and it would look like a kindness while doing it. The failure
is not a wrong answer, it is the loss of an alarm: stale imports resolve again, and the next
relocation has no signal that anything was left behind.

The rule is asserted three ways because each catches a different way of breaking it: no declarations
here, no import of the carrier, and — the one that actually states the contract — a helper that used
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


def test_it_declares_no_functions_or_classes():
    """A helper here is a helper at the wrong address — that is the whole finding this file records."""
    declared = [
        node.name
        for node in _tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert declared == [], (
        f"api_v2.py declares {declared}. It is the composition surface; anything callable belongs in "
        "a domain router or an api_core leaf, not at a router's address."
    )


def test_it_does_not_import_the_control_plane():
    """Importing the carrier here is how a re-export starts: the names become attributes of this
    module, and `from service.routers.api_v2 import <helper>` resolves again."""
    tree = _tree()
    carrier = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "control_plane" in ast.unparse(node)
    ]
    assert carrier == [], f"api_v2.py imports the control plane: {carrier}"


def test_every_top_level_statement_is_composition():
    """Docstring, imports, the router, and include_router calls. Nothing else — an assignment or a
    call that is not composition is the beginning of a second life for this module."""
    unexpected = []
    for node in _tree().body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the module docstring
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["router"]:
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func).endswith("include_router")
        ):
            continue
        unexpected.append(ast.unparse(node).splitlines()[0][:80])
    assert unexpected == [], f"api_v2.py has non-composition statements: {unexpected}"


def test_it_exports_no_helper_under_any_name():
    """`__all__` would re-export by declaration rather than by import — the same restoration of the
    stale paths, spelled differently."""
    for node in _tree().body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            assert "__all__" not in names, "api_v2.py declares __all__ — it publishes nothing but `router`"


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
