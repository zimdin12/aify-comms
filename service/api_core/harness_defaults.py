"""The model and effort an agent of each runtime gets when it was given none (P0 C12): one owner.

A runtime HAS defaults when the settings declare `managed_<name>_model`, `<name>` being its adapter's
settings name (`service.runtimes.settings_name`: claude, codex, hermes, pi). Derived, so a runtime that
is given settings is covered without being added here. Three call sites used to list the runtimes by
hand (spawn, environment assignment, "apply defaults"), and hermes was in none of them.

Pure: settings and values in, values out.
"""

from __future__ import annotations

from typing import Any, Optional

from service.api_core.settings import DEFAULT_SETTINGS
from service.runtimes import settings_name, supported_runtimes


def _keys(runtime: str) -> Optional[tuple[str, str]]:
    try:
        name = settings_name(runtime)
    except ValueError:
        return None
    model_key, effort_key = f"managed_{name}_model", f"managed_{name}_effort"
    return (model_key, effort_key) if model_key in DEFAULT_SETTINGS and effort_key in DEFAULT_SETTINGS else None


def defaulted_runtimes() -> list[str]:
    """Every runtime that has per-harness defaults, in the adapters' order."""
    return [runtime for runtime in supported_runtimes() if _keys(runtime)]


def defaults_for(settings: dict[str, Any], runtime: str) -> Optional[tuple[str, str]]:
    """The (model, effort) a new agent of `runtime` gets, or None when the runtime has no defaults.

    A saved empty model means the runtime's own; a saved empty effort falls back to the declared
    default, which for claude and codex is "high" and for hermes and pi is empty (the runtime's own)."""
    keys = _keys(runtime)
    if not keys:
        return None
    model_key, effort_key = keys
    model = str(settings.get(model_key, DEFAULT_SETTINGS[model_key]) or "").strip()
    effort = str(settings.get(effort_key) or DEFAULT_SETTINGS[effort_key] or "").strip()
    return model, effort


def with_defaults(settings: dict[str, Any], runtime: str, model: str, runtime_config: dict) -> tuple[str, dict]:
    """`model` and `runtime_config` with an empty model or effort filled from the runtime's defaults.

    An effort counts as given when `runtimeConfig` carries `effort` or `thinking`, the two keys the
    launch reads it from (launch_env.py). An empty default fills nothing."""
    defaults = defaults_for(settings, runtime)
    if not defaults:
        return model, runtime_config
    default_model, default_effort = defaults
    config = dict(runtime_config or {})
    if default_effort and not str(config.get("effort") or config.get("thinking") or "").strip():
        config["effort"] = default_effort
    return (str(model or "").strip() or default_model), config
