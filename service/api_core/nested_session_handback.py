"""A session a nested `claude` took over goes back to its own bridge, on proof that bridge is still alive.

THE INCIDENT, 2026-09-26 (sc-manager). An agent ran `claude mcp list` and `claude -p` from its own shell.
Each child `claude` inherited the agent's identity and session id, so its aify-comms bridge registered as
that agent with the same session handle, and the same-session relaunch rule (`same_mode_bridge_gate.py`)
let it supersede the real bridge. The child exited seconds later, its bridge reported resident-lost as the
current owner, and the agent was set `stopped`. The real bridge kept beating and was ignored as
superseded, so for 2.5 hours no message created a run, until the operator had the agent re-register.

THE LOSS WRITES NO STOP, AND THE RECLAIM TOUCHES NO STATUS. When a bridge that took the session over
from a same-handle bridge is lost, the agent was not already stopped, and the loss would have stopped
it, the agent is set `offline` instead, and the bridges it superseded are OFFERED the session
(`handback_offer`). A bridge that then beats is alive, and a beat is the only thing that proves it: it
takes ownership back, and its own heartbeat makes the agent active again. A predecessor killed by a real
relaunch never beats again, so nothing is handed back and the agent stays offline until its next
launch; it reads `offline` rather than `stopped`, and both refuse sends. A freshness window on the
predecessor's older beats could not tell those two apart (0.7.4 review of d51472e3).

WHY NOT A STOP THAT A BEAT LIFTS. That design needed to know every writer that could stop the agent
after the loss, so that a beat never lifted a stop an operator meant; four reviews each found another
route (session Stop, a caller-supplied reason equal to the dashboard's note, a status PATCH, a run PATCH
with `agentStatus`). Here there is nothing to lift: the reclaim moves ownership only, and every heartbeat
already leaves a stopped agent stopped, so an explicit stop from any route stands.
"""

from __future__ import annotations

import json


def was_stopped(agent) -> bool:
    """The agent row before the loss: a stop already in place is someone else's, and stays."""
    return str(agent["status"] or "") == "stopped" or str(agent["launch_mode"] or "").strip().lower() == "none"


async def _superseded_same_handle(db, *, agent_id: str, lost_bridge_id: str) -> str:
    lost = await (await db.execute(
        "SELECT session_handle FROM bridge_instances WHERE id = ? AND agent_id = ?", (lost_bridge_id, agent_id),
    )).fetchone()
    handle = str((lost["session_handle"] if lost else "") or "").strip()
    if not handle:
        return ""
    taken = await (await db.execute(
        "SELECT 1 FROM bridge_instances WHERE agent_id = ? AND superseded_by = ? AND session_handle = ? LIMIT 1",
        (agent_id, lost_bridge_id, handle),
    )).fetchone()
    return handle if taken else ""


async def offer_handback(db, *, agent_id: str, lost_bridge_id: str, before_loss, now: str) -> bool:
    """After the loss's own settlement. When the lost bridge had taken over a same-handle session and the
    agent was not stopped before, replace the loss's stop with `offline` and offer the session back.
    True when it did; the caller re-reads the agent."""
    if was_stopped(before_loss):
        return False
    handle = await _superseded_same_handle(db, agent_id=agent_id, lost_bridge_id=lost_bridge_id)
    if not handle:
        return False
    await db.execute(
        "UPDATE agents SET status = 'offline', launch_mode = ?, status_note = ? WHERE id = ?",
        (
            before_loss["launch_mode"] or "detached",
            "The bridge that took this session over closed; the session's own bridge reclaims it when it beats.",
            agent_id,
        ),
    )
    await db.execute(
        "UPDATE bridge_instances SET handback_offer = ? WHERE agent_id = ? AND superseded_by = ? AND session_handle = ?",
        (json.dumps({"at": now}), agent_id, lost_bridge_id, handle),
    )
    return True


async def reclaim_on_beat(db, *, agent_id: str, bridge_id: str) -> bool:
    """Called for a beat from a superseded bridge. True when that beat took the session back; the caller
    then handles the beat as an ordinary one. Status is never written here."""
    mine = await (await db.execute(
        "SELECT superseded_by, handback_offer FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )).fetchone()
    if not mine or not mine["handback_offer"]:
        return False
    taker = str(mine["superseded_by"] or "").strip()
    agent = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
    try:
        runtime_state = json.loads(agent["runtime_state"] or "{}") if agent else {}
    except ValueError:
        runtime_state = {}
    # Nothing newer may have taken the session since the offer.
    if not agent or str(runtime_state.get("bridgeInstanceId") or "").strip() != taker:
        return False
    await db.execute(
        "UPDATE bridge_instances SET superseded_by = '', superseded_at = NULL, handback_offer = NULL"
        " WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )
    runtime_state["bridgeInstanceId"] = bridge_id
    await db.execute("UPDATE agents SET runtime_state = ? WHERE id = ?", (json.dumps(runtime_state), agent_id))
    return True
