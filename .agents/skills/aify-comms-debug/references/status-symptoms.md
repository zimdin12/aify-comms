# aify-comms debug: Status symptoms: when the badge disagrees with reality

Split out of `status.md` (2026-08-03) so one symptom does not load the whole catalogue. Sibling files are listed in the skill's routing table.

## Contents

- [CHECK THIS FIRST — agent latches `working` forever, or never shows `working`: its bridge has NO agent id (2026-07-14)](#check-this-first-agent-latches-working-forever-or-never-shows-working-its-bridge-has-no-agent-id-2026-07-14)
- [Agent shows `online`, but no live worker exists](#agent-shows-online-but-no-live-worker-exists)
- [Agent shows `online`/`Console ready` but messages stay queued (status lied)](#agent-shows-online-console-ready-but-messages-stay-queued-status-lied)
- [Agent reads idle but its queued work NEVER delivers (deaf agent) — 2026-07-26](#agent-reads-idle-but-its-queued-work-never-delivers-deaf-agent-2026-07-26)
- [Agent shows online without a console (Plan 5 Section C)](#agent-shows-online-without-a-console-plan-5-section-c)
- [Managed claude shows `online` while it is clearly thinking](#managed-claude-shows-online-while-it-is-clearly-thinking)
- [Managed claude flaps to `online` while working — but only when the Console is CLOSED](#managed-claude-flaps-to-online-while-working-but-only-when-the-console-is-closed)
- [Managed claude showed `blocked` mid-generation (2026-06-07)](#managed-claude-showed-blocked-mid-generation-2026-06-07)

## CHECK THIS FIRST — agent latches `working` forever, or never shows `working`: its bridge has NO agent id (2026-07-14)

**Symptom (either half, or both in sequence).** One agent is permanently `working` while idle and
never self-heals — it survives every bridge fix, every restart of *other* agents, and even a
manual `/turn-end` (after which it flips to the mirror symptom: it stays `online` while clearly
working and can never show `working` again). Everything ELSE about the agent is healthy: it
registers, sends and receives messages, appears in `comms_agents`, and heartbeats.

**Root cause.** Its wrapper was launched **without an agent id**, so `AIFY_AGENT_ID` is not in the
bridge's process env. EVERY turn-state path is gated on that one variable:

| Path | Gate | Effect when unset |
|------|------|-------------------|
| bridge turn detector | `server.js` — `if (AIFY_AGENT_ID && …)` | never arms (no KEEP-FRESH, no KEEP-CLEARED, no turn-start/turn-end) |
| `Stop` hook → `/turn-end` | `if [ -n "$AIFY_AGENT_ID" ]` | dead — nothing can CLEAR |
| `UserPromptSubmit` / `PostToolUse` → `/turn-start` | `if [ -n "$AIFY_AGENT_ID" ]` | dead — a typed turn never SETS |
| session-store capture hook | keyed by agent id | dead — the detector couldn't resolve the transcript even if it armed |

The **channel-sidecar** carries the agent id in its own config, so it survives and still POSTs
`/turn-start` on an inbound wake. That leaves the agent **SET-only, never CLEAR** → latched
`working`. Once the in-turn backstop ages the stale flag out, it derives to `online` and — since
the turn-start paths are dead too — can never read `working` again. Both symptoms, one cause.

It degrades **invisibly**: comms, messaging and heartbeats all keep working, so nothing looks
broken from any other angle. (Live incident: `general-manager` ran this way for weeks.)

**Diagnose (30 seconds).** The DB cannot tell you this — check the PROCESS env:

```bash
# Every claude bridge + whether it has an identity. <ANONYMOUS> on a REGISTERED agent = this bug.
for p in $(pgrep -f "aify-comms/mcp/stdio/server.js"); do
  aid=$(tr '\0' '\n' < /proc/$p/environ | grep -m1 '^AIFY_AGENT_ID=' | cut -d= -f2)
  ppid=$(awk '{print $4}' /proc/$p/stat)
  echo "${aid:-<ANONYMOUS>}  pid=$p  cwd=$(readlink /proc/$ppid/cwd)"
done
```

Corroborating evidence: the agent's `agent_status_state.last_event` is **frozen at `turn_start`**
for far longer than any real turn (a live detector emits KEEP-FRESH or KEEP-CLEARED every 45s, so
silence = no detector), while its `bridge_instances.last_seen` stays fresh. And
`/tmp/aify-claude-session-<agent>.json` is **absent** while every healthy agent has one.

**Anonymous bridges that are NOT this bug:** the environment bridge (`--environment-bridge`) and
a plain unregistered `claude` session with the comms MCP attached. Both are legitimately id-less.

**Fix.** You cannot repair a running process — re-registering does NOT help (`comms_register`
writes DB rows; `AIFY_AGENT_ID` is read once at bridge boot), and neither does Claude Code's
in-app `/resume` picker (it swaps the conversation *inside* the same process, keeping its env).
The session must be **relaunched** with an identity. `--resume` preserves the full conversation:

```bash
claude-aify --aify-agent <agent-id> --resume <session-handle>
```

**Prevention (shipped 2026-07-14).** `claude-aify` now recovers the agent id from a bare
`--resume <handle>` — it asks the service which agent owns that handle (authoritative; survives a
`/tmp` wipe), falls back to the local session store, and if the id is still unknown prints
`NO AGENT ID: aify turn/status detection is DISABLED` instead of degrading in silence. The
operator-facing resume command (`resume_command`, surfaced by the dashboard) now carries
`--aify-agent`; it previously did not, which is how the identity got dropped in the first place —
the product was handing out the command that broke the agent.

## Agent shows `online`, but no live worker exists

**Symptom.** Dashboard or `comms_agent_info` reports a managed Codex/Hermes
agent as `online`, but its Console is gone, sends do not visibly
land in a real worker, or the session only has an old `vterm_*`/historical
terminal row.

**Cause.** Older service builds cached `agent_live_state.status` using
heartbeat freshness, not live worker presence. A wrapper PTY could exit while
another heartbeat kept the cache row fresh, so the UI kept showing
`online`. A related bug invalidated the corrected writeback
immediately, and readiness/registration changes could leave future-dated cache
rows in place.

> **Note (2026-06-18):** the live-status cache no longer lives in the
> `agent_live_state` SQLite table at all — it is a process-global in-memory dict
> (`_LIVE_STATE_CACHE`, `service/reconcilers/status_cache.py` — moved there in v0.5; the router reaches it as `status_cache._LIVE_STATE_CACHE`). The table is RETAINED for
> schema compatibility but is no longer read or written on any path (vestigial), so
> a dump of `agent_live_state` is NOT the live status — don't debug from it. This
> also resolved the recurring `database is locked` 503s (the table was being
> refresh-WRITTEN on every dashboard poll — the read-path write storm + WAL bloat);
> reads now serve from memory with zero DB writes on the hot path. The cache is
> process-global, so the service MUST stay single-worker. See DECISIONS.md,
> "Live-status cache is in-memory, not SQLite".

**Fix (2026-06-01 / 2026-06-02).** Update and restart/rebuild the service. Current
builds downgrade managed wrapper-backed agents with no live `terminal_sessions` row
to `available`, persist that downgrade, invalidate live-state cache on
`PATCH /agents/{id}/ready`, and invalidate cache on registration. **`online` now
means deliverable — it requires a live CLAIMER, not just process presence.** Both
managed **claude** AND managed **hermes** are now in the channel-sidecar-delivery
gate: `online` requires a live, non-superseded channel-sidecar (the actual claimer —
`claude-channel.js` for claude, the `hermes-managed-host.js` delivery loop for
hermes) in addition to a live console PTY. A live PTY / `-aify` wrapper / virtual-rpc
row alone can no longer manufacture `online`. As of 2026-06-02 the delivery loop also
publishes an explicit claimer **lease** (acquired when it becomes a live claimer,
released on clean teardown), so a cleanly-exited loop is immediately non-deliverable
rather than waiting out a staleness window. A headless orphan (live sidecar, no
console — a visible-TUI violation and a proliferation source) reports `available` and
is reaped by `_reconcile_managed_worker_hygiene` (60s reconcile loop), which now
covers the hermes triad and also reaps ghost console rows (dead worker, stale
`attached` terminal). **As of 2026-06-07 (`8ef31a2`) the 60s reaper is the BACKSTOP, not
the primary path for lifecycle-driven teardown:** Stop/Restart/Reset/cli_takeover now
SYNCHRONOUSLY kill the live managed PTY (session-control enqueues a terminal stop;
`TERMINAL_MANAGER.stop` escalates SIGTERM→SIGKILL), so they no longer leave a headless
orphan waiting out the 60s hygiene reaper — the orphan path now only catches crash/leak
residue, not an operator Stop/Restart. Host-side defenses back this: the managed worker tree is
tree-killed when its console PTY closes, the channel-sidecar self-exits once its
parent process is gone, and the env bridge reaps console rows whose local
`process_id` is dead. After updating, restart the affected environment bridge or
wrapper so a real worker can re-register and recreate the backing terminal.

**Also (2026-06-02, `3ca464a`): a managed agent reads `offline` when its owning
environment bridge is down**, regardless of any surviving delivery-loop heartbeat —
a managed agent can only be hosted by its owning env bridge, so its effective status
is gated on that bridge. The status path resolves the STORED owning environment
(resolved id → `runtime_config.environmentId` → `machine_id`+runtime match), so even
after the worker row is gone the gate still fires. So killing `aify-comms` makes its
managed agents show `offline` immediately, not a stale `available`/`online`. Resident
agents are excluded (their liveness is the resident bridge, not the env bridge).

## Agent shows `online`/`Console ready` but messages stay queued (status lied)

**Symptom.** A managed claude shows `online` with a live Console, yet dispatches
don't deliver.

**Cause.** `online` used to derive from the wrapper PTY's terminal session, but
for managed claude the PTY only RENDERS — `claude-channel.js` (the channel
sidecar) is the actual claimer. A live PTY with a dead/superseded sidecar
delivered nothing.

**Fix (2026-06-01).** Managed claude now requires BOTH a live console PTY AND a
live, non-superseded channel-sidecar to be `online`; otherwise it honestly
reports `available` (note: "No live channel sidecar heartbeat (not
deliverable)"). The inverse case is also handled: a live sidecar with no console
is a "headless orphan" (visible-TUI violation + proliferation source) — it reads
`available` and is reaped by `_reconcile_managed_worker_hygiene` (60s reconcile
loop), backed host-side by PTY-close tree-kill of the worker and channel-sidecar
self-exit when its parent claude is gone. (2026-06-07, `8ef31a2`: an operator
Stop/Restart/Reset/cli_takeover now kills the managed PTY SYNCHRONOUSLY —
session-control enqueues a terminal stop and `TERMINAL_MANAGER.stop` escalates
SIGTERM→SIGKILL — so a lifecycle action no longer strands a headless orphan for the
60s reaper; the reaper is the backstop for crash/leak residue.) If you see
`available` with a live Console, the sidecar is down — restart the wrapper (and
ensure the self-heal/per-agent-id build is deployed).

## Agent reads idle but its queued work NEVER delivers (deaf agent) — 2026-07-26

**Symptom.** The dashboard shows the agent `online`/`available`, its bridge is alive, but
queued runs sit in `queued` forever. In the worst case the agent answers NOTHING at all — not
even ordinary sends.

**Cause.** The delivery gates (send-time queue decision + `/dispatch/claim`) key on the RAW
`agent_turn_state.turn_busy` flag — deliberately, because deriving it from status is what made
explicitly-queued sends land mid-turn. But `turn_busy` is only cleared by a turn-END event, and
two paths can never clear it:

- `_clear_turn_busy_for_dead_bridges` SKIPS `turn_bridge_id IN ('', 'user-prompt-submit')` —
  every hook-driven resident-claude turn — and skips any turn whose bridge row is still fresh.
- A killed harness, a failed `Stop` hook, or a transcript classifier stuck reading in-flight
  latches `turn_busy=1` with no further writes.

Past the 30-min ceiling `derive()` already clamps `in_turn`, so the agent READS idle while
delivery is still holding — the disagreement you're looking at. And because the claim gate's
early return is gated on `"steer" in capabilities`, a target WITHOUT steer gets no run at all:
**deaf to every dispatch**. `_row_capabilities` strips `steer` from resident claude without
`channelEnabled`, managed hermes without `channelEnabled`, resident hermes without a gateway
URL, and resident pi/opencode — so this is not an exotic configuration.

**Fix (2026-07-26, `b6601ac`).** Both gates go through `_turn_busy_holds_delivery`, which reads
the raw flag but bounds it by `TURN_BUSY_BACKSTOP_SECONDS` — the SAME ceiling status uses, so
delivery and displayed status can never disagree permanently. Real turns are unaffected: the
KEEP-FRESH detector re-stamps `turn-start` every 45s, so only an abandoned flag ages out.

**Triage.** Check the raw flag and its age directly:

```bash
# inside the container
sqlite3 /data/aify.db "SELECT agent_id, turn_busy, turn_bridge_id, turn_updated_at
  FROM agent_turn_state WHERE turn_busy = 1;"
```

A row whose `turn_updated_at` is older than a few minutes while the agent is visibly idle is a
latched flag. It now self-heals at 30 min; if it keeps recurring for the SAME agent, the real
bug is upstream in that runtime's turn-end signal (see the KEEP-CLEARED detector above), not in
the gate.

## Agent shows online without a console (Plan 5 Section C)

**Symptom.** Dashboard shows a managed agent as `online`. Clicking through to the agent never loads a Console widget; no live terminal_session attaches. The wrapper PTY exited some time ago, but the agent never downgraded.

**Detection.** Compare cached status against actual worker presence. **Note
(2026-06-18):** the live status is now served from the in-memory `_LIVE_STATE_CACHE`,
NOT the `agent_live_state` table (which is vestigial — see the note above), so the
query below reads a table the service no longer uses; trust `comms_agent_info` / the
dashboard for the actual served status, and use the `terminal_sessions` half of the
query to confirm worker presence:

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
aid = 'YOUR-AGENT-ID'
live = c.execute('SELECT status, updated_at, refresh_after FROM agent_live_state WHERE agent_id=?', (aid,)).fetchone()
terms = c.execute(\"SELECT id, status FROM terminal_sessions WHERE agent_id=? AND status NOT IN ('stopped','failed','exited')\", (aid,)).fetchall()
print('live:', live)
print('active terms:', terms)
"
```

If `live[0]=='online'` AND `active terms` is empty, that's the Plan 5 Section C bug — `agent_live_state` cached `online` and `refresh_after` was keyed off heartbeat freshness rather than worker presence, so a sibling/operator heartbeat kept the lie alive.

**Fix.** Rebuild the container so `_enforce_live_worker_gate` (added at `api_v2.py:352` in commits `b58142e` + `f38f57d`) is loaded. On the next `GET /api/v1/agents` or `/agents/{id}` read, the gate validates the live worker and downgrades to `available`; a cache writeback ensures subsequent reads stay consistent. No manual DB patch is needed once Plan 5 is in.

## Managed claude shows `online` while it is clearly thinking

**Symptom.** A managed claude is visibly working — the Console shows `✻ Crunched for 3m 12s
(esc to interrupt)` — but the dashboard dot reads `online`, not `working`. Often "right after
one tool call it went online but it's actually still working".

**Cause.** The transcript turn-detector is structurally blind to LIVE generation: claude's
transcript grows per *completed message*, so during a long thinking phase the tail still shows
the last ENDED message → the detector reads not-working.

**Fix (2026-06-05).** The managed PTY's spinner footer now drives a TTL "console-working
lease" (`agent_console_signal`, `POST /agents/{id}/console-working`) that derives `working`
for the spinner window. It's additive/weak: gated on a live worker, never clears a real
turn, self-expires ~20s after the spinner stops. If it still under-reports: confirm the
service was rebuilt and the bridge reinstalled, and that the spinner footer actually renders
in the Console. Disable nothing — it can't manufacture `working` for an idle/dead agent.

## Managed claude flaps to `online` while working — but only when the Console is CLOSED

**Symptom.** A managed claude reads `working` while you have its dashboard Console **open**,
but flaps to `online` (then back to `working` when you reopen the Console) while it is still
working. "It starts working again if I have the terminal open on my screen, otherwise it goes
to online." Delivery still works — it's only the status dot.

**Cause.** The console-working lease (above) refreshes from the spinner footer streaming
through the managed PTY, but claude (Ink) only re-emits that footer while its PTY is
*actively rendered*. With the Console closed, an unwatched working claude goes quiet on the
PTY, the lease expires, and the dot falls to `online`. Opening the Console sends a resize →
claude repaints → the footer streams again → the lease refreshes (hence the correlation with
"having the terminal open").

**Fix (2026-06-05).** The bridge now runs a managed-claude PTY **repaint keepalive**
(`terminal-runtime._armConsoleKeepalive`): every ~4s it SIGWINCHes the PTY so claude re-emits
its footer whether or not anyone watches, and the lease TTL was widened to 20s to span the
keepalive cadence. claude-managed-only, best-effort, no visible flicker (the resize shrinks one
column then restores synchronously, so claude redraws an identical frame). To deploy: re-run
`install.sh --client claude` (re-copies the bridge) **and** restart the env bridge / claude
wrapper, then rebuild the service for the TTL. Kill-switch `AIFY_NO_CONSOLE_KEEPALIVE=1`;
cadence override `AIFY_CONSOLE_KEEPALIVE_MS`. If it still flaps after deploy: confirm the
keepalive is armed (managed claude PTY) and the service carries the 20s TTL. *Note — not a
time-grace:* a deaf/stale console can be recent too, so no age threshold distinguishes "working
but unwatched" from "wedged"; forcing the signal to keep flowing is the only truthful fix.

**Update (idle-grace → re-probe, 2026-06-18, `cf6ef25`).** The keepalive doesn't nudge at the full
~4s rate forever: after a sustained run of idle-prompt ticks it would drop to a SLOW re-probe
cadence (`consoleKeepaliveIdleReprobeTicks`, ~16s — below the 20s lease) instead of stopping
entirely. The earlier "stop after grace" let a turn RESUMING after a long idle never be
re-discovered (quiet PTY never re-emits a working footer → lease lapses → false `online`, the #224
residual); the re-probe re-discovers resumed work within the lease window with negligible churn (an
idle console only re-emits its idle residue → no working pulse). Defense-in-depth: a transient
`consoleClass==='unknown'` footer refreshes the lease only when a turn is known in-flight. If a
managed claude still flips to `online` mid-turn: confirm the bridge carries `cf6ef25` (re-run
`install.sh` + restart the env bridge) — the transcript turn-state detector is the primary backstop,
this keepalive is the console-lease layer.

## Managed claude showed `blocked` mid-generation (2026-06-07)

**Symptom.** A managed claude with a live active run flips to `blocked` while it is actually
generating — typically right after a subagent/Task dispatch whose prose contains
decision-flavored language ("which option do you want", "your call", "let me know how to
proceed"). The agent is not actually stuck at a prompt; it's still producing tokens.

**Cause.** The awaiting-input classifier read the terminal tail for prompt-/decision-shaped
phrasing to derive `blocked`. Subagent/Task PROSE that merely *discusses* a decision tripped
it, so a busy claude mid-generation got mislabeled `blocked`.

**Fix (`4ef1db3`, 2026-06-07).** A live spinner footer **short-circuits** the awaiting-input
hint: when the managed PTY tail carries a running spinner (`✻ … esc to interrupt` /
`✻ <verb> for <N>s`), claude is provably generating, so decision-flavored subagent/Task prose
no longer fires `blocked` while the spinner runs. A REAL prompt pauses the spinner (claude
stops emitting the footer when it's actually waiting on the menu/question), so a genuine
awaiting-input state still reads `blocked` — the spinner's presence/absence is the
discriminator. If a busy managed claude still flips to `blocked` after deploy, confirm the
service was rebuilt and the spinner footer actually renders in the Console.
