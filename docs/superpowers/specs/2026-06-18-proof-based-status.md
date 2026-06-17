# Proof-based status — design spec (2026-06-18)

> Operator mandate: status must be **proven, not assumed**. The `*-aify` wrapper is
> the source of truth for its agent; aify-comms reflects what it proves and only adds
> the states aify-comms itself owns. Kill the time-decay (idle/offline/stale minute
> thresholds) — they are guesses, not proof.

## Principle

A status change happens because something **proved** it:
- the wrapper pushed an **event** (turn-start, turn-end, awaiting-input), or
- the wrapper's **heartbeat** proves it is alive (or its absence proves it is gone), or
- the **operator** acted (stop/disable), or
- **aify-comms' own lifecycle** knows there is no worker yet (managed, not started).

No status is ever derived from "it has been N minutes since X."

## Vocabulary (6 states — `idle` and `stale` removed)

| status | meaning | who proves it |
|--------|---------|---------------|
| `working` | in a turn right now | wrapper: turn-start (until turn-end) |
| `online` | alive, not in a turn | wrapper: heartbeat + (no open turn) |
| `blocked` | alive, waiting on operator input | wrapper: awaiting-input event |
| `available` | managed, registered, env reachable, **no live worker yet** (lazy-starts on send) | aify-comms lifecycle |
| `offline` | gone — heartbeat absent / disconnected | aify-comms liveness (absence of proof) |
| `stopped` | operator-disabled hard block (refuses sends, never auto-starts) | operator |

**offline vs stopped (operator Q):** kept separate. `stopped` is *intent* — the
operator disabled it; sends are refused and it never auto-starts. `offline` is
*absence* — it crashed/exited/was-killed but is not blocked; a managed agent
lazy-starts on the next send, a resident one is simply down until relaunched.

## Ownership boundary

- **Wrapper owns:** `working` / `online` / `blocked` — pushed as events + a liveness heartbeat. The wrapper must ALWAYS report accurately, including holding the turn flag for the whole turn (no flapping between output bursts).
- **aify-comms owns:**
  - `offline` — heartbeat gone. **Instant** on a clean disconnect (wrapper sends a deregister/offline beat on SIGINT/PTY-close), else a **short no-heartbeat window** (≈ 3 missed beats; exact value = `<TBD from heartbeat-cadence audit>`s). This replaces `offline_minutes` (was 30 min) and the resident `stale` window.
  - `available` — managed agent, env reachable, no live worker. (Unchanged in spirit.)
  - `stopped` — operator disable.

## What gets removed (time-decay junk)

- `idle` status + `idle_minutes` setting + `idle_too_long` input.
- `stale` status + the resident stale window (→ becomes `offline`).
- `offline_minutes` (30 min) → replaced by the short liveness window (seconds).
- The 30-min `TURN_BUSY_BACKSTOP` and assorted re-pulse/backstop carve-outs that
  existed to paper over unreliable turn signals — once the wrapper holds the turn
  flag honestly, these are dead. (Exact list from the server-status audit.)
- Reassess the dual input-build paths (`_gather_status_inputs` vs the
  `_compute_live_status_cache` byproduct) — collapse to one if the parity duplication
  is now junk.

## Liveness window (the one legitimate time element)

`offline` = `now - last_heartbeat > LIVENESS_WINDOW` OR clean-disconnect received.
`LIVENESS_WINDOW` is a single short value. **Audit finding: every wrapper beats
liveness at a uniform 30s** (server.js `startLivenessHeartbeat` 30_000; claude/hermes
sidecars 30_000; turn-busy hb 30_000). So **`agent_liveness_seconds = 90`** (3 missed
beats) cleanly separates "missed one beat" from "dead", and sits under the existing
`TURN_BUSY_STALE_SECONDS=120` / `CHANNEL_SIDECAR_STALE_SECONDS=180` windows (which can
then tighten toward it). One setting, seconds. Replaces `idle_minutes`(5) +
`offline_minutes`(30) + the resident stale window.

**Clean-disconnect (audit):** only the *resident* bridge sends an offline beat today
(`/agents/{id}/resident-lost` on SIGINT, server.js shutdownWithStatus). Managed
sidecars (claude-channel.js / hermes-channel.js / hermes-managed-host.js) send NOTHING
on exit — they just stop beating. So instant-offline-on-disconnect is resident-only
now; managed relies on the 90s window until the wrapper-side adds a managed offline
beat (Phase 2).

## Orphan handling (live symptom: online/available but actually idle/dead)

A dead worker / sidecar / terminal / bridge must promptly resolve to the correct
absence state:
- managed, env reachable, worker gone → `available` (and reap the orphan rows).
- resident, bridge heartbeat gone → `offline`.
No agent should sit `online` because a *detached/orphaned* heartbeat keeps beating —
liveness must be tied to the OWNER process, not a survivor loop. (Exact reap paths +
the sc-coder case from the wrapper/orphan audit.)

## Phasing

1. **Service side (now):** simplify `derive()` (drop idle/stale), replace the minute
   thresholds with the short liveness window, remove the dead carve-outs, collapse
   duplicate derivations, fix orphan→absence mapping. No wrapper restart needed.
2. **Wrapper side (after SC team's chunk):** make every `*-aify` wrapper hold the
   turn flag for the whole turn + send a clean-disconnect offline beat, so
   `working`/`online` are truly event-pure (eliminates the flap).

## Test/border plan

Every test asserting `idle`/`stale`, the dashboard `resolveStatus`/filter entries,
the `/status` WS push, the settings UI fields (both dashboards), and the
input-build parity tests are borders — enumerated by the server-status audit and
updated in lockstep. Matrix invariants (managed reachable-env+dead-worker→available;
resident bridge-gone→offline; in-turn+live→working/blocked) stay green.
