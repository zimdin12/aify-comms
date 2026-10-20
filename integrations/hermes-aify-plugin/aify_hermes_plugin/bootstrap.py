from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from types import ModuleType
from typing import Callable

from .gateway_patch import patch_gateway_server
from .patches import (
    patch_codex_runtime,
    patch_hermes_cli_web_server,
    patch_hermes_cli_main,
)


PatchFunc = Callable[[ModuleType], None]

_PATCHES: dict[str, PatchFunc] = {
    "agent.codex_runtime": patch_codex_runtime,
    "hermes_cli.main": patch_hermes_cli_main,
    "hermes_cli.web_server": patch_hermes_cli_web_server,
    "tui_gateway.server": patch_gateway_server,
}

_INSTALLED = False


def _debug_enabled() -> bool:
    return os.environ.get("AIFY_HERMES_PLUGIN_DEBUG", "").strip() == "1"


def _log(message: str) -> None:
    if not _debug_enabled():
        return
    print(f"[aify-hermes-plugin] {message}", file=sys.stderr, flush=True)


def _apply(fullname: str, module: ModuleType) -> None:
    patch = _PATCHES.get(fullname)
    if patch is None:
        return
    try:
        patch(module)
        _log(f"patched {fullname}")
    except Exception as exc:
        _log(f"failed to patch {fullname}: {exc}")
        if os.environ.get("AIFY_HERMES_PLUGIN_STRICT", "").strip() == "1":
            raise


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, fullname: str, loader: importlib.abc.Loader):
        self.fullname = fullname
        self.loader = loader

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        create_module = getattr(self.loader, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self.loader.exec_module(module)  # type: ignore[attr-defined]
        _apply(self.fullname, module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if fullname not in _PATCHES:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _PatchLoader(fullname, spec.loader)
        return spec


# aify-wrapper's hermes plugins, by the variable hermes-aify names each in, and the module name each loads as:
# the Herdr state hooks (only when its pane is claimed or aify-env assigned one), and the turn-start context
# (only for an agent, aify-wrapper's hermes-turn-start.py).
_WRAPPER_PLUGINS: dict[str, str] = {
    "AIFY_HERDR_HERMES_PLUGIN": "aify_herdr_state",
    "AIFY_TURN_START_HERMES_PLUGIN": "aify_turn_start",
}
_LOADED: dict[str, ModuleType] = {}


def _register_wrapper_plugin(ctx, variable: str) -> None:  # noqa: ANN001 - hermes PluginContext
    """Hand the PluginContext to the aify-wrapper plugin hermes-aify names in `variable`, if it names one.
    Not a patch, so it is registered on EVERY context: hermes keeps hooks per plugin manager, runs one
    manager per profile home, and a forced re-discovery clears them, while _INSTALLED is per process.
    """
    path = os.environ.get(variable, "").strip()
    if ctx is None or not path:
        return
    try:
        if variable not in _LOADED:
            spec = importlib.util.spec_from_file_location(_WRAPPER_PLUGINS[variable], path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _LOADED[variable] = module
        _LOADED[variable].register(ctx)
        _log(f"registered {variable} hooks from {path}")
    except Exception as exc:  # each is an addition; hermes startup is not
        _log(f"{variable} hooks not registered: {exc}")


def install(ctx=None) -> None:  # noqa: ANN001 - hermes PluginContext
    global _INSTALLED
    for variable in _WRAPPER_PLUGINS:
        _register_wrapper_plugin(ctx, variable)
    if _INSTALLED:
        return
    _INSTALLED = True

    sys.meta_path.insert(0, _PatchFinder())
    for fullname in list(_PATCHES):
        module = sys.modules.get(fullname)
        if isinstance(module, ModuleType):
            _apply(fullname, module)
    _log("installed import hook")
