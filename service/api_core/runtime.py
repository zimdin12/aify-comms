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


def _normalize_launch_mode(mode: Any) -> str:
    """Fold case and whitespace on `launch_mode`, the way its two sibling fields already are.

    IT WAS THE ONE IDENTITY FIELD STORED VERBATIM. `runtime` and `session_mode` are normalised on the
    way in; `req.launchMode or "detached"` went to the column exactly as the caller spelled it, and
    every reader then asks `(row["launch_mode"] or "detached") == "none"` — case-sensitively, at four
    Python sites and two more in the bridge.

    `none` is not a cosmetic value: `agent_stop_resume.py` writes it as part of STOP
    (`SET status = 'stopped', launch_mode = 'none'`), so it means "the operator stopped this agent;
    do not start it". A row holding `"None"` instead reads as not-stopped everywhere, and the next
    send cold-starts an agent the operator deliberately stopped.

    `"None"` is not a hostile input, it is the obvious accident: `str(None)` in Python and
    `String(null)`/`"None"` from a hand-written client both produce it, and `comms_register` takes
    `launchMode` as a free-form `z.string()`.

    CASE ONLY, deliberately. Rejecting an unknown mode would be a wider behaviour change than this
    defect calls for — `_SESSION_MODES` exists for session_mode and no such set exists here, and
    inventing one would need a ruling on values like `codex-live` that appear in tests. Folding case
    is behaviour-preserving for every valid spelling and fixes the one that is not.
    """
    return str(mode or "detached").strip().lower() or "detached"


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
