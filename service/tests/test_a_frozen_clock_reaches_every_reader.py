r"""A test that freezes the clock must freeze the clock the code under test reads.

76 modules under `service/` do `from service.clock import now as _now`, which copies the function
OBJECT into each module's namespace. Patching one module's `_now` freezes one seventy-sixth of the
clock and leaves the other 75 ticking -- and the failure is invisible, because two calls usually
land in the same second anyway.

THAT IS EXACTLY HOW IT WENT. `test_the_status_refresh_is_not_n_plus_one.py` carried a `_frozen_clock`
that patched `status_refresh._now`, twice, the second time recorded in its own docstring as a fix
for the first being incomplete. The function under test never calls that name: it takes `now` as a
parameter, defaults it to None, and `_compute_live_status_cache` stamps `updated_at` from
`status_inputs._now`. So the freeze had never worked, and the flake it was written for came back a
third time in a full-suite run on 2026-08-29.

This file is about the REACH of the freeze rather than its effect, because reach is the thing that
looked fine.
"""

from __future__ import annotations

import unittest

import service.clock as clock
from service.tests import frozen_clock
from service.tests.frozen_clock import clock_bindings, frozen_service_clock


class AFrozenClockReachesEveryReaderTests(unittest.TestCase):
    def test_the_freeze_reaches_the_binding_the_status_path_actually_reads(self):
        """Named, not counted. `status_inputs._now` is the one that stamps `updated_at`, and it is
        the one three years of freezing this path never touched."""
        import service.api_core.status_inputs as status_inputs
        import service.api_core.status_refresh as status_refresh

        with frozen_service_clock() as frozen:
            self.assertEqual(frozen, status_inputs._now())
            self.assertEqual(frozen, status_refresh._now())
            self.assertEqual(frozen, clock.now())

    def test_every_binding_is_restored_afterwards(self):
        import service.api_core.status_inputs as status_inputs

        before = status_inputs._now
        with frozen_service_clock():
            self.assertIsNot(status_inputs._now, before)
        self.assertIs(status_inputs._now, before)
        self.assertIs(status_inputs._now, clock.now)

    def test_a_module_imported_inside_the_block_is_frozen_too(self):
        """`service.clock.now` itself is patched, so a late import binds the frozen function rather
        than the live one. Without this a test that imports lazily -- which most of them do, inside
        the test body -- would silently escape the freeze."""
        with frozen_service_clock() as frozen:
            import importlib
            module = importlib.reload(importlib.import_module("service.api_core.status_inputs"))
            self.assertEqual(frozen, module._now())
        importlib.reload(module)
        self.assertIs(module._now, clock.now)

    def test_the_reach_is_derived_and_plausible(self):
        """Anti-vacuity for the helper itself: a freeze reaching nothing reads exactly like one that
        works, which is the whole failure this replaces."""
        bindings = clock_bindings()
        self.assertGreaterEqual(len(bindings), 3, f"only {len(bindings)} clock bindings found")
        self.assertIn("service.api_core.status_inputs", bindings)

    def test_a_freeze_that_would_reach_almost_nothing_REFUSES(self):
        """And leaves the clock alone when it refuses. A helper that half-applied a freeze and then
        raised would be worse than one that never ran."""
        import service.api_core.status_inputs as status_inputs

        original_floor = frozen_clock._MINIMUM_BINDINGS
        before = status_inputs._now
        frozen_clock._MINIMUM_BINDINGS = 10_000
        try:
            with self.assertRaises(AssertionError):
                with frozen_service_clock():
                    self.fail("the block ran despite the freeze refusing")
        finally:
            frozen_clock._MINIMUM_BINDINGS = original_floor
        self.assertIs(status_inputs._now, before, "a refused freeze left a module patched")
        self.assertIs(clock.now, before, "a refused freeze left service.clock patched")

    def test_the_frozen_value_can_be_chosen(self):
        with frozen_service_clock("2026-01-01T00:00:00Z") as frozen:
            self.assertEqual("2026-01-01T00:00:00Z", frozen)
            self.assertEqual("2026-01-01T00:00:00Z", clock.now())


if __name__ == "__main__":
    unittest.main()
