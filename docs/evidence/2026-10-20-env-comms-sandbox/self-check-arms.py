"""Mutation arms for env-against-comms.mjs --self-check: each arm breaks one guarded behaviour and must turn it red.
Restores by byte copy and checks it. Run from the repo root: python docs/evidence/2026-10-20-env-comms-sandbox/self-check-arms.py"""
import pathlib, subprocess
p = pathlib.Path('scripts/sandbox/env-against-comms.mjs'); orig = p.read_bytes(); t = orig.decode()
arms = [
 ("redact is identity", 'const redact = (text) => String(text).split(KEY).join("<redacted key>");', 'const redact = (text) => String(text);'),
 ("outcome ignores cleanup", 'exit: primary.ok && clean.ok ? 0 : 1,', 'exit: primary.ok ? 0 : 1,'),
 ("unknown read as absent", 'return r.status !== null && /no such|not found/i.test(r.out) ? "absent" : "unknown";', 'return "absent";'),
 ("existing name adopted", '  if (before !== "absent") throw new Error(`refusing to create ${kind} ${name}: it is ${before}, so it is not this run\'s`);\n', ''),
 ("generation not checked", '&& a.generation === b.generation && a.publication > b.publication', '&& a.publication > b.publication'),
 ("reason not checked", 'a.kind === "retired" && a.reason === "shutdown"', 'a.kind === "retired"'),
]
base = subprocess.run(['node', str(p), '--self-check'], capture_output=True, text=True, timeout=120)
print(f"BASELINE exit={base.returncode}")
for name, old, new in arms:
    assert t.count(old) == 1, name
    p.write_bytes(t.replace(old, new).encode())
    try:
        r = subprocess.run(['node', str(p), '--self-check'], capture_output=True, text=True, timeout=120)
    finally:
        p.write_bytes(orig)
    fails = [l for l in r.stdout.splitlines() if l.startswith('FAIL')]
    print(f"{'RED  ' if r.returncode else 'GREEN'} {name} | restored={p.read_bytes() == orig} | {fails}")
