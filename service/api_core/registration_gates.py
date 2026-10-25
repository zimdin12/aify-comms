"""Whether a registration may proceed: is the environment reachable, is a worker live, is another
bridge already holding this mode, and is the cwd one this machine can actually use.

Several gates, plus a conflict predicate, a cwd validator and a two-line helper. They are one module
because they are asked in sequence about the same request and each can refuse it — a reader working out
why a registration was rejected needs those answers in one place, not one module per question.

This docstring said "four gates ... 192 lines" until v0.5.4 and both numbers were wrong by then. It then
said FIVE and was wrong again within the same afternoon, because another gate came out of the route. So
neither a count nor a line total is stated here now: nothing in the suite reads prose, and a number in a
docstring only records the day someone wrote it. The gates are listed by NAME, which rots visibly.

    _enforce_env_reachable_gate           is the owning environment actually online
    _enforce_live_worker_gate             is there really a live worker behind a cached `online`
    _enforce_same_mode_bridge_gate        is another bridge already holding this mode (v0.5.4)
    _enforce_driving_mode_switch_gate     would this switch a mode out from under a live turn (v0.5.4)
    _enforce_tombstone_registration_gate  is this a tombstoned id not asking to be restored (v0.5.4)
    _enforce_tombstone_resurrection_gate  would this resurrect a deliberately-removed agent (v0.5.4)

The last four arrived by extract-method out of the 684-line `register_agent`, which is why they read as
route body rather than as helpers designed here: their bodies are verbatim, and the inline-back proof in
`test_register_agent_split_is_inert.py` (retired in v0.6.13) required them to stay that way. The two tombstone
gates are deliberately separate and run in order — the first refuses when nothing asked for a restore,
the second decides whether a restore that DID ask is fresh enough to allow.

CWD VALIDATION IS NOT COSMETIC, and the two regexes are here rather than in the carrier for a measured
reason: both had ZERO code readers there. Every consumer reached them through a borrow accessor, which
is the pattern the reviewer named — all-consumers-through-accessors is hiding-place evidence, not
ownership evidence. A Windows drive path and a WSL drive path are the two shapes that look valid and
then fail much later, when a Codex thread created with a backslash cwd cannot be resumed. The failure
lands nowhere near the cause, which is exactly why it is checked at registration.

`_enforce_live_worker_gate` and `_enforce_env_reachable_gate` raise HTTPException. That is deliberate
and already the pattern for validation leaves here (api_core/routing.py, api_core/validation.py);
converting them to a custom exception re-raised at the route would change the client's response, and
this series does not change behaviour.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import HTTPException

from service.api_core.capabilities import _has_codex_live_app_server
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _parsed_timestamp, _timestamp_sort_key
from service.api_core.resume_command import _resume_command_for


#: Both separators. It matched only `C:/` until 2026-08-16, so the gate below refused `C:/repo` on a
#: linux host and ADMITTED `C:\repo` — the canonical Windows drive-letter path, and exactly what its
#: own refusal message names. A backslash cwd then reached the codex app-server as a directory that
#: does not exist on that host, which is the failure this gate exists to prevent.
_WINDOWS_DRIVE_CWD_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_WSL_DRIVE_CWD_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")


def _machine_family(machine_id: Any) -> str:
    return str(machine_id or "").strip().split(":", 1)[0].lower()


def _validate_registration_cwd(
    *,
    agent_id: str,
    runtime: str,
    session_mode: str,
    machine_id: str,
    cwd: str,
    runtime_config: Optional[dict[str, Any]] = None,
) -> None:
    normalized_runtime = _normalize_runtime(runtime)
    normalized_session_mode = _normalize_session_mode(session_mode)
    resolved_cwd = str(cwd or "").strip()
    family = _machine_family(machine_id)
    if not resolved_cwd or normalized_runtime != "codex" or normalized_session_mode != "resident":
        return
    if not _has_codex_live_app_server(runtime_config):
        return
    if family in {"linux", "darwin", "wsl"} and _WINDOWS_DRIVE_CWD_RE.match(resolved_cwd):
        hint = '/mnt/<drive>/...' if family in {"linux", "wsl"} else "/Users/..."
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on {family}. '
                f'Use a native host path such as "{hint}", not a Windows drive-letter path.'
            ),
        )
    if family == "win32" and _WSL_DRIVE_CWD_RE.match(resolved_cwd):
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on Windows. '
                # An example path, shown to the person reading the refusal: the drive is theirs, not ours.
                'Use forward-slash drive-letter form like "C:/repo", not a "/mnt/..." WSL path.'
            ),
        )


async def _enforce_driving_mode_switch_gate(req, row, normalized_runtime, normalized_session_mode) -> None:
    """Refuse a re-register that would change an agent's session mode out from under a live turn.

    v0.5.4, extracted verbatim out of `register_agent`. A gate: it raises or returns None, which is why it
    belongs beside the other four rather than in the route.

    THE CARVE-OUT IS THE INTERESTING PART. `driver_state == "driving"` means a bridge is actively driving a
    turn, and switching mode under it strands that turn — so the change is refused with a 409 that tells
    the operator how to resume. managed -> resident is EXEMPT because that is the graceful takeover the
    product offers deliberately; refusing it would break a documented flow. So the guard fires on a mode
    change that is not the graceful one.

    Returns None when the registration may proceed; raises HTTPException(409) otherwise.
    """
    if row and not bool(req.restoreDeleted):
        existing_mode = _normalize_session_mode(row["session_mode"] or "resident")
        driver_state = str((row["driver_state"] if "driver_state" in row.keys() else "") or "idle").strip().lower()
        graceful_resident_candidate = (
            normalized_session_mode == "resident" and existing_mode == "managed"
        )
        if (
            driver_state == "driving"
            and existing_mode != normalized_session_mode
            and not graceful_resident_candidate
        ):
            resume_command = _resume_command_for(
                row["runtime"] or normalized_runtime,
                row["session_handle"] or "",
                req.agentId,
            )
            detail = (
                f"agent '{req.agentId}' is currently {existing_mode} — "
                f"switch it to {normalized_session_mode} in the dashboard first, then run: "
                f"{resume_command}"
                if resume_command
                else (
                    f"agent '{req.agentId}' is currently {existing_mode} — "
                    f"switch it to {normalized_session_mode} in the dashboard first."
                )
            )
            raise HTTPException(409, detail)


async def _enforce_tombstone_resurrection_gate(db, req, tombstone) -> None:
    """Refuse a passive re-register that would resurrect a deliberately-removed agent.

    v0.5.4, extracted verbatim out of `register_agent`. The comments inside are the incident record and are
    preserved exactly. The short version: the bridge sets `restoreDeleted=true` on EVERY auto-register, so
    with no freshness check a lingering bridge that predates a deletion would clear the tombstone and bring
    the agent back into /agents and the dashboard DM rail.

    The test is relaunch freshness — only a bridge whose `bridgeStartedAt` is NEWER than the tombstone's
    `removed_at` may restore. An explicit operator restore (`restoreDeleted=true` with
    `autoRegister=false`) is still honoured: that is a person asking, not a stale beat.

    WRITES: deletes the tombstone row on the restore path, left UNCOMMITTED for the caller's transaction.
    Raises HTTPException(410) to keep the agent deleted.
    """
    if tombstone and req.restoreDeleted:
        # Tombstone-resurrection guard (2026-06-03). The bridge sets
        # restoreDeleted=true UNCONDITIONALLY on every auto/comms_register, so
        # a still-running bridge that predates the deletion would otherwise
        # clear the tombstone and resurrect a deliberately-removed agent
        # (it reappears in /api/v1/agents and the dashboard DM rail). Mirror
        # the environment forget-tombstone freshness check: only a GENUINE
        # fresh relaunch — a bridge whose bridgeStartedAt is NEWER than the
        # tombstone's removed_at — may restore. A passive auto re-register
        # from a bridge that launched BEFORE the deletion (or with no/older
        # bridgeStartedAt) keeps the agent deleted (410, tombstone untouched).
        #
        # An explicit, operator-initiated restore (restoreDeleted=true with
        # autoRegister=false — not a passive bridge beat) is preserved: a
        # deliberate operator bring-back still clears the tombstone.
        removed_at = _timestamp_sort_key(tombstone["removed_at"] if "removed_at" in tombstone.keys() else "")
        # `_parsed_timestamp`, NOT `_timestamp_sort_key`: `bridgeStartedAt` is CALLER-SUPPLIED, and a
        # sort key keeps an unparseable value as itself, where letters outrank digits. "now" or
        # "Sat Aug 16 2026" compared GREATER than any real `removed_at` and read as a fresh
        # relaunch, so any bridge sending a non-ISO timestamp cleared the tombstone -- the exact
        # resurrection this guard was written to stop. Unparseable is now NO EVIDENCE.
        incoming_started = _parsed_timestamp(req.bridgeStartedAt)
        relaunched = bool(incoming_started) and (not removed_at or incoming_started > removed_at)
        if req.autoRegister and not relaunched:
            raise HTTPException(
                410,
                (
                    f"Agent '{req.agentId}' was intentionally removed at "
                    f"{tombstone['removed_at']}; a lingering bridge cannot "
                    "resurrect it. Relaunch the agent to restore."
                ),
            )
        # FIX 4 (2026-06-03): COLLATE NOCASE so the explicit-restore clear path
        # matches the same row the case-insensitive lookup above found.
        await db.execute(
            "DELETE FROM agent_tombstones WHERE agent_id = ? COLLATE NOCASE",
            (req.agentId,),
        )


async def _enforce_tombstone_registration_gate(req, tombstone) -> None:
    """Refuse any registration of a tombstoned agent id that is not asking to restore it.

    v0.5.4, extracted verbatim out of `register_agent`. The FIRST of the two tombstone gates and the
    blunter one: no `restoreDeleted` flag at all means the caller is not asking to bring the agent back,
    so the id stays refused whether the beat is automatic or manual. `_enforce_tombstone_resurrection_gate`
    below handles the harder case — `restoreDeleted=true`, where freshness decides.

    TWO DISTINCT 410 MESSAGES, preserved exactly. An auto-register is told re-registration is blocked; a
    manual one is told which flag to pass. Collapsing them into one message would take the actionable
    instruction away from the human and hand it to a bridge that cannot act on it.

    Returns None only when there is no tombstone; otherwise raises HTTPException(410).
    """
    if tombstone and not req.restoreDeleted:
        if req.autoRegister:
            raise HTTPException(
                410,
                (
                    f"Agent '{req.agentId}' was intentionally removed at "
                    f"{tombstone['removed_at']}; auto re-registration is blocked."
                ),
            )
        raise HTTPException(
            410,
            (
                f"Agent '{req.agentId}' was intentionally removed. "
                "Pass restoreDeleted=true to register this ID again."
            ),
        )


# _enforce_same_mode_bridge_gate and _fresh_same_mode_bridge_conflict moved to
# service/api_core/same_mode_bridge_gate.py in v0.5.4 — they call only each other, and the
# gate is correct only in company with the freshness test that bounds it.
