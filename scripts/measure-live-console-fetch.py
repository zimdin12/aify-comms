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

#: A viewer WIDER than any stored geometry, which is what separates the two snapshot branches.
#: `render_live_screen` returns the SCREEN's own cols whatever the viewer asked for, while the
#: replay arm renders the tail at max(source, viewer). So at 200 columns a replaying console
#: answers 200 and a live one answers its own width.
#:
#: A PURE READ: the GET handler performs only SELECTs and `_attach_terminal_snapshot`, with no
#: `resize_live_screen` or `feed_live_screen` call, which is what makes it safe to point at a
#: console somebody is watching. Its CONTROL is
#: `test_the_console_branch_discriminator_separates_both_branches.py` -- against the live fleet
#: this signal has only ever answered LIVE, and a verdict with one observed value is worth
#: nothing until the same signal is shown separating a screenless terminal from one with a screen.
WIDE_VIEWER = 200


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
    # "of N LIVE", not "of N total": `list_terminals` defaults to status=live, so this listing
    # is the live population and never the whole one -- the same default that made a
    # nine-terminal reading look like a bound on the screen cache.
    print(f"{len(live)} attached of {len(rows)} LIVE terminal(s), asked {ROUNDS}x each at "
          f"their own geometry")
    print()

    refusals: list[str] = []
    results = []
    for row in live:
        tid = str(row["id"])
        cols = int(row.get("cols") or 80)
        rows_n = int(row.get("rows") or 24)
        spans, snapshot, tail = [], None, None
        for _ in range(ROUNDS):
            # THE SAME VIEW AS THE BRANCH PROBE BELOW, so the two calls differ in exactly
            # one thing -- the viewer width. A default-view request timed against a
            # console-view one is two different responses compared as if they were one.
            ms, body = get(f"/terminals/{tid}?cols={cols}&rows={rows_n}&view=console")
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
            # THE FIELDS MUST BE THE TEXT QUANTITIES THIS TABLE PRINTS THEM AS. A list `output`
            # and a dict `snapshot` both have a len(), so both published a number that is not a
            # character count; a missing field published 0, which reads as an empty console rather
            # than as an absent one. Review put all three through the previous version.
            raw_snapshot = term.get("snapshot")
            raw_tail = term.get("output")
            if not isinstance(raw_snapshot, str) or not isinstance(raw_tail, str):
                refusals.append(
                    f"{tid}: snapshot is {type(raw_snapshot).__name__} and output is "
                    f"{type(raw_tail).__name__}; this table prints both as character counts")
                continue
            spans.append(ms)
            snapshot = len(raw_snapshot)
            tail = len(raw_tail)
        # WHICH BRANCH, asked once per terminal at a wider viewer than its own geometry.
        _, wide = get(f"/terminals/{tid}?cols={WIDE_VIEWER}&rows={rows_n}&view=console")
        widened = (wide.get("terminal") or {}).get("renderedCols")
        branch = ("replay" if widened == WIDE_VIEWER
                  else "live" if widened == cols else f"unclear({widened!r})")
        if len(spans) == ROUNDS:
            results.append((statistics.median(spans), tid, row.get("agentId"), cols, rows_n,
                            snapshot, tail, branch))

    if refusals:
        print("NOTHING IS PUBLISHED:")
        for line in dict.fromkeys(refusals):
            print(f"  - {line}")
        return 1
    if not results:
        print("UNKNOWN: no attached terminal answered cleanly, so nothing was measured.")
        return 2

    results.sort()
    print(f"  {'p50 ms':>8}  {'agent':22} {'asked':>9} {'STORED TAIL':>12} {'snapshot':>9}"
          f"  {'branch':>8}")
    for ms, _tid, agent, cols, rows_n, snapshot, tail, branch in results:
        print(f"  {ms:8.1f}  {str(agent)[:22]:22} {cols:4}x{rows_n:<4} {tail:>12,} {snapshot:>9,}"
              f"  {branch:>8}")

    times = [r[0] for r in results]
    tails = sorted(r[6] for r in results)
    branches = {}
    for r in results:
        branches[r[7]] = branches.get(r[7], 0) + 1
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
    print(f"BRANCHES: {branches} -- read from `renderedCols` at a {WIDE_VIEWER}-column viewer,")
    print("which is a direct signal rather than an inference from timing. The 51ms replay figure in")
    print("the projection section describes a path no console reported here is on; it is reached")
    print("when a terminal has no live screen -- after a service restart, for a plain-log runtime,")
    print("past 256 screens, or once one has been dropped.")
    print()
    print("A bimodal split in the TIMINGS would be weaker evidence of the same thing; a single mode")
    print("is NOT proof of one branch, because this times a full HTTP round trip rather than the")
    print("branch. The branch column above does not depend on that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
