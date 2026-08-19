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
    #
    # EXCLUDING WHAT THE SERVICE WROTE IN THE AGENT'S NAME (v0.6 Phase 4, #10b). When a run ends with
    # no reply, `_mirror_missing_dispatch_handoff` tells the sender so — and authors that message AS
    # THE TARGET, because `from_agent` is what threads the notice into the right conversation. The row
    # is otherwise identical to a real one: same `source='direct'`, same table, same shape. Read
    # naively, the system NOTICING that an agent is dead advanced that agent's productivity clock, and
    # the roster reported a corpse as having just produced something. That is the exact false claim
    # this map was added to retire; the docstring above says "only what it SENDS evidences that it is
    # running", and a message it did not write is not something it sent.
    #
    # FAILED AND CANCELLED ONLY. A completed run's handoff carries the target's OWN result, so the
    # agent genuinely produced; excluding it would under-report on the roster, where `lastSentAt` is
    # the only evidence of production there is. Only the notices that say "this agent produced
    # nothing" are removed.
    #
    # `handoff_message_id` is the marker rather than a new `messages.source` value, and that is a
    # ruling rather than convenience: `source` is a binary discriminator and about ten readers treat
    # `'direct'` as "a DM" — analytics, claim gating, run reports, managed-worker sweeps. A third value
    # would silently change every one of them in order to fix the one reader that is wrong.
    # THE GUARD IS APPLIED AFTERWARDS, NOT IN THIS QUERY, and that is a measured decision. Every
    # SQL-side form was tried against a copy of the live database (46 agents, 31,363 messages,
    # 19,230 runs):
    #
    #     plain query, covering index                    1.91 ms median /   3.05 ms p95
    #     + NOT IN (SELECT handoff_message_id ...)     126.59 ms        / 251.51 ms
    #     + NOT EXISTS, with an index on that column    77.69 ms        / 164.13 ms
    #
    # Both lose `SEARCH m USING COVERING INDEX idx_messages_from`: the guard needs `m.id`, which the
    # covering index does not carry, so every row becomes a table fetch. 40-66x on `GET /agents`,
    # which is the dashboard poll path and the cost class that produced the `database is locked` era.
    # The exclusion set meanwhile is TWO rows in 31,363, and applying it changes ZERO agents today.
    # Paying 66x on every poll to correct two rows is the wrong trade; doing it in Python for the
    # handful of agents it can possibly affect is the right one.
    cursor = await db.execute(
        f"""
        SELECT m.from_agent AS agent_id, MAX(m.timestamp) AS ts
        FROM messages m
        WHERE m.from_agent IN ({placeholders})
        GROUP BY m.from_agent
        """,
        tuple(agent_ids),
    )
    latest: dict[str, int] = {}
    for row in await cursor.fetchall():
        if row["ts"]:
            latest[row["agent_id"]] = int(row["ts"])

    # v0.6 Phase 4, #10b: drop the notices the SERVICE wrote in an agent's name.
    #
    # When a run ends with no reply, `_mirror_missing_dispatch_handoff` tells the sender so, and
    # authors that message AS THE TARGET — deliberately, because `from_agent` is what threads it into
    # the right conversation. The row is otherwise identical to a real one: same `source='direct'`,
    # same table, same shape. Read naively, the system NOTICING that an agent is dead advances that
    # agent's productivity clock, and the roster reports a corpse as having just produced something.
    # That is the exact false claim this map exists to retire.
    #
    # Only FAILED and CANCELLED notices are excluded. A completed run's handoff carries the target's
    # own result, so the agent genuinely produced, and dropping it would under-report on the roster
    # where `lastSentAt` is the only evidence of production there is.
    #
    # Cheap because it is narrow: this reads only the excluded notices belonging to the agents asked
    # about — two rows on the live database — and re-derives a maximum only for an agent whose latest
    # message IS one of them. In the ordinary case that is one small indexed read and no recompute.
    #
    # `r.status IN (...)` bare, NOT `LOWER(COALESCE(r.status, ''))`. Wrapping the column in functions
    # makes it unsargable and the plan drops to `SCAN r` over every run, which measured 52.41 ms on
    # the roster path against 1.99 ms for the form below — the whole saving, given away for defensive
    # syntax. The statuses are written from lowercase constants (the live database holds exactly
    # 'completed', 'delivered', 'failed'), and a case variant would degrade to counting that one row
    # as before rather than to an error.
    if latest:
        cursor = await db.execute(
            f"""
            SELECT m.id AS id, m.from_agent AS agent_id, m.timestamp AS ts
            FROM messages m
            JOIN dispatch_runs r ON r.handoff_message_id = m.id
            WHERE m.from_agent IN ({placeholders})
              AND r.status IN ('failed', 'cancelled')
            """,
            tuple(agent_ids),
        )
        authored_by_service: dict[str, list[str]] = {}
        suspect: dict[str, set[int]] = {}
        for row in await cursor.fetchall():
            authored_by_service.setdefault(row["agent_id"], []).append(str(row["id"]))
            suspect.setdefault(row["agent_id"], set()).add(int(row["ts"]))

        for agent_id, excluded_ids in authored_by_service.items():
            if latest.get(agent_id) not in suspect.get(agent_id, set()):
                continue  # its newest message is a real one; nothing to re-derive
            marks = ",".join("?" for _ in excluded_ids)
            row = await (await db.execute(
                f"SELECT MAX(timestamp) AS ts FROM messages WHERE from_agent = ? AND id NOT IN ({marks})",
                (agent_id, *excluded_ids),
            )).fetchone()
            if row and row["ts"]:
                latest[agent_id] = int(row["ts"])
            else:
                latest.pop(agent_id, None)  # everything it "sent" was written for it

    for agent_id, ts in latest.items():
        out.setdefault(agent_id, {})["lastSentAt"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts / 1000)
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
