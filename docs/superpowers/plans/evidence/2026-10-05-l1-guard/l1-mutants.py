#!/usr/bin/env python3
"""Each mutant undoes one decision of the L1 guard's second version; the guard test file must go red, at the
assertion named. Runs in a throwaway copy (~/wrapper-l1 in WSL); every file is restored from its saved bytes."""
import os, re, subprocess, sys

ROOT = os.path.expanduser("~/wrapper-l1")
TEST = "tests/a-launcher-takes-defaults-from-its-definition.test.js"

def read(p):
    with open(os.path.join(ROOT, p), newline="") as f:
        return f.read()

def write(p, s):
    with open(os.path.join(ROOT, p), "w", newline="") as f:
        f.write(s)

HERMES_BLOCK = ('if [ "$HERMES_EXPLICIT_SESSION_HANDLE" = "false" ] && [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ] && '
                '[ -n "$HERMES_INHERITED_SESSION_HANDLE" ]; then\n  HERMES_SESSION_HANDLE="$HERMES_INHERITED_SESSION_HANDLE"\n'
                '  HERMES_EXPLICIT_SESSION_HANDLE="true"\nfi\n')
CODEX_BLOCK_HEAD = 'if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]; then\n  # The app-server owns the managed TUI;'

def m_marker(files):
    p = "wrappers/claude-aify.sh.in"
    s = files[p]
    old = 'if ! { [ -n "${AIFY_AGENT_ID:-}" ] && [ "$CLAUDE_AIFY_AGENT_ID" = "$AIFY_AGENT_ID" ]; }; then'
    assert s.count(old) == 1
    return {p: s.replace(old, 'if ! { [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ] && [ -n "${AIFY_AGENT_ID:-}" ] && [ "$CLAUDE_AIFY_AGENT_ID" = "$AIFY_AGENT_ID" ]; }; then')}

def m_hermes_above(files):
    p = "wrappers/hermes-aify.sh.in"
    s = files[p]
    assert s.count(HERMES_BLOCK) == 1
    s = s.replace(HERMES_BLOCK, "")
    anchor = 'HERMES_EXPLICIT_SESSION_HANDLE="false"\n'
    assert s.count(anchor) == 1
    # Above the guard, and before the argument loop, as b7e60a7 had it.
    return {p: s.replace(anchor, anchor + HERMES_BLOCK.replace('[ "$HERMES_EXPLICIT_SESSION_HANDLE" = "false" ] && ', ""))}

def m_codex_above(files):
    p = "wrappers/codex-aify.sh.in"
    s = files[p]
    start = s.index(CODEX_BLOCK_HEAD)
    end = s.index("fi\n", start) + 3
    block = s[start:end]
    s = s[:start] + s[end:]
    anchor = "CODEX_PERMISSION_FLAGS=()\n"
    assert s.count(anchor) == 1
    return {p: s.replace(anchor, anchor + block)}

def m_fixed_list(files):
    p = "wrappers/claude-aify.sh.in"
    s = files[p]
    old = '  for _aify_managed_name in ${!AIFY_MANAGED_@}; do unset "$_aify_managed_name"; done'
    assert s.count(old) == 1
    return {p: s.replace(old, "  unset AIFY_MANAGED_VIA_WRAPPER AIFY_MANAGED_MODEL AIFY_MANAGED_EFFORT AIFY_MANAGED_SESSION")}


def m_guard_only_on_marker(files):
    # The previous commit's rule: the guard acts only when the marker is 1 (re-review of the guard's second version).
    p = "wrappers/claude-aify.sh.in"
    s = files[p]
    old = 'if ! { [ -n "${AIFY_AGENT_ID:-}" ] && [ "$CLAUDE_AIFY_AGENT_ID" = "$AIFY_AGENT_ID" ]; }; then'
    assert s.count(old) == 1
    return {p: s.replace(old, 'if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ] && ! { [ -n "${AIFY_AGENT_ID:-}" ] && [ "$CLAUDE_AIFY_AGENT_ID" = "$AIFY_AGENT_ID" ]; }; then')}

def m_inherited_beats_command_line(files):
    p = "wrappers/hermes-aify.sh.in"
    s = files[p]
    old = 'if [ "$HERMES_EXPLICIT_SESSION_HANDLE" = "false" ] && [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]'
    assert s.count(old) == 1
    return {p: s.replace(old, 'if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]')}

MUTANTS = [
    ("M1 the marker is part of the guard again (claude)", m_marker, r"CLAUDE: an empty, absent or other agent"),
    ("M2 hermes reads the inherited session above the guard", m_hermes_above, r"HERMES: a nested launch does not resume"),
    ("M3 codex adds --disable apps above the guard", m_codex_above, r"CODEX: a nested launch does not take"),
    ("M4 a fixed list instead of the prefix (claude)", m_fixed_list, r"CLAUDE: a repeated --aify-agent"),
    ("M5 the guard acts only on marker 1 (claude)", m_guard_only_on_marker, r"CLAUDE: an empty, absent or other agent"),
    ("M6 the inherited hermes handle beats --resume", m_inherited_beats_command_line, r"HERMES: a nested launch does not resume"),
]

survivors = []
names = ["wrappers/claude-aify.sh.in", "wrappers/codex-aify.sh.in", "wrappers/hermes-aify.sh.in"]
good = {p: read(p) for p in names}
try:
    for label, mutate, expected in MUTANTS:
        changed = mutate(dict(good))
        for p, s in changed.items():
            write(p, s)
        r = subprocess.run(["node", "--test", TEST], cwd=ROOT, capture_output=True, text=True)
        fails = [l for l in r.stdout.splitlines() if l.startswith("not ok")]
        hit = any(re.search(expected, l) for l in fails)
        if not hit or r.returncode == 0:
            survivors.append(label)
        print(f"{label}: {'KILLED at the named test' if hit else 'SURVIVED' if not fails else 'RED elsewhere only'} exit={r.returncode}")
        for l in fails:
            print("    " + l)
        for p in changed:
            write(p, good[p])
finally:
    for p, s in good.items():
        write(p, s)
r = subprocess.run(["node", "--test", TEST], cwd=ROOT, capture_output=True, text=True)
print("restored:", [l for l in r.stdout.splitlines() if l.startswith(("# pass", "# fail"))])

if survivors or r.returncode != 0:
    print("FAILED survivors:", survivors)
    sys.exit(1)
print("all mutants killed at their named test")
