"""What an agent session can do once its handle changes.

RELOCATED, not rewritten, in v0.5.4 — byte-identical from `service/routers/agents/shared.py`. Eight
lines depending on a type hint and one api_core leaf, which makes it a layer-0 helper that had been
sitting a layer too high.

SIX ROUTERS IMPORTED IT AND FIVE NEVER CALLED IT. Only `session_mode.py` had a live use, and that use
has now moved into `session_handle_change.py`, so this function's entire caller list is one leaf. The
five dead imports were invisible while the name lived in a shared module that everything imports
from anyway; repointing them at a real module is what made them countable.

THE MOVE WAS FORCED BY A REAL BLOCK, which is the THIRD time in v0.5.4 that a small helper in a
router's shared module has blocked a split: `_apply_status_event` blocked the turn-busy extraction
for a release, `_dispatch_requires_reply` blocked the send-message dispatch start, and this one
blocked the session-handle mirror. An api_core leaf importing from `service.routers` is the cycle the
layering exists to prevent, so each time the choice is to relocate the helper or to abandon the
split. The pattern is worth naming: `routers/agents/shared.py` is where leaf-shaped helpers
accumulate, and every one of them is a future block.

`nativeResume` TRACKS THE HANDLE and the other two do not. A handle means the runtime can resume
itself; `bridgeResume` is always true because the bridge can always relaunch; `persistent` is
defaulted rather than set, so an explicit false already on the record survives.
"""
from __future__ import annotations

from typing import Any

from service.api_core.serialization import _json_loads_or


def _session_capabilities_replacing_handle(capabilities: Any, session_handle: str) -> dict[str, Any]:
    existing = capabilities if isinstance(capabilities, dict) else _json_loads_or(capabilities, {})
    result = dict(existing or {}) if isinstance(existing, dict) else {}
    handle_present = bool(str(session_handle or "").strip())
    result.setdefault("persistent", True)
    result["bridgeResume"] = True
    result["nativeResume"] = handle_present
    return result
