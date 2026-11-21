"""The host must recognise the exact FREE_SINCE reason and publish a fresh revision (P0 C7)."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from service.api_core.definition_push import FREE_SINCE
from service.tests.test_agent_definition_push import ENV_REPOS

SYNC_MODULE = Path("lib") / "plugins" / "aify-comms" / "definition-sync.mjs"


class TheHostAndServiceShareTheDefinitionVocabulary(unittest.TestCase):
    def test_the_host_recognises_the_services_free_since_reason(self):
        repo = next((candidate for candidate in ENV_REPOS if (candidate / SYNC_MODULE).is_file()), None)
        if repo is None:
            self.skipTest("no aify-env checkout with the definition sync; set AIFY_ENV_REPO")
        if shutil.which("node") is None:
            self.skipTest("node is not on PATH")
        script = "console.log(JSON.stringify((await import(process.argv[1])).FREE_SINCE));"
        run = subprocess.run(["node", "--input-type=module", "-e", script, (repo / SYNC_MODULE).as_uri()],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), FREE_SINCE)
