# Hermes Resume-Pointer Sync — Investigation + Fix

> **Status:** Historical investigation/fix plan; periodic resume-marker synchronization is
> implemented. Do not run the manual marker/handle stopgap below as current guidance.

**Symptom:** A managed hermes agent starts a FRESH "(untitled)" session on every stop+start instead of resuming the previous one. The sessions are NOT lost — the hermes TUI shows them all ("1 live · 26 resumable", incl. the user's "Context Retention Banana Test" etc.). Only the *resume pointer* is wrong.

## Root cause (fully traced, 2026-06-05, next-tech-lead)

Hermes has TWO ids per session: a DURABLE `session_key` (e.g. `20260605_161037_c57d9a`, what `--resume`/`session.resume` need, persisted in `~/.hermes/state.db`) and an EPHEMERAL runtime id (e.g. `02299e31`, dead after a restart). Three on-disk pointers, all out of sync:

| Pointer | Value (live example) | Problem |
|---|---|---|
| TUI active-session file (`/tmp/aify-hermes-active-<agent>.json`) | `{"session_id":"02299e31"}` | the LIVE session, but EPHEMERAL — dies on restart |
| resume marker (`/tmp/aify-hermes-session-<agent>`) | `20260605_054328_970d9c` | DURABLE but a GC'd OLD session — never updated to the live one |
| aify `agents.session_handle` | `20260605_054328_970d9c` | same dead key (heartbeat reports the marker) |

The DURABLE key of the *current* live session (`161037`) is captured **nowhere**. Why: the only place that converts the live ephemeral id → its durable `session_key` and writes the marker is `waitForActiveSession` (hermes-managed-host.js:1104-1107) — and that runs **ONLY on an aify-comms DELIVERY**. When the operator types directly in the TUI (or the TUI mints a new session), nothing converts/persists the new session's durable key. So:

1. Launch: resolve reads marker `054328` (dead) → fresh "(untitled)" session created (`161037`).
2. Operator works in it (persists fine, 6 msgs).
3. Nothing writes `161037` to the marker (no aify delivery went through the loop).
4. Stop+start: resolve reads marker `054328` (still) → fresh again → loops forever.

`discoverSessionId` (adapters/hermes.js:73) returns the EPHEMERAL active-file id as PRIMARY, so even the report-back path can't fix it (an ephemeral id is dead on restart).

## Fix: periodic resume-pointer sync (DURABLE)

Add a best-effort periodic beat in the hermes loop that keeps the marker + aify handle = the **live session's DURABLE key**, independent of aify delivery:

```
every ~15-20s (best-effort; never throws into delivery):
  eph = read TUI active-session file (ephemeral, the operator's current session)
  if eph:
    open gateway WS (gateway URL marker) → session.active_list
    row = pickSessionRowById(list, eph)        // find the live row
    durable = rowResumeKey(row)                 // its session_key
    if durable && durable !== current marker:
      writeSessionIdMarker(agent, durable)      // resume pointer now tracks the live session
      httpCall PATCH /agents/<agent>/session-handle  { sessionHandle: durable }  // aify agrees
    close WS
```

Then on restart, resolve reads the marker (the live session's durable key) → resumes it. Compose with the existing fixes: durable-marker (9353f86), DB-validate (5c1617a), dead-handle unset (98bcc91).

### Tasks
1. `startResumeMarkerSync({ agentId, gatewayUrl, activeSessionFile, tempDir, httpCall, openWs, intervalMs })` — self-contained, best-effort, `unref()`'d timer; returns a stop fn.
2. Wire it into `runDeliveryLoop` after the gateway URL is resolved; stop it in teardown.
3. Unit-test: live ephemeral whose row carries a durable `session_key` → marker becomes the durable key + PATCH fired; no active-file / no row → no-op; gateway error → swallowed.
4. Verify open question: do `session.active_list` rows carry `session_key`? If not, resolve via `session.list` (the DB) by matching the ephemeral → its durable key (the resolve path already queries session.list).

### Risk
Touches the load-bearing managed-hermes delivery loop. Keep it a SEPARATE best-effort beat (failures isolated from delivery). Deploy = `install.sh --client hermes` + wrapper restart (bridge-side).

## PROVEN (2026-06-05): the pointer sync is necessary AND sufficient

Probing the live gateway directly:
- `session.list` returns **26 rows** as `{"sessions":[{"id":"20260605_161037_c57d9a","title":"Context Retention Banana Test","message_count":6,...}]}` — note rows have **`id` = the durable key** and **no `session_key` field** (so `rowResumeKey` correctly falls back to `id`).
- `pickSessionRowById(session.list, "20260605_161037_c57d9a")` → **MATCH**, `rowResumeKey` → the durable key. The parser is FINE (an earlier "active_list fallback" reading was gateway-state volatility, not a parser bug).
- End-to-end: with the marker SET to a real session, `resolve-session` returned a **real resumable session** (`marker(live)` / `marker(db-resumable)`), never fresh. With the dead marker (`054328`), it goes fresh.

**Conclusion:** marker-based resume works; the ONLY defect is that the marker never tracks the live session. The periodic resume-pointer sync above is the complete fix. Source the durable key from `rowResumeKey` of the agent's live `active_list` row (which yields the durable key), and when active_list is momentarily empty fall back to leaving the marker unchanged. No `session.list` parser change and no DB-validate softening needed.

## Immediate stopgap (no code)
Manually point the marker + handle at a real session from the screenshot (e.g. the banana test) so ONE restart resumes it:
```bash
echo -n 20260605_161037_c57d9a > /tmp/aify-hermes-session-next-tech-lead
curl -s -X PATCH http://localhost:8800/api/v1/agents/next-tech-lead/session-handle \
  -H 'content-type: application/json' -d '{"sessionHandle":"20260605_161037_c57d9a"}'
```
Then restart the wrapper — it should resume that session. (Still re-drifts until the periodic sync lands.)
