#!/usr/bin/env python3
"""Reproducible team-comms baseline — the guardrail for any change to comms guidance.

WHY THIS IS A CHECKED-IN SCRIPT AND NOT AN AD-HOC QUERY
-------------------------------------------------------
Because the ad-hoc version was wrong, and silently. The first baseline (2026-08-09)
reported a 25.4% thread-closure rate. `comms-senior-dev`, running its own heuristic on the
same DB, got 57.7% — and, applying MY OWN regex, got 55.1%. So the gap was never
vocabulary; it was implementation. The bug: the query had no `ORDER BY timestamp`, so
`thread[-1]` was not the thread's LAST message, just the last row SQLite happened to
return. Closure was being tested against an arbitrary middle message.

That number was about to become the guardrail for judging whether a change to team
guidance helped or hurt. A broken guardrail is worse than none: it produces a confident
before/after comparison out of noise. Hence: one script, checked in, both parties run it,
same numbers or we stop.

CLOSURE DEFINITION (agreed with comms-senior-dev, 2026-08-09)
-------------------------------------------------------------
A thread is CLOSED when its LAST message carries an explicit terminal label. Bare "ACK",
"agree" and "sent" are deliberately NOT terminal — they are the ambiguous tokens that
inflated the exploratory heuristic to ~57%. "Closed" must mean a decision was recorded,
not that someone acknowledged a message.

Threads that are neither closed nor recently active are reported separately as
`silent_unclosed` rather than folded into either bucket — a thread that simply stopped is
a different outcome from one that concluded, and merging them hides exactly the decay this
baseline exists to detect.

WINDOW ANCHORING
----------------
The window is anchored to MAX(messages.timestamp), not to `now()`. Anchoring to wall-clock
makes the "same" baseline drift every time it runs, which is how two people comparing
notes end up arguing about different populations.

Usage:
    docker exec aify-comms-service python /tmp/comms_baseline.py [--days 14] [--json]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3

DEFAULT_DB = "/data/aify.db"

# Explicit terminal labels only. Each asserts an OUTCOME. Deliberately excluded:
# ACK / agree / sent / ok / thanks — acknowledgement is not a decision, and treating it as
# one is what produced the 57% exploratory figure.
TERMINAL_LABELS = (
    "closed", "decision", "blocked", "withdrawn", "rework", "approved", "approve",
    "merged", "tagged", "rejected", "shipped", "resolved", "frozen", "locked",
)
TERMINAL_RE = re.compile(r"\b(" + "|".join(TERMINAL_LABELS) + r")\b", re.IGNORECASE)

# How much of the last message to inspect. Terminal labels are conventionally in the
# subject or the opening line; scanning the whole body would match a label quoted from
# earlier in the discussion and call an open thread closed.
BODY_SCAN_CHARS = 400

# A thread with no terminal label whose last message is older than this is not "in
# progress" — it stopped.
SILENT_AFTER_HOURS = 24


def build_threads(rows):
    """Group messages into reply-chains, rooted at the earliest ancestor in-window.

    `rows` MUST be ordered by timestamp ascending — see the module docstring for the bug
    this caused when it was not.
    """
    by_id = {r["id"]: r for r in rows}
    parent = {r["id"]: (r["in_reply_to"] or "") for r in rows}

    def root(mid):
        depth = 0
        while parent.get(mid) and parent[mid] in by_id and depth < 500:
            mid = parent[mid]
            depth += 1
        return mid

    threads = collections.defaultdict(list)
    for r in rows:
        threads[root(r["id"])].append(r)
    return threads


def is_closed(thread):
    last = thread[-1]  # safe ONLY because rows are timestamp-ordered
    haystack = f"{last['subject'] or ''} {(last['body'] or '')[:BODY_SCAN_CHARS]}"
    return bool(TERMINAL_RE.search(haystack))


def collect(db_path: str, days: int) -> dict:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    anchor = db.execute("SELECT MAX(timestamp) FROM messages").fetchone()[0]
    if not anchor:
        raise SystemExit("no messages in DB")
    since_ms = anchor - days * 86_400_000

    rows = list(db.execute(
        """
        SELECT id, in_reply_to, from_agent, to_agent, type, subject,
               COALESCE(body, '') AS body, LENGTH(COALESCE(body, '')) AS len, timestamp
        FROM messages
        WHERE timestamp > ?
        ORDER BY timestamp ASC
        """,
        (since_ms,),
    ))
    if not rows:
        raise SystemExit("no messages in window")

    threads = build_threads(rows)
    closed = silent = active = 0
    deep_total = deep_closed = 0
    for t in threads.values():
        deep = len(t) >= 6
        deep_total += 1 if deep else 0
        if is_closed(t):
            closed += 1
            deep_closed += 1 if deep else 0
        elif (anchor - t[-1]["timestamp"]) > SILENT_AFTER_HOURS * 3_600_000:
            silent += 1
        else:
            active += 1

    total_chars = sum(r["len"] for r in rows)
    by_type = collections.Counter()
    chars_by_type = collections.Counter()
    for r in rows:
        by_type[r["type"]] += 1
        chars_by_type[r["type"]] += r["len"]

    per_agent = collections.Counter()
    for r in rows:
        per_agent[r["from_agent"]] += r["len"]

    # Two-participant chains in long threads — the shape that carries most of the cost.
    pingpong = pingpong_chars = 0
    for t in threads.values():
        if len(t) >= 8 and len({m["from_agent"] for m in t}) <= 2:
            pingpong += 1
            pingpong_chars += sum(m["len"] for m in t)

    # Anchor the dispatch window to the SAME instant as the message window (reviewer catch,
    # 2026-08-09). This originally used datetime('now'), which meant the message half of the
    # baseline was reproducible and the dispatch half drifted with wall-clock — so two people
    # running the script minutes apart would agree on closure and disagree on dispatch, which
    # is precisely the class of confusion the anchor exists to remove.
    # messages.timestamp is epoch MILLISECONDS; dispatch_runs.requested_at is ISO.
    since_iso = db.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', ? / 1000, 'unixepoch')", (since_ms,)
    ).fetchone()[0]
    disp = dict(db.execute(
        """
        SELECT status, COUNT(*) FROM dispatch_runs
        WHERE requested_at > ?
        GROUP BY status
        """,
        (since_iso,),
    ).fetchall())
    db.close()

    return {
        "schema": 1,
        "window_days": days,
        "anchored_to_max_message_ms": anchor,
        "closure_definition": "explicit terminal label in last message (ACK/agree/sent excluded)",
        "terminal_labels": list(TERMINAL_LABELS),
        "messages": len(rows),
        "body_chars": total_chars,
        "approx_tokens": total_chars // 4,
        "threads": len(threads),
        "threads_closed": closed,
        "threads_silent_unclosed": silent,
        "threads_active": active,
        "closure_rate_pct": round(100 * closed / len(threads), 1),
        "threads_depth_ge6": deep_total,
        "depth_ge6_closed": deep_closed,
        "depth_ge6_closure_pct": round(100 * deep_closed / max(deep_total, 1), 1),
        "two_party_long_threads": pingpong,
        "two_party_long_thread_chars": pingpong_chars,
        "messages_by_type": dict(by_type),
        "chars_by_type": dict(chars_by_type),
        "response_share_pct": round(100 * chars_by_type.get("response", 0) / total_chars, 1),
        "avg_response_chars": round(
            chars_by_type.get("response", 0) / max(by_type.get("response", 1), 1)
        ),
        "top_agents_by_chars": dict(per_agent.most_common(8)),
        "dispatch_window_since_iso": since_iso,
        "dispatch_by_status": disp,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    print(json.dumps(collect(args.db, args.days), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
