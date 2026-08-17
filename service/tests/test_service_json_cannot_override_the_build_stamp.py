"""`config/service.json` may not overwrite what the build stamp observed.

REPORTED FROM ANOTHER INSTANCE, 2026-08-17: their service reported version `3.6.6` while running
`0.5.4`. Cause: `ServiceConfig.load()` applied service.json with a generic loop —

    for key, value in data.items():
        if key not in ("custom", "containers") and hasattr(config, key):
            setattr(config, key, value)

— so ANY key that happened to name a config attribute won, including `version`, and it ran AFTER the
stamp. That is the second override hole of exactly the class CLAUDE.md already documents for
`.env`'s `SERVICE_VERSION`: the version is supposed to have ONE source, and two different files could
quietly beat it.

THE VERSION WAS THE MILD HALF. The same loop reached `build_sha`, and that value is what
`aify-comms doctor`'s `service` check compares against repo HEAD to answer "is the container serving
my code". A service.json could therefore make the project's only stale-deploy instrument agree with a
sha nothing was ever built from — a false green in the exact place that tool exists to prevent one.
That is the same failure shape as doctor's own `env-bridge` false green (`756f3a5`) and the
`unknown-all` fix (`a2f9e42`): a check that reports ok while verifying nothing.

These five fields are OBSERVATIONS of a build, not configuration. No operator hand-edit could make
one of them true, so the fix refuses them rather than trying to reconcile them.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from service.config import _STAMP_OWNED_KEYS, ServiceConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
STAMP_PATH = REPO_ROOT / "service" / "_build_stamp.json"

# Everything that could ALSO decide these fields. Sealed on every load below, because a test that
# leaves them ambient passes on any machine where they happen to be unset — the defect class three
# review rounds of this series failed on.
AMBIENT = (
    "SERVICE_VERSION", "SERVICE_NAME", "SERVICE_DESCRIPTION",
    "AIFY_BUILD_SHA", "AIFY_BUILD_SHORT", "AIFY_BUILD_BRANCH", "AIFY_BUILT_AT",
    "CONFIG_DIR",
)

HOSTILE = {
    "version": "3.6.6",
    "build_sha": "0000000000000000000000000000000000000000",
    "build_short": "0000000",
    "build_branch": "not-a-branch",
    "built_at": "1999-01-01T00:00:00Z",
}


def _load_with_service_json(payload: dict) -> ServiceConfig:
    """`ServiceConfig.load()` against a service.json we control, with every env carrier removed."""
    saved = {name: os.environ.pop(name, None) for name in AMBIENT}
    try:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "service.json").write_text(json.dumps(payload), encoding="utf-8")
            os.environ["CONFIG_DIR"] = tmp
            for name in AMBIENT:
                if name != "CONFIG_DIR":
                    assert name not in os.environ, f"{name} leaked into a sealed load"
            return ServiceConfig.load()
    finally:
        os.environ.pop("CONFIG_DIR", None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def _stamped() -> dict:
    """What the stamp says, or {} when it is absent (it is gitignored — a fresh checkout has none)."""
    if not STAMP_PATH.exists():
        return {}
    try:
        data = json.loads(STAMP_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


class ServiceJsonCannotOverrideTheStamp(unittest.TestCase):
    def test_a_hostile_service_json_changes_none_of_the_build_identity(self):
        config = _load_with_service_json(HOSTILE)
        for key, planted in HOSTILE.items():
            self.assertNotEqual(
                getattr(config, key), planted,
                f"service.json overrode `{key}` — the build stamp is supposed to own it",
            )

    def test_the_reported_symptom_exactly(self):
        # Their instance: version 3.6.6 reported by a service running 0.5.4.
        config = _load_with_service_json({"version": "3.6.6"})
        self.assertNotEqual(config.version, "3.6.6")
        stamp = _stamped()
        expected = str(stamp.get("version") or "0.0.0-dev")
        self.assertEqual(
            config.version, expected,
            "with a stamp present the version must come from it; with no stamp it must fall back to "
            "the dataclass default — never to service.json",
        )

    def test_service_json_STILL_configures_everything_it_owns(self):
        # ANTI-VACUITY, and the regression that matters most here: the fix is a `continue` inside the
        # loop that applies service.json at all. Refusing too much would silently stop honouring the
        # file the README tells operators to hand-edit, and every test above would still pass.
        config = _load_with_service_json({"log_level": "debug", "custom": {"k": "v"}})
        self.assertEqual(config.log_level, "debug", "service.json no longer configures the service")
        # A SUBSET check, deliberately: `load()` merges env-derived container defaults
        # (`compose_project_name`, `network_name`) into `custom` after reading the file, so an equality
        # assertion here would be asserting the test machine's environment rather than this contract.
        self.assertEqual(config.custom.get("k"), "v", "the custom block stopped being read")

    def test_the_refused_set_matches_what_the_stamp_actually_writes(self):
        # Drift gate. `scripts/stamp.sh` is the writer; if it gains a sixth field, this set must gain
        # it too, or the new field is overridable from day one and nothing says so.
        stamp_sh = (REPO_ROOT / "scripts" / "stamp.sh").read_text(encoding="utf-8")
        written = {key for key in ("sha", "short", "branch", "built_at", "version")
                   if f'"{key}":' in stamp_sh}
        self.assertEqual(
            written, {"sha", "short", "branch", "built_at", "version"},
            "stamp.sh no longer writes the fields this test assumes; re-derive _STAMP_OWNED_KEYS",
        )
        # The config attribute names differ from the stamp's JSON keys (`sha` -> `build_sha`), so the
        # mapping is asserted rather than inferred.
        self.assertEqual(
            _STAMP_OWNED_KEYS,
            {"version", "build_sha", "build_short", "build_branch", "built_at"},
            "the stamp-owned key set changed; every one of these is read back out of the stamp in "
            "ServiceConfig.load() and must stay refused to service.json",
        )

    def test_every_refused_key_is_a_real_config_attribute(self):
        # A typo in the set would refuse nothing and read as a guard.
        config = ServiceConfig()
        for key in _STAMP_OWNED_KEYS:
            self.assertTrue(hasattr(config, key), f"`{key}` is not a ServiceConfig attribute")


if __name__ == "__main__":
    unittest.main()
