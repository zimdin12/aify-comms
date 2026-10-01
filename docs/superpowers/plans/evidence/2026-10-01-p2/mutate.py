"""Apply one mutation at a time to a source file, run a test command, restore the bytes from a copy.

usage: python mutate.py <repo> <mutations.json>
mutations.json: [{"file": "lib/x.mjs", "old": "...", "new": "...", "test": "node --test tests/x.test.js", "name": "..."}]
Each mutation must match its file exactly once (an unmatched mutation is reported, never skipped silently).
A mutation SURVIVES when its test command exits 0.
"""
import json
import os
import shutil
import subprocess
import sys

repo, spec = sys.argv[1], sys.argv[2]
mutations = json.load(open(spec, encoding="utf-8"))
#: The test sets a mutation names (P1's, and P2's C8 set). ALL is every agent-definition test plus the command's and the gate's.
ALIASES = {
    "P2": "node --test tests/plugins-follow-the-registry.test.js tests/aify-comms-plugin.test.js "
          "tests/service-plugins.test.js tests/plugin-bootstrap.test.js tests/production-picker-bootstrap.test.js "
          "tests/aify-comms-claim.test.js tests/the-doctor-says-whether-this-host-claims.test.js",
    "SCHEMA": "node --test tests/agent-definition-schema.test.js",
    "ALL": "node --test tests/agent-definition-schema.test.js tests/agent-definition-recovery.test.js "
           "tests/agent-definition-store.test.js tests/agent-definition-crash.test.js "
           "tests/aify-env-agents-command.test.js tests/agent-definitions-have-one-writer.test.js "
           "tests/agent-definition-review-regressions.test.js",
}
for m in mutations:
    m["test"] = ALIASES.get(m["test"], m["test"])
results = []
for m in mutations:
    path = f"{repo}/{m['file']}"
    backup = path + ".mutation-backup"
    shutil.copyfile(path, backup)
    try:
        src = open(path, encoding="utf-8", newline="").read()
        count = src.count(m["old"])
        if count != 1:
            results.append((m["name"], f"NOT APPLIED (matched {count} times)", []))
            continue
        open(path, "w", encoding="utf-8", newline="").write(src.replace(m["old"], m["new"]))
        try:
            run = subprocess.run(m["test"], shell=True, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        except subprocess.TimeoutExpired:
            # A hang is not a pass, but it is not a named kill either: reported apart, never counted killed.
            results.append((m["name"], "HUNG (no verdict in 300 s)", []))
            continue
        # WHICH TESTS killed it, not only that something did: the verdict alone cannot show the
        # mutant died for the reason its name claims.
        failed = [line for line in run.stdout.splitlines() if line.startswith("not ok ")]
        results.append((m["name"], "SURVIVED" if run.returncode == 0 else f"killed (exit {run.returncode})", failed))
    finally:
        shutil.copyfile(backup, path)
        os.remove(backup)
for name, verdict, failed in results:
    print(f"{verdict:32} {name}")
    for line in failed:
        print(f"{'':34}{line}")
bad = [r for r in results if not r[1].startswith("killed")]
print(f"{len(results) - len(bad)}/{len(results)} killed")
sys.exit(1 if bad else 0)
