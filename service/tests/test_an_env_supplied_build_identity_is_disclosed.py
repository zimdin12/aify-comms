"""The whole chain: env sets a stamp field, config records it, `/version` says so.

WHY A CHAIN TEST AND NOT THREE UNIT TESTS. The verdict half lives in `doctor-predicates.js` and was
tested by injecting `identityOverriddenBy` directly -- which proves the predicate and nothing about
whether anything ever produces that field. This project has shipped that exact shape more than once:
a correct helper whose call site was never wired, green tests either side of a gap. Review asked for the
seam, and it is the seam that carries the finding.

THE FINDING. `ServiceConfig.load()` refuses the five stamp-owned fields from `config/service.json` --
added after an instance announced 3.6.6 while running 0.5.4, and the recorded reason is not the version
but `build_sha`, which `aify-comms doctor` compares against repo HEAD to detect a stale deploy.
Environment variables reach the same five fields and are NOT refused, so `AIFY_BUILD_SHA=<anything>`
made doctor report a clean match for a build that never existed.

NOT REFUSED HERE EITHER, deliberately: `SERVICE_VERSION` is a documented one-off override and a CI image
built outside this repo may legitimately stamp its own sha. What was missing is that the override left
NO TRACE. It does now, and the verdict withholds certification only for the field that can actually
manufacture the comparison.

MEASURED BEFORE ANY OF IT: nothing in the deploy path sets any of the five -- not docker-compose, not
the Dockerfile, not stamp.sh -- `.env.example` carries `SERVICE_VERSION` commented out with a warning,
and the live container has none set. A latent footgun guarded only by prose, which is exactly what the
`service.json` half was before it was closed.
"""

from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.config import _STAMP_OWNED_KEYS, ServiceConfig


@contextmanager
def env(**pairs):
    """Set env vars for one load and restore them, whatever the assertion does."""
    previous = {k: os.environ.get(k) for k in pairs}
    try:
        for k, v in pairs.items():
            os.environ[k] = v
        yield
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class AnEnvSuppliedBuildIdentityIsDisclosed(unittest.TestCase):
    def test_the_stamp_owned_set_is_what_it_claims(self):
        """POSITIVE CONTROL. Every assertion below is about this set; if it were empty or renamed, the
        recording branch would never run and the tests would pass against nothing."""
        self.assertEqual(
            _STAMP_OWNED_KEYS,
            frozenset({"version", "build_sha", "build_short", "build_branch", "built_at"}),
        )

    def test_a_clean_load_records_NOTHING(self):
        """The normal case, and the one that must stay silent. An empty list is the honest state: the
        build identity came from the stamp, and a caveat on every boot would be noise nobody reads."""
        config = ServiceConfig.load()
        self.assertEqual(config.stamp_overrides, [], "a clean load claimed an override")

    def test_an_env_supplied_SHA_is_recorded_by_name(self):
        with env(AIFY_BUILD_SHA="deadbeefdeadbeef"):
            config = ServiceConfig.load()
        self.assertIn("build_sha", config.stamp_overrides)
        self.assertEqual(config.build_sha, "deadbeefdeadbeef", "the override did not take effect at all")

    def test_every_stamp_owned_field_is_recorded_when_env_supplies_it(self):
        """All five, because the guard is written against a SET and a loop -- covering one would prove
        nothing about the other four, which is how the `service.json` half came to be checked and the
        env half not."""
        for env_key, field in (
            ("SERVICE_VERSION", "version"),
            ("AIFY_BUILD_SHA", "build_sha"),
            ("AIFY_BUILD_SHORT", "build_short"),
            ("AIFY_BUILD_BRANCH", "build_branch"),
            ("AIFY_BUILT_AT", "built_at"),
        ):
            with self.subTest(field=field), env(**{env_key: "supplied"}):
                config = ServiceConfig.load()
                self.assertEqual(
                    config.stamp_overrides, [field],
                    f"{env_key} overrode {field} without recording it",
                )

    def test_a_NON_stamp_field_is_not_recorded(self):
        """ANTI-VACUITY. A branch that recorded every env var would satisfy the tests above while making
        the field meaningless -- most of `env_map` is ordinary configuration that says nothing about
        which build is running."""
        with env(LOG_LEVEL="debug"):
            config = ServiceConfig.load()
        self.assertEqual(config.stamp_overrides, [], "an ordinary setting was reported as a build override")

    def test_the_version_endpoint_carries_it_only_when_it_happened(self):
        """The transport. The verdict can only refuse what it is told, and doctor reads this endpoint."""
        from service.routers import health

        source = Path(health.__file__).read_text(encoding="utf-8")
        self.assertIn("identityOverriddenBy", source, "/version no longer discloses the override")
        self.assertIn(
            'if config.stamp_overrides else {}', source,
            "the field is emitted unconditionally; a clean payload must not grow an always-empty key",
        )


if __name__ == "__main__":
    unittest.main()
