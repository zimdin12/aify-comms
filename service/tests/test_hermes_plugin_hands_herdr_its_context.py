"""The hermes plugin passes its PluginContext to aify-wrapper's Herdr state plugin.

hermes-aify exports AIFY_HERDR_HERMES_PLUGIN when its Herdr pane is claimed or aify-env assigned
one. That file registers lifecycle hooks, and hermes only runs hooks for plugins listed in
`plugins.enabled`, so it has to ride on the plugin this repo already installs. These tests run the
thin loader exactly as install.sh writes it, in a child interpreter, because bootstrap.install()
changes sys.meta_path for the whole process.

hermes-aify names aify-wrapper's hermes-turn-start.py module in AIFY_TURN_START_HERMES_PLUGIN for an agent, and the
same loader hands it the context; without the variable that module is never imported.
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


# Records its own import, so a test can tell a module that was never loaded from one loaded and not registered.
TURN_START_PLUGIN = """
import json, os
with open(os.environ["RECORD"], "a") as f:
    f.write(json.dumps({"imported": "turn-start"}) + "\\n")
def register(ctx):
    ctx.register_hook("pre_llm_call", "turn-start-hook")
    with open(os.environ["RECORD"], "a") as f:
        f.write(json.dumps({"turn-start": ctx.name}) + "\\n")
"""

# hermes-aify names aify-wrapper's hermes-file-activity.py in AIFY_FILE_ACTIVITY_HERMES_PLUGIN when the operator
# opts a launch into file activity (dashboard #53); the loader treats it exactly like the turn-start module.
FILE_ACTIVITY_PLUGIN = TURN_START_PLUGIN.replace("turn-start", "file-activity").replace("pre_llm_call", "post_tool_call")


class HermesPluginHandsHerdrItsContext(unittest.TestCase):
    def run_loader(self, herdr_plugin: str | None, gate: str = "1", turn_start: str | None = None,
                   file_activity: str | None = None) -> tuple[dict, list]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "loader.py").write_text(thin_loader(), encoding="utf-8")
            (root / "driver.py").write_text(DRIVER, encoding="utf-8")
            record = root / "record.jsonl"
            env = {k: v for k, v in os.environ.items() if not k.startswith("AIFY_")}
            env.update({"AIFY_HERMES_PLUGIN": gate, "AIFY_HERMES_PLUGIN_PATH": str(PLUGIN_ROOT), "RECORD": str(record)})
            (root / "turn_start.py").write_text(TURN_START_PLUGIN, encoding="utf-8")
            if turn_start is not None:
                env["AIFY_TURN_START_HERMES_PLUGIN"] = turn_start.replace("@", str(root / "turn_start.py"))
            (root / "file_activity.py").write_text(FILE_ACTIVITY_PLUGIN, encoding="utf-8")
            if file_activity is not None:
                env["AIFY_FILE_ACTIVITY_HERMES_PLUGIN"] = file_activity.replace("@", str(root / "file_activity.py"))
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
        result, calls = self.run_loader(HERDR_PLUGIN, gate="", turn_start="@")
        self.assertEqual(calls, [])
        self.assertFalse(result["patched"])

    def test_the_turn_start_module_hermes_aify_names_is_imported_once_and_given_every_context(self) -> None:
        result, calls = self.run_loader(HERDR_PLUGIN, turn_start="@")
        self.assertEqual(calls, [{"ctx": "first"}, {"imported": "turn-start"}, {"turn-start": "first"},
                                 {"ctx": "second"}, {"turn-start": "second"}])
        self.assertEqual(result["first"], ["pre_llm_call", "pre_llm_call"])
        self.assertTrue(result["patched"])

    def test_no_turn_start_variable_never_imports_the_module(self) -> None:
        # The module is on disk beside the loader in every run; only the variable decides whether it is loaded.
        for unset in (None, "", "   "):
            with self.subTest(variable=unset):
                result, calls = self.run_loader(HERDR_PLUGIN, turn_start=unset)
                self.assertEqual(calls, [{"ctx": "first"}, {"ctx": "second"}])
                self.assertEqual(result["first"], ["pre_llm_call"])

    def test_a_missing_turn_start_module_costs_neither_the_patches_nor_the_herdr_hooks(self) -> None:
        result, calls = self.run_loader(HERDR_PLUGIN, turn_start="@.missing")
        self.assertEqual(calls, [{"ctx": "first"}, {"ctx": "second"}])
        self.assertTrue(result["patched"])

    def test_the_file_activity_module_hermes_aify_names_is_imported_once_and_given_every_context(self) -> None:
        result, calls = self.run_loader(HERDR_PLUGIN, file_activity="@")
        self.assertEqual(calls, [{"ctx": "first"}, {"imported": "file-activity"}, {"file-activity": "first"},
                                 {"ctx": "second"}, {"file-activity": "second"}])
        self.assertEqual(result["first"], ["pre_llm_call", "post_tool_call"])

    def test_no_file_activity_variable_never_imports_the_module(self) -> None:
        for unset in (None, "", "   "):
            with self.subTest(variable=unset):
                result, calls = self.run_loader(HERDR_PLUGIN, file_activity=unset)
                self.assertEqual(calls, [{"ctx": "first"}, {"ctx": "second"}])


if __name__ == "__main__":
    unittest.main()
