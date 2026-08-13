"""Which top-level functions in a JS file reference NOTHING from module scope.

THE CRITERION, STATED, because three earlier attempts at this number each used a different one and
none said so:

  a function is FREE if, after stripping string literals and comments, no identifier in its body is a
  module-level name declared in the same file, and it touches none of `state`, `document`, `window`,
  `localStorage`, `fetch`.

Earlier versions counted only CALL targets — `\\bname\\s*\\(` — which misses three real dependencies:

    codexConsoleConnections.get(id)     a variable READ, never called
    setTimeout(refresh, 250)            a function passed as a VALUE
    `${apiBase}${path}`                 a read inside a template literal

That undercount made `api`, `refreshSoon`, `renderSection`, `evaluateFlowGates` and
`codexConsoleClose` look movable when each holds module-scope state. Counting every identifier is
crude and OVER-inclusive, which is the safe direction: it can only refuse a function that was in fact
movable, never approve one that is not.

Usage:  python js_free_fns.py <file.js>
"""
import io
import re
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8", newline="").read()
lines = src.split("\n")

MOD_DECL = (
    re.compile(r"^(?:export )?(?:async )?function ([A-Za-z_$][\w$]*)"),
    re.compile(r"^(?:export )?(?:const|let|var) ([A-Za-z_$][\w$]*)"),
)
mod = set()
for line in lines:
    for pat in MOD_DECL:
        m = pat.match(line)
        if m:
            mod.add(m.group(1))
            break

fn_head = re.compile(r"^(?:export )?(?:async )?function ([A-Za-z_$][\w$]*)\s*\(")
fns = {}
for i, line in enumerate(lines):
    m = fn_head.match(line)
    if not m:
        continue
    depth, started = 0, False
    for j in range(i, len(lines)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        if started and depth == 0:
            break
    fns[m.group(1)] = (i, j)

TEMPLATE = re.compile(r"`(?:[^`\\]|\\.)*`")
SINGLE = re.compile(r"'(?:[^'\\]|\\.)*'")
DOUBLE = re.compile(r'"(?:[^"\\]|\\.)*"')
LINE_COMMENT = re.compile(r"//[^\n]*")
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
BROWSER = {"state", "document", "window", "localStorage", "fetch"}


def outside_refs(name):
    i, j = fns[name]
    body = "\n".join(lines[i:j + 1])
    # A template literal can contain ${...} with real references, so keep those and drop the rest.
    # COMMENTS FIRST. Stripping string literals before comments treats an apostrophe in prose --
    # "can't", "agent's" -- as an opening quote, and the match then runs to the next real quote,
    # swallowing the code between them. That is what made `renderAnalyticsPage` (which plainly uses
    # `state` and `byId`) read as free: the comment on its second line begins a phantom string.
    body = LINE_COMMENT.sub("", body)
    body = TEMPLATE.sub(lambda m: " ".join(re.findall(r"\$\{([^}]*)\}", m.group(0))), body)
    body = SINGLE.sub("''", body)
    body = DOUBLE.sub('""', body)
    idents = set(IDENT.findall(body)) - {name}
    return (idents & mod) | (idents & BROWSER)


free, blocked = [], []
for name in fns:
    refs = outside_refs(name)
    span = fns[name][1] - fns[name][0] + 1
    (free if not refs else blocked).append((name, span, sorted(refs)[:4]))

free.sort(key=lambda x: -x[1])
total = sum(f[1] for f in free)
print("%s: %d lines, %d top-level functions" % (path, len(lines), len(fns)))
print("FREE (no module-scope reference at all): %d functions, %d lines  ->  %d"
      % (len(free), total, len(lines) - total))
for name, span, _ in free:
    print("   %4d  %s" % (span, name))
