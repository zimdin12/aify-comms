"""The hermes plugin passes its PluginContext to aify-wrapper's Herdr state plugin.

hermes-aify exports AIFY_HERDR_HERMES_PLUGIN when its Herdr pane is claimed or aify-env assigned
one. That file registers lifecycle hooks, and hermes only runs hooks for plugins listed in
`plugins.enabled`, so it has to ride on the plugin this repo already installs. These tests run the
thin loader exactly as install.sh writes it, in a child interpreter, because bootstrap.install()
changes sys.meta_path for the whole process.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO / "integrations" / "hermes-aify-plugin"

HERDR_PLUGIN = """
import json, os
def register(ctx):
    ctx.register_hook("pre_llm_call", "herdr-hook")
    with open(os.environ["RECORD"], "a") as f:
        f.write(json.dumps({"ctx": ctx.name}) + "\\n")
"""

DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("aify_thin_loader", sys.argv[1])
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
class Ctx:
    def __init__(self, name):
        self.name, self.hooks = name, []
    def register_hook(self, hook, callback):
        self.hooks.append(hook)
first, second = Ctx("first"), Ctx("second")
loader.register(first)
loader.register(second)
patched = any(type(f).__name__ == "_PatchFinder" for f in sys.meta_path)
print(json.dumps({"first": first.hooks, "second": second.hooks, "patched": patched}))
"""


def thin_loader() -> str:
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    match = re.search(r'cat > "\$plugin_dir/__init__.py" <<\'PYEOF\'\n(.*?)\nPYEOF\n', text, re.S)
    assert match, "install.sh no longer writes the hermes thin loader as a PYEOF heredoc"
    return match.group(1).replace("__AIFY_PLUGIN_PATH__", str(PLUGIN_ROOT))


class HermesPluginHandsHerdrItsContext(unittest.TestCase):
    def run_loader(self, herdr_plugin: str | None, gate: str = "1") -> tuple[dict, list]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "loader.py").write_text(thin_loader(), encoding="utf-8")
            (root / "driver.py").write_text(DRIVER, encoding="utf-8")
            record = root / "record.jsonl"
            env = {k: v for k, v in os.environ.items() if not k.startswith("AIFY_")}
            env.update({"AIFY_HERMES_PLUGIN": gate, "AIFY_HERMES_PLUGIN_PATH": str(PLUGIN_ROOT), "RECORD": str(record)})
            if herdr_plugin is not None:
                path = root / "herdr_state.py"
                if herdr_plugin:
                    path.write_text(herdr_plugin, encoding="utf-8")
                env["AIFY_HERDR_HERMES_PLUGIN"] = str(path)
            done = subprocess.run(
                [sys.executable, str(root / "driver.py"), str(root / "loader.py")],
                env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            calls = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
            return json.loads(done.stdout.strip().splitlines()[-1]), calls

    def test_every_context_the_loader_is_given_reaches_the_herdr_plugin(self) -> None:
        result, calls = self.run_loader(HERDR_PLUGIN)
        # Hermes keeps hooks per plugin manager, so a second context must be registered too, even
        # though the import patches are installed once per process.
        self.assertEqual(calls, [{"ctx": "first"}, {"ctx": "second"}])
        self.assertEqual(result["first"], ["pre_llm_call"])
        self.assertEqual(result["second"], ["pre_llm_call"])
        self.assertTrue(result["patched"])

    def test_no_herdr_plugin_named_registers_nothing(self) -> None:
        result, calls = self.run_loader(None)
        self.assertEqual(calls, [])
        self.assertEqual(result["first"], [])
        self.assertTrue(result["patched"])

    def test_a_missing_herdr_plugin_still_installs_the_patches(self) -> None:
        result, calls = self.run_loader("")
        self.assertEqual(calls, [])
        self.assertTrue(result["patched"], "a broken Herdr plugin path must not cost the gateway patches")

    def test_outside_hermes_aify_the_loader_does_nothing(self) -> None:
        result, calls = self.run_loader(HERDR_PLUGIN, gate="")
        self.assertEqual(calls, [])
        self.assertFalse(result["patched"])


if __name__ == "__main__":
    unittest.main()
