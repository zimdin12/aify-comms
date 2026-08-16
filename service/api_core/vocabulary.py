"""The single Python owner of the cross-language vocabulary.

v0.5.1d. Loads `service/contracts/vocabulary.json`, which is the contract described in finding H1 of
the v0.2 review: *"the system's core vocabulary has no single home — it is hand-copied across the
language boundary."*

This module is the ONLY place Python declares those words. Every Python reader imports from here —
`control_plane.py` and the `api_core` leaves that need them — so there is no second Python copy to
drift. (Until the v0.5 domain extraction that reader was `api_v2.py`; naming it here went stale when
that file became 53 lines of `include_router` calls and stopped importing anything from this module.) The JS bridge keeps its own literal map — it has to, because
`install.sh` copies only `mcp/stdio/` to `~/.aify-comms` and a file under `service/` does not exist
on that host — and an agreement test in each suite fails if the two ever disagree.

A leaf: standard library only, no service imports, so it can never join an import cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "vocabulary.json"


def _load() -> dict:
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        return json.load(handle)


_CONTRACT = _load()

#: Read-only so a caller cannot mutate the vocabulary for everyone else in-process. The service is
#: deliberately single-worker with process-global caches, which means one careless `.update()` would
#: be visible to every subsequent request rather than to one caller.
RUNTIME_ALIASES = MappingProxyType(dict(_CONTRACT["runtimes"]["aliases"]))
CANONICAL_RUNTIMES = frozenset(_CONTRACT["runtimes"]["canonical"])
LAUNCHABLE_RUNTIMES = frozenset(_CONTRACT["runtimes"]["launchable"])
SESSION_MODES = frozenset(_CONTRACT["sessionModes"]["values"])
AGENT_STATUSES = tuple(_CONTRACT["agentStatuses"]["values"])
AGENT_STATUS_MEANINGS = MappingProxyType(dict(_CONTRACT["agentStatuses"]["meanings"]))
