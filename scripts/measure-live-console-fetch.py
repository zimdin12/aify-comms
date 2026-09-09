"""Which branch do the operator's REAL consoles take -- the 6.6ms live one or the 51ms replay?

THE PROJECTION MEASUREMENT IS A CONTRAST BETWEEN TWO SEEDED FIXTURES. It says a console with live
state answers about eight times faster than one without. It does not say which of those a real
console on this fleet is, and that is the whole difference between an interesting number and a
relevant one.

READ-ONLY, AND AT EACH TERMINAL'S OWN GEOMETRY. Requesting an unusual `cols` would have been a
cleaner discriminator -- the replay branch renders at max(source, viewer) while the live branch
returns the screen's own width -- and it is NOT used here: the risk of perturbing a live console the
operator is watching is not worth a cleaner signal. Each terminal is asked at the size it already
reports, which is exactly what opening its console in the dashboard does.

THE DISCRIMINATOR IS THEREFORE TIME, and it is weaker: a bimodal split is evidence of two branches,
a single mode is not proof of one. Stated rather than glossed.

WHAT IT FOUND, 2026-09-09, nine attached terminals on build `3e7387a6`: 17.2ms to 40.9ms, a 2.4x
spread with NO bimodality, and snapshots of 2.6 to 6.7 KB. That last figure is the one that
recalibrates the projection section: its 51ms replay arm seeds the service's MAXIMUM 64 KB tail,
and no real console here is within an order of magnitude of that. The fixture contrast is a bound,
and the fleet sits well inside it.

    python scripts/measure-live-console-fetch.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
import time
import urllib.request

ROOT = "C:/Docker/aify-comms"
BASE = "http://localhost:8800/api/v1"
ROUNDS = 3


def key() -> str:
    """The resolver prints its warning to STDERR and the key to STDOUT -- but only when run with a
    cwd it can find its own repo from. Run it FROM the repo root, and fail loudly rather than
    proceeding keyless, since an unauthenticated read would 401 and look like an empty fleet."""
    out = subprocess.run(["bash", "scripts/api-key.sh"], cwd=ROOT, capture_output=True, text=True)
    lines = [l.strip() for l in out.stdout.strip().splitlines() if l.strip()]
    if not lines:
        raise SystemExit(f"no key resolved (rc={out.returncode}): {out.stderr.strip()[:200]}")
    return lines[-1]


K = key()


def get(path: str):
    request = urllib.request.Request(f"{BASE}{path}", headers={"X-API-Key": K})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return (time.perf_counter() - started) * 1000, json.loads(body)


_, listing = get("/terminals?limit=500")
rows = listing.get("terminals") or listing.get("items") or []
live = [r for r in rows if str(r.get("status")) == "attached"]
print(f"{len(live)} attached terminal(s), asked {ROUNDS}x each at their own geometry")
print()

results = []
for row in live:
    tid = str(row["id"])
    cols = int(row.get("cols") or 80)
    rows_n = int(row.get("rows") or 24)
    spans = []
    rendered = None
    for _ in range(ROUNDS):
        ms, body = get(f"/terminals/{tid}?cols={cols}&rows={rows_n}&view=console")
        spans.append(ms)
        term = body.get("terminal") or {}
        rendered = (term.get("renderedCols"), term.get("renderedRows"), len(term.get("snapshot") or ""))
    results.append((statistics.median(spans), tid, row.get("agentId"), cols, rows_n, rendered))

results.sort()
print(f"  {'p50 ms':>8}  {'agent':22} {'asked':>9}  {'rendered':>12}  snapshot")
for ms, tid, agent, cols, rows_n, rendered in results:
    rc, rr, size = rendered
    print(f"  {ms:8.1f}  {str(agent)[:22]:22} {cols:4}x{rows_n:<4}  {str(rc):>5}x{str(rr):<5}  {size} chars")

if results:
    times = [r[0] for r in results]
    print()
    print(f"  fastest {min(times):.1f}ms   slowest {max(times):.1f}ms   "
          f"spread {max(times) / max(0.001, min(times)):.1f}x")
    print()
    print("A bimodal split here is evidence of the two branches; a single mode is NOT proof of one,")
    print("because this measures TIME through a full HTTP round trip and not the branch itself.")
