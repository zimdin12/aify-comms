"""Python RuntimeAdapter package — parallel to mcp/stdio/adapters/.

Plan 2 introduces this so the server (Python) and bridge (JS) can both
own per-runtime capabilities through their own adapter classes. Plan 3
extends with console_command / inject_message / interrupt / steer.

Per-adapter modules (claude/codex/hermes/pi/opencode) are imported lazily so
the base contract is usable before subclasses land (Task 1 of Plan 2 ships
the base + factory wiring; Tasks 2-6 add the subclass modules).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from .base import RuntimeAdapter

if TYPE_CHECKING:  # pragma: no cover — type-only hints
    from .claude import ClaudeAdapter
    from .codex import CodexAdapter
    from .hermes import HermesAdapter
    from .pi import PiAdapter
    from .opencode import OpencodeAdapter


# canonical-name → (module_name, class_name). Imports happen on first lookup
# so missing subclass modules don't break `from service.runtimes.base import …`.
_REGISTRY: dict[str, tuple[str, str]] = {
    "claude-code": ("claude", "ClaudeAdapter"),
    "codex": ("codex", "CodexAdapter"),
    "hermes": ("hermes", "HermesAdapter"),
    "pi": ("pi", "PiAdapter"),
    "opencode": ("opencode", "OpencodeAdapter"),
}

_ALIASES: dict[str, str] = {
    "claude": "claude-code",
    "claude_code": "claude-code",
    "hermes-agent": "hermes",
    "hermes_agent": "hermes",
    "oh-my-pi": "pi",
    "oh_my_pi": "pi",
    "omp": "pi",
    "pi-agent": "pi",
    "pi_agent": "pi",
}


def _load_adapter_class(canonical: str) -> type[RuntimeAdapter]:
    module_name, class_name = _REGISTRY[canonical]
    module = importlib.import_module(f".{module_name}", __name__)
    return getattr(module, class_name)


def adapter_for(name: str | None) -> RuntimeAdapter:
    key = str(name if name is not None else "").strip().lower()
    canonical = _ALIASES.get(key, key)
    if canonical not in _REGISTRY:
        raise ValueError(
            f'Unknown runtime "{name}". Known: {", ".join(_REGISTRY.keys())}'
        )
    cls = _load_adapter_class(canonical)
    return cls()


def supported_runtimes() -> list[str]:
    return list(_REGISTRY.keys())


__all__ = [
    "RuntimeAdapter",
    "adapter_for",
    "supported_runtimes",
]
