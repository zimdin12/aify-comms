"""What does a REAL console on this fleet cost to fetch, and how big is the tail behind it?

WHY. The projection probe contrasts two seeded fixtures -- a maximum-tail replay against a live
screen -- and never asked which of them resembles a console an operator actually opens.

WHAT THIS CORRECTED, and it inverted the answer. An earlier version of this script reported real
snapshots of 2.6-6.7 KB "against a 64 KB fixture" and concluded the fixture was a bound nothing on
the fleet came near. THOSE ARE DIFFERENT QUANTITIES: 64 KB is the STORED TAIL the replay renders
FROM, and the snapshot is what the response CARRIES. The fixture's own snapshot is 3,269 characters
-- squarely inside the real range -- so the comparison established nothing at all. Measured properly,
the real STORED TAILS are 49,623 to 65,536 characters with the MEDIAN AT THE CAP: five of nine live
consoles sit exactly on 65,536. The fixture is the typical case here, not a pessimistic one.

WHAT IT REFUSES. A response about a different terminal, a response with no terminal object, and a
span that is not a positive finite duration are all refused rather than averaged into the table --
review published all three against the previous version. Timing alone also never names a BRANCH:
a bimodal split would be evidence of two, a single mode is not proof of one, and this times a full
HTTP round trip to a container rather than the branch itself.

READ-ONLY, AND AT EACH TERMINAL'S OWN GEOMETRY. Asking with an unusual `cols` would separate the
branches outright -- the replay path renders at max(source, viewer) while the live path returns the
screen's own width -- and it is refused here: these are consoles the operator may be watching and a
measurement is not worth perturbing them. The GET path was checked for `resize_live_screen` and
`feed_live_screen` calls first; it makes neither.

    python scripts/measure-live-console-fetch.py
"""
from __future__ import annotations

import json
import math
import statistics
import subprocess
import time
import urllib.request

ROOT = "C:/Docker/aify-comms"
BASE = "http://localhost:8800/api/v1"
ROUNDS = 3


def key() -> str:
    """The key the SERVICE is configured with, from the resolver that owns that question."""
    out = subprocess.run(["bash", "scripts/api-key.sh"], cwd=ROOT, capture_output=True, text=True)
    lines = [l.strip() for l in out.stdout.strip().splitlines() if l.strip()]
    if not lines:
        raise SystemExit(f"no key resolved (rc={out.returncode}): {out.stderr.strip()[:200]}")
    return lines[-1]


K = key()


def get(path: str):
    request = urllib.request.Request(f"{BASE}{path}", headers={"X-API-Key": K})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
    return (time.perf_counter() - started) * 1000, json.loads(body)


def main() -> int:
    _, listing = get("/terminals?limit=500")
    rows = listing.get("terminals") or listing.get("items") or []
    live = [r for r in rows if str(r.get("status")) == "attached"]
    print(f"{len(live)} attached terminal(s) of {len(rows)} total, asked {ROUNDS}x each at their "
          f"own geometry")
    print()

    refusals: list[str] = []
    results = []
    for row in live:
        tid = str(row["id"])
        cols = int(row.get("cols") or 80)
        rows_n = int(row.get("rows") or 24)
        spans, snapshot, tail = [], None, None
        for _ in range(ROUNDS):
            ms, body = get(f"/terminals/{tid}?cols={cols}&rows={rows_n}")
            # A SPAN THAT IS NOT A DURATION IS NOT A MEASUREMENT. Written positively because NaN
            # fails every comparison, so "reject the negatives" would admit it.
            if not (math.isfinite(ms) and ms > 0.0):
                refusals.append(f"{tid}: a span of {ms!r} is not a positive finite duration")
                continue
            term = body.get("terminal")
            # AND AN ABSENT TERMINAL OBJECT IS NOT AN EMPTY ONE. `body.get("terminal") or {}` made a
            # response carrying no terminal at all indistinguishable from one carrying an empty
            # console, and both were averaged into the table.
            if not isinstance(term, dict):
                refusals.append(f"{tid}: the response carries no terminal object "
                                f"({type(term).__name__})")
                continue
            if str(term.get("id")) != tid:
                refusals.append(f"{tid}: answered about {term.get('id')!r} instead")
                continue
            spans.append(ms)
            snapshot = len(term.get("snapshot") or "")
            tail = len(term.get("output") or "")
        if len(spans) == ROUNDS:
            results.append((statistics.median(spans), tid, row.get("agentId"), cols, rows_n,
                            snapshot, tail))

    if refusals:
        print("NOTHING IS PUBLISHED:")
        for line in dict.fromkeys(refusals):
            print(f"  - {line}")
        return 1
    if not results:
        print("UNKNOWN: no attached terminal answered cleanly, so nothing was measured.")
        return 2

    results.sort()
    print(f"  {'p50 ms':>8}  {'agent':22} {'asked':>9} {'STORED TAIL':>12} {'snapshot':>9}")
    for ms, _tid, agent, cols, rows_n, snapshot, tail in results:
        print(f"  {ms:8.1f}  {str(agent)[:22]:22} {cols:4}x{rows_n:<4} {tail:>12,} {snapshot:>9,}")

    times = [r[0] for r in results]
    tails = sorted(r[6] for r in results)
    print()
    print(f"  fetch  fastest {min(times):.1f}ms  slowest {max(times):.1f}ms  "
          f"spread {max(times) / max(0.001, min(times)):.1f}x")
    print(f"  TAILS  min {tails[0]:,}  median {tails[len(tails) // 2]:,}  max {tails[-1]:,}")
    print()
    print("THE TAIL IS THE QUANTITY THE REPLAY PATH RENDERS FROM, and it is what makes the")
    print("projection fixture representative or not. The SNAPSHOT is what the response carries and")
    print("bounds neither retained history nor replay cost -- comparing one against the other is the")
    print("error this script was corrected for.")
    print()
    print("A bimodal split in the timings would be evidence of the two branches; a single mode is")
    print("NOT proof of one, because this times a full HTTP round trip rather than the branch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
