"""Managed environments: which one owns an agent, whether it is reachable, whether a spawn is
already in flight. Leaf module.

Layer-0 slice of the v0.5.4 decomposition. Six reads that answer "can this agent be started here, and
is something already starting it".

WHY THE TWO SPAWN WINDOWS BELONG HERE. `SPAWN_INFLIGHT_WINDOW_SECONDS` suppresses a duplicate start;
`SPAWN_STARTING_WINDOW_SECONDS` decides how long the dashboard keeps SHOWING `starting`. They are
deliberately the same number, and `test_status_starting` asserts that equality — because if the
display expires before the suppressor, there is a window where the dashboard says nothing is starting
while the dispatcher still refuses to start one. Two numbers that happen to agree today will drift
apart if they live apart, so the constants and both readers are in one file.

DB ACCESS: `db` passed to every function, reads only, no connection opened and no transaction taken.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL
from service.api_core.records import _environment_record_to_dict
from service.api_core.runtime import (
    _normalize_runtime,
    _normalize_session_mode,
    _runtime_capability_for_environment,
    _runtime_unlaunchable_reason,
)
from service.api_core.liveness import _LIVE_SESSION_STATUSES
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.clock import ISO_SECONDS, iso_to_epoch as _iso_to_epoch
from service.env_status import environment_effective_status as _environment_effective_status


SPAWN_INFLIGHT_WINDOW_SECONDS = 300
SPAWN_STARTING_WINDOW_SECONDS = SPAWN_INFLIGHT_WINDOW_SECONDS


async def load_session_environment_by_agent(db) -> dict:
    """Every agent's live-session environment binding, in one query.

    The per-agent form is `ORDER BY last_seen DESC LIMIT 1`; this fetches the same rows under the same
    ordering and keeps the FIRST per agent, which is the same row that LIMIT 1 would have returned.
    An agent with no live session is simply absent, and the resolver reads an absent key as "no
    binding" -- the answer the per-agent query gives when it finds nothing.

    Built by the caller once per request and passed down, for the same reason as the environments
    cache: a map outliving the request would have to be invalidated by every session write.
    """
    # THE CANONICAL SET, imported rather than retyped. The first version of this preload spelled the
    # five states out again under a new name, and `test_status_set_literal_twins_are_frozen` failed
    # it on sight -- a second hardcoded copy of a status set is how the copies start disagreeing.
    # Sorted for a stable placeholder order; the set itself is unordered.
    states = tuple(sorted(_LIVE_SESSION_STATUSES))
    placeholders = ", ".join("?" for _ in states)
    rows = await (await db.execute(
        "SELECT agent_id, environment_id FROM agent_sessions "
        f"WHERE status IN ({placeholders}) ORDER BY last_seen DESC",
        states,
    )).fetchall()
    out: dict = {}
    for row in rows:
        agent_id = row["agent_id"]
        if agent_id not in out:
            out[agent_id] = str(row["environment_id"] or "")
    return out


async def _managed_owning_environment_row(
    db, agent_row, *, resolved_environment_id: str = "", environments_by_machine=None,
    session_environment_by_agent=None,
):
    """FIX B (2026-06-02): resolve the OWNING environment row for a MANAGED agent.

    A managed agent can only be spawned/hosted by its environment bridge, so its
    effective liveness must be gated on that env bridge — NOT on a surviving
    delivery-loop heartbeat. The operator killed the env bridge and managed agents
    stayed `available`/`online` because detached loops kept heartbeating; the hole
    was that the status compute resolved `environment_id` ONLY from the live session
    row / runtime_state, both of which are absent once the worker dies.

    Resolution order (the agent's STORED binding):
      1. the already-resolved id (session row / runtime_state.environmentId), then
      2. the spawn-time binding, from runtime_config.environmentId or
         runtime_state.environmentId -- aify-env's claim writes the SECOND of those,
         and reading only the first left every spawn-registered agent unresolvable,
         then
      3. the environment on the agent's machine_id that advertises its runtime.

    Returns the environments row, or None if no owning environment can be
    determined (e.g. an unbound agent with no machine/runtime match) — callers must
    NOT force offline on None (preserve the unbound `available` fall-through).
    """
    # 1. already-resolved id.
    env_id = str(resolved_environment_id or "").strip()
    # 2. THE SPAWN-TIME BINDING, FROM EITHER CARRIER IT MAY HAVE ARRIVED IN.
    #
    # `runtime_config` was the only one read, and NOTHING WRITES IT. aify-env's claim reports
    # `runtimeState: {environmentId, spawnRequestId, mode, resumePolicy}` -- `claim.mjs` builds that
    # object and the service stores it as `runtime_state`. So this step, whose whole job is to
    # recover the binding a spawn recorded, read a key that has never been populated and fell
    # through every time.
    #
    # MEASURED 2026-09-06 on the operator's host. Four managed agents, one host, all four carrying
    # `runtime_state.environmentId = windows:StevenZ-L:default`:
    #
    #     sc-critic   machine_id set, session running  -> resolved  -> available
    #     sc-lead     no machine_id, session stopped   -> None      -> OFFLINE
    #     sc-coder    no machine_id, session stopped   -> None      -> OFFLINE
    #     sc-tester   no machine_id, session stopped   -> None      -> OFFLINE
    #
    # Every one of them knew its environment. The three that read `offline` were unresolvable only
    # because the two steps that could still have answered had both aged out: step 2.5 needs a LIVE
    # session, and step 3 needs a `machine_id` that a spawn-registered agent never receives. So an
    # agent whose host is up, advertising and able to run it reported `offline` -- and `offline`
    # tells the operator there is no cold start to be had, while `available` promises one. The host
    # was ready the whole time.
    #
    # A STORED BINDING BEATS A LIVE ONE HERE PRECISELY BECAUSE IT DOES NOT AGE. The live-session and
    # machine_id steps below both answer "where is it running now", which is the wrong question for
    # an agent that is not running -- and that is the only case this whole resolution exists for.
    if not env_id:
        try:
            runtime_config = _json_loads_or(agent_row["runtime_config"], {})
            env_id = str(runtime_config.get("environmentId") or "").strip()
        except Exception:
            env_id = ""
    # 2.5 (2026-06-17, Phase I flip parity): the agent's LIVE session binding. The
    # event-engine status callers (_gather_status_inputs / the _compute_live_status_cache
    # byproduct) pass resolved_environment_id="" — they don't pre-resolve the session env
    # the way the legacy derivation does (it passes environment_id at line ~4562). Without
    # this, a managed agent whose owning env is recorded only on its agent_sessions row
    # (no machine_id / no runtime_config.environmentId) resolved to NO env and wrongly read
    # `offline` under the new engine. Restores legacy parity. A dead env still reads offline
    # (the row resolves but its _environment_effective_status is offline → env_reachable False).
    if not env_id:
        try:
            if session_environment_by_agent is not None:
                # PRELOADED by a caller resolving many agents: the roster asked this once per agent,
                # 50 round-trips at 50 agents. The map is built with the same states and the same
                # most-recent-first ordering, so an agent absent from it has no live session --
                # which is the same answer this query gives when it finds no row.
                env_id = str(session_environment_by_agent.get(agent_row["id"]) or "").strip()
            else:
                sess = await (await db.execute(
                    "SELECT environment_id FROM agent_sessions WHERE agent_id = ? "
                    "AND status IN ('starting','running','recovering','restarting','cli-takeover') "
                    "ORDER BY last_seen DESC LIMIT 1",
                    (agent_row["id"],),
                )).fetchone()
                env_id = str((sess["environment_id"] if sess else "") or "").strip()
        except Exception:
            env_id = ""
    if env_id:
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))).fetchone()
        if row:
            return row
    # 2.9 THE SPAWN-TIME BINDING FROM `runtime_state`, read AFTER the live session and not before it.
    #
    # Placed above 2.5 at first, which inverted a precedence this module promises elsewhere: a LIVE
    # session is where the agent is running NOW, and a stored id is where it was told to run. An
    # independent review drove the divergent shape -- a `running` session on the online host beside a
    # stale `runtime_state` pointing at a dead one -- and got the DEAD environment out of this
    # resolver where the pre-change code gave the live one. No user-visible wrong answer was
    # demonstrated and the shape may not occur today, but it also put `_gather_status_inputs` and the
    # `_compute_live_status_cache` byproduct on different answers, and those two agreeing is a
    # promise this module keeps deliberately.
    #
    # Below the live session it still fixes the case it was added for: an agent with NO live session
    # and no machine_id, which is every managed agent whose worker has died -- and the only case this
    # whole resolution exists for.
    if not env_id:
        try:
            runtime_state = _json_loads_or(agent_row["runtime_state"], {})
            env_id = str(runtime_state.get("environmentId") or "").strip()
        except Exception:
            env_id = ""
    # ITS OWN LOOKUP, because the one above already ran. Moving this step below the live-session
    # branch put it after that branch's `if env_id: return row`, so it resolved an id nothing then
    # read and every spawn-registered agent fell through to the machine_id step it has no id for --
    # the original defect, restored by the reorder that was supposed to be safe. Its own test caught
    # it immediately, which is the only reason this paragraph is not a bug report.
    if env_id:
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))).fetchone()
        if row:
            return row

    # 3. machine_id + runtime match (the environment that advertises this runtime
    #    on the agent's machine). Mirrors how spawn picks an environment.
    machine_id = str(agent_row["machine_id"] or "").strip()
    runtime = _normalize_runtime(agent_row["runtime"] or "")
    if not machine_id:
        return None
    # REQUEST-SCOPED CACHE, supplied by callers that resolve many agents in a row. The roster
    # resolves one environment per agent, and this query depends on nothing but machine_id -- on a
    # fleet where the agents share a host it is the same answer every time. Measured at 50 agents:
    # 50 identical reads of a two-row table, each an event-loop hop to aiosqlite's worker thread.
    #
    # Passed in rather than held on the module or the connection, deliberately. A cache with a
    # lifetime longer than the caller's own loop would have to be invalidated by whatever writes
    # `environments` -- heartbeats do, constantly -- and a stale environment here reads as a managed
    # agent being gated against a host that no longer exists. The caller owns the dict, so the
    # lifetime is visible at the call site instead of being a property of this module.
    if environments_by_machine is None:
        candidates = await (await db.execute(
            "SELECT * FROM environments WHERE machine_id = ? ORDER BY last_seen DESC",
            (machine_id,),
        )).fetchall()
    else:
        if machine_id not in environments_by_machine:
            environments_by_machine[machine_id] = await (await db.execute(
                "SELECT * FROM environments WHERE machine_id = ? ORDER BY last_seen DESC",
                (machine_id,),
            )).fetchall()
        candidates = environments_by_machine[machine_id]
    for row in candidates:
        environment = _environment_record_to_dict(row)
        if _runtime_capability_for_environment(environment, runtime):
            return row
    return None


async def _managed_spawn_is_starting(db, agent_id: str) -> bool:
    """True when a spawn for this agent is RUNNING, has no worker yet, and is still inside the
    startup window.

    Deliberately narrower than "a spawn row says running": the row alone is exactly the signal that
    was wrong all morning. Requires a claim (`started_at`), so a queued-but-unclaimed spawn — which
    nothing is starting yet — does not qualify either.
    """
    row = await (await db.execute(
        """
        SELECT started_at, updated_at, created_at
        FROM spawn_requests
        WHERE agent_id = ?
          AND status = 'running'
          AND COALESCE(started_at, '') != ''
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if not row:
        return False
    started = _iso_to_epoch(row["started_at"] or row["updated_at"] or row["created_at"])
    if not started:
        # Undeterminable age is NOT inside the window. `_iso_to_epoch` returns 0.0 on an
        # unparseable value, and 0.0 would otherwise compute as "56 years old" — harmless here, but
        # only by accident. Stated explicitly: an age we cannot measure must not buy an unbounded
        # `starting`, so it falls back to what this window reported before the state existed.
        return False
    return (time.time() - started) <= SPAWN_STARTING_WINDOW_SECONDS


class ConsoleBootingOnce:
    """One agent's boot answer, computed at most once per status computation.

    `_compute_live_status_cache` asked this question TWICE for the same agent in the same request:
    once through `_decide_effective_status` (the authoritative path) and once directly, for the
    WS-12 display-parity line further down. Measured 2026-08-28 by counting `aiosqlite` execute()
    calls through one COLD `GET /api/v1/agents`:

        agents refreshed        4      8
        terminal_sessions read  8     16      = 2 per agent
        all statements         48     84

    Eight of the nine per-agent queries run once; this one ran twice. Removing the second takes a
    cold request from 84 statements to 76 at the 8-agent refresh cap.

    LAZY ON PURPOSE. `_decide_effective_status` documents why the read was not hoisted out of its
    late branch: hoisting "would also add a database query to EVERY status computation on a hot
    path", since both call sites are guarded and often neither fires. This keeps both guards exactly
    as they are and only prevents the SECOND computation, so an agent that reaches neither branch
    still pays nothing.

    SCOPE IS ONE AGENT, ONE COMPUTATION. A fleet-wide or request-wide memo would have to reason
    about when a console's row can change underneath it; this cannot go stale, because it does not
    outlive the single derivation that created it.
    """

    __slots__ = ("_db", "_agent_id", "_answer")

    def __init__(self, db, agent_id: str) -> None:
        self._db = db
        self._agent_id = agent_id
        self._answer: bool | None = None

    async def value(self) -> bool:
        if self._answer is None:
            self._answer = await _managed_console_is_booting(self._db, self._agent_id)
        return self._answer


async def _managed_console_is_booting(db, agent_id: str) -> bool:
    """True when the agent's live console came up but NO channel-sidecar has registered for it
    YET — a worker BOOTING (sidecar still coming), distinct from a sidecar that registered for
    THIS console and then died (the 13c4ae8 'online but deaf' case → stays `available`).

    TIME-WINDOW-FREE (2026-06-05): keys purely on a relational fact — has any channel-sidecar
    been last-seen AT/AFTER the current console's `created_at`? The sidecar is the worker's own
    child, so it always registers AFTER its console; therefore:
      - no sidecar seen since this console started  → it hasn't come up yet → BOOTING.
      - a sidecar WAS seen at/after console start (now stale) → it came up then died → DEAF.
    Cross-restart safe: an old sidecar row from a PRIOR session has last_seen < the new
    console's created_at, so a relaunch correctly reads BOOTING until its own sidecar registers.
    No arbitrary grace: a boot whose sidecar never arrives shows `online` only while its console
    is live/streaming (the existing liveness gate); a dead/hung console is reaped separately.
    """
    console = await (await db.execute(
        f"""
        SELECT created_at FROM terminal_sessions
        WHERE agent_id = ?
          AND status IN {TERMINAL_LIVE_FILTER_SQL}
          -- SYNTHETIC ROWS ARE NOT A CONSOLE, and this query was the odd one out. Six other queries
          -- asking "does agent ? have a live terminal row" carry this exclusion; measured
          -- 2026-08-26, this was the only one asking that question without it. Plan 4 deprecated
          -- `vterm_` terminals, and pre-Plan-4 rows persist in operator DBs with status='running'
          -- and no cleanup path outside a resident takeover -- which is how sc-coder and
          -- sc-architect kept reading `online` over a dead worker on 2026-05-26.
          --
          -- WHAT IT COSTS HERE: this takes the most-recently-UPDATED live row's `created_at` as
          -- "when the console started". A stale synthetic row that out-ranks a real console on
          -- `updated_at` therefore supplies an OLD start time, which makes any past sidecar look
          -- like it registered after this console came up -- reporting a genuinely BOOTING worker
          -- as not-booting. Whether a `vterm_` row can win that ORDER BY today is unproven; the
          -- alignment is worth having either way, since the other six settled the question.
          AND id NOT LIKE 'vterm_%'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if not console:
        return False
    console_started = _iso_to_epoch(str(console["created_at"] or ""))
    if not console_started:
        return False
    sidecar = await (await db.execute(
        "SELECT MAX(last_seen) AS last_seen FROM bridge_instances "
        "WHERE agent_id = ? AND bridge_kind = 'channel-sidecar'",
        (agent_id,),
    )).fetchone()
    sidecar_seen = _iso_to_epoch(str((sidecar["last_seen"] if sidecar else "") or ""))
    # BOOTING iff no channel-sidecar has been seen since this console started.
    return not (sidecar_seen and sidecar_seen >= console_started)


async def _managed_environment_status(db, row) -> tuple[str, str, str]:
    if not row or _normalize_session_mode(row["session_mode"] or "resident") != "managed":
        return "", "", ""
    runtime_state = _json_loads_or(row["runtime_state"], {})
    environment_id = str(runtime_state.get("environmentId") or "").strip()
    if not environment_id:
        session_cursor = await db.execute(
            """
            SELECT environment_id
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (row["id"],),
        )
        session = await session_cursor.fetchone()
        environment_id = str((session["environment_id"] if session else "") or "").strip()
    if not environment_id:
        return "", "", ""

    settings = await _load_settings(db)
    env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
    env = await env_cursor.fetchone()
    env_status = _environment_effective_status(
        env,
        offline_seconds=settings.get("environment_offline_seconds", 90),
    ) if env else "offline"
    env_bridge = str((env["bridge_id"] if env else "") or "").strip()
    return environment_id, env_status, env_bridge


async def _has_pending_or_booting_spawn_request(db, agent_id: str) -> bool:
    """Like _has_claimable_spawn_request, but ALSO counts a RECENT `running` request
    (worker mid-boot, before it registers a session). Bug D fix (2026-07-02): a second
    cold-start created while one worker was still booting produced a duplicate whose
    kill-prior could murder the booting worker. Time-bound (5 min) so a stuck `running`
    orphan never blocks future autostarts (the orphan reaper frees those anyway)."""
    # Shared with the `starting` display window — see SPAWN_INFLIGHT_WINDOW_SECONDS. These were two
    # independent numbers (300 here, 180 there) and the gap between them was a window where the
    # status said "idle, send something" while this function was still refusing to start a second
    # worker.
    running_cutoff = time.strftime(
        ISO_SECONDS, time.gmtime(time.time() - SPAWN_INFLIGHT_WINDOW_SECONDS)
    )
    # `starting` is the bridge's pre-`running` PATCH — count it with the time-bounded
    # arm so a concurrent coldstart in that sub-second window can't duplicate.
    # `running` rows with finished_at set are KNOWN-DEAD workers (report_terminal_dead
    # stamps them) — a dead worker must not suppress the respawn it just made necessary.
    row = await (await db.execute(
        """
        SELECT id
        FROM spawn_requests
        WHERE agent_id = ?
          AND (
            status IN ('queued', 'claimed')
            OR (
              status IN ('starting', 'running')
              AND COALESCE(finished_at, '') = ''
              AND COALESCE(NULLIF(updated_at, ''), created_at) >= ?
            )
          )
        LIMIT 1
        """,
        (agent_id, running_cutoff),
    )).fetchone()
    return bool(row)


async def _select_online_environment_for_runtime(
    db, runtime: str, *, offline_seconds: int = 90
) -> Optional[dict[str, Any]]:
    """Pick the freshest ONLINE environment that advertises `runtime`.

    Used by Phase 2 auto-bind: when a managed agent has no usable session
    environment, bind it to a live env so it can be cold-started on first
    message. Deterministic order: most-recently-seen environment first, so a
    freshly-heartbeating bridge is preferred. Returns the environment dict, or
    None when no online environment advertises the runtime.
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if not normalized_runtime:
        return None
    cursor = await db.execute("SELECT * FROM environments ORDER BY last_seen DESC")
    for env_row in await cursor.fetchall():
        environment = _environment_record_to_dict(env_row, offline_seconds=offline_seconds)
        if str(environment.get("status") or "").lower() != "online":
            continue
        if not _runtime_capability_for_environment(environment, normalized_runtime):
            continue
        # An environment that has already said it cannot start this runtime is not a candidate for it.
        # Skipping here lets a LATER environment that can be chosen instead, which is the whole point
        # of scanning more than one; refusing at the spawn gate alone would stop at the first match.
        if _runtime_unlaunchable_reason(environment, normalized_runtime):
            continue
        return environment
    return None


# v0.5.4: moved out of the control plane, which had no claim on it beyond history. Its ONLY dependency
# is `_managed_environment_status` directly above, and both of its readers reached it through borrow
# shims — the all-consumers-through-a-shim shape that says the carrier was a hiding place, not an owner.
async def _managed_environment_unavailable_reason(db, row) -> Optional[str]:
    environment_id, env_status, _env_bridge = await _managed_environment_status(db, row)
    if not environment_id:
        return None
    if env_status not in {"online", "degraded"}:
        return f'managed environment "{environment_id}" is {env_status}'
    return None
