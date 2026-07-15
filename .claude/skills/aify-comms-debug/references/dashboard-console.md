# aify-comms troubleshooting: Dashboard console-mode & Console UX

## Dashboard console-mode: lock storm, flicker, statuses, parsing, env-not-found

This cluster was hardened on the `feature/dashboard-console-mode` branch. All fixes are in current builds; symptoms below mean the running container or host bridge predates them — rebuild the service (`docker compose up -d --build`) and/or restart the host bridge.

**`Database temporarily unavailable: database is locked` (503 in dashboard) — RESOLVED 2026-06-18 (`97a497a`; 0 locks verified, was ~18/min steady / 137/min post-restart).** The recurring steady-state cause was NOT the flickering console — it was the live-status cache being a SQLite table (`agent_live_state`) refresh-WRITTEN on every dashboard poll: that read-path write storm starved SQLite's single writer, and the constant status READS kept the WAL from checkpointing (it bloated to 41–83MB → slow commits). Fix: the live-status cache now lives in a process-global in-memory dict (`_LIVE_STATE_CACHE`, `service/routers/api_v2.py`) — reads serve from memory with ZERO DB writes on the hot read path, so a read can never take the write lock, and the WAL stays small (~5MB). The `agent_live_state` table is retained for schema compatibility but no longer read/written (vestigial). Constraint: the cache is process-global, so the service MUST stay single-worker (one uvicorn process / one event loop; the dashboard-next container only proxies) — scaling out requires a shared store (Redis) or sticky routing. Belt-and-suspenders (`581341d`): `GET /agents`, `/agents/{id}`, `/sessions` catch a transient lock and serve cached data instead of 503ing. (Heartbeat `last_seen` + turn-state still write SQLite at low frequency — they didn't lock in testing; Stages 2–3 of `docs/superpowers/plans/2026-06-18-in-memory-hot-state.md` are the headroom for larger fleets.) The EARLIER flickering-console fix is still valid for terminal-output load: `service/db.py` sets `PRAGMA busy_timeout`, `synchronous=NORMAL` per connection (WAL is set once at init — a persistent file-level setting); `api_v2.py` has a coalescing terminal-output write queue and returns a JSON 503 instead of an HTML 500. If you still see HTML 500s/crashes (vs a graceful JSON 503), the container predates these fixes — rebuild. See DECISIONS.md, "Live-status cache is in-memory, not SQLite".

> **Second cause, fixed 2026-06-29 (read-path repair-WRITES on GET list endpoints).** Even after the cache moved in-memory, a few `GET` list endpoints still ran maintenance repairs that WROTE on every poll — under a connected fleet (~40+ req/s) those write txns serialized behind terminal output and showed up as the top `SLOW-REQ` offenders. Fix: `GET /spawn-requests`, `GET /dispatch/runs`, and `GET /stats` are now pure reads (their repairs run in the 60s reconcile loop / already run on the `GET /agents` poll). `GET /agents` and `GET /sessions` deliberately KEEP their read-path repairs (they correct the roster/console-binding state in that response; a 60s lag would show a dead terminal as attached). If you add a repair to a GET handler, it must affect the correctness of THAT response — otherwise put it in the reconcile loop. Diagnostic middleware in `service/main.py` logs `SLOW-REQ`/`DB-LOCK`/5xx if you need to re-confirm. The residual idle CPU is inherent fleet load (req volume × live-bridge count), not a lock. See DECISIONS.md, "Read GET endpoints must not run repair-WRITES on the poll path".

**Console text scrambled / flickering / "can't see what's happening".** Causes + fixes (all in current builds; symptom means stale container/bridge — rebuild): (1) the dashboard rebuilt the whole console DOM per `terminal_output` frame — fixed by streaming each delta into the live xterm, deduped/ordered by monotonic `outputSeq`, skipping full refresh for non-visible terminals; (2) the live broadcast was per-POST and reordered vs seq under concurrency, so the `seq <= lastSeq` dedupe dropped a frame → ANSI desync → scrambled — fixed by emitting one ordered, coalesced, post-commit broadcast from the write-queue flush (flushes are serialized per terminal); (3) the default xterm DOM renderer janks under heavy output — current builds load the WebGL renderer with DOM fallback. Contract: the service is the sole source of `outputSeq` (bridge sends none); any new output path must route through `TERMINAL_OUTPUT_WRITES` so the sequence stays monotonic and the single ordered broadcast is preserved.

> **Full-screen-TUI scramble on attach/refresh — FIXED 2026-06-30 (server-rendered snapshot).** The deeper cause (separate from the live-stream items above): on attach/refresh the client replayed the RAW PTY byte log into a fresh xterm. A full-screen TUI's log is meant to drive a live screen at a fixed size; replaying it (mid-screen after the 64KB trim, at a possibly-different width) overlaps every historical draw into garbage, and refresh re-replayed the same log so it never recovered. Fix: `service/terminal_snapshot.py` replays the log through a headless VT emulator (`pyte`) sized to the viewer's cols/rows; `GET /terminals/{id}?cols=&rows=` returns a clean current-screen `snapshot` that the dashboard paints (after `term.reset()`) instead of the raw log. Live deltas still stream raw. Lazy/one-shot/executor-offloaded; `pyte` is optional (falls back to the raw log). If a console still scrambles after a hard-reload, the new-dashboard bundle is stale (rebuild `new-dashboard`) or the service predates the fix (`pyte` not installed → rebuild `service`). See DECISIONS.md, "Console replay uses a server-rendered screen snapshot".

**Environment does not advertise terminal support / WSL Codex Console is unavailable.** First check `/api/v1/environments`: if the runtime is available but the environment says `terminal=false` / `pty=false`, this is not a Codex problem. The bridge cannot load `node-pty`, so Console is disabled for all runtimes on that host. In that same WSL/Linux checkout, run `node -e "import('./mcp/stdio/terminal-runtime.js').then(m=>console.log(m.bridgeTerminalSupported()))"`. If it prints `false` or `node-pty` reports a missing `pty.node`, run `npm --prefix mcp/stdio rebuild node-pty`, then restart the `aify-comms` environment bridge. Current bridge heartbeats leave `terminalRuntimes` empty when PTY support is unavailable so the UI does not imply per-runtime support.

**"Environment does not advertise terminal support for claude-code" + dispatch sits queued forever.**

**Symptom.** Dashboard chat to a managed claude agent. The dispatch_run row stays `status='queued'`, `execution_mode='channel'`, no controls recorded. Clicking Start Console for the same agent shows the literal error *"Environment <id> does not advertise terminal support for claude-code"* with a `terminalRuntimes` list that omits `claude-code`.

**Cause.** Channel-route delivery (the `insert_messages_via_console=false` default) is NOT "no PTY at all" — it's "wrapper PTY exists, but delivery flows through MCP notifications instead of typing into stdin". `claude-channel.js` runs INSIDE a `claude-aify` wrapper as an MCP child of Claude and is the actor that claims the channel dispatch and emits the `<channel source="aify-comms-channel" ...>` event. If the bridge can't spawn a `claude-aify` wrapper PTY for the agent, the channel dispatch has nothing to claim it → `queued` forever. The bridge advertises `claude-code` in `terminalRuntimes` only when it can resolve a real `claude` executable; the dashboard's per-runtime support check uses that list. So the two symptoms have the same root cause: the bridge can't find `claude`.

**Fix.** From the same user/shell that runs `aify-comms` on the bridge host:
```powershell
Get-Command claude
Get-Command claude-aify.cmd
```
If either is missing, set `AIFY_CLAUDE_COMMAND` to the absolute path of the real `claude` binary BEFORE starting the bridge (system-wide PATH leaks between WSL and Windows make this common). Then restart `aify-comms`. Re-check `/api/v1/environments` — `terminalRuntimes` should now include `claude-code`. Re-dispatch; the queued run should claim within the dispatch-poll cycle (~3s) and the channel notification land in the wrapper.

**Workaround if you can't fix the bridge host right now.** Launch a resident `claude-aify --aify-agent <id>` on any machine where claude resolves; the resident wrapper claims the channel dispatch directly (same machine isn't required for channel route — the wrapper's `claude-channel.js` polls the service over HTTP).

**Broken agent statuses (everything "active", idle consoles shown "working", live Claude shown "active", old stopped terminal shown as current Console, or live agents shown "offline").** Cause: status was derived in multiple places that disagreed, and stale terminal/session bindings survived after bridge or runtime exits. Fix: all status flows through one live-state engine (`_compute_live_status_cache`/`_refresh_agent_live_state`); a bridge-id mismatch only forces offline when the session is not live and has no active run; `starting` counts as a live session; stopped/failed Console terminals are cleared as current session bindings and remain historical only. Current builds classify `working` from a real active run or a fresh bridge-reported `turnBusy` heartbeat, not from attached console bytes or stale delivered runs. Managed Claude PTY turns stay as running active runs until the reply closes them; if their terminal tail clearly asks for operator input or a decision, the agent is `blocked` instead of healthy `working`, but the normal Claude prompt/footer chrome alone is not blocked (and the auto-answered claude/hermes session-resume picker — Resume full session as-is / Don't ask me again — is suppressed too; only genuine y/n or password prompts flag). Completion-style unthreaded `info` messages can close active terminal runs during send/reconcile, and Claude PTY runs that visibly return to an idle prompt after output are completed-without-reply instead of pinning `working`. Stale unowned active runs are reconciled periodically, and recent overdue reply-contract reminders are sent by the periodic service loop; busy or blocked targets are deferred by the automatic reminder pass and retried after the agent returns idle. An attached-but-runless console is reachable/`active`, not `working`. While a working agent's terminal receives output, its yellow dot briefly pulses orange as a live-output hint, not a separate status. If an idle agent still shows `working` or statuses look wrong, the container or host bridge predates these fixes — rebuild the service and restart the host bridge.

**Dashboard Next normal chat sends queue instead of delivering live.** Cause: stale dashboard HTML/JS had the `Queue if busy` composer checkbox checked by default, so every normal Send posted `queueIfBusy=true`. Fix shipped: the checkbox is opt-in. Unchecked Send mirrors normal `comms_send`; checked Send intentionally waits behind active/queued work. If normal sends still create queued-only rows, rebuild the service and hard-refresh the dashboard.

**Dashboard Next shows an old managed xterm after switching the identity to resident.** Cause: the UI treated any cached terminal id as current, even when the agent's `sessionMode` was `resident` or the terminal row was stopping/stopped/failed. Fix shipped: the Session Console selector only uses managed xterm/cache when the identity is not `resident` and the terminal status is live. Resident agents show their resident attach surface or an explicit unavailable state; switch back to managed before expecting the managed PTY to receive dashboard-typed turns.

**`Dashboard parsing error` / `Unexpected token <`.** Cause: a non-JSON error body (proxy 502, gateway, unwrapped 5xx) was fed to `response.json()`. Fix: `apiFetch` degrades any non-JSON body to a structured `{ok:false,error}` toast. Persisting means stale dashboard HTML — rebuild.

**Continue/Compact says `environment does not exist`, no dropdowns, Regenerate does nothing.** Cause: free-text environment/runtime inputs and a Regenerate that rebuilt from the stale original session. Fix: Environment and Runtime are dropdowns scoped to live environments (source env kept as a flagged option if offline), workspace has a datalist, and Regenerate rebuilds from the current form selections. Stale dashboard HTML means rebuild.

**Open terminal for a managed/Pi agent: `session does not exist`.** Cause: the dashboard held a client-cached session id that went stale after a rebuild/re-register, so `/sessions/{id}/console/start` 404'd before any bridge code ran. Fix: console start refreshes sessions and retries once against the freshly resolved session; the bridge separately heals dead Pi/Hermes handles (see Pi sections above).

**Pi managed run hangs forever on missing/expired auth.** Cause: the Pi RPC adapter waited silently when Oh My Pi could not authenticate. Fix: Pi RPC classifies auth/provider failures and startup silence and fails fast with an actionable message (run `omp` manually in that environment to re-auth); dead saved Pi session IDs heal to a fresh session and the stale server `sessionHandle` is cleared via `PATCH /agents/{id}/session-handle`. Resident Pi does not auto-heal — it fails with a clear "clear the saved handle / start fresh" message by design.

**Operational note: never rebuild while service files are mid-edit.** The Docker image COPYs the working tree, not git HEAD. Running `docker compose up -d --build` while `service/` has an uncommitted syntax error bakes a broken image and the container crash-loops on `SyntaxError`. Before any rebuild: AST-check (`python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"`), run `python -m unittest service.tests.test_api_v2_regressions`, and commit. Recover by rebuilding from a known-green commit.

## Each keystroke in the dashboard Console submits as a command

**Symptom.** Operator types into the dashboard Console; every individual letter behaves like a separate Enter — the wrapper sees `c`, then `cd`, then `cd<space>`, etc. as distinct submissions.

**Cause.** The bridge's `terminal-input` control handler used to auto-append `\r` to every input body. Combined with the dashboard sending keystrokes individually, that meant each letter arrived as a submitted line.

**Fix.** Already fixed in commit `c1a1da1` — bridge does raw passthrough now (`TERMINAL_MANAGER.input(terminalId, rawBody)` with no auto-`\r`). The dashboard sends `\r` explicitly when the operator presses Enter. If you still see this, restart the bridge (the change is in `mcp/stdio/server.js` and loads at bridge start).

## Can't copy text out of a Console terminal

**Symptom.** Selecting text in a dashboard Console (xterm.js) and trying to copy does nothing — no clipboard contents, or only a "use browser copy/menu" toast. Plain click-drag may not even select, because the attached TUI is capturing the mouse (mouse tracking).

**Cause.** The dashboard is usually served over plain `http://192.168.x:8800` (a non-secure origin), where `navigator.clipboard` is `undefined`, so the async Clipboard API silently fails. And an interactive TUI grabs the mouse, so a plain drag is sent to the app instead of selecting text.

**Fix / how to copy (`69711d6`, in the `99cdada` merge).** Three ways, all working on the http origin via a `document.execCommand('copy')` textarea fallback:
- **Copy button** on the Console toolbar (next to Refresh/Stop) — copies the current selection, or selects + copies the whole scrollback buffer if nothing is selected.
- **Ctrl+Shift+C** — copies the current xterm selection (now routed through the same robust copy path, not the old "use browser menu" dead end).
- **Shift+drag** — hold Shift while dragging to select text even while the TUI captures the mouse, then use the Copy button or Ctrl+Shift+C.

Paste and interactive input are unchanged. If copy still fails after updating, the running Dashboard Next container predates the fix — rebuild with `docker compose up -d --build`.

## Console opens a second time for an already-running wrapper

**Symptom.** Operator clicks Start Console (or the dashboard auto-attaches) on an agent that already has a live wrapper PTY. A new sibling `terminal_sessions` row is created and a second wrapper PTY spawns instead of attaching to the existing one.

**Cause.** Pre-`fd00c85`, `start_session_console` always created a fresh terminal_session, even when the agent_session already had a live `terminal_id` in `{starting, attached, running, active, idle, recovering}`.

**Fix.** Already fixed in `fd00c85` — the endpoint now checks the existing terminal_id first and returns `{reused:true, terminal:{...}}` without spawning a sibling. Audit event `console_attach_reused_existing` confirms it in the audit log. If you still see it, container needs rebuild to pick up the api_v2.py change.

## Fresh Console seed starts with garbage / a broken ANSI escape

**Symptom.** Opening a Console (a fresh xterm attach) sometimes renders a line or two of on-screen
garbage at the very top of the scrollback — stray characters or a half-applied color/format — before
the live stream looks normal.

**Cause.** The seed sent to a freshly-attached xterm is the tail of the server-side terminal-output
buffer (capped at ~64KB). The buffer used to be trimmed to that cap at a raw BYTE boundary, so the
seed could begin in the MIDDLE of an ANSI escape sequence (`ESC[...m`). xterm then interpreted the
truncated escape's leftover bytes as literal output → the garbage at the top of the seed.

**Fix (`4a0bfb8`, 2026-06-07).** The 64KB buffer now trims at a clean LINE boundary (it drops to the
next newline rather than cutting mid-byte), so the seed always starts at the beginning of a line and
never mid-escape — a fresh xterm seed no longer renders broken-ANSI garbage. Cosmetic only (it never
affected delivery or the live stream). Rebuild the service and Dashboard Next containers if you
still see seed garbage after updating.
