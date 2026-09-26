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


class AShaSuppliedToTheStampIsAnOverrideTests(unittest.TestCase):
    """v0.7 review: GIT_SHA forced `dirty` to false and left no trace, so the doctor could certify a
    typed sha against a checkout whose runtime files had changed."""

    def test_the_service_reports_a_stamp_time_sha_as_an_override(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            supplied = VersionSaysWhenABuildWasDirtyTests._load(None, {"sha": "b" * 40, "sha_from_env": True}, tmp)
            self.assertIn("build_sha", supplied.stamp_overrides)
            measured = VersionSaysWhenABuildWasDirtyTests._load(None, {"sha": "b" * 40, "sha_from_env": False}, tmp)
            self.assertNotIn("build_sha", measured.stamp_overrides, "control: a measured sha is not an override")

    def test_stamp_sh_asks_the_checkout_even_when_a_sha_is_supplied(self):
        import os
        import shutil
        import subprocess
        import tempfile

        from service.tests._launchers import bash

        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "scripts").mkdir()
            (repo / "service").mkdir()
            shutil.copy(repo_root / "scripts" / "stamp.sh", repo / "scripts" / "stamp.sh")
            (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            (repo / "service" / "app.py").write_text("x = 1\n", encoding="utf-8")
            git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "c"], check=True)
            (repo / "service" / "app.py").write_text("x = 2\n", encoding="utf-8")
            # A shell that exports these (hermes' tool shell does) hands native git a `/c/...` path it
            # cannot use, so the stamp reads no checkout at all. That is the caller's shell, not what
            # this test is about.
            env = {k: v for k, v in os.environ.items() if k not in ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL")}
            env["GIT_SHA"] = "c" * 40
            done = subprocess.run([bash(), (repo / "scripts" / "stamp.sh").as_posix()], capture_output=True, text=True, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            stamp = json.loads((repo / "service" / "_build_stamp.json").read_text(encoding="utf-8"))
            self.assertEqual((stamp["sha"], stamp["dirty"], stamp["sha_from_env"]), ("c" * 40, True, True))
