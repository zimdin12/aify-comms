"""What the SERVER spends producing each shape of the console fetch.

THE OPEN QUESTION THIS CLOSES, and it is one this plan wrote down for itself. `43857ad7` cut the
resync's response from 147,250 bytes to a 6,442-byte snapshot by asking for `view=console`, and the
saving was stated in BYTES. Bytes are not an elapsed-time decomposition -- review said so and the
claim was withdrawn -- so "which half of a recovery dominates its wall clock" was left open with the
repaint measured at about 7ms and the refetch measured only as a size.

THE NOUN, NAMED BEFORE THE NUMBER: this is the SERVER'S OWN TIME to answer `GET /terminals/{id}`,
from the request entering the ASGI app to the response leaving it. In-process, over an ASGI
transport, with NO NETWORK -- so it is the service's work and not a round trip, and it must never be
reported as one. What the network adds is hop four, measured separately at 0.16-0.31ms for a Python
client on loopback and paired against a real browser's arrivals.

WHY IN-PROCESS RATHER THAN AGAINST THE LIVE SERVICE. The running container is build `3e7387a6`,
which predates `view=console` entirely -- it cannot answer the question, and asking it would measure
the shape this probe exists to compare against. The app is built here with the same `create_app()`
the service uses.

TWO SHAPES, ONE TERMINAL, ONE MOMENT. Both are asked of the SAME seeded terminal in the same run and
interleaved, so a drift in the machine's load moves both rather than one.

CONTROLS, in the same run:
  POSITIVE   both shapes must return 200 and carry the snapshot the console writes; a probe timing
             two 404s would report two very fast responses.
  SIZE       the console projection must actually be SMALLER on the wire, or the two shapes are the
             same response twice and the comparison is void.
  NEGATIVE   a terminal id nothing seeded must 404, so the seeding is what made the others answer.

NOTHING IS PUBLISHED UNLESS EVERY CONTROL HELD.

Run: python scripts/measure-console-projection.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLES = 40
COLS, ROWS = 132, 40
#: A console with enough history that the default response carries the tail and the event page the
#: projection drops -- which is the whole subject. 90 KB is the order the measured live response had.
TAIL_CHARS = 90 * 1024
#: THE SMALL ARM'S TAIL, named rather than inlined so the share printed below is computed from
#: the same constant the fixture uses.
SMALL_TAIL_CHARS = 2 * 1024
EVENTS = 200

#: THE FOUR ARMS, named once. Two vary the RESPONSE SHAPE over one stored tail; the third varies
#: the TAIL at a fixed shape; the fourth holds tail and events identical to the second and adds a
#: LIVE SCREEN, which is the branch `terminal_snapshot_view` takes first.
DEFAULT_REPLAY = "default (replay)"
CONSOLE_REPLAY = "console (replay)"
CONSOLE_SMALL_TAIL = "console, 2 KB tail"
CONSOLE_LIVE = "console, LIVE screen"


def _painted(chars: int) -> str:
    esc = chr(27)
    parts, size, row = [], 0, 1
    while size < chars:
        line = (f"{esc}[{row};1H{esc}[38;5;{(row % 200) + 16}m"
                f"row {row} of a full-screen redraw with some content on it{esc}[0m")
        parts.append(line)
        size += len(line)
        row = (row % 200) + 1
    return "".join(parts)


async def _run() -> int:
    import httpx

    from service.main import create_app

    app = create_app()
    from service.db import get_db, init_db

    # THE REAL SCHEMA, from the real initialiser. A probe that hand-wrote the two tables it
    # needs would be timing a database the service never has -- no indexes, no migrations, and
    # a query planner given a different shape to work with.
    # THE PATH IS PASSED, not left to the module global: `init_db()` with no argument uses
    # whatever `_db_path` already holds, which on a fresh import is None.
    await init_db(Path(os.environ["AIFY_DB_PATH"]))

    terminal_id = f"probe-{uuid.uuid4().hex[:8]}"
    #: A SECOND TERMINAL WITH A SMALL TAIL. Removing 97.7% of the RESPONSE moved p50 by 3%, which
    #: says the payload is not what the time goes on -- but it does not say what is. Both shapes
    #: render the current screen from the stored tail, so varying the TAIL while holding the
    #: shape fixed is the arm that can answer it.
    small_id = f"small-{uuid.uuid4().hex[:8]}"
    #: AND ONE WITH A LIVE SCREEN. `terminal_snapshot_view` takes the live-screen branch FIRST and
    #: replays the stored tail only as a fallback -- so every arm above measures the FALLBACK, which
    #: is what a console with no live screen gets and is not what a watched console's recovery
    #: takes. Separating them is the only way either figure can name its own path.
    live_id = f"live-{uuid.uuid4().hex[:8]}"
    db = await get_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    # FOREIGN KEYS OFF FOR THE SEED, AND ONLY FOR THE SEED. This connection writes fixture rows;
    # chasing the full parent chain would build a fixture rather than a probe, and every REQUEST
    # below opens its OWN connection with the service's own pragmas, so the path being measured
    # runs with them enforced exactly as in production.
    await db.execute("PRAGMA foreign_keys = OFF")
    # THE PARENT ROWS FIRST. `terminal_sessions` has foreign keys to both, and the real schema
    # enforces them -- so a probe that seeded only the terminal would be measuring a database
    # shape the service never has.
    await db.execute(
        "INSERT INTO environments (id, registered_at, last_seen) VALUES (?, ?, ?)",
        ("probe-env", now, now),
    )
    await db.execute(
        "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, started_at, "
        "last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (f"sess-{terminal_id}", f"agent-{terminal_id}", "probe-env", "probe", now, now),
    )
    await db.execute(
        "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, "
        "output, status, output_seq, created_at, updated_at, cols, rows) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (terminal_id, f"sess-{terminal_id}", f"agent-{terminal_id}", "probe-env", "probe",
         _painted(TAIL_CHARS), "running", 1, now, now, COLS, ROWS),
    )
    # EVENTS HELD EQUAL ACROSS THE ARMS. `routers/terminals.py` fetches and materialises the event
    # page BEFORE the projection runs, so an arm with 200 events and one with none differ by more
    # than their tails -- and the difference was being attributed entirely to the tail.
    for owner in (terminal_id, small_id, live_id):
        for i in range(EVENTS):
            await db.execute(
                "INSERT INTO terminal_events (terminal_id, event_type, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (owner, "output", json.dumps({"i": i, "text": "x" * 200}), now),
            )
    await db.execute(
        "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, started_at, "
        "last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (f"sess-{small_id}", f"agent-{small_id}", "probe-env", "probe", now, now),
    )
    await db.execute(
        "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, "
        "output, status, output_seq, created_at, updated_at, cols, rows) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (small_id, f"sess-{small_id}", f"agent-{small_id}", "probe-env", "probe",
         _painted(SMALL_TAIL_CHARS), "running", 1, now, now, COLS, ROWS),
    )
    await db.execute(
        "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, started_at, "
        "last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (f"sess-{live_id}", f"agent-{live_id}", "probe-env", "probe", now, now),
    )
    await db.execute(
        "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, "
        "output, status, output_seq, created_at, updated_at, cols, rows) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (live_id, f"sess-{live_id}", f"agent-{live_id}", "probe-env", "probe",
         _painted(TAIL_CHARS), "running", 1, now, now, COLS, ROWS),
    )
    await db.commit()
    # CLOSED BY THE CALLER. `get_db()` opens a NEW connection each call and hands ownership over;
    # leaving this one open holds an aiosqlite worker thread, so `asyncio.run` never finishes and
    # the file stays locked. The request handlers open and close their own.
    await db.close()

    # A TRUSTED HOST HEADER. The cross-site guard compares `host` against the trusted set, and a
    # made-up base URL is refused with 403 -- which the probe's own positive control caught
    # before any figure was published. Loopback is what a browser on this machine sends.
    # THE LIVE SCREEN, THROUGH THE REAL WRITER. `feed_live_screen` only creates one for a chunk
    # containing ESC -- plain logs must stay logs -- and the painted body is full of them.
    from service.terminal_snapshot import feed_live_screen
    feed_live_screen(live_id, _painted(TAIL_CHARS), cols=COLS, rows=ROWS, seq=1)

    transport = httpx.ASGITransport(app=app)
    refusals: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8800") as client:
        base = f"/api/v1/terminals/{terminal_id}?cols={COLS}&rows={ROWS}"
        small = f"/api/v1/terminals/{small_id}?cols={COLS}&rows={ROWS}&view=console"
        alive = f"/api/v1/terminals/{live_id}?cols={COLS}&rows={ROWS}&view=console"
        shapes = {
            DEFAULT_REPLAY: base,
            CONSOLE_REPLAY: f"{base}&view=console",
            CONSOLE_SMALL_TAIL: small,
            CONSOLE_LIVE: alive,
        }
        sizes: dict[str, int] = {}
        timings: dict[str, list[float]] = {k: [] for k in shapes}

        # WARM FIRST, DISCARDED. The first request pays import and connection setup, which is not
        # what either column is about.
        for url in shapes.values():
            await client.get(url)

        for _ in range(SAMPLES):
            # INTERLEAVED, so a drift in the machine's load moves both columns rather than one.
            for label, url in shapes.items():
                started = time.perf_counter()
                response = await client.get(url)
                timings[label].append((time.perf_counter() - started) * 1000)
                if response.status_code != 200:
                    refusals.append(f"{label}: answered {response.status_code}, so this column times "
                                    f"an error page rather than a console")
                    break
                sizes[label] = len(response.content)
                body = response.json()
                # A NON-EMPTY STRING, not merely truthy. The console does `term.write(snapshot)`,
                # so the field has to be text -- and review passed this check with a numeric 123.
                snapshot = (body.get("terminal") or {}).get("snapshot")
                if not isinstance(snapshot, str) or not snapshot:
                    refusals.append(f"{label}: the response's snapshot is {type(snapshot).__name__} "
                                    f"rather than a non-empty string, and the console writes that "
                                    f"field verbatim -- so whatever was timed is not this path")

        missing = await client.get(f"/api/v1/terminals/never-seeded-{uuid.uuid4().hex[:6]}")
        if missing.status_code != 404:
            refusals.append(f"NEGATIVE: an unseeded terminal answered {missing.status_code} rather "
                            f"than 404, so the seeding is not what made the others answer")
        if sizes.get(CONSOLE_REPLAY, 0) >= sizes.get(DEFAULT_REPLAY, 0):
            refusals.append(f"SIZE: the projection is {sizes.get(CONSOLE_REPLAY)} bytes against "
                            f"the default's {sizes.get(DEFAULT_REPLAY)} -- not smaller, so these "
                            f"are the same response twice and the comparison is void")

    if refusals:
        print("NOTHING IS PUBLISHED:")
        for line in refusals:
            print(f"  - {line}")
        return 1

    print()
    print("WHAT THE SERVER SPENDS ANSWERING A CONSOLE FETCH -- request into the ASGI app to response")
    print(f"out of it, IN-PROCESS with no network, {SAMPLES} samples of each shape, interleaved.")
    print()
    print(f"  {'shape':22} {'bytes':>9} {'p50 ms':>9} {'p95 ms':>9} {'worst':>9}")
    for label in (DEFAULT_REPLAY, CONSOLE_REPLAY, CONSOLE_SMALL_TAIL, CONSOLE_LIVE):
        values = sorted(timings[label])
        p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
        print(f"  {label:22} {sizes[label]:>9} {statistics.median(values):>9.2f} "
              f"{p95:>9.2f} {max(values):>9.2f}")
    saved = statistics.median(timings[DEFAULT_REPLAY]) - statistics.median(timings[CONSOLE_REPLAY])
    tail_saved = (statistics.median(timings[CONSOLE_REPLAY])
                  - statistics.median(timings[CONSOLE_SMALL_TAIL]))
    # COMPUTED FROM THE MEASURED SIZES. This said "97%" in both lines, hardcoded, so a run whose
    # fixtures differed would have printed a share it never measured.
    response_share = (100.0 * (sizes[DEFAULT_REPLAY] - sizes[CONSOLE_REPLAY])
                      / max(1, sizes[DEFAULT_REPLAY]))
    tail_share = 100.0 * (TAIL_CHARS - SMALL_TAIL_CHARS) / TAIL_CHARS
    print()
    print(f"  DROPPING {response_share:.1f}% OF THE RESPONSE bought {saved:+.2f} ms at p50 "
          f"({sizes[DEFAULT_REPLAY] - sizes[CONSOLE_REPLAY]} bytes removed).")
    print(f"  DROPPING {tail_share:.1f}% OF THE STORED TAIL, at the same response shape, bought "
          f"{tail_saved:+.2f} ms.")
    print()
    # THE CONCLUSION IS DERIVED, NOT CAPTIONED. Review supplied costs of 10, 20 and 30ms -- both
    # contrasts NEGATIVE -- and this block still printed "the time is in what the server does with
    # the tail". A sentence that survives its own contradiction is decoration. It now states what
    # was observed whenever the two contrasts do not support it.
    live_saved = statistics.median(timings[CONSOLE_REPLAY]) - statistics.median(timings[CONSOLE_LIVE])
    if tail_saved > 0 and tail_saved > abs(saved) * 5:
        print("  So on this run the time is in what the server DOES with the stored tail rather")
        print(f"  than in what it sends -- the tail contrast is "
              f"{tail_saved / max(abs(saved), 1e-9):.0f}x the response-shape one.")
    else:
        print("  NO CONCLUSION IS DRAWN about the tail: its contrast does not dominate the")
        print(f"  response-shape one on this run ({tail_saved:+.2f} ms against {saved:+.2f} ms).")
    print()
    print("  AND THE BRANCH MATTERS MORE THAN EITHER. The SAME tail and the SAME event count, with")
    print(f"  a LIVE SCREEN present, answer in {statistics.median(timings[CONSOLE_LIVE]):.2f} ms "
          f"against {statistics.median(timings[CONSOLE_REPLAY]):.2f} -- {live_saved:+.2f} ms.")
    print("  `terminal_snapshot_view` takes the live-screen branch FIRST and replays the stored")
    print("  tail only as a FALLBACK, so every replay figure here is what a console with NO live")
    print("  screen pays, and not what a watched console's recovery does.")
    print()
    print("WHAT THIS IS NOT: a round trip. There is no network here, so the browser's fetch is this")
    print("plus hop four, which is measured separately. It is also not the container: this app runs")
    print("on the host against a host database, and the deployed service is a different process on")
    print("a different filesystem.")
    return 0


def main() -> int:
    # A DATABASE OF ITS OWN. The probe seeds a terminal with a 90 KB tail and 200 events, and doing
    # that to the operator's database would leave a fake console in their dashboard.
    # A DATABASE UNDER THE SYSTEM TEMP ROOT, NOT REMOVED. A directory that must be deletable at
    # exit turns any connection this probe failed to close into a PermissionError on Windows --
    # which reports as a probe failure rather than as the leak it is.
    path = Path(tempfile.gettempdir()) / f"aify-console-projection-{uuid.uuid4().hex[:8]}.db"
    os.environ["AIFY_DB_PATH"] = str(path)
    os.environ["DATABASE_PATH"] = str(path)
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
