r"""Whose id may become `runtime_state.bridgeInstanceId` on a registration.

ONE FIELD, TWO MEANINGS. It is read as "which bridge owns this agent" by `aify-comms doctor`'s
`managed-orphans`, by the bridge's `managed-ownership.mjs`, and by the service's own
`bridge_not_current` guards. For a RESIDENT agent that is its own MCP bridge. For a MANAGED agent it is
the ENVIRONMENT BRIDGE that hosts the delivery loop -- a different process entirely, whose id arrives
by a different path (`spawn-loop` and `managed-environment-sync` PATCH it).

OBSERVED ON THE OPERATOR'S HOST, 2026-08-29, on a live and perfectly healthy agent::

    live environment bridge          e720826b-741c-455f-b8bd-e4659777e0c7
    comms-senior-dev, 03:44          cd17b4c8-ebdb-4980-9492-b2a69435b590
    comms-senior-dev, minutes later  a0897fff-a6c4-4c99-8349-8b9d750dd22a
    ef-manager (just spawned)        e720826b-741c-455f-b8bd-e4659777e0c7

`aify-comms doctor` then reported that agent as an orphaned managed delivery loop "bound to no live
bridge", while it was answering messages, and its remedy is to relaunch the environment bridge -- which
reaps the managed fleet. A false alarm whose fix is destructive is worse than no alarm.

THE FIRST FIX WAS IN THE WRONG PLACE AND A REVIEWER PROVED IT. The bridge-side guard
(`environment-ownership-claim.mjs`) suppresses the follow-up PATCH, and the registration POST that
precedes it still carries `bridgeId` -- which this service unconditionally wrote into a FRESH
`runtime_state`, replacing the environment bridge's answer. Executed against the real route: before
``{"bridgeInstanceId": "environment-bridge"}``, after ``{"bridgeInstanceId": "sidecar-bridge"}``, with
all seven of the new bridge-side tests passing. A guard that runs after the load-bearing write is
decoration.

So the authority decision lives HERE, where the write happens, and the bridge-side guard stays as the
polite half: a non-owner should not send the claim, and this refuses it if it does.
"""
from __future__ import annotations

#: The keys a registration may not reset for a MANAGED agent, because they are the ENVIRONMENT's
#: answers rather than the registering session's.
#:
#: `bridgeInstanceId` is which environment bridge hosts the delivery loop. `environmentId` is which
#: environment the agent belongs to, and `managed-environment-sync.mjs` reads it directly::
#:
#:     const belongsToEnvironment = session || String(runtimeState.environmentId || "") === environment.id;
#:
#: so an agent with no ACTIVE session -- an `available` managed agent waiting to be cold-started, the
#: common resting state -- stops being adopted by its own environment once a re-registration clears it.
#: Found by the test written for the bridgeInstanceId half, on the same line of code.
#:
#: THIS NARROWS "re-register is a full state refresh", which DECISIONS.md records as deliberate. The
#: narrowing is to two keys, both of which answer a question the registering process cannot answer,
#: and both of which have an observed consumer that reads them as authority. Everything else still
#: refreshes.
ENVIRONMENT_OWNED_RUNTIME_STATE = ("bridgeInstanceId", "environmentId")


def preserved_environment_state(
    *,
    session_mode: str,
    managed_wrapper_child: bool = False,
    existing_runtime_state: dict | None = None,
) -> dict:
    """The environment-owned keys a managed registration must carry forward.

    Empty for a resident agent (it owns its own answers) and for a managed agent with nothing recorded
    yet -- there is no prior answer to keep, and inventing one is the failure this whole module is
    about.
    """
    mode = str(session_mode or "").strip().lower()
    if not (mode == "managed" or managed_wrapper_child):
        return {}
    existing = existing_runtime_state or {}
    kept = {}
    for key in ENVIRONMENT_OWNED_RUNTIME_STATE:
        value = str(existing.get(key) or "").strip()
        if value:
            kept[key] = value
    return kept


def registration_owner_bridge_id(
    *,
    bridge_id: str,
    session_mode: str,
    managed_wrapper_child: bool = False,
    existing_bridge_instance_id: str = "",
) -> tuple[str, str]:
    """The id to store, and why.

    :returns: ``(bridge_instance_id, reason)``. An empty id means store nothing: for a brand-new
        managed agent there is no prior owner to keep and no authority in this request, and leaving the
        field unset is the honest answer -- the environment bridge writes it when it adopts or spawns
        the agent. Guards that read it fail closed on an empty value, which is the safe direction; a
        WRONG owner is the one that sends work to a process that is not hosting anything.
    """
    requested = str(bridge_id or "").strip()
    mode = str(session_mode or "").strip().lower()
    existing = str(existing_bridge_instance_id or "").strip()

    # A managed agent's own sidecar has no standing to say which environment bridge hosts it. Both
    # signals are honoured: the declared mode, and the wrapper flag the launcher sets -- because
    # session-mode resolution has its own history of falling toward "resident" when unsure, and a
    # guard that a mis-resolved mode walks past is decoration.
    if mode == "managed" or managed_wrapper_child:
        if existing:
            return existing, "managed agent: kept the environment bridge already recorded"
        return "", "managed agent: no environment bridge has claimed it yet, so nothing is recorded"

    if requested:
        return requested, f"{mode or 'unknown'} agent: its own bridge is its owner"
    # No id offered and none to keep. Preserving beats inventing.
    return existing, "no bridge id offered; kept whatever was recorded"


def patched_owner_bridge_id(
    *,
    session_mode: str,
    current_bridge_instance_id: str,
    incoming_bridge_instance_id: str,
    live_environment_bridge_ids=(),
) -> tuple[str, str]:
    """The same question one endpoint later: who may CHANGE a managed agent's owner.

    `registration_owner_bridge_id` above answers it for the registration POST, where the answer is
    always "not the registering sidecar". `PATCH /agents/{id}/runtime-state` is the other door, and it
    is the one the environment bridge itself comes through -- `managed-environment-sync.mjs` re-adopts
    an agent by PATCHing its own `BRIDGE_INSTANCE_ID`. So "refuse every change" is wrong here, and it
    is exactly what the service did.

    THE GUARD WAS NARROW AND GOT WIDENED BY A COMMIT ABOUT SOMETHING ELSE. Before `e3c3ce8c`
    ("fix(sessions): make ownership switching manual", 2026-05-26) the rule fired only when the
    incoming id was a PENDING RESIDENT TAKEOVER candidate::

        if managed and isinstance(pending, dict) and next.bridgeInstanceId == pending.bridgeId:
            next.bridgeInstanceId = current.bridgeInstanceId

    That commit removed `pendingResidentTakeover` and replaced the condition with an unconditional
    one -- keeping the action while deleting the reason for it. A managed agent's owner became frozen
    at its first non-empty value for life.

    MEASURED ON THE OPERATOR'S HOST, 2026-08-29: 19 of 24 managed agents carried a `bridgeInstanceId`
    that was not the one online environment bridge (`e720826b-...`), six of them sharing one dead
    generation. The only two that read correctly were the two the CURRENT bridge had spawned -- the
    spawn path writes `runtime_state` directly and never meets this guard.

    WHAT READS IT, at the strength each one actually has. `claim_block_reason` returns
    `bridge_not_current` on a mismatch for any agent that is not managed-with-an-`environmentId`, so
    such an agent has no valid claimer and its run sits queued. `aify-comms doctor`'s
    `managed-orphans` calls a working agent an orphan and prescribes a bridge relaunch, which reaps
    the fleet. `reap-managed-survivors.js` skips survivors owned by a different LIVE bridge, a
    protection a stale owner removes -- latent here, since it needs two live environments. All three
    are read from source; demonstrating any of them needs a bridge restart.

    :param live_environment_bridge_ids: the `bridge_id` of every environment whose DERIVED status is
        online. Derived, not stored -- `environment_effective_status` ages a silent bridge out, and a
        stored column would let a dead bridge keep its authority.
    :returns: ``(bridge_instance_id, reason)``.
    """
    mode = str(session_mode or "").strip().lower()
    current = str(current_bridge_instance_id or "").strip()
    incoming = str(incoming_bridge_instance_id or "").strip()

    if mode != "managed":
        # A resident agent IS its own bridge, so its own PATCH is authoritative. Unchanged.
        return incoming, "resident agent: its own bridge is its owner"
    if not current:
        return incoming, "managed agent with no recorded owner: the first environment bridge claims it"
    if not incoming or incoming == current:
        # An omitted field is not a request to clear an environment-owned answer. Every PATCH caller
        # merges over its local cache, so a missing id means the caller did not know it -- not that
        # nobody owns this agent. A live environment bridge can still replace it below.
        return current, "no change requested; kept the recorded environment bridge"

    live = {str(value or "").strip() for value in (live_environment_bridge_ids or ())}
    live.discard("")
    if incoming in live:
        return incoming, "a live environment bridge claimed this agent, which is the answer it owns"
    # The sidecar case the guard was built for, and still the default: an id belonging to no live
    # environment bridge has no standing to reassign one. Fails closed, including when the caller
    # could not determine which bridges are live -- an empty set refuses every change rather than
    # allowing them all.
    return current, "the claiming id is not a live environment bridge; kept the recorded one"
