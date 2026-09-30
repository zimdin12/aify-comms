"""Apply one mutation at a time to a source file, run a test command, restore the bytes from a copy.

usage: python mutate.py <repo> <mutations.json>
mutations.json: [{"file": "lib/x.mjs", "old": "...", "new": "...", "test": "node --test tests/x.test.js", "name": "..."}]
Each mutation must match its file exactly once (an unmatched mutation is reported, never skipped silently).
A mutation SURVIVES when its test command exits 0.
"""
import json
import shutil
import subprocess
import sys

repo, spec = sys.argv[1], sys.argv[2]
mutations = json.load(open(spec, encoding="utf-8"))
#: The P1 test sets a mutation names. ALL is every agent-definition test plus the command's and the gate's.
ALIASES = {
    "SCHEMA": "node --test tests/agent-definition-schema.test.js",
    "ALL": "node --test tests/agent-definition-schema.test.js tests/agent-definition-recovery.test.js "
           "tests/agent-definition-store.test.js tests/agent-definition-crash.test.js "
           "tests/aify-env-agents-command.test.js tests/agent-definitions-have-one-writer.test.js",
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
            results.append((m["name"], f"NOT APPLIED (matched {count} times)"))
            continue
        open(path, "w", encoding="utf-8", newline="").write(src.replace(m["old"], m["new"]))
        run = subprocess.run(m["test"], shell=True, cwd=repo, capture_output=True, text=True, timeout=600)
        results.append((m["name"], "SURVIVED" if run.returncode == 0 else f"killed (exit {run.returncode})"))
    finally:
        shutil.copyfile(backup, path)
        import os
        os.remove(backup)
for name, verdict in results:
    print(f"{verdict:32} {name}")
bad = [r for r in results if not r[1].startswith("killed")]
print(f"{len(results) - len(bad)}/{len(results)} killed")
sys.exit(1 if bad else 0)
