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
