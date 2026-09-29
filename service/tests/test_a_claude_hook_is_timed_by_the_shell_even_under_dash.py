"""A Claude hook's fired-at time is taken by the shell, before node starts, even when that shell is dash.

External review, 2026-09-29 (T1/S3). Claude Code runs hook commands with /bin/sh, which is dash on
Debian and Ubuntu, and dash has no `$EPOCHREALTIME`. The command passed `${EPOCHREALTIME:-}`, so under
dash the time was empty and agent-state-event.mjs fell back to when node started, 32-160 ms late: the
delay the ordering exists to remove, so a late Stop from one turn could still clear the next. The
command now falls back to GNU `date +%s.%N` in the shell, and the parser keeps its first six decimals.

Run for real: the command `agent_state_hook_command` renders is executed by dash (Git for Windows ships
/usr/bin/dash) with EPOCHREALTIME absent, against a stub script that records the time it was handed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", INSTALL_SH.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    assert match, f"install.sh no longer defines {name}; repoint this test"
    return match.group(0)


class AClaudeHookIsTimedByTheShellTests(unittest.TestCase):
    def test_no_hook_command_passes_an_empty_default_time(self):
        # Both Claude commands (the state hook and the stop gate) are built inline in install.sh.
        text = INSTALL_SH.read_text(encoding="utf-8")
        self.assertIn("EPOCHREALTIME", text, "CONTROL: install.sh no longer names the shell time at all")
        self.assertEqual(text.count('"${EPOCHREALTIME:-}"'), 0, "a hook command still passes an empty time under dash")

    def test_under_dash_the_rendered_hook_hands_node_the_shells_own_time(self):
        dash, bash = shutil.which("dash"), shutil.which("bash")
        if not dash or not bash:
            self.skipTest("dash and bash are both required: this test runs the command under a real dash")
        with tempfile.TemporaryDirectory() as bridge:
            record = Path(bridge) / "fired-at.txt"
            stub = Path(bridge) / "agent-state-event.mjs"
            stub.write_text(
                "import { writeFileSync } from 'node:fs';\n"
                "writeFileSync(process.env.RECORD, String(process.env.AIFY_HOOK_FIRED_AT ?? ''));\n",
                encoding="utf-8",
            )
            # The command as install.sh renders it, with the bridge dir pointed at the stub.
            posix_bridge = subprocess.run([bash, "-c", 'cygpath -u "$1" 2>/dev/null || printf %s "$1"', "_", bridge],
                                          capture_output=True, text=True).stdout.strip()
            rendered = subprocess.run(
                [bash, "-c", _function("agent_state_hook_command") + '\nagent_state_hook_command turn-start'],
                capture_output=True, text=True, env={**os.environ, "AIFY_BRIDGE_DIR": posix_bridge},
            )
            command = rendered.stdout
            self.assertIn("node", command, f"CONTROL: nothing was rendered: {rendered.stderr}")
            env = {k: v for k, v in os.environ.items() if k != "EPOCHREALTIME"}
            env.update(AIFY_AGENT_ID="dash-agent", AIFY_COMMS_URL="http://127.0.0.1:9", RECORD=str(record))
            before = time.time()
            subprocess.run([dash, "-c", command], env=env, timeout=30)
            after = time.time()
            self.assertTrue(record.exists(), "CONTROL: the stub never ran, so this test judged nothing")
            fired = record.read_text(encoding="utf-8").strip()
        self.assertRegex(fired, r"^\d+[.,]\d{6,9}$", f"under dash the hook handed node no time of its own: {fired!r}")
        seconds = float(fired.replace(",", "."))
        self.assertTrue(before - 1 <= seconds <= after + 1, f"{seconds} is not when the hook ran ({before}..{after})")
