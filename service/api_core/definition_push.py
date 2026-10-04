"""Applying a host's agent-definition snapshot (P0 C3, C5's descriptive fields, C6's withdrawal).

ONE TRANSACTION per push, so a registration or another push racing this one cannot interleave with
it: the fence, the ordering against the machine's stores, ownership, and every row it writes are
decided on what this transaction read.

WHAT A PUSH WRITES, and nothing else:
- `agent_definitions`: the desired state of every id the machine owns;
- `agents`, for a defined id: the descriptive columns (name, role, instructions, herdr space) and
  `definition_state`. A NEW id also gets its effective columns from the definition, since nothing has
  run yet for them to describe. An existing agent's effective columns are left to the paths that
  write them today (registration, running settlement), so a live run is never rewritten (C5).
- `definition_stores`, `definition_stores_retired`: the machine's store order.
The dashboard learns of it from the change feed, which reports the tables a commit wrote.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from service.api_core.definition_snapshot import (
    PushOrder, canonical, fence_refusal, is_counter, push_order, snapshot_problems,
)
from service.api_core.operator_authz import is_operator_actor

#: The harness a definition names, as the runtime this service files agents under.
HARNESS_RUNTIME = {"claude": "claude-code", "codex": "codex", "hermes": "hermes"}
#: The other way: the harness a definition names for a runtime this service files agents under.
RUNTIME_HARNESS = {runtime: harness for harness, runtime in HARNESS_RUNTIME.items()}


def push_body_problems(body: dict) -> list[str]:
    """What is missing or malformed in the push's own fields (the entries are judged separately)."""
    found = []
    for name in ("bridgeId", "machineId", "storeId", "snapshotDigest"):
        if not isinstance(body.get(name), str) or not body[name].strip():
            found.append(f"{name}: a non-empty string is required")
    if not is_counter(body.get("revision")):
        found.append("revision: a whole number from 1 is required")
    return found


async def _removed_incarnation(db, agent_id: str, store_id: str) -> int:
    """The newest incarnation of this id a done removal request took from this store, or 0.

    `done` means the host removed exactly `expected_incarnation` (C4 checks it before applying), and
    request rows are never pruned, so this is durable evidence of what the operator removed. A done
    request settled `nothing removed` took nothing, so it is not counted: with the tombstone no longer
    gating this check, counting it refused the same lifetime defined again (db.py backfills the
    consequence of rows older than the column)."""
    row = await (await db.execute(
        # NOCASE, as the tombstone it answers for is matched (agent_sessions._agent_tombstone): `Coder` cleared
        # `coder`'s tombstone with a binary match here (review of cf4f5710).
        "SELECT MAX(expected_incarnation) AS taken FROM definition_requests WHERE agent_id = ? COLLATE NOCASE "
        "AND store_id = ? AND status = 'done' AND consequence IN ('pending', 'removed') "
        "AND json_type(patch, '$.remove') = 'true'", (agent_id, store_id),
    )).fetchone()
    return row["taken"] or 0


async def _id_refusal(db, entry: dict, owner: str, machine_id: str, store_id: str) -> str:
    """Why this machine may not define this id, worded as registration words it, or "".

    A push creates agent rows, so it refuses an operator name (no agent may hold one, whatever the key
    setting). A removed id (tombstoned) is DEFINED AGAIN by a newer definition: the host's operator wrote
    it after the removal, which registration's `restoreDeleted` from a fresh launch also allows. What is
    refused is a definition no newer than one a removal took, the stale copy the tombstone guards against
    (found live 2026-10-04: a re-defined id was refused for good, and nothing on the host said so).

    THE REMOVAL HISTORY decides, with or without a tombstone: a newer definition clears the tombstone, and the
    removed copy must stay refused after it (review of 78052e25). A store's incarnations only rise, so a
    definition its host writes after the removal is always above it.
    """
    agent_id = entry["id"]
    if owner and owner != machine_id:
        return f"defined on {owner}"
    if is_operator_actor(agent_id):
        return "reserved for the operator and cannot be an agent id"
    taken = await _removed_incarnation(db, agent_id, store_id)
    if taken and entry.get("incarnation", 0) <= taken:
        return (f"incarnation {entry.get('incarnation')} is no newer than incarnation {taken}, which the "
                f"operator removed; define it again (`aify-env agents set`) to restore it")
    return ""


async def _ensure_agent_row(db, entry: dict, machine_id: str, now: str) -> None:
    # ADMITTED BY C1 (definition_schema.py): every field is present and of its type, so nothing here
    # supplies a default the host never wrote.
    agent = entry["definition"]
    descriptive = (agent["name"], agent["role"], agent["instructions"], 1 if agent["herdrSpace"] else 0)
    row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (entry["id"],))).fetchone()
    if row:
        await db.execute(
            "UPDATE agents SET name = ?, role = ?, instructions = ?, herdr_space = ?, definition_state = 'defined' "
            "WHERE id = ?",
            (*descriptive, entry["id"]),
        )
        return
    await db.execute(
        "INSERT INTO agents (id, name, role, instructions, herdr_space, runtime, session_mode, cwd, model, "
        "machine_id, runtime_config, definition_state, registered_at, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'defined', ?, ?)",
        (entry["id"], *descriptive, HARNESS_RUNTIME[agent["harness"]], agent["mode"], agent["workspace"],
         agent["model"], machine_id, json.dumps({"effort": agent["effort"]} if agent["effort"] else {}), now, now),
    )


async def apply_definition_push(db, environment: dict, body: dict, now: str) -> dict:
    """Judge and apply one push inside the caller's transaction. Raises HTTPException on a refusal.

    Returns what changed: `outcome` (applied, replay), and the ids `applied`, `kept` (an invalid file
    whose last good definition is kept), `withdrawn`, `refused` (with the reason, for that id only) and
    `invalid` (an invalid file for an id this machine has no definition of).
    """
    problems = push_body_problems(body)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    machine_id = body["machineId"].strip()
    refusal = fence_refusal(environment, body["bridgeId"], machine_id)
    if refusal:
        raise HTTPException(409, f"definition push refused: {refusal}")
    entries = body.get("entries")
    problems = snapshot_problems(entries, body["snapshotDigest"])
    if problems:
        raise HTTPException(422, "; ".join(problems))

    store_id, revision = body["storeId"].strip(), body["revision"]
    current_row = await (await db.execute(
        "SELECT store_id, revision, snapshot_digest, outcome FROM definition_stores WHERE machine_id = ?", (machine_id,),
    )).fetchone()
    current = dict(current_row) if current_row else None
    retired_rows = await (await db.execute(
        "SELECT store_id, retired_by, retired_at FROM definition_stores_retired WHERE machine_id = ?", (machine_id,),
    )).fetchall()
    retired = {row["store_id"]: row for row in retired_rows}
    order = push_order(current, set(retired), store_id, revision, body["snapshotDigest"])

    if order is PushOrder.RETIRED:
        await db.execute(
            "UPDATE definition_stores_retired SET refused_count = refused_count + 1, last_refused_at = ? "
            "WHERE machine_id = ? AND store_id = ?", (now, machine_id, store_id),
        )
        by = retired[store_id]
        raise HTTPException(409, f"definition store {store_id} was retired on {machine_id} by store "
                                 f"{by['retired_by']} at {by['retired_at']}: two stores claim machine {machine_id}")
    if order is PushOrder.STALE:
        raise HTTPException(409, f"stale: store {store_id} is already at revision {current['revision']}")
    if order is PushOrder.CONFLICT:
        raise HTTPException(409, f"conflict: store {store_id} revision {revision} was already applied with "
                                 f"another digest; nothing applied")
    if order is PushOrder.REPLAY:
        # THE HOST IS STILL HERE, though nothing changed: the one write a replay makes. aify-env re-sends an
        # unchanged snapshot every minute, so without this stamp an idle host and a gone one look the same
        # (P0 C11, arm 3). No definition, revision or outcome moves.
        await db.execute("UPDATE definition_stores SET pushed_at = ? WHERE machine_id = ? AND store_id = ?",
                         (now, machine_id, store_id))
        return await _replayed(db, machine_id, store_id, current["outcome"], entries)

    if order is PushOrder.NEW_STORE:
        await db.execute(
            "INSERT OR IGNORE INTO definition_stores_retired (machine_id, store_id, retired_by, retired_at) "
            "VALUES (?, ?, ?, ?)", (machine_id, current["store_id"], store_id, now),
        )
    await db.execute(
        "INSERT INTO definition_stores (machine_id, store_id, revision, snapshot_digest, environment_id, updated_at, pushed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(machine_id) DO UPDATE SET store_id = excluded.store_id, "
        "revision = excluded.revision, snapshot_digest = excluded.snapshot_digest, "
        "environment_id = excluded.environment_id, updated_at = excluded.updated_at, pushed_at = excluded.pushed_at",
        (machine_id, store_id, revision, body["snapshotDigest"], str(environment.get("id") or ""), now, now),
    )
    result = await _apply_entries(db, machine_id, store_id, entries, now)
    unresolved = {"refused": [r["id"] for r in result["refused"]], "invalid": result["invalid"], "kept": result["kept"]}
    await db.execute("UPDATE definition_stores SET outcome = ? WHERE machine_id = ?", (json.dumps(unresolved), machine_id))
    return result


#: A refused id that has since become free. A replay never takes it (C3: a replay changes nothing).
FREE_SINCE = "free since this revision was applied; a fresh revision defines it"


async def _replayed(db, machine_id: str, store_id: str, recorded, entries: list[dict]) -> dict:
    """THE SAME REVISION GETS THE SAME ANSWER, re-judged and never re-applied. What the revision left
    unresolved is reported again, so a refusal is not lost when a host repeats its push. A refused id
    is judged as it stands now, read-only: one that has become free says so, and the host defines it
    with a fresh revision (P0 C3).

    `outcomeRecorded` is false for a revision applied before outcomes were stored (a P3a database):
    its empty lists then mean "unknown", and a fresh revision is how the host learns the answer."""
    unresolved = json.loads(recorded) if recorded is not None else {}
    owners = {row["agent_id"]: row["machine_id"] for row in await (await db.execute(
        "SELECT agent_id, machine_id FROM agent_definitions")).fetchall()}
    # A stored refusal cannot have become this machine's own: only a fresh revision or a new store
    # defines an id here, and either one replaces the stored outcome.
    sent = {entry["id"]: entry for entry in entries}
    refused = [{"id": agent_id, "reason": await _id_refusal(
                    db, sent.get(agent_id, {"id": agent_id}), owners.get(agent_id), machine_id, store_id) or FREE_SINCE}
               for agent_id in unresolved.get("refused", [])]
    return {"outcome": "replay", "applied": [], "withdrawn": [], "refused": refused,
            "invalid": unresolved.get("invalid", []), "kept": unresolved.get("kept", []),
            "outcomeRecorded": recorded is not None}


async def _apply_entries(db, machine_id: str, store_id: str, entries: list[dict], now: str) -> dict:
    held = {row["agent_id"]: row for row in await (await db.execute(
        "SELECT agent_id, machine_id, store_id, incarnation FROM agent_definitions")).fetchall()}
    owners = {agent_id: row["machine_id"] for agent_id, row in held.items()}
    result = {"outcome": "applied", "applied": [], "kept": [], "withdrawn": [], "refused": [], "invalid": []}
    for entry in entries:
        owner = owners.get(entry["id"])
        refusal = await _id_refusal(db, entry, owner, machine_id, store_id)
        if refusal:
            result["refused"].append({"id": entry["id"], "reason": refusal})
            continue
        if entry["state"] == "invalid":
            if owner == machine_id:
                # THE LAST GOOD DEFINITION STAYS. A broken file is not a removal (C3).
                await db.execute(
                    "UPDATE agent_definitions SET host_state = 'invalid', host_problems = ?, updated_at = ? "
                    "WHERE agent_id = ?", (json.dumps(sorted(entry["problems"])), now, entry["id"]),
                )
                result["kept"].append(entry["id"])
            else:
                result["invalid"].append(entry["id"])
            continue
        await db.execute(
            "INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, definition_digest, "
            "body, available, unavailable_reason, host_state, host_problems, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', '[]', ?) ON CONFLICT(agent_id) DO UPDATE SET "
            "machine_id = excluded.machine_id, store_id = excluded.store_id, incarnation = excluded.incarnation, "
            "revision = excluded.revision, definition_digest = excluded.definition_digest, body = excluded.body, "
            "available = excluded.available, unavailable_reason = excluded.unavailable_reason, "
            "host_state = 'valid', host_problems = '[]', updated_at = excluded.updated_at",
            (entry["id"], machine_id, store_id, entry["incarnation"], entry["revision"], entry["definitionDigest"],
             canonical(entry["definition"]), 1 if entry["available"] else 0,
             "" if entry["available"] else entry["unavailableReason"], now),
        )
        await _ensure_agent_row(db, entry, machine_id, now)
        # DEFINED AGAIN: the removal's tombstone goes with it, or the worker's registration is refused.
        await db.execute("DELETE FROM agent_tombstones WHERE agent_id = ? COLLATE NOCASE", (entry["id"],))
        result["applied"].append(entry["id"])
    # WITHDRAWAL IS NOT REMOVAL (C6): the definition goes, the agent, its sessions, messages and runs
    # stay, and a running worker keeps running.
    present = {entry["id"] for entry in entries}
    for agent_id, owner in owners.items():
        if owner == machine_id and agent_id not in present:
            withdrawn = {"machineId": machine_id, "storeId": held[agent_id]["store_id"],
                         "incarnation": held[agent_id]["incarnation"]}
            await db.execute("DELETE FROM agent_definitions WHERE agent_id = ?", (agent_id,))
            await db.execute("UPDATE agents SET definition_state = 'withdrawn', definition_withdrawn = ? WHERE id = ?",
                             (json.dumps(withdrawn), agent_id))
            result["withdrawn"].append(agent_id)
    return result
