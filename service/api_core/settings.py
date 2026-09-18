"""Service settings: the defaults, the cache, and the single accessor.

v0.5.1g, and the reviewer's REVERSED ruling from v0.5 slice 7. Back then `_load_settings` was allowed
to stay router-owned and be borrowed, on the grounds that seaming it would change more than the
moment of a read. The measurement changed that: it is reached by twelve route domains, which makes
router ownership the problem rather than the pragmatic choice.

MOVED VERBATIM, AND THAT IS THE WHOLE CONSTRAINT. Same call sites, same call timing, same cache
semantics — including the `"pytest" not in sys.modules` bypass, which exists so tests get isolation
and set-then-read behaviour rather than a 5-second stale window. Hoisting settings reads to callers,
batching them per request, or changing the TTL would each be a behaviour change, and v0.5.x is
contracted to an empty behaviour changelog. None of that happened here.

`_SETTINGS_CACHE` is a PROCESS GLOBAL and moving it is the delicate part: a second module-level
assignment anywhere would fork it silently — no error, the cache simply stops being shared — so
`service/tests/test_process_global_identity.py` tracks its owner and fails if a second one appears.
That file's GLOBALS map was updated in the same commit, which is the mechanism working as intended:
the owner moved, so the line naming the owner had to move too.

A leaf: it imports only its own declarations (`settings_spec.py`) and takes the db handle as an argument.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from service.api_core.settings_spec import BY_KEY, defaults


#: Derived from the declarations in `settings_spec.py`, which carry each setting's type, bounds and
#: meaning. Every reader still asks this dict for a fallback, so it keeps its name.
DEFAULT_SETTINGS = defaults()


# In-memory settings cache (perf, 2026-06-04). _load_settings is called on nearly
# every hot request (dispatch/claim, heartbeat, status compute) — 55 call sites — so
# re-reading + JSON-parsing the settings table each time was a measurable chunk of the
# poll-load CPU that was hammering the service. Cache the merged dict with a short TTL;
# writes invalidate immediately (_invalidate_settings_cache) so changes still apply at
# once. Callers get a shallow copy so they can't mutate the cached dict.
_SETTINGS_CACHE: dict[str, Any] = {"value": None, "at": 0.0}


_SETTINGS_CACHE_TTL = 5.0


async def _load_settings(db):
    cached = _SETTINGS_CACHE["value"]
    if (
        cached is not None
        and "pytest" not in sys.modules  # bypass under tests: preserves isolation + set-then-read
        and (time.monotonic() - _SETTINGS_CACHE["at"]) < _SETTINGS_CACHE_TTL
    ):
        return dict(cached)
    settings = {**DEFAULT_SETTINGS}
    sc = await db.execute("SELECT key, value FROM settings")
    for row in await sc.fetchall():
        # Only declared settings: a row left by a retired key is ignored, never served.
        if row["key"] not in BY_KEY:
            continue
        try:
            settings[row["key"]] = json.loads(row["value"])
        except Exception:
            pass
    _SETTINGS_CACHE["value"] = dict(settings)
    _SETTINGS_CACHE["at"] = time.monotonic()
    return settings


# Moved here in v0.5.2c: the invalidator belongs with the cache it invalidates. It had been
# left in the router when the cache moved in v0.5.1g, which meant the router still looked like
# the owner of settings-cache lifecycle while owning none of the state.
def _invalidate_settings_cache() -> None:
    _SETTINGS_CACHE["value"] = None
    _SETTINGS_CACHE["at"] = 0.0



def _managed_terminal_backing_enabled(settings: dict[str, Any]) -> bool:
    return bool(settings.get("managed_terminal_backing_enabled", DEFAULT_SETTINGS["managed_terminal_backing_enabled"]))
