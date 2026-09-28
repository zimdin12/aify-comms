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

THE WHOLE CHAIN, NOT ONE LINK (external review, 2026-09-29). Two nested runs that overlap stack up:
each same-session registration supersedes only the current owner, so the parent's bridge is superseded
by the first child and the first child by the second. The first child's loss is ignored because it is
not the owner, and an offer to only the bridges the second child superseded went to the first child,
which is gone, so the parent's beats were refused until someone re-registered it. The offer now goes to
every same-handle bridge behind the lost one, and names the bridge it was made for: whichever of them
beats first takes the session, and only while that bridge still owns it.

A CHILD THAT DIES WITHOUT A WORD (same review). A crashed bridge sends no resident-lost, so nothing ever
offered the session back. When the owner has not beaten within the resident lease, a same-handle bridge
behind it that beats takes the session back and supersedes the silent owner. That is the lease the
registration gate already uses to tell a live owner from a dead one (`same_mode_bridge_gate.py`): past
it, the parent could re-register over the owner anyway. It needs the predecessor's new beat as well,
so a predecessor a relaunch killed still hands nothing back.
"""

from __future__ import annotations

import json
import time

from service.clock import iso_to_epoch

# Bounds the walk behind a bridge; a real chain is one or two links.
_MAX_CHAIN = 64


def was_stopped(agent) -> bool:
    """The agent row before the loss: a stop already in place is someone else's, and stays."""
    return str(agent["status"] or "") == "stopped" or str(agent["launch_mode"] or "").strip().lower() == "none"


async def _same_handle_chain_behind(db, *, agent_id: str, bridge_id: str) -> list[str]:
    """Every bridge of the agent holding `bridge_id`'s session handle that `bridge_id` superseded,
    directly or through another bridge of the chain. Empty when `bridge_id` has no handle."""
    row = await (await db.execute(
        "SELECT session_handle FROM bridge_instances WHERE id = ? AND agent_id = ?", (bridge_id, agent_id),
    )).fetchone()
    handle = str((row["session_handle"] if row else "") or "").strip()
    if not handle:
        return []
    seen, chain, frontier = {bridge_id}, [], [bridge_id]
    while frontier and len(chain) < _MAX_CHAIN:
        marks = ",".join("?" for _ in frontier)
        rows = await (await db.execute(
            f"SELECT id FROM bridge_instances WHERE agent_id = ? AND session_handle = ? AND superseded_by IN ({marks})",
            (agent_id, handle, *frontier),
        )).fetchall()
        frontier = [r["id"] for r in rows if r["id"] not in seen]
        seen.update(frontier)
        chain.extend(frontier)
    return chain


def _is_silent(bridge, *, lease_seconds: float) -> bool:
    """The owner has not beaten within the resident lease, the registration gate's test for a dead owner."""
    seen = iso_to_epoch((bridge["last_seen"] if bridge else "") or "")
    return not seen or time.time() - seen > max(15, float(lease_seconds or 150))


async def offer_handback(db, *, agent_id: str, lost_bridge_id: str, before_loss, now: str) -> bool:
    """After the loss's own settlement. When the lost bridge had taken over a same-handle session and the
    agent was not stopped before, replace the loss's stop with `offline` and offer the session back.
    True when it did; the caller re-reads the agent."""
    if was_stopped(before_loss):
        return False
    chain = await _same_handle_chain_behind(db, agent_id=agent_id, bridge_id=lost_bridge_id)
    if not chain:
        return False
    await db.execute(
        "UPDATE agents SET status = 'offline', launch_mode = ?, status_note = ? WHERE id = ?",
        (
            before_loss["launch_mode"] or "detached",
            "The bridge that took this session over closed; the session's own bridge reclaims it when it beats.",
            agent_id,
        ),
    )
    marks = ",".join("?" for _ in chain)
    await db.execute(
        f"UPDATE bridge_instances SET handback_offer = ? WHERE agent_id = ? AND id IN ({marks})",
        (json.dumps({"at": now, "from": lost_bridge_id}), agent_id, *chain),
    )
    return True


async def reclaim_on_beat(db, *, agent_id: str, bridge_id: str, lease_seconds: float, now: str) -> bool:
    """Called for a beat from a superseded bridge. True when that beat took the session back; the caller
    then handles the beat as an ordinary one. Status is never written here.

    It takes the session back when it stands behind the current owner in the same session, and either
    the session was offered back when that owner was lost, or that owner has gone silent."""
    mine = await (await db.execute(
        "SELECT handback_offer, superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )).fetchone()
    agent = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not mine or not agent:
        return False
    try:
        runtime_state = json.loads(agent["runtime_state"] or "{}")
    except ValueError:
        runtime_state = {}
    owner_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
    if not owner_id or owner_id == bridge_id:
        return False
    if bridge_id not in await _same_handle_chain_behind(db, agent_id=agent_id, bridge_id=owner_id):
        return False
    offer = json.loads(mine["handback_offer"]) if mine["handback_offer"] else {}
    # An offer written before offers named their bridge was made by the one that superseded this bridge.
    offered = bool(offer) and str(offer.get("from") or mine["superseded_by"] or "").strip() == owner_id
    owner = await (await db.execute(
        "SELECT last_seen FROM bridge_instances WHERE id = ? AND agent_id = ?", (owner_id, agent_id),
    )).fetchone()
    if not offered and not _is_silent(owner, lease_seconds=lease_seconds):
        return False
    await db.execute(
        "UPDATE bridge_instances SET superseded_by = '', superseded_at = NULL, handback_offer = NULL"
        " WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )
    # The silent owner, if it ever beats again, stands behind this bridge rather than beside it.
    await db.execute(
        "UPDATE bridge_instances SET superseded_by = ?, superseded_at = COALESCE(superseded_at, ?)"
        " WHERE id = ? AND agent_id = ? AND COALESCE(superseded_by, '') = ''",
        (bridge_id, now, owner_id, agent_id),
    )
    runtime_state["bridgeInstanceId"] = bridge_id
    await db.execute("UPDATE agents SET runtime_state = ? WHERE id = ?", (json.dumps(runtime_state), agent_id))
    return True
