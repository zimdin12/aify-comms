"""Which query inside `GET /stats` costs the 200 milliseconds, at the live fleet's scale.

WHY THIS EXISTS. `/stats` is on the dashboard's poll cycle -- every open tab asks for it every ~15
seconds -- and measured against the live service on 2026-09-08 it takes 191-223ms to return 2,857
bytes. That is 52% of the whole cycle's service time for 0.75% of its bytes, with `/health` at 1.2ms
in the same run as the control. The endpoint is a batch of aggregates over `messages`, and one of
them was already collapsed from three passes into one on 2026-08-29 after it logged 2,262 SLOW-REQ
warnings in 8.5 hours. It is still 200ms, so the previous fix was not the whole cost.

NOT THE LIVE DATABASE. It lives in a Docker volume behind a running service, and opening a second
connection to time queries would contend with the writes every console depends on. So this builds its
own database at the live fleet's measured scale and times each statement against that. The absolute
numbers are therefore this host's, not production's; the RANKING is the answer being sought, and a
ranking is what a fix needs.

THE SCALE IS TAKEN FROM THE FIGURES THE CODE ITSELF RECORDS: 34,107 messages and 31,913 read
receipts, measured on the operator's database on 2026-08-29 and written into the comment above the
query that was collapsed. Using the same numbers keeps this comparable with that work.

CONTROLS, in the same run. POSITIVE: a trivial `SELECT 1` gives the floor a statement cannot beat, so
a query at the floor is doing nothing. And the populated row counts are ASSERTED -- a benchmark
against an empty table would report every query as instant and look like good news.

THE TOTAL HERE IS ~13ms AND THE LIVE ENDPOINT IS ~200ms, AND THAT GAP IS NOT EXPLAINED BY THIS FILE.
Candidates it does not distinguish between: a larger live database, a colder page cache, aiosqlite's
thread hop per statement, response serialisation, and contention with the write queue every console
depends on. What this establishes is which STATEMENT dominates the statement time -- and that answer
is lopsided enough (87% to one query) to be worth acting on without first closing the gap. Reporting
the ranking as if it were the endpoint's cost breakdown would be the same error this block has now
made three times in other probes.

Run: python scripts/measure-stats-queries.py
"""

from __future__ import annotations

import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MESSAGES = 34_107
RECEIPTS = 31_913
AGENTS = 47
ROUNDS = 5
LF = chr(10)

# THE REAL INDEXES, from `service/schema.py`, and getting this wrong is how a benchmark flatters its
# subject. The first version of this file invented a composite index on `read_receipts` and gave the
# messages table two indexes it does not have -- so it would have measured a database production does
# not run. It turned out to be roughly faithful ANYWAY, for a reason worth writing down rather than
# being lucky about: `read_receipts` declares `PRIMARY KEY (message_id, agent_id)`, and SQLite backs
# a composite primary key with exactly that index. So "add a composite index" is not a fix available
# here -- it is already there, under another name.
SCHEMA = [
    """CREATE TABLE agents (id TEXT PRIMARY KEY, role TEXT, status TEXT, last_seen TEXT)""",
    """CREATE TABLE agent_tombstones (
           agent_id TEXT PRIMARY KEY, removed_at TEXT NOT NULL,
           removed_by TEXT DEFAULT '', bridge_id TEXT DEFAULT '', reason TEXT DEFAULT '')""",
    """CREATE TABLE messages (
           id TEXT PRIMARY KEY, from_agent TEXT, to_agent TEXT, source TEXT, channel TEXT,
           in_reply_to TEXT, timestamp INTEGER NOT NULL, body TEXT)""",
    """CREATE TABLE read_receipts (
           message_id TEXT NOT NULL, agent_id TEXT NOT NULL, read_at TEXT NOT NULL,
           PRIMARY KEY (message_id, agent_id))""",
    """CREATE INDEX idx_messages_to ON messages(to_agent, timestamp DESC)""",
    """CREATE INDEX idx_messages_channel ON messages(channel, timestamp DESC)""",
    """CREATE INDEX idx_messages_from ON messages(from_agent, timestamp DESC)""",
    """CREATE INDEX idx_messages_timestamp ON messages(timestamp DESC)""",
    """CREATE INDEX idx_messages_reply ON messages(in_reply_to)""",
    """CREATE INDEX idx_messages_source ON messages(source)""",
    """CREATE INDEX idx_read_receipts_agent ON read_receipts(agent_id)""",
    """CREATE INDEX idx_read_receipts_msg ON read_receipts(message_id)""",
]

# The statements `routers/stats.py` issues, by the name this script reports them under. Copied
# verbatim from the route so the ranking is about the real text -- a paraphrase would rank a query
# nobody runs.
QUERIES = {
    "count agents": "SELECT COUNT(*) FROM agents",
    "count direct messages": "SELECT COUNT(*) FROM messages WHERE source = 'direct'",
    "messages today": "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
    "direct in 24h": "SELECT COUNT(*) FROM messages WHERE source = 'direct' AND timestamp >= ?",
    "channel in 24h": (
        "SELECT COUNT(*) FROM messages WHERE source = 'channel' AND to_agent IS NULL "
        "AND timestamp >= ?"),
    "THE THREE UNREAD COUNTS (one pass)": """
        SELECT
          SUM(CASE WHEN a.id IS NOT NULL AND m.source = 'direct'  THEN 1 ELSE 0 END),
          SUM(CASE WHEN a.id IS NOT NULL AND m.source = 'channel' THEN 1 ELSE 0 END),
          SUM(CASE WHEN EXISTS (SELECT 1 FROM agent_tombstones t
                                WHERE t.agent_id = m.to_agent) THEN 1 ELSE 0 END)
        FROM messages m
        LEFT JOIN agents a ON a.id = m.to_agent
        LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
        WHERE m.to_agent IS NOT NULL AND r.message_id IS NULL
        """,
    # THE 24h BOUND IS PART OF IT, and leaving it out was this script's own first defect: the
    # unbounded form GROUP BYs the whole message history and ranked first at 15.1ms, which is a
    # query the route does not run. A benchmark that mis-copies its subject ranks a statement
    # nobody executes -- and the docstring above claimed the text was verbatim, which made the
    # error invisible to anyone reading rather than checking.
    "active conversation pairs (24h)": """
        SELECT COUNT(*) FROM (
            SELECT
                CASE WHEN from_agent < to_agent THEN from_agent ELSE to_agent END AS a,
                CASE WHEN from_agent < to_agent THEN to_agent ELSE from_agent END AS b
            FROM messages
            WHERE source = 'direct'
              AND to_agent IS NOT NULL
              AND timestamp >= ?
            GROUP BY a, b
        )
        """,
    "CONTROL: SELECT 1": "SELECT 1",
}

NEEDS_TIMESTAMP = {"messages today", "direct in 24h", "channel in 24h",
                   "active conversation pairs (24h)"}


def build(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    for statement in SCHEMA:
        db.execute(statement)
    rng = random.Random(20260908)
    agents = [f"agent-{i}" for i in range(AGENTS)]
    db.executemany("INSERT INTO agents (id, role, status, last_seen) VALUES (?,?,?,?)",
                   [(a, "coder", "online", "2026-09-08T00:00:00Z") for a in agents])
    db.executemany("INSERT INTO agent_tombstones (agent_id, removed_at) VALUES (?,?)",
                   [(f"gone-{i}", "2026-09-01T00:00:00Z") for i in range(12)])

    now_ms = int(time.time() * 1000)
    rows = []
    for i in range(MESSAGES):
        source = "direct" if i % 4 else "channel"
        to_agent = rng.choice(agents) if (source == "direct" or i % 8) else None
        rows.append((f"m-{i}", rng.choice(agents), to_agent, source,
                     now_ms - rng.randrange(0, 30 * 24 * 3600 * 1000), "body"))
    db.executemany("INSERT INTO messages (id, from_agent, to_agent, source, channel, in_reply_to, "
                   "timestamp, body) VALUES (?,?,?,?,NULL,NULL,?,?)", rows)
    # Receipts for a prefix of the messages, so a realistic majority is READ and the unread query
    # still has a population to walk.
    db.executemany("INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                   [(rows[i][0], rows[i][2] or agents[0], "2026-09-08T00:00:00Z")
                    for i in range(RECEIPTS)])
    db.commit()
    db.execute("ANALYZE")
    return db


def main() -> int:
    # ignore_cleanup_errors: a failure INSIDE the block leaves the connection open, and on
    # Windows the directory then cannot be removed -- so the real error was being buried
    # under a PermissionError from the teardown.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "stats-bench.sqlite3"
        db = build(path)

        # THE POPULATION IS ASSERTED. A benchmark against empty tables reports every query as
        # instant, which reads exactly like good news.
        counts = {
            "messages": db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "read_receipts": db.execute("SELECT COUNT(*) FROM read_receipts").fetchone()[0],
            "agents": db.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
        }
        if counts["messages"] != MESSAGES or counts["read_receipts"] != RECEIPTS:
            sys.stderr.write(f"NOTHING IS PUBLISHED: the fixture holds {counts}, not the scale this "
                             f"benchmark claims to measure.{LF}")
            return 1

        since = int(time.time() * 1000) - 24 * 3600 * 1000
        results = []
        for name, sql in QUERIES.items():
            args = (since,) if name in NEEDS_TIMESTAMP else ()
            samples = []
            db.execute(sql, args).fetchall()          # one warm-up, discarded
            for _ in range(ROUNDS):
                started = time.perf_counter()
                db.execute(sql, args).fetchall()
                samples.append((time.perf_counter() - started) * 1000.0)
            samples.sort()
            results.append((name, samples[len(samples) // 2]))

        floor = dict(results)["CONTROL: SELECT 1"]
        total = sum(ms for name, ms in results if not name.startswith("CONTROL"))
        if floor > 1.0:
            db.close()
            sys.stderr.write(f"NOTHING IS PUBLISHED: the control statement took {floor:.3f}ms, so "
                             f"this host is not quiet enough to rank anything.{LF}")
            return 1
        # CLOSED INSIDE THE BLOCK. Windows refuses to unlink an open file, so leaving the connection
        # to garbage collection made the temporary directory's own cleanup raise -- a benchmark that
        # completes and then dies on teardown reads exactly like a benchmark that failed.
        db.close()

    print(f"GET /stats, query by query, against {counts['messages']:,} messages and "
          f"{counts['read_receipts']:,} receipts")
    print(f"{'query':38s} {'p50 ms':>9s} {'share':>8s}")
    for name, ms in sorted(results, key=lambda r: -r[1]):
        share = "" if name.startswith("CONTROL") else f"{100 * ms / total:7.1f}%"
        print(f"{name:38s} {ms:9.3f} {share:>8s}")
    print("")
    print(f"The measured queries total {total:.1f}ms on this host, against a {floor:.3f}ms floor.")
    print("WHAT THIS IS NOT: the live database, whose contents and page cache are its own. The "
          "RANKING is what this establishes; the live endpoint's 191-223ms was measured separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
