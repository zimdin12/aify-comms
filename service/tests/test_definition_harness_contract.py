"""The service uses the pinned Env export even when no Env checkout is present."""
import hashlib
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from service.api_core import vocabulary
from service.api_core.definition_schema import HARNESSES
from service.tests.test_agent_definition_push import ENV_REPOS


CONTRACT = vocabulary.CONTRACT_PATH.with_name("harness-facts-v1.json")


class DefinitionHarnessContract(unittest.TestCase):
    def test_pinned_export_drives_definition_admission_and_both_maps(self):
        raw = CONTRACT.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), vocabulary._HARNESS_FACTS_SHA256)
        export = json.loads(raw)
        self.assertEqual(export["schemaVersion"], 1)
        self.assertIs(export["harnesses"]["pi"]["definition"], False)
        admitted = {name: fact["runtime"] for name, fact in export["harnesses"].items()
                    if fact["definition"] is True}
        self.assertEqual(admitted["claude"], "claude-code")
        self.assertEqual(vocabulary.HARNESS_RUNTIME, admitted)
        self.assertEqual(vocabulary.RUNTIME_HARNESS, {runtime: name for name, runtime in admitted.items()})
        self.assertEqual(HARNESSES, tuple(admitted))
        self.assertNotIn("pi", HARNESSES)
        self.assertIn("pi", vocabulary.CANONICAL_RUNTIMES)
        self.assertEqual(vocabulary.RUNTIME_ALIASES["omp"], "pi")

    def test_vendored_export_matches_the_env_checkout(self):
        relative = Path("lib") / CONTRACT.name
        export = next((repo / relative for repo in ENV_REPOS if (repo / relative).is_file()), None)
        if export is None:
            self.skipTest("no aify-env checkout with the harness facts export; set AIFY_ENV_REPO")
        self.assertEqual(export.read_bytes(), CONTRACT.read_bytes(),
                         "env's harness facts moved; re-vendor and update _HARNESS_FACTS_SHA256")

    def test_startup_refuses_absent_and_changed_bytes(self):
        with TemporaryDirectory() as temp:
            service = Path(temp) / "service"
            api_core, contracts = service / "api_core", service / "contracts"
            api_core.mkdir(parents=True)
            contracts.mkdir()
            module = api_core / "vocabulary.py"
            shutil.copyfile(vocabulary.__file__, module)
            shutil.copyfile(vocabulary.CONTRACT_PATH, contracts / vocabulary.CONTRACT_PATH.name)
            target = contracts / CONTRACT.name

            def load():
                return subprocess.run([sys.executable, "-I", str(module)], capture_output=True,
                                      text=True, timeout=30, cwd=temp)

            missing = load()
            self.assertNotEqual(missing.returncode, 0, missing.stderr)
            self.assertIn("cannot read Env harness facts", missing.stderr)

            pristine = CONTRACT.read_bytes()
            target.write_bytes(pristine)
            accepted = load()
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            target.write_bytes(pristine + b"\n")
            changed = load()
            self.assertNotEqual(changed.returncode, 0, changed.stderr)
            self.assertIn("Env harness facts SHA-256 mismatch", changed.stderr)

    def test_rehashed_export_still_requires_supported_schema_and_unambiguous_projection(self):
        good = {"runtime": "claude-code", "definition": True}
        cases = (
            ({"schemaVersion": True, "harnesses": {"claude": good}}, "unsupported schema"),
            ({"schemaVersion": 2, "harnesses": {"claude": good}}, "unsupported schema"),
            ({"schemaVersion": 1, "harnesses": []}, "invalid harness table"),
            ({"schemaVersion": 1, "harnesses": {"claude": {**good, "definition": 1}}}, "invalid harness row"),
            ({"schemaVersion": 1, "harnesses": {"claude": {**good, "runtime": ""}}}, "invalid harness row"),
            ({"schemaVersion": 1, "harnesses": {"claude": good, "also-claude": good}}, "duplicate definition runtime"),
            ({"schemaVersion": 1, "harnesses": {"claude": {**good, "definition": False}}}, "no definition runtimes"),
        )
        with TemporaryDirectory() as temp:
            path = Path(temp) / CONTRACT.name
            raw = json.dumps({"schemaVersion": 1, "harnesses": {
                "other": {"runtime": "other-runtime", "definition": True},
                "claude": {**good, "definition": False},
            }}).encode("utf-8")
            path.write_bytes(raw)
            self.assertEqual(vocabulary._load_definition_harnesses(path, hashlib.sha256(raw).hexdigest()),
                             {"other": "other-runtime"})
            for data, reason in cases:
                with self.subTest(reason=reason):
                    raw = json.dumps(data).encode("utf-8")
                    path.write_bytes(raw)
                    with self.assertRaisesRegex(RuntimeError, reason):
                        vocabulary._load_definition_harnesses(path, hashlib.sha256(raw).hexdigest())
