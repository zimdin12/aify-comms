"""Recording an environment heartbeat: one write transaction from the host-proof read to the commit.

MOVED FROM `service/routers/environments.py` (audit item S2), statement for statement:
`service/tests/test_heartbeat_and_analytics_sql_freeze.py` pins every SQL statement, its parameters,
the commit and the broadcast, recorded before the move. What a beat says is read in
`service/api_core/environment_beat.py`; this module decides what the row becomes.

THE ORDER IS THE CONTRACT. `BEGIN IMMEDIATE` comes before the first read, so the host proof is judged
and the row written under one write lock. Every refusal returns before any write. The commit comes
before the held-terminal cleanup, which only an accepted beat may run, and the router broadcasts only
after this returns, so a broadcast never announces an uncommitted row.

The caller owns the connection: it opens it, and it closes it after broadcasting.

The logger keeps the router's name so a refactor does not silently move somebody's log filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from service.api_core.environment_beat import (
    HOST_OWNED_METADATA,
    EnvironmentBeat,
    _bridge_owned_metadata_keys,
    _bridge_started_at,
    _is_host_tier,
)
from service.api_core.environment_registration import _record_environment_registration
from service.api_core.host_proof import enroll_host_proof, judge_host_proof
from service.api_core.records import _environment_record_to_dict
from service.api_core.serialization import _json_loads_or, _timestamp_sort_key
from service.api_core.settings import _load_settings
from service.api_core.superseded_bridge_stops import _queue_stop_for_superseded_bridge
from service.env_status import environment_effective_status as _environment_effective_status
from service.env_status import environment_has_live_bridge as _environment_has_live_bridge
from service.reconcilers.host_held_terminals import end_terminals_the_host_no_longer_holds
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger("aify_comms.routers.environments")


@dataclass(frozen=True)
class HeartbeatOutcome:
    """What the beat came to. `refusal` is the host proof's refusal and nothing was written; `written`
    means the row was committed and the change may be announced; otherwise `body` is the answer."""

    body: dict = field(default_factory=dict)
    refusal: str = ""
    written: bool = False


@dataclass(frozen=True)
class StoredMerge:
    """The row this beat will write, once what the caller omitted is taken from what is stored."""

    registered_at: str
    effective_roots: Any
    runtimes: Any
    next_metadata: dict


@dataclass(frozen=True)
class ClaimArbitration:
    """Whether this beat may hold the row: the claimer it was refused in favour of, or the bridge it supersedes."""

    superseded_bridge_id: str = ""
    refused: Optional[dict] = None


async def record_environment_heartbeat(db, beat: EnvironmentBeat, presented: str) -> HeartbeatOutcome:
    env_id, req, now = beat.env_id, beat.req, beat.now
    # THE WRITE LOCK FROM THE FIRST READ TO THE COMMIT: the proof is judged and the row written as one, so a
    # beat that read "no proof yet" cannot write after another enrolled the machine (review of 08f3e7e5, H2-R1).
    await db.execute("BEGIN IMMEDIATE")
    existing_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
    existing = await existing_cursor.fetchone()
    # THE MACHINE'S HOST, PROVEN (HIGH 2): the row's machine and the one claimed, so a beat cannot move a
    # proven row to another machine id. Before any write, so a refused beat changes nothing.
    proof = await judge_host_proof(db, [existing["machine_id"] if existing else "", req.machineId], presented)
    if proof.refusal:
        return HeartbeatOutcome(refusal=proof.refusal)
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
        incoming_started = _bridge_started_at(beat.metadata)
        relaunched = bool(incoming_started) and (not forgotten_at or incoming_started > forgotten_at)
        if not relaunched:
            # Lingering/passive heartbeat — keep the env forgotten, do not touch
            # last_seen or status. Return the tombstoned record as-is.
            return HeartbeatOutcome(body={"ok": True, "environment": _environment_record_to_dict(existing), "forgotten": True})
    merged = merge_with_stored(beat, existing)
    claim = arbitrate_claim(beat, existing)
    if claim.refused is not None:
        return HeartbeatOutcome(body={
            "ok": True,
            "environment": _environment_record_to_dict(existing),
            "claimer": claim.refused,
        })
    await _record_environment_registration(
        db, existing, env_id, req, merged.effective_roots, merged.runtimes, beat.requested_status,
        merged.next_metadata, merged.registered_at, now,
    )
    await _queue_stop_for_superseded_bridge(db, env_id, claim.superseded_bridge_id, req, now)
    await _invalidate_bound_agents_on_transition(db, env_id, existing, beat.requested_status)
    await enroll_host_proof(db, proof, presented, now)
    await db.commit()
    #: ONLY HERE, past every refusal: a superseded or refused beat carries another host's view.
    if str(req.bridgeId or "").strip():
        await end_terminals_the_host_no_longer_holds(
            db, env_id, beat.held_terminals, offline=beat.requested_status == "offline",
            bridge_id=str(req.bridgeId or "").strip(),
        )
    row_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
    row = await row_cursor.fetchone()
    environment = _environment_record_to_dict(row)
    # THE ACCEPTED CASE SAYS SO TOO, and it has to: `accepted: False` is only legible against an
    # `accepted: True` that a caller can also see. Without this pair, a caller could not tell a
    # refusal from a service too old to answer the question -- and "the field is missing" would
    # have to be read as success, which is how the silence got here in the first place.
    #
    # `bridgeId` is echoed rather than assumed: a beat with no bridgeId is an ADVERTISEMENT, not
    # a claim, and reporting it as an accepted claimer would invent the very authority the
    # advertise/claim split exists to withhold.
    claimer_id = str(req.bridgeId or "").strip()
    return HeartbeatOutcome(written=True, body={
        "ok": True,
        "environment": environment,
        "claimer": {
            "accepted": bool(claimer_id),
            "bridgeId": claimer_id,
            "reason": "" if claimer_id else "no bridgeId: this beat describes the host, it does not claim work",
        },
    })


def merge_with_stored(beat: EnvironmentBeat, existing) -> StoredMerge:
    """What the row becomes: the caller's claims, and the stored values for everything it did not mention."""
    req, now, cwd_roots, runtimes, metadata = beat.req, beat.now, beat.cwd_roots, beat.runtimes, beat.metadata
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
    #: OWNERSHIP IS ENFORCED AT THE BOUNDARY, not assumed from a naming convention.
    #:
    #: Deriving the preserved set by prefix decided WHICH keys are bridge-owned and said nothing
    #: about who may WRITE them. Preservation below runs as "restore the stored value if the key
    #: is not already here" — and `next_metadata` starts as a copy of the CALLER's metadata, so
    #: a caller that SENT `bridgeBuild` already had the key present and its forged value won.
    #:
    #: What that bought a host advertiser was not cosmetic. `bridgeLastSeen` is the evidence the
    #: spawn gate reads to decide a host has a live bridge, so forging it gets spawns accepted
    #: that nothing can claim. `bridgeBuild` is what `bridge-current` compares against repo HEAD,
    #: so forging it silences the one instrument that can say a bridge is running old code.
    #:
    #: Only a bridge sends a `bridgeId`. A caller without one has its whole `bridge*` namespace
    #: dropped before the merge; everything else it sent is kept, so an advertisement is still an
    #: advertisement. aify-env emits no such key by design, which a cross-repo test pins.
    if not str(req.bridgeId or "").strip():
        metadata = {
            key: value for key, value in metadata.items()
            if not (isinstance(key, str) and key.startswith("bridge"))
        }
    next_metadata = {**metadata, "advertisedCwdRoots": advertised_roots}
    # WHERE THE HOST'S aify-env WILL START WORK, which only aify-env reports. Kept when another caller (a bridge)
    # beats without it, so the status that reads it does not flicker with whoever spoke last.
    if "launchRoots" not in next_metadata and "launchRoots" in existing_metadata:
        next_metadata["launchRoots"] = existing_metadata["launchRoots"]
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
    return StoredMerge(registered_at=registered_at, effective_roots=effective_roots, runtimes=runtimes,
                       next_metadata=next_metadata)


def arbitrate_claim(beat: EnvironmentBeat, existing) -> ClaimArbitration:
    """Whether a beat from one bridge may take a row another bridge holds.

    Runs only when both name a bridge, and then the incoming metadata is the caller's own: the
    `bridge*` filter in `merge_with_stored` applies only to a beat with no `bridgeId`.
    """
    env_id, req, metadata = beat.env_id, beat.req, beat.metadata
    superseded_bridge_id = ""
    if existing and str(existing["bridge_id"] or "").strip() and str(req.bridgeId or "").strip():
        existing_bridge_id = str(existing["bridge_id"] or "").strip()
        incoming_bridge_id = str(req.bridgeId or "").strip()
        if existing_bridge_id != incoming_bridge_id:
            existing_metadata = _json_loads_or(existing["metadata"], {})
            existing_started = _bridge_started_at(existing_metadata)
            incoming_started = _bridge_started_at(metadata)
            # A DEAD INCUMBENT HAS NO STANDING, and this is the half that was missing.
            #
            # Arbitration compared START TIMES and never asked whether the holder was alive, so a
            # bridge that started later and then EXITED held the row for ever: nothing older
            # could take it, and only something started even later could. Measured 2026-09-03 --
            # a restarted aify-env claimed the row, exited, and the surviving daemon beat every
            # 30 seconds for 20 minutes and was refused every time, while `/spawn` told the
            # operator to start a claimer that was already running.
            #
            # `bridgeLastSeen` is the liveness fact this service already keeps and already gates
            # `/spawn` on. Arbitration ignoring it was the inconsistency: the endpoint would not
            # have let the incumbent claim anything either. Stale means beyond the SAME window,
            # so the two answers cannot disagree.
            #
            # This can only ever ADMIT a live beat, never refuse one -- the branch below is
            # skipped, not extended -- and by the time it applies, the incumbent has been silent
            # longer than any spawn would have waited for it.
            incumbent_is_live = _environment_has_live_bridge(
                {"metadata": existing_metadata}, bridge_rows_say_live=None,
            )
            if existing_started and not incumbent_is_live:
                logger.info(
                    "environment %s: bridge %s started later but has been silent since %s; "
                    "handing the row to live bridge %s",
                    env_id, existing_bridge_id,
                    existing_metadata.get("bridgeLastSeen") or "never", incoming_bridge_id,
                )
                existing_started = None
            # THE HOST TIER OUTRANKS A BRIDGE, whatever the start times say. Added 2026-09-04
            # (external review, Round 8 H4).
            #
            # Arbitration was start-time-only, so a LEGACY aify-comms environment bridge -- one
            # on a host that has not re-run install.sh -- took this row simply by starting later,
            # and then became the only party `_claim_spawn_request_once` would let claim. Two
            # spawners on one host is the collision the environment tier exists to end, and
            # v0.6.2 deleting that cluster makes every surviving bridge old code nobody tracks.
            #
            # ABSENT MEANS LEGACY, so a fleet that has not upgraded behaves exactly as before,
            # and so does an older aify-env against this service. When both sides are the same
            # kind, this says nothing and the start-time rule below decides, unchanged.
            incoming_is_host_tier = _is_host_tier(metadata)
            existing_is_host_tier = _is_host_tier(existing_metadata)
            if incoming_is_host_tier and not existing_is_host_tier:
                logger.info(
                    "environment %s: host tier %s takes the row from legacy bridge %s "
                    "(kind outranks start time)",
                    env_id, incoming_bridge_id, existing_bridge_id,
                )
                superseded_bridge_id = existing_bridge_id
                existing_started = None
            elif existing_is_host_tier and not incoming_is_host_tier:
                # REFUSED, and it says why in the words the reader needs: this is not a clock
                # problem and re-registering will not help. The bridge is the thing that should
                # not be running.
                return ClaimArbitration(refused={
                    "accepted": False,
                    "bridgeId": existing_bridge_id,
                    "reason": (
                        "this environment is held by the aify-env host tier, which outranks "
                        "an aify-comms environment bridge. That bridge is retired: re-run "
                        "install.sh on this host and relaunch its wrappers."
                    ),
                })
            if existing_started and (not incoming_started or incoming_started < existing_started):
                # REFUSED, AND IT SAYS SO. `ok: True` alone is what a heartbeat that was
                # ACCEPTED returns, so a bridge whose beat was arbitrated away could not tell
                # the difference -- it kept beating every 30s, believing it was the claimer,
                # while `bridgeLastSeen` never moved and `/spawn` refused every request. That
                # is the shape this repo has fixed three times elsewhere ("no evidence is not a
                # pass"), and on 2026-09-02 it cost a day here: a plugin sending its start time
                # in the wrong place landed on this branch forever and reported healthy.
                #
                # `ok` STAYS TRUE: the request was well-formed and the row is fine. What is
                # added is WHO the claimer is, so a caller can compare it with its own id.
                return ClaimArbitration(refused={
                    "accepted": False,
                    "bridgeId": existing_bridge_id,
                    "reason": (
                        "an existing bridge started later than this one, or this beat "
                        "carried no metadata.bridgeStartedAt to arbitrate on"
                    ),
                })
            if incoming_started and (not existing_started or incoming_started > existing_started):
                superseded_bridge_id = existing_bridge_id
    if (
        existing
        and beat.requested_status != "online"
        and str(existing["bridge_id"] or "").strip()
        and str(req.bridgeId or "").strip()
        and str(existing["bridge_id"] or "").strip() != str(req.bridgeId or "").strip()
    ):
        # The same silence, on the other refusal: a bridge saying it is going offline must not
        # take down a row a DIFFERENT bridge now owns. Correct, and previously indistinguishable
        # from having been recorded.
        return ClaimArbitration(refused={
            "accepted": False,
            "bridgeId": str(existing["bridge_id"] or "").strip(),
            "reason": "another bridge owns this row, and this beat was not an online one",
        })
    return ClaimArbitration(superseded_bridge_id=superseded_bridge_id)


async def _invalidate_bound_agents_on_transition(db, env_id: str, existing, requested_status: str) -> None:
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
