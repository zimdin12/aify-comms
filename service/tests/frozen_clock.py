"""One `now` for a whole test body, across EVERY module that reads the clock.

WHY THIS IS DERIVED RATHER THAN A LIST. 76 modules under `service/` do
`from service.clock import now as _now`, which binds the function OBJECT into each module's own
namespace. Patching one module's `_now` therefore freezes one seventy-sixth of the clock, and the
other 75 keep ticking.

THAT IS NOT HYPOTHETICAL. `test_the_status_refresh_is_not_n_plus_one.py` froze
`status_refresh._now` to stop a one-second flake, twice, and its docstring recorded the second
attempt as a fix for the first being incomplete. It was still incomplete: the function under test,
`_refresh_agent_live_state`, never calls `_now()` at all -- it takes `now` as a parameter, defaults
it to None, and hands that to `_compute_live_status_cache`, which stamps `updated_at` from
`status_inputs._now`. The frozen name was on a sibling function the test does not call. Reproduced
on demand by putting a real 1.2s sleep between the two derivations the test compares: the only
differing field was `updated_at`, live `...:34:32Z` against prefetched `...:34:33Z`.

So the freeze had never once worked. It passed because two derivations usually land in the same
second, which is the same reason the flake was rare rather than absent.

SCOPE, stated because a freeze that quietly misses something is what this replaces: every
`service.*` module ALREADY IMPORTED whose `_now` is `service.clock.now`, plus `service.clock.now`
itself so a module imported later inside the block also gets the frozen value. A module that
imported the clock under a different name is not reached, and `frozen_service_clock` fails loudly
rather than freezing nothing.
"""
from __future__ import annotations

import contextlib
import sys
from typing import Iterator

import service.clock as _clock

#: A freeze that reached almost nothing would be indistinguishable from one that worked, so the
#: helper refuses below a floor. 76 modules bind the clock as of 2026-08-29; the floor is far under
#: that because which modules are imported depends on the test, and only the shape matters.
_MINIMUM_BINDINGS = 3


@contextlib.contextmanager
def frozen_service_clock(stamp: str | None = None) -> Iterator[str]:
    """Freeze every reachable `_now` at one value for the duration of the block.

    Yields the frozen stamp so a caller can assert against it.
    """
    frozen = stamp or _clock.now()
    original_now = _clock.now
    patched: list[tuple[object, str]] = []

    def frozen_now() -> str:
        return frozen

    for name, module in list(sys.modules.items()):
        if not name.startswith("service.") and name != "service":
            continue
        if getattr(module, "_now", None) is original_now:
            setattr(module, "_now", frozen_now)
            patched.append((module, name))
    _clock.now = frozen_now

    if len(patched) < _MINIMUM_BINDINGS:
        _clock.now = original_now
        for module, _ in patched:
            setattr(module, "_now", original_now)
        raise AssertionError(
            "frozen_service_clock reached only {} module binding(s); a freeze that touches nothing "
            "reads exactly like one that works. Import the modules under test first.".format(
                len(patched)
            )
        )
    try:
        yield frozen
    finally:
        _clock.now = original_now
        for module, _ in patched:
            setattr(module, "_now", original_now)


def clock_bindings() -> list[str]:
    """Every imported `service.*` module currently binding `service.clock.now` as `_now`.

    Exposed so a test can assert the freeze's REACH rather than only its effect -- the failure this
    module exists for was a freeze whose reach was one module and whose effect looked fine.
    """
    return sorted(
        name
        for name, module in sys.modules.items()
        if (name == "service" or name.startswith("service."))
        and getattr(module, "_now", None) is _clock.now
    )
