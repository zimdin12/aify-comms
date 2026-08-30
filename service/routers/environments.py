"""The `environments` route domain: registration heartbeat, roots, and environment controls.

v0.5.2f. Six handlers and three domain-local helpers. The constant that came with them,
SUPERSEDE_STOP_STALE_SECONDS, left again in v0.5.4 with the only block that read it.

This is the first domain whose handlers WRITE state that the fleet depends on — the heartbeat is how
an environment bridge stays ONLINE, and `aify-comms doctor`'s `env-bridge` check keys on exactly that
status. So the domain bars matter more here than they did for stats: every handler is byte-identical,
and the mutating routes stay on `JsonApiRoute` and keep their SQLite write-lock retry.

BORROWED, measured: `_environment_record_to_dict` has nine users outside this domain, so it is
reached through a function-scope import rather than copied. Everything else the handlers touch is
already leaf-owned.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Request

from service import longpoll
from service.api_core.claim_emptiness import environment_control_is_empty
from service.environment_claim import _claim_environment_control_once
from service.api_core.environment_registration import _record_environment_registration
from service.api_core.superseded_bridge_stops import _queue_stop_for_superseded_bridge
from service.api_core.routing import domain_router
from service.api_core.records import _environment_record_to_dict
from service.api_core.serialization import _json_loads_or, _parsed_timestamp, _timestamp_sort_key
from service.api_core.runtime import _normalize_runtime
from service.api_core.settings import _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.env_status import ENVIRONMENT_REGISTRABLE_STATUSES
from service.env_status import environment_effective_status as _environment_effective_status
from service.models import (
    EnvironmentControlClaim,
    EnvironmentControlRequest,
    EnvironmentControlUpdate,
    EnvironmentHeartbeat,
    EnvironmentRootsUpdate,
)
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger("aify_comms.routers.environments")

router = domain_router()




# SUPERSEDE_STOP_STALE_SECONDS moved to service/api_core/superseded_bridge_stops.py in v0.5.4 —
# it travelled with the drain that was its only reader.


def _canonical_runtimes(rows: Any) -> list:
    """Runtime rows with their names put through the shared vocabulary.

    THE SERVICE OWNS THE VOCABULARY, and this is the half it was not doing. A host sends the names it
    can see on disk -- `claude`, `omp` -- because `service/contracts/vocabulary.json` already maps them
    in both languages with an agreement test per side, and a second copy of that map in the environment
    tier is exactly the drift the contract exists to prevent.

    NOT A CORRECTNESS FIX. Both readers of these rows normalise both sides already, so a stored
    `claude` matches a lookup for `claude-code`. What it fixes is a row that reads `claude` while every
    agent on it reads `claude-code` -- two screens that agree only if you know the alias table.

    Idempotent: `claude-code` maps to itself, so a bridge sending canonical names is unaffected. A row
    that is not a dict is passed through rather than dropped, because inventing a shape is worse than
    storing an odd one, and the readers all use `.get`.
    """
    if not isinstance(rows, list):
        return rows
    canonical = []
    for row in rows:
        if not isinstance(row, dict):
            canonical.append(row)
            continue
        name = _normalize_runtime(row.get("runtime"))
        canonical.append({**row, "runtime": name} if name else row)
    return canonical


def _derived_environment_id(kind: Any, hostname: Any) -> str:
    """`kind:hostname:default`, the id a caller may omit.

    ONE IMPLEMENTATION, HERE. The join keys this service's own table, and a second advertiser that
    built the same string itself would agree until either copy of the rule changed -- at which point
    it would not fail, it would create a DUPLICATE environment beside the real one and leave the
    managed agents bound to whichever the bridge wrote.

    `kind` is host knowledge the service cannot compute: it distinguishes wsl, docker, windows,
    macos and linux from environment variables and `/.dockerenv` on the host itself. So the host
    sends the two facts and the service performs the join.

    THE HOSTNAME IS NOT LOWERCASED, and that is inherited rather than chosen. The live row is
    `windows:StevenZ-L:default` while its `machineId` is `win32:stevenz-l` -- the service normalises
    machineId with a field validator and has never normalised this. Lowercasing here would mint a
    new id for every existing environment and orphan the agents bound to the old one.

    Returns "" when either fact is missing, so the caller's own "id is required" refusal still fires
    rather than a half-built id like `windows::default` reaching the table.
    """
    kind_text = str(kind or "").strip()
    host_text = str(hostname or "").strip()
    if not kind_text or not host_text:
        return ""
    return f"{kind_text}:{host_text}:default"


#: What only a BRIDGE can know about itself, and therefore what an advertisement must leave alone.
#:
#: An environment-tier heartbeat describes the HOST -- runtimes, roots, terminal availability -- and
#: declares no `bridgeId`. Its metadata carries no `bridgeStartedAt` either, and `next_metadata`
#: REPLACES the stored metadata, so an advertisement erased the timestamp the supersede arbitration
#: reads. Preserving `bridge_id` alone was not enough: with two ids and no start times, the branch
#: that refuses an OLDER incoming bridge cannot fire, and a stale bridge reclaims the environment a
#: fresh one owns.
#:
#: DERIVED FROM ITS READERS, not guessed: `bridgeStartedAt` is read by `_bridge_started_at` here and
#: by `environment_claim.py`, and nothing else in the service reads a `bridge*` metadata key. A
#: second one belongs in this tuple the day it gets a reader.
#: The metadata keys only a BRIDGE can answer for, DERIVED by prefix rather than listed.
#:
#: A list of two was here, and it was one short: `bridgeBuild` rides in the same blob, `next_metadata`
#: replaces the blob, and aify-env's advertisement beats every 30s -- so a bridge's reported build was
#: erased within half a minute of being written, and `bridge-current` went back to reporting no
#: evidence. That check exists to answer "is a running bridge executing old code", which nothing else
#: can, and the cutover silently disabled it.
#:
#: `bridge*` is the honest rule: aify-env is not a bridge and sends no key by that name, so a prefix
#: match cannot over-claim, and a key added to the bridge's payload later is covered without anyone
#: remembering this line. `BRIDGE_OWNED_METADATA` stays as the explicit floor for the two that must be
#: preserved even if the prefix convention is ever broken.
BRIDGE_OWNED_METADATA = ("bridgeStartedAt", "bridgeLastSeen", "bridgeBuild")


def _bridge_owned_metadata_keys(existing_metadata) -> tuple:
    """Every key in the stored metadata that only a bridge could have written.

    Prefix-derived, with the named floor unioned in so a rename upstream cannot silently drop one.
    """
    stored = tuple(
        key for key in (existing_metadata or {})
        if isinstance(key, str) and key.startswith("bridge")
    )
    return tuple(dict.fromkeys(BRIDGE_OWNED_METADATA + stored))

#: What only the HOST can answer, and therefore what a caller describing no host must not erase.
#:
#: These three ride inside `metadata` rather than in columns, and `next_metadata` replaces the blob --
#: so the preservation rule the nine columns got did not reach them. Since the 2026-08-30 cutover the
#: aify-comms bridge stands down from describing a host whenever aify-env is advertising it, and its
#: beat carries none of the three; without this, standing down empties the terminal answer instead of
#: leaving it to the tier that owns it.
#:
#: Each is paired with the REQUEST FIELD that produces it, because "the caller said nothing about the
#: terminal" is the condition, and the metadata key is only where the answer is kept.
HOST_OWNED_METADATA = (
    ("terminal", "terminal"),
    ("pty", "pty"),
    ("terminalRuntimes", "terminalRuntimes"),
)


def _bridge_started_at(metadata: Any) -> str:
    if isinstance(metadata, dict):
        # `_parsed_timestamp`: this value comes from the registering bridge and decides whether a
        # FORGOTTEN environment may come back. A sort key keeps an unparseable string as itself and
        # letters sort above digits, so "now" beat every real `forgottenAt`. Same hole as the agent
        # tombstone gate, in the guard that explicitly says it "mirrors" it -- both fixed together.
        return _parsed_timestamp(metadata.get("bridgeStartedAt"))
    return ""


def _normalize_roots(roots: Optional[list[str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for root in roots or []:
        value = str(root or "").strip()
        if not value or value.startswith("-"):
            continue
        key = value.replace("\\", "/").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


@router.get("/environments")
async def list_environments(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM environments WHERE status != 'forgotten'")
        environments = [
            _environment_record_to_dict(row, offline_seconds=settings.get("environment_offline_seconds", 90))
            for row in await cursor.fetchall()
        ]
        status_rank = {"online": 0, "degraded": 1, "unknown": 2, "offline": 3, "disabled": 4}
        environments.sort(key=lambda env: (status_rank.get(env.get("status") or "", 5), str(env.get("label") or "").lower(), str(env.get("id") or "").lower()))
        return {"ok": True, "environments": environments}
    finally:
        await db.close()


@router.post("/environments/heartbeat")
async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):
    env_id = str(req.id or "").strip() or _derived_environment_id(req.kind, req.hostname)
    if not env_id:
        raise HTTPException(400, "Environment id is required")

    now = _now()
    #: `is None`, NOT falsiness. For a list, "said nothing" and "said there are none" are different
    #: claims, and `or []` collapsed them -- so a heartbeat that omitted either field erased it. The
    #: stored value is restored below, once `existing` has been read.
    cwd_roots = _normalize_roots(req.cwdRoots) if req.cwdRoots is not None else None
    runtimes = _canonical_runtimes(req.runtimes) if req.runtimes is not None else None
    metadata = req.metadata or {}
    if req.terminal is not None:
        metadata["terminal"] = bool(req.terminal)
    if req.pty is not None:
        metadata["pty"] = bool(req.pty)
    if req.terminalRuntimes is not None:
        metadata["terminalRuntimes"] = [
            _normalize_runtime(str(runtime or ""))
            for runtime in req.terminalRuntimes
            if str(runtime or "").strip()
        ]
    requested_status = str(req.status or "online").strip().lower()
    if requested_status not in ENVIRONMENT_REGISTRABLE_STATUSES:
        requested_status = "online"
    db = await get_db()
    try:
        existing_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        existing = await existing_cursor.fetchone()
        # Forget-tombstone guard (2026-06-03): a row in `forgotten` status is the
        # environment-level equivalent of an agent tombstone. A passive heartbeat
        # from a still-running aify-comms bridge that predates the forget MUST NOT
        # resurrect it (the old bug: the blind UPDATE below flipped status back to
        # 'online' seconds after the operator forgot the env). Only a genuine fresh
        # (re)launch — a bridge whose bridgeStartedAt is newer than forgottenAt —
        # is allowed to clear the tombstone and re-register. Mirrors how agent
        # registration honors agent_tombstones unless explicitly restored.
        if existing and str(existing["status"] or "").strip().lower() == "forgotten":
            forgotten_meta = _json_loads_or(existing["metadata"], {})
            forgotten_at = _timestamp_sort_key(forgotten_meta.get("forgottenAt"))
            incoming_started = _bridge_started_at(metadata)
            relaunched = bool(incoming_started) and (not forgotten_at or incoming_started > forgotten_at)
            if not relaunched:
                # Lingering/passive heartbeat — keep the env forgotten, do not touch
                # last_seen or status. Return the tombstoned record as-is.
                return {"ok": True, "environment": _environment_record_to_dict(existing), "forgotten": True}
        registered_at = existing["registered_at"] if existing else now
        existing_metadata = _json_loads_or(existing["metadata"], {}) if existing else {}
        manual_roots = bool(existing_metadata.get("manualRoots"))
        #: Three cases, and the middle one is the fix. Manual roots always win; a caller that said
        #: nothing keeps what the row holds; a caller that spoke is believed, including when it says
        #: the list is empty.
        stored_roots = _json_loads_or(existing["cwd_roots"], []) if existing else []
        if manual_roots and existing:
            effective_roots = stored_roots
        elif cwd_roots is None:
            effective_roots = stored_roots
        else:
            effective_roots = cwd_roots
        #: Same rule for the runtimes. Blanking these is the worst of the set: an environment with no
        #: runtimes advertises nothing that can be spawned on it.
        if runtimes is None:
            runtimes = _json_loads_or(existing["runtimes"], []) if existing else []
        #: What the CALLER advertised, which is a different fact from what the row now holds. A
        #: caller that said nothing has not advertised an empty list, so the previous claim stands.
        advertised_roots = cwd_roots if cwd_roots is not None else (
            existing_metadata.get("advertisedCwdRoots", []) if existing else []
        )
        next_metadata = {**metadata, "advertisedCwdRoots": advertised_roots}
        # WHEN A BRIDGE LAST SPOKE, which is a different question from when this ROW was last written.
        # Only a bridge sends a `bridgeId`, so only a bridge sets this; an advertisement from aify-env
        # preserves it below rather than refreshing it. Without the split, a host with no bridge reads
        # `online` off aify-env's beat and accepts spawns nothing can claim.
        if str(req.bridgeId or "").strip():
            next_metadata["bridgeLastSeen"] = now
        if not str(req.bridgeId or "").strip():
            for bridge_key in _bridge_owned_metadata_keys(existing_metadata):
                if bridge_key in existing_metadata and bridge_key not in next_metadata:
                    next_metadata[bridge_key] = existing_metadata[bridge_key]
        # And the host's own answers, for a caller that described no host. Keyed on the request field
        # being absent: a caller that sent `terminal: false` is making a claim and is believed.
        for request_field, metadata_key in HOST_OWNED_METADATA:
            if getattr(req, request_field, None) is None and metadata_key in existing_metadata:
                next_metadata.setdefault(metadata_key, existing_metadata[metadata_key])
        if manual_roots:
            next_metadata.update({
                "manualRoots": True,
                "manualRootsUpdatedAt": existing_metadata.get("manualRootsUpdatedAt", ""),
                "manualRootsUpdatedBy": existing_metadata.get("manualRootsUpdatedBy", ""),
            })
        superseded_bridge_id = ""
        if existing and str(existing["bridge_id"] or "").strip() and str(req.bridgeId or "").strip():
            existing_bridge_id = str(existing["bridge_id"] or "").strip()
            incoming_bridge_id = str(req.bridgeId or "").strip()
            if existing_bridge_id != incoming_bridge_id:
                existing_metadata = _json_loads_or(existing["metadata"], {})
                existing_started = _bridge_started_at(existing_metadata)
                incoming_started = _bridge_started_at(metadata)
                if existing_started and (not incoming_started or incoming_started < existing_started):
                    return {"ok": True, "environment": _environment_record_to_dict(existing)}
                if incoming_started and (not existing_started or incoming_started > existing_started):
                    superseded_bridge_id = existing_bridge_id
        if (
            existing
            and requested_status != "online"
            and str(existing["bridge_id"] or "").strip()
            and str(req.bridgeId or "").strip()
            and str(existing["bridge_id"] or "").strip() != str(req.bridgeId or "").strip()
        ):
            return {"ok": True, "environment": _environment_record_to_dict(existing)}
        await _record_environment_registration(
            db, existing, env_id, req, effective_roots, runtimes, requested_status,
            next_metadata, registered_at, now,
        )
        await _queue_stop_for_superseded_bridge(db, env_id, superseded_bridge_id, req, now)
        # Env recovery / status transition: when the env flips between online and
        # offline/degraded, bound agents' derived status (offline ↔ available/online)
        # changes too. Invalidate their live-status cache so the transition shows
        # immediately rather than after the ~90s env window / 60s sweep.
        prior_status = str((existing["status"] if existing else "") or "").strip().lower()
        # Env-down is DERIVED from staleness and never writes environments.status, so on
        # recovery prior_status == requested_status == 'online' and the stored-column
        # check below is skipped — leaving bound agents cached 'offline' up to the ~180s
        # horizon (bughunt 2026-07-03). Also invalidate when the env was EFFECTIVELY
        # offline (stale last_seen) before this heartbeat, regardless of the stored column.
        _env_offline_seconds = max(30, int((await _load_settings(db)).get("environment_offline_seconds", 90) or 90))
        prior_effective = _environment_effective_status(existing, offline_seconds=_env_offline_seconds) if existing else "offline"
        if existing and (prior_status != requested_status or prior_effective == "offline"):
            bound_rows = await (await db.execute(
                "SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?",
                (env_id,),
            )).fetchall()
            for bound in bound_rows:
                bound_agent = str(bound["agent_id"] or "").strip()
                if bound_agent:
                    await _invalidate_agent_live_state(db, bound_agent)
        await db.commit()
        row_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        row = await row_cursor.fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_heartbeat", {"environmentId": env_id, "bridgeId": req.bridgeId or ""})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


@router.patch("/environments/{environment_id:path}/roots")
async def update_environment_roots(environment_id: str, req: EnvironmentRootsUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        metadata = _json_loads_or(env["metadata"], {})
        if req.resetToBridgeAdvertised:
            roots = _normalize_roots(metadata.get("advertisedCwdRoots") or _json_loads_or(env["cwd_roots"], []))
            next_metadata = {k: v for k, v in metadata.items() if k not in {"manualRoots", "manualRootsUpdatedAt", "manualRootsUpdatedBy"}}
            next_metadata["manualRoots"] = False
            next_metadata["manualRootsResetAt"] = now
            next_metadata["manualRootsResetBy"] = req.requestedBy or "dashboard"
        else:
            roots = _normalize_roots(req.roots or [])
            if not roots:
                raise HTTPException(400, "At least one root is required. Use resetToBridgeAdvertised to return to bridge-advertised roots.")
            next_metadata = {
                **metadata,
                "manualRoots": True,
                "manualRootsUpdatedAt": now,
                "manualRootsUpdatedBy": req.requestedBy or "dashboard",
                "previousCwdRoots": _json_loads_or(env["cwd_roots"], []),
            }
        await db.execute(
            """
            UPDATE environments
            SET cwd_roots = ?,
                metadata = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(roots), json.dumps(next_metadata), now, environment_id),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_roots_updated", {"environmentId": environment_id})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


@router.post("/environments/{environment_id:path}/control")
async def control_environment(environment_id: str, req: EnvironmentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"stop", "forget"}:
        raise HTTPException(400, "Environment control action must be stop or forget")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        if action == "forget":
            await db.execute("DELETE FROM environment_controls WHERE environment_id = ?", (environment_id,))
            await db.execute(
                """
                UPDATE environments
                SET status = 'forgotten',
                    bridge_id = '',
                    bridge_version = '',
                    runtimes = '[]',
                    metadata = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (json.dumps({**_json_loads_or(env["metadata"], {}), "forgottenAt": now, "forgottenBy": req.requestedBy or "dashboard"}), now, environment_id),
            )
            await db.commit()
            ws = await _get_ws(request)
            if ws: await ws.broadcast("environment_forgotten", {"environmentId": environment_id})
            return {"ok": True, "action": action, "environmentId": environment_id}

        control_id = f"envctl-{uuid.uuid4().hex}"
        await db.execute(
            """
            INSERT INTO environment_controls (
                id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                control_id,
                environment_id,
                env["bridge_id"] or "",
                env["machine_id"] or "",
                action,
                "pending",
                req.requestedBy or "dashboard",
                now,
            ),
        )
        await db.execute("UPDATE environments SET status = ? WHERE id = ?", ("disabled", environment_id))
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'lost',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE environment_id = ?
              AND status IN ('starting', 'running', 'recovering', 'restarting')
            """,
            (now, now, environment_id),
        )
        await db.execute(
            """
            UPDATE agents
            SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                launch_mode = 'none',
                runtime_state = '{}',
                last_seen = ?
            WHERE id IN (SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?)
            """,
            (now, environment_id),
        )
        # `offline` is not a manual-status short-circuit, so the live-status cache
        # would otherwise keep serving the old status for these bound agents until
        # the 60s sweep. Invalidate each so the disable reflects immediately.
        bound_rows = await (await db.execute(
            "SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?",
            (environment_id,),
        )).fetchall()
        for bound in bound_rows:
            bound_agent = str(bound["agent_id"] or "").strip()
            if bound_agent:
                await _invalidate_agent_live_state(db, bound_agent)
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_requested", {"environmentId": environment_id, "action": action})
        return {"ok": True, "controlId": control_id, "action": action, "environmentId": environment_id}
    finally:
        await db.close()


@router.post("/environments/controls/claim")
async def claim_environment_control(req: EnvironmentControlClaim):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only on the
    # exact "nothing pending" shape; a claimed control (has controlId) returns at once.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_environment_control_once(req),
        environment_control_is_empty,
        scope="env-control",
        fallback_s=3.0,
        lock_result={"ok": True, "control": None},
    )


# _claim_environment_control_once moved to service/environment_claim.py in v0.5.4 - it owns
# its own connection and transaction, which is the service-level rule dispatch_claim.py set.


@router.patch("/environments/controls/{control_id}")
async def update_environment_control(control_id: str, req: EnvironmentControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, "Environment control status must be completed or failed")
    db = await get_db()
    try:
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = ?, handled_at = ?, error = ? WHERE id = ?",
            (status, now, req.error or "", control_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_updated", {"controlId": control_id, "status": status})
        return {"ok": True, "controlId": control_id, "status": status}
    finally:
        await db.close()
