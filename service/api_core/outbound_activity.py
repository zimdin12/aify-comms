"""When did each agent last SEND something — the read the roster's liveness view is built on.

Moved out of `service/routers/agents/shared.py` in v0.5.4, byte-identical. It is a pure read with no
dependency beyond stdlib, and it was one of three functions making that module 838 lines while six
router files imported through it.

The name is worth reading carefully: OUTBOUND. `service/api_core/records.py` carries a note about a
false "silent lane" claim that came from confusing this with inbound traffic — an agent receiving
messages is not an agent that is alive, and only what it SENDS evidences that it is running.
"""
from __future__ import annotations

import time
from typing import Any

async def _get_outbound_activity_map(db, agent_ids: list[str], *, include_runs: bool = True) -> dict[str, dict[str, Any]]:
    """When did each agent last PRODUCE something — send a message, finish a run?

    AUDIT FINDING 1 (2026-08-10). Every field on the agent-health surface answered about INBOUND
    or about registration liveness, and none about production:

        unread      inbound messages not yet read      — the wrong direction
        last read   the last message it CONSUMED       — the wrong direction
        last seen   registration/heartbeat liveness    — and a bare status PATCH advances it
        status      worker reachability                — not productivity

    During an outage every one of those stayed individually true while a reply sat undelivered, so
    a manager told the operator three times that a lane was dead. It was not.

    The reporter asked for a DEGRADED/STALE marker. The reviewer argued — correctly, and this is
    why the fix is shaped this way — that a STALE marker retires a DIFFERENT artifact ("the
    delivery path is verified") and STILL cannot say what an agent last produced. Even a perfect
    one leaves callers inferring productivity from inbound fields, which is exactly how the false
    claim was made. Outbound activity is the field that retires it; STALE is complementary.

    Deliberately reads `messages.from_agent` and finished runs — the two places production is
    recorded — and nothing about delivery. Answering one question well beats answering two vaguely,
    which is the failure being fixed.
    """
    if not agent_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_ids)
    out: dict[str, dict[str, Any]] = {a: {} for a in agent_ids}

    # Last message SENT. `messages.timestamp` is epoch MILLISECONDS, not ISO — the schema's one
    # trap, and mixing it with the ISO columns below silently sorts wrong.
    cursor = await db.execute(
        f"""
        SELECT m.from_agent AS agent_id, MAX(m.timestamp) AS ts
        FROM messages m
        WHERE m.from_agent IN ({placeholders})
        GROUP BY m.from_agent
        """,
        tuple(agent_ids),
    )
    for row in await cursor.fetchall():
        ts = row["ts"]
        if ts:
            out.setdefault(row["agent_id"], {})["lastSentAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts) / 1000)
            )

    # Last run this agent COMPLETED as the worker. Distinct from "a run targeting it exists",
    # which the dispatch-state field already reports and which says nothing about output.
    #
    # OFF BY DEFAULT ON THE ROSTER, and that is a measured decision rather than caution. The cost
    # is not in the aggregate, it is in the FAN-OUT: with one agent SQLite searches
    # `idx_dispatch_runs_target_status(target_agent, status, requested_at)`; with the whole roster
    # in an `IN (...)` list it abandons that index and builds a temp B-tree over every completed
    # run. Measured on the live DB (18,005 runs), same statement, only the parameter count differs:
    #
    #     42 agents (roster)   37.0 ms median / 42.0 ms p95   TEMP B-TREE FOR GROUP BY
    #      1 agent  (detail)    0.01 ms median /  0.34 ms p95  SEARCH USING idx_dispatch_runs_target_status
    #
    # `GET /agents` is the dashboard's poll path and DECISIONS.md (2026-06-29) is explicit that
    # cost there is what produced the last `database is locked` era — so the roster does not run
    # this at all. `lastSentAt` alone answers "has this agent produced anything", the question the
    # false silent-lane claim turned on, at 2.55 ms on a covering index.
    #
    # The reviewer's alternative was a new index shaped `(status, target_agent, finished_at DESC)`,
    # declined for the surviving detail path — but "index-covered" would be too strong and the
    # reviewer was right to push back on it. `idx_dispatch_runs_target_status` does not include
    # `finished_at`, so it assists the target/status SEARCH and MAX() still reads that agent's
    # matching rows. The single-agent cost therefore scales with ONE AGENT'S history, not the
    # table's — measured live, same plan throughout:
    #
    #     agent with ~0 completed runs      0.004 ms
    #     sc-claude,   3,109 completed       3.84 ms median /  4.83 ms p95
    #     sc-manager,  7,383 completed      13.19 ms median / 16.20 ms p95
    #
    # 13 ms on a detail view someone opened deliberately is acceptable; 13 ms on a 2-second poll
    # across 42 agents is the lock class. That is the whole distinction. Reverse this decision if
    # either (a) run detail returns to a hot/poll path, or (b) a single heavy agent's detail view
    # gets a latency target this exceeds — then an index or a materialized outbound table earns
    # its write cost. Measure first; the 3,500× spread above is why assuming does not work here.
    if not include_runs:
        return out
    cursor = await db.execute(
        f"""
        SELECT target_agent AS agent_id, MAX(finished_at) AS ts
        FROM dispatch_runs
        WHERE target_agent IN ({placeholders})
          AND status = 'completed'
          AND COALESCE(finished_at, '') != ''
        GROUP BY target_agent
        """,
        tuple(agent_ids),
    )
    for row in await cursor.fetchall():
        if row["ts"]:
            out.setdefault(row["agent_id"], {})["lastCompletedRunAt"] = str(row["ts"])

    return out
