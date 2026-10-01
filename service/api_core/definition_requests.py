"""An operator's change to a defined agent, applied by the host that owns it (P0 C4).

THE SERVICE QUEUES; THE HOST DECIDES. A request is a compare-and-set on the lifetime and revision the
service held when the operator asked, so a change made on the host in between refuses it there.
`pending -> claimed -> done | refused | expired`. Each function runs inside its caller's transaction.

Every read of the queue first expires what no host claimed in time, so expiry needs no sweep of its
own: a request is judged by its `expires_at` whenever anything looks at it.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException

from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.definition_guard import defined_on
from service.api_core.definition_push import RUNTIME_HARNESS
from service.api_core.definition_schema import AGENT_FIELDS
from service.api_core.definition_snapshot import fence_refusal, is_counter
from service.api_core.serialization import _iso_add_seconds

#: C4: a request no host claims in this time expires.
REQUEST_TTL_SECONDS = 600
#: The statuses a request is still waiting in. One per agent at a time.
_OPEN_SQL = "('pending', 'claimed')"
_FINISHED = ("done", "refused", "expired")
#: The agent fields a patch may set: every C1 field but the id, which names the file.
EDITABLE_FIELDS = tuple(field for field in AGENT_FIELDS if field != "id")


def is_removal(patch: Any) -> bool:
    return isinstance(patch, dict) and list(patch) == ["remove"] and patch["remove"] is True


def patch_problems(patch: Any) -> list[str]:
    """What is wrong with a patch's SHAPE, or []. Its values are the host's to judge by C1."""
    if is_removal(patch):
        return []
    if not isinstance(patch, dict) or not patch:
        return ['patch: an object of agent fields to change, or {"remove": true}']
    return [f"patch: {key!r} is not an agent field a request may change" for key in sorted(patch)
            if key not in EDITABLE_FIELDS]


def request_record(row) -> dict:
    return {
        "id": row["id"], "agentId": row["agent_id"], "machineId": row["machine_id"], "storeId": row["store_id"],
        "expectedIncarnation": row["expected_incarnation"], "expectedRevision": row["expected_revision"],
        "patch": json.loads(row["patch"]), "requestedBy": row["requested_by"], "status": row["status"],
        "outcome": row["outcome"] or "", "resultIncarnation": row["result_incarnation"],
        "resultRevision": row["result_revision"], "createdAt": row["created_at"], "expiresAt": row["expires_at"],
        "claimedAt": row["claimed_at"] or "", "finishedAt": row["finished_at"] or "",
        "consequence": row["consequence"] or "",
    }


async def expire_unclaimed(db, now: str) -> None:
    await db.execute(
        "UPDATE definition_requests SET status = 'expired', outcome = 'no host claimed it within 10 minutes', "
        "finished_at = ? WHERE status = 'pending' AND expires_at <= ?", (now, now))


async def admit(db, agent_id: str, patch: Any, requested_by: str, now: str) -> dict:
    """Queue one change for a defined agent, against the lifetime and revision held now. An agent no
    host defines is refused before its patch is judged: there is nothing for a patch to apply to."""
    await expire_unclaimed(db, now)
    held = await (await db.execute(
        "SELECT machine_id, store_id, incarnation, revision FROM agent_definitions WHERE agent_id = ?",
        (agent_id,))).fetchone()
    if not held:
        raise HTTPException(404, f'"{agent_id}" is not defined by any host, so there is no definition to change')
    problems = patch_problems(patch)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    waiting = await (await db.execute(
        f"SELECT id FROM definition_requests WHERE agent_id = ? AND status IN {_OPEN_SQL}", (agent_id,))).fetchone()
    if waiting:
        raise HTTPException(409, f'"{agent_id}" already has a change waiting for its host ({waiting["id"]}); '
                                 f'ask again once that one is done')
    request_id = f"defreq_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO definition_requests (id, agent_id, machine_id, store_id, expected_incarnation, expected_revision, "
        "patch, requested_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, agent_id, held["machine_id"], held["store_id"], held["incarnation"], held["revision"],
         json.dumps(patch), requested_by, now, _iso_add_seconds(now, REQUEST_TTL_SECONDS)))
    return await request_by_id(db, request_id)


def assignment_patch(agent_id: str, owner: str, env_machine: str, *, workspace: Optional[str], runtime: str,
                     model: Optional[str], runtime_config: Optional[dict]) -> dict:
    """PURE. The patch an environment assignment of an agent defined on `owner` becomes (C5): the fields
    it names, as the definition spells them. Its environment can only be one on that machine."""
    if env_machine != owner:
        raise HTTPException(409, f'"{agent_id}" is defined on {owner}; assign it an environment on that machine, '
                                 f'not on {env_machine or "an unknown machine"}')
    patch = {}
    if runtime:
        if runtime not in RUNTIME_HARNESS:
            raise HTTPException(422, f'runtime "{runtime}" is not a harness a definition can name '
                                     f'({", ".join(sorted(RUNTIME_HARNESS))})')
        patch["harness"] = RUNTIME_HARNESS[runtime]
    if workspace is not None:
        patch["workspace"] = workspace
    if model is not None:
        patch["model"] = model
    if (runtime_config or {}).get("effort") is not None:
        patch["effort"] = runtime_config["effort"]
    return patch


async def queue_if_defined(db, agent_id: str, patch: Any, requested_by: str, now: str) -> Optional[dict]:
    """THE C5 ROWS: an operator's edit of a DEFINED agent becomes a request its host applies, queued
    here; None for an agent no host defines, whose edit applies directly as it always has. The check
    and the queueing are one write transaction. The caller must hold no open transaction."""
    await db.execute("BEGIN IMMEDIATE")
    if not await defined_on(db, agent_id):
        await db.rollback()
        return None
    queued = await admit(db, agent_id, patch, requested_by, now)
    await db.commit()
    return queued


async def queued_for_its_host(db, agent_id: str, patch: Any, requested_by: str, now: str) -> dict:
    """What a route that edits agents answers for a DEFINED one: its edit, queued for the host."""
    queued = await queue_if_defined(db, agent_id, patch, requested_by, now)
    if queued is None:
        raise HTTPException(409, f'"{agent_id}" stopped being defined while this was asked; ask again')
    return {"ok": True, "agentId": agent_id, "request": queued}


async def request_by_id(db, request_id: str) -> Optional[dict]:
    row = await (await db.execute("SELECT * FROM definition_requests WHERE id = ?", (request_id,))).fetchone()
    return request_record(row) if row else None


async def requests_for(db, agent_id: str, now: str) -> list[dict]:
    await expire_unclaimed(db, now)
    rows = await (await db.execute(
        "SELECT * FROM definition_requests WHERE agent_id = ? ORDER BY created_at DESC, id DESC", (agent_id,))).fetchall()
    return [request_record(row) for row in rows]


def _undeliverable(row, held, current_store: str) -> str:
    """Why a host may no longer apply this request, or "" (C4: refused at the claim)."""
    if row["store_id"] != current_store:
        return "made for a store this machine has since replaced"
    if held is None:
        return "the definition was withdrawn before its host claimed this"
    if held["machine_id"] != row["machine_id"] or held["store_id"] != row["store_id"]:
        return f"the definition moved to {held['machine_id']} before its host claimed this"
    return ""


async def claim(db, environment: Optional[dict], bridge_id: str, machine_id: str, now: str) -> list[dict]:
    """The requests this machine's current store should apply, marked claimed. Fenced as a push is (C3).

    A claimed request that was never reported is handed out again: the host's apply is idempotent
    (C4 step 2), so a host that crashed between claim and report finishes it on its next claim.
    """
    refusal = fence_refusal(environment, bridge_id, machine_id)
    if refusal:
        raise HTTPException(409, f"definition request claim refused: {refusal}")
    await expire_unclaimed(db, now)
    store = await (await db.execute(
        "SELECT store_id FROM definition_stores WHERE machine_id = ?", (machine_id,))).fetchone()
    current_store = store["store_id"] if store else ""
    rows = await (await db.execute(
        f"SELECT * FROM definition_requests WHERE machine_id = ? AND status IN {_OPEN_SQL} ORDER BY created_at, id",
        (machine_id,))).fetchall()
    delivered = []
    for row in rows:
        held = await (await db.execute(
            "SELECT machine_id, store_id FROM agent_definitions WHERE agent_id = ?", (row["agent_id"],))).fetchone()
        reason = _undeliverable(row, held, current_store)
        if reason:
            await db.execute("UPDATE definition_requests SET status = 'refused', outcome = ?, finished_at = ? "
                             "WHERE id = ?", (reason, now, row["id"]))
            continue
        await db.execute("UPDATE definition_requests SET status = 'claimed', "
                         "claimed_at = CASE WHEN claimed_at = '' THEN ? ELSE claimed_at END WHERE id = ?",
                         (now, row["id"]))
        delivered.append(row["id"])
    return [await request_by_id(db, request_id) for request_id in delivered]


def report_problems(body: dict) -> list[str]:
    status = body.get("status")
    if status not in ("done", "refused"):
        return ["status: done or refused"]
    if not isinstance(body.get("outcome", ""), str):
        return ["outcome: a string"]
    if status == "done" and not (is_counter(body.get("resultIncarnation")) and is_counter(body.get("resultRevision"))):
        return ["resultIncarnation, resultRevision: a done request names the lifetime and revision it left"]
    return []


async def report(db, environment: Optional[dict], request_id: str, body: dict, now: str) -> dict:
    """Record what the host did with a request it CLAIMED. Reporting the same result again changes
    nothing, so a lost acknowledgement costs nothing; a different result for a finished request is
    refused. A `done` removal leaves the service's own consequence `pending` in the same write, so it
    is owed until it is settled, whichever report finds it (`settle_removal`)."""
    refusal = fence_refusal(environment, body.get("bridgeId", ""), body.get("machineId", ""))
    if refusal:
        raise HTTPException(409, f"definition request report refused: {refusal}")
    problems = report_problems(body)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    await expire_unclaimed(db, now)
    row = await (await db.execute("SELECT * FROM definition_requests WHERE id = ?", (request_id,))).fetchone()
    if not row:
        raise HTTPException(404, f"no definition request {request_id}")
    if row["machine_id"] != str(body["machineId"]).strip():
        raise HTTPException(409, f"definition request {request_id} is for {row['machine_id']}")
    result = (body.get("resultIncarnation"), body.get("resultRevision")) if body["status"] == "done" else (None, None)
    if row["status"] in _FINISHED:
        if (row["status"], row["result_incarnation"], row["result_revision"]) == (body["status"], *result):
            return request_record(row)
        raise HTTPException(409, f"definition request {request_id} is already {row['status']}")
    if row["status"] != "claimed":
        # A HOST REPORTS ONLY WHAT IT CLAIMED: the claim is where a request it can no longer apply is
        # refused, so a report that skipped it would apply what the claim would have refused.
        raise HTTPException(409, f"definition request {request_id} was never claimed; claim it first")
    owed = "pending" if body["status"] == "done" and is_removal(json.loads(row["patch"])) else ""
    await db.execute(
        "UPDATE definition_requests SET status = ?, outcome = ?, result_incarnation = ?, result_revision = ?, "
        "finished_at = ?, consequence = ? WHERE id = ?",
        (body["status"], body.get("outcome", ""), *result, now, owed, request_id))
    return await request_by_id(db, request_id)


async def settle_removal(db, request_id: str, why: str) -> None:
    """Record what the service did about a `done` removal: `removed`, or why nothing was (C4: it says so
    on the request). Two reports that both found it pending settle the same fact: the second's fence
    finds the first's tombstone and allows, so it records `removed` too."""
    await db.execute("UPDATE definition_requests SET consequence = ? WHERE id = ?",
                     ("removed" if not why else f"nothing removed: {why}", request_id))


async def removal_refusal(db, request: dict) -> str:
    """Why a host's `done` on this removal must remove nothing here, or "" (C4: the service's own
    consequences are fenced). Removal runs only while the service still holds the definition at the
    request's store and lifetime, or saw that very store withdraw it at that very lifetime."""
    expected = {"machineId": request["machineId"], "storeId": request["storeId"],
                "incarnation": request["expectedIncarnation"]}
    held = await (await db.execute(
        "SELECT machine_id, store_id, incarnation FROM agent_definitions WHERE agent_id = ?",
        (request["agentId"],))).fetchone()
    if held:
        found = {"machineId": held["machine_id"], "storeId": held["store_id"], "incarnation": held["incarnation"]}
        return "" if found == expected else (
            f"the definition is now {found['machineId']} store {found['storeId']} lifetime {found['incarnation']}, "
            f"not the one this removal was for")
    agent = await (await db.execute(
        "SELECT definition_withdrawn FROM agents WHERE id = ?", (request["agentId"],))).fetchone()
    if agent and json.loads(agent["definition_withdrawn"] or "{}") == expected:
        return ""
    if agent is None and await _agent_tombstone(db, request["agentId"]):
        # ALREADY REMOVED: a removal that deleted the row and crashed before settling, with the host's
        # withdrawal landing since. The row that recorded the withdrawal is the row it deleted.
        return ""
    return "the definition this removal was for ended another way"
