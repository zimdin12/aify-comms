"""A session a nested `claude` took over goes back to its own bridge, on proof that bridge is still alive.

THE INCIDENT, 2026-09-26 (sc-manager). An agent ran `claude mcp list` and `claude -p` from its own shell.
Each child `claude` inherited the agent's identity and session id, so its aify-comms bridge registered as
that agent with the same session handle, and the same-session relaunch rule (`same_mode_bridge_gate.py`)
let it supersede the real bridge. The child exited seconds later, its bridge reported resident-lost as the
current owner, and the agent was set `stopped`. The real bridge kept beating and was ignored as
superseded, so for 2.5 hours no message created a run, until the operator had the agent re-register.

THE PROOF IS A BEAT AFTER THE LOSS. When the current bridge of a resident agent is lost and the agent is
stopped for it, every bridge that bridge superseded with the same session handle is OFFERED the session back
(`handback_offer`), and the agent stops as before. A bridge that then beats is alive, and a beat is the
only thing that proves it: it reclaims the session and the stop is lifted. A predecessor killed by a real
relaunch never beats again, so a relaunched session that closes, however soon, leaves the agent stopped. A
freshness window on the predecessor's older beats cannot tell those two apart and handed a quick relaunch
to a dead bridge (0.7.4 review of d51472e3).

THE OFFER HOLDS ONLY WHILE THE LOSS'S OWN STOP DOES, and it is identified, not merely compared.
  - No offer when the agent was already stopped before the loss: that stop is someone else's, and a beat
    must never lift it (review of 17fd7b85).
  - The offer appends a service-made token to the note the loss wrote, and records the agent's status,
    launch mode, session mode and that note. Every writer that stops an agent writes its own note, and none
    can write this one, so any later stop -- the agent control, the session control, a CLI takeover, even
    one whose other fields match exactly -- changes the recorded state and the offer lapses. Comparing the
    fields alone failed when the loss's caller-supplied reason equalled the dashboard's stop note, and
    withdrawing route by route missed the session control's Stop (reviews of 17fd7b85 and 486c8262).
  - A writer that moves ownership and leaves those fields alone is refused by the owner check.

The cost is one heartbeat interval: from the nested bridge's exit to the real bridge's next beat, the agent
reads stopped.
"""

from __future__ import annotations

import json
import uuid

_STATE = ("status", "launch_mode", "session_mode", "status_note")


def was_stopped(agent) -> bool:
    """The agent row before the loss: a stop already in place is not the loss's to offer back."""
    return str(agent["status"] or "") == "stopped" or str(agent["launch_mode"] or "").strip().lower() == "none"


async def offer_handback(db, *, agent_id: str, lost_bridge_id: str, now: str) -> None:
    """Offer the session back to the same-handle bridges `lost_bridge_id` superseded, for as long as the
    stop the loss just wrote stands. Call it after that stop is written."""
    lost = await (await db.execute(
        "SELECT session_handle FROM bridge_instances WHERE id = ? AND agent_id = ?", (lost_bridge_id, agent_id),
    )).fetchone()
    handle = str((lost["session_handle"] if lost else "") or "").strip()
    if not handle:
        return
    takers = await (await db.execute(
        "SELECT 1 FROM bridge_instances WHERE agent_id = ? AND superseded_by = ? AND session_handle = ? LIMIT 1",
        (agent_id, lost_bridge_id, handle),
    )).fetchone()
    if not takers:
        return
    token = uuid.uuid4().hex[:12]
    await db.execute(
        "UPDATE agents SET status_note = TRIM(COALESCE(status_note, '') || ?) WHERE id = ?",
        (f" (its own bridge may reclaim it: offer {token})", agent_id),
    )
    agent = await (await db.execute(
        "SELECT status, launch_mode, session_mode, status_note FROM agents WHERE id = ?", (agent_id,),
    )).fetchone()
    offer = json.dumps({"at": now, "token": token, **{key: agent[key] for key in _STATE}})
    await db.execute(
        "UPDATE bridge_instances SET handback_offer = ?"
        " WHERE agent_id = ? AND superseded_by = ? AND session_handle = ?",
        (offer, agent_id, lost_bridge_id, handle),
    )


async def reclaim_on_beat(db, *, agent_id: str, bridge_id: str) -> bool:
    """Called for a beat from a superseded bridge. True when that beat reclaimed the session."""
    mine = await (await db.execute(
        "SELECT superseded_by, handback_offer FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )).fetchone()
    try:
        offer = json.loads(mine["handback_offer"] or "") if mine else None
    except ValueError:
        offer = None
    if not isinstance(offer, dict):
        return False
    taker = str(mine["superseded_by"] or "").strip()
    agent = await (await db.execute(
        "SELECT status, launch_mode, session_mode, status_note, runtime_state FROM agents WHERE id = ?", (agent_id,),
    )).fetchone()
    if not agent or any(agent[key] != offer.get(key) for key in _STATE):
        return False  # something else has written the agent since the loss: its word stands
    try:
        runtime_state = json.loads(agent["runtime_state"] or "{}")
    except ValueError:
        runtime_state = {}
    if str(runtime_state.get("bridgeInstanceId") or "").strip() != taker:
        return False
    await db.execute(
        "UPDATE bridge_instances SET superseded_by = '', superseded_at = NULL, handback_offer = NULL"
        " WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id),
    )
    runtime_state["bridgeInstanceId"] = bridge_id
    # Lifted the way the dashboard's Resume lifts a stop (agent_stop_resume.py).
    await db.execute(
        "UPDATE agents SET runtime_state = ?, status = 'idle', status_note = '',"
        " launch_mode = CASE WHEN launch_mode = 'none' THEN 'detached' ELSE launch_mode END"
        " WHERE id = ?",
        (json.dumps(runtime_state), agent_id),
    )
    return True
