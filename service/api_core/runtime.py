"""Runtime and session-mode normalization, resolved against the vocabulary contract.

v0.5.1e. These are the highest-fanout normalizers in the service — `_normalize_runtime` alone is
reached by more route domains than almost anything else in the router — and they are pure: they map
an input spelling onto a canonical word and nothing more.

They read the contract (`service/contracts/vocabulary.json`, via `service/api_core/vocabulary.py`)
rather than a literal declared next to them, which is the whole point of shipping the contract first:
the words live in one place, and the functions that normalize onto those words live beside them
instead of inside a 20,000-line router.

A leaf. It imports the vocabulary leaf and the standard library, nothing else, so it cannot join an
import cycle.
"""

from __future__ import annotations

from typing import Any, Optional

from service.api_core.vocabulary import (
    RUNTIME_ALIASES as _RUNTIME_ALIASES,
    SESSION_MODES as _SESSION_MODES,
)


def _normalize_runtime(runtime: Any) -> str:
    key = str(runtime or "generic").strip().lower()
    return _RUNTIME_ALIASES.get(key, key or "generic")


def _normalize_session_mode(mode: Any) -> str:
    value = str(mode or "resident").strip().lower()
    return value if value in _SESSION_MODES else "resident"


def _runtime_capability_for_environment(environment: dict[str, Any], runtime: str) -> Optional[dict[str, Any]]:
    normalized = _normalize_runtime(runtime)
    for item in environment.get("runtimes") or []:
        if _normalize_runtime(item.get("runtime") or "") == normalized:
            return item
    return None


#: Runtimes whose managed worker is driven NATIVELY by the bridge — an app-server or persistent RPC
#: session — as opposed to the channel-sidecar runtimes in `api_core/channel_delivery.py`. v0.5.4: moved
#: out of the control plane, whose only claim on it was history; its readers are the dispatch_messages
#: package and `service/db.py`.
#:
#: A LITERAL, not a contract read, and that is worth flagging rather than fixing here: `_normalize_runtime`
#: below resolves spellings against `service/contracts/vocabulary.json` while this set hardcodes four
#: words. Reconciling the two is a behaviour question (the contract could disagree), so it is left alone
#: by a structural slice.
_NATIVE_MANAGED_RUNTIMES = {"codex", "pi", "opencode", "hermes"}
