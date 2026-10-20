"""aify-comms serves its registration spec at `/.well-known/aify-service` (guided install S1).

A new host learns a service by asking it, before it holds any key. So the route answers without one,
serves only a spec stamped for the build that is running, holds the format owner's rules, and carries
no secret. Its `verify` block is checked against this service's own key middleware, so the spec cannot
promise a probe the service does not keep.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.api_core.service_spec import served_spec, spec_problems
from service.config import ServiceConfig
from service.main import APIKeyMiddleware, create_app
from service.routers import health
from service.routers import service_spec as spec_router
from service.tests.served_routes import walk_routes

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROUTE = "/.well-known/aify-service"
BUILD = "a" * 40
KEY = "planted-service-key-7f3e"


def _comms_spec() -> dict:
    """The document comms' builder produces, asked of the real module, never retyped here."""
    script = (
        "import { commsServiceSpec } from './mcp/stdio/service-spec.mjs';"
        "process.stdout.write(JSON.stringify(commsServiceSpec({version: '9.9.9',"
        f" source: {{git: {{repo: 'o/r', revision: '{BUILD}'}}}}, manifestSha256: 'f'.repeat(64)}})));"
    )
    done = subprocess.run(["node", "--input-type=module", "-e", script], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


class _Served(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.spec_path = self.tmp / "_service_spec.json"
        self.config = ServiceConfig()
        self.config.build_sha = BUILD
        self.config.api_key = KEY
        for patch in (mock.patch.object(spec_router, "SPEC_PATH", self.spec_path),
                      mock.patch.object(spec_router, "get_config", lambda: self.config)):
            patch.start()
            self.addCleanup(patch.stop)

    def stamp(self, spec, build=BUILD):
        self.spec_path.write_text(json.dumps({"buildSha": build, "spec": spec}), encoding="utf-8")

    def client(self):
        app = FastAPI()
        app.add_middleware(APIKeyMiddleware, api_key=KEY)
        app.include_router(health.router)
        app.include_router(spec_router.router)

        @app.get("/api/v1/agents")
        async def _protected():
            return []

        return TestClient(app)


class TheSpecIsServedWithoutAKeyTests(_Served):
    def test_a_spec_stamped_for_this_build_is_served_unauthenticated(self):
        spec = _comms_spec()
        self.stamp(spec)
        response = self.client().get(SPEC_ROUTE)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), spec)

    def test_the_real_app_serves_the_route_and_skips_the_key_only_for_it(self):
        paths = {getattr(route, "path", "") for route in walk_routes(create_app())}
        self.assertIn(SPEC_ROUTE, paths)


class OnlyAValidSpecForThisBuildIsServedTests(_Served):
    def test_a_spec_stamped_for_another_build_is_refused(self):
        self.stamp(_comms_spec(), build="b" * 40)
        response = self.client().get(SPEC_ROUTE)
        self.assertEqual(response.status_code, 503)
        self.assertIn("another build", response.json()["error"])

    def test_no_stamped_spec_is_refused(self):
        response = self.client().get(SPEC_ROUTE)
        self.assertEqual(response.status_code, 503)

    def test_an_unknown_build_serves_nothing(self):
        self.config.build_sha = "unknown"
        self.stamp(_comms_spec(), build="unknown")
        self.assertEqual(self.client().get(SPEC_ROUTE).status_code, 503)

    def test_an_unknown_spec_version_is_refused(self):
        spec = {**_comms_spec(), "specVersion": 2}
        self.stamp(spec)
        self.assertEqual(self.client().get(SPEC_ROUTE).status_code, 503)


class TheServedDocumentHoldsTheFormatRulesTests(_Served):
    """S1, "The concrete document": `fixed` holds no endpoint, mcp or credential reference, every probe
    names its path and header, every secret's probe resolves, and a pending artifact has no hash."""

    def test_comms_document_breaks_no_rule(self):
        spec = _comms_spec()
        self.assertEqual(spec_problems(spec), [])
        self.assertTrue(spec["verify"]["probes"], "control: there is a probe for the rule to resolve")
        self.assertTrue(any("probe" in s for s in spec["secrets"]), "control: a secret names a probe")
        self.assertTrue(spec["fixed"], "control: fixed has fields for the rule to check")

    def test_each_rule_refuses_its_violation_before_it_is_served(self):
        base = _comms_spec()
        artifact = base["artifacts"][0]
        broken = {
            "fixed holds endpoint": {**base, "fixed": {**base["fixed"], "endpoint": "http://x"}},
            "fixed holds mcp": {**base, "fixed": {**base["fixed"], "mcp": []}},
            "fixed holds credentialRef": {**base, "fixed": {**base["fixed"], "credentialRef": "x.key"}},
            "fixed holds secretsCredentialRef": {**base, "fixed": {**base["fixed"], "secretsCredentialRef": "y"}},
            "a dangling probe": {**base, "secrets": [{"field": "credentialRef", "required": True, "probe": "nope"}]},
            "a probe with no header": {**base, "verify": {**base["verify"], "probes": {"api": {"path": "/x"}}}},
            "a pending artifact with a hash": {**base, "artifacts": [{**artifact, "source": {"pending": "why"}}]},
        }
        for name, spec in broken.items():
            with self.subTest(name):
                self.assertNotEqual(spec_problems(spec), [])
                self.stamp(spec)
                self.assertEqual(self.client().get(SPEC_ROUTE).status_code, 503)

    def test_a_pending_artifact_without_a_hash_is_served(self):
        base = _comms_spec()
        artifact = {k: v for k, v in base["artifacts"][0].items() if k != "sha256"}
        spec = {**base, "artifacts": [{**artifact, "source": {"pending": "no origin"}}]}
        self.stamp(spec)
        self.assertEqual(self.client().get(SPEC_ROUTE).status_code, 200)


class TheSpecCarriesNoSecretTests(_Served):
    def test_no_key_and_no_aify_value_reaches_the_document(self):
        planted = {"AIFY_API_KEY": "planted-aify-key-91c2", "AIFY_AGENT_ID": "planted-agent-55d1",
                   "CLAUDE_MCP_API_KEY": "planted-claude-key-0b7a"}
        self.stamp(_comms_spec())
        with mock.patch.dict(os.environ, planted):
            body = self.client().get(SPEC_ROUTE).text
        for value in (KEY, *planted.values()):
            with self.subTest(value):
                self.assertNotIn(value, body)

    def test_control_the_same_probe_finds_a_value_that_is_there(self):
        self.stamp({**_comms_spec(), "version": KEY})
        self.assertIn(KEY, self.client().get(SPEC_ROUTE).text)


class TheSpecsProbesHoldAgainstThisServiceTests(_Served):
    """The probe a connect runs must be one this service keeps: 401 unkeyed, 401 wrong key, 200 keyed,
    on a GET route the real app serves, with health answering unkeyed."""

    def test_every_probe_and_health_answer_as_the_spec_promises(self):
        spec = _comms_spec()
        client = self.client()
        self.assertEqual(client.get(spec["verify"]["health"]["path"]).status_code, 200)
        real = {getattr(r, "path", "") for r in walk_routes(create_app()) if "GET" in (getattr(r, "methods", None) or ())}
        for name, probe in spec["verify"]["probes"].items():
            with self.subTest(name):
                self.assertIn(probe["path"], real, "the probe path is not a GET route the real app serves")
                self.assertEqual(client.get(probe["path"]).status_code, 401)
                self.assertEqual(client.get(probe["path"], headers={probe["header"]: "wrong"}).status_code, 401)
                self.assertEqual(client.get(probe["path"], headers={probe["header"]: KEY}).status_code, 200)


class StampWritesTheSpecForItsOwnShaTests(unittest.TestCase):
    """`scripts/stamp.sh` binds the spec to the sha it stamps, and a failed write leaves no spec rather
    than the previous build's."""

    SPEC_MODULES = ("service-spec-cli.mjs", "service-spec.mjs", "service-env-names.mjs", "service-name.mjs")

    def _repo(self, tmp: Path, *, with_cli: bool) -> Path:
        repo = tmp / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "service").mkdir()
        (repo / "mcp" / "stdio").mkdir(parents=True)
        shutil.copy(REPO_ROOT / "scripts" / "stamp.sh", repo / "scripts" / "stamp.sh")
        (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        (repo / "mcp" / "stdio" / "server.js").write_text("s\n", encoding="utf-8")
        if with_cli:
            for name in self.SPEC_MODULES:
                shutil.copy(REPO_ROOT / "mcp" / "stdio" / name, repo / "mcp" / "stdio" / name)
        git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "c"], check=True)
        return repo

    def _stamp(self, repo: Path):
        from service.tests._launchers import bash
        env = {k: v for k, v in os.environ.items()
               if k not in ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL", "GIT_SHA", "GIT_BRANCH", "AIFY_VERSION")}
        done = subprocess.run([bash(), (repo / "scripts" / "stamp.sh").as_posix()], capture_output=True,
                              text=True, env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_spec_is_bound_to_the_stamped_sha(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(Path(d), with_cli=True)
            self._stamp(repo)
            stamp = json.loads((repo / "service" / "_build_stamp.json").read_text(encoding="utf-8"))
            stamped = json.loads((repo / "service" / "_service_spec.json").read_text(encoding="utf-8"))
            self.assertEqual(stamped["buildSha"], stamp["sha"])
            self.assertEqual(stamped["spec"]["version"], "9.9.9")
            self.assertEqual(served_spec(json.dumps(stamped), stamp["sha"])[1], "")

    def test_a_failed_write_leaves_no_spec_rather_than_the_last_builds(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(Path(d), with_cli=False)
            stale = repo / "service" / "_service_spec.json"
            stale.write_text(json.dumps({"buildSha": "b" * 40, "spec": {}}), encoding="utf-8")
            self._stamp(repo)
            self.assertFalse(stale.exists(), "the previous build's spec survived a failed write")


class ServedSpecUnitTests(unittest.TestCase):
    def test_text_that_is_not_a_bound_object_is_refused(self):
        for text in ("not json", "[]", json.dumps({"buildSha": BUILD})):
            with self.subTest(text):
                spec, reason = served_spec(text, BUILD)
                self.assertIsNone(spec)
                self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main()
