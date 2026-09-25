"""`/version` carries the stamp's `dirty` flag, which `aify-comms doctor`'s `service` check reads.

A build from a tree with uncommitted changes to code the image runs matches no commit; without the
flag the doctor reported it as serving HEAD (v0.7 scan B19).
"""

import json
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from service import config as config_module
from service.config import ServiceConfig


class VersionSaysWhenABuildWasDirtyTests(unittest.TestCase):
    def _load(self, stamp: dict, tmp: Path) -> ServiceConfig:
        stamp_path = tmp / "_build_stamp.json"
        stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
        # `load()` finds the stamp beside its own module file, so the module's `__file__` is pointed
        # at a scratch directory rather than the checkout's real stamp.
        with mock.patch.object(config_module, "__file__", str(tmp / "config.py")):
            return ServiceConfig.load()

    def test_the_flag_is_read_from_the_stamp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.assertTrue(self._load({"sha": "a" * 40, "dirty": True}, tmp).build_dirty)
            self.assertFalse(self._load({"sha": "a" * 40, "dirty": False}, tmp).build_dirty)
            self.assertFalse(self._load({"sha": "a" * 40}, tmp).build_dirty, "an older stamp is not dirty")
            self.assertFalse(self._load({"sha": "a" * 40, "dirty": "yes"}, tmp).build_dirty,
                             "only a JSON true counts")

    def test_version_returns_it(self):
        from fastapi import FastAPI

        from service.routers import health

        app = FastAPI()
        app.include_router(health.router)
        cfg = ServiceConfig()
        cfg.build_dirty = True
        with mock.patch.object(health, "get_config", lambda: cfg), \
                mock.patch.object(health, "_check_update", lambda sha: None):
            body = TestClient(app).get("/version").json()
        self.assertIs(body["dirty"], True)
