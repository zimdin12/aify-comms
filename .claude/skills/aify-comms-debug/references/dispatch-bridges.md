# aify-comms debug: Bridges, sidecars, wake modes and session ownership

## Whole managed fleet went down seconds after someone ran `aify-comms` (2026-08-11)

**Symptom.** Nine managed agents went `offline`/`available` at once, with no deploy, no container
restart, and nothing in the service log to explain it. The only preceding action was a four-second
run of the bare `aify-comms` command, intended purely to confirm the launcher still worked.

**Cause.** `aify-comms` with no arguments is **not** an info command — it execs
`server.js --environment-bridge` and starts a real environment bridge. That new instance
**supersedes** the bridge already serving the environment; the older one exits, and on exit it
**reaps the managed workers it was hosting**. A four-second foreground run is therefore enough to
take down the entire managed fleet for that environment. Exiting the new bridge does not bring the
reaped workers back.

**Do not diagnose this as an install or deploy fault.** It was first misdiagnosed as an `install.sh`
regression, which cost a round of investigation before a repro disproved it. If a fleet drops with no
deploy, check shell history for a bare `aify-comms` before anything else.

**Fix / prevention.**
- Inspect with `aify-comms --check` (validates node, the script path, and that it parses; registers
  nothing and starts nothing), `aify-comms --help`, or `aify-comms doctor`.
- Recovery is to restart the environment bridge deliberately and let the managed agents re-spawn;
  `aify-comms doctor` `env-bridge` confirms one is actually ONLINE, not merely registered.
- The launcher now prints a banner naming the supersede-and-reap behaviour before it starts.

## Channel-routed claude dispatches stay queued forever (resident or managed)

**Symptom.** Send to a claude-code agent (resident OR managed). `dispatch_runs.status` stays `queued`, `execution_mode='channel'`. No `claimed` event, no delivery. The bridge appears alive (heartbeats), `aify-comms` MCP tools work for OTHER tasks, but channel dispatches sit forever.

**Cause.** Known Claude Code bug ([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)): when Claude loads many stdio MCP servers simultaneously, the slower ones get stuck in `still connecting` state. With a typical operator `~/.claude.json` (10+ servers including browsermcp, claude.ai connectors, etc.), `aify-comms-channel` loses the init race. Claude never registers the `notifications/claude/channel` listener, so the bridge's `mcp.notification()` calls are silently dropped — even though the MCP server itself is running and the channel-bridge reports `delivered`.

Verify by running `claude -p "list MCP servers"` from a plain shell — if `aify-comms-channel` shows under "still connecting" (instead of "connected"), the race is biting.

**Fix.** Set `AIFY_CLAUDE_STRICT_MCP=1` before launching `claude-aify` — it forces `--strict-mcp-config` with ONLY `aify-comms` + `aify-comms-channel`, which sidesteps the init race. (The default, flipped 2026-05-25, loads your full `~/.claude.json` MCP list — that's where the race comes from, but it means your other MCP servers ARE available in the wrapper.) Re-run `install.sh --client claude` if the wrapper is stale, then relaunch `claude-aify` with the env var set.

If you're on Windows Git Bash and the regenerated wrapper still fails (`2 MCP servers failed`, `aify-comms is currently disconnected`), the wrapper's MCP config paths may be MSYS-style. The wrapper uses `cygpath -m "$SCRIPT_DIR"` to convert to Windows-native paths. If cygpath isn't available in your Git Bash, install it (`pacman -S cygwin-tools` or update Git for Windows).

## Channel/resident dispatches silently fail on Windows — bridge can't reach localhost:8800

**Symptom.** Send to a resident or channel-routed claude-code agent. The dispatch_runs row stays `status='queued'` forever, `claim_bridge_id=''`. The channel-bridge child process is alive (verify with `Get-CimInstance Win32_Process | Where-Object CommandLine -match claude-channel.js`), `agent_turn_state.turn_busy=0`, the wrapper has the right flags (`--dangerously-load-development-channels`), and `~/.claude/projects/*/[session].jsonl | grep -c notifications/claude/channel` returns **0**. Nothing about the bridge looks wrong — but no claim ever happens and no `<channel source="aify-comms-channel">` event reaches the operator's session.

Smoke test that confirms the bug:
```bash
curl --max-time 5 http://localhost:8800/health                # times out
curl --max-time 5 http://127.0.0.1:8800/health                # returns immediately
node -e 'fetch("http://localhost:8800/health",{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log).catch(e=>console.log("ERR",e.message))'  # ERR aborted
node -e 'fetch("http://127.0.0.1:8800/health",{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log).catch(e=>console.log("ERR",e.message))'  # OK
```

**Cause.** Docker Desktop on Windows reports IPv6 port bindings (`docker port aify-comms-service` shows both `0.0.0.0:8800` and `[::]:8800`), but its IPv6 port forwarding to the container is unreliable — connections to `::1` hang silently. On Windows, `localhost` resolves to IPv6 `::1` first, so node's `fetch()` and curl both hit the broken IPv6 path. The channel bridge's `/dispatch/claim` poll aborts at `HTTP_TIMEOUT_MS` (20s) every cycle, no run is ever claimed, no `notifications/claude/channel` is ever emitted, and the symptom looks identical to a missing channel-server registration or a queue-routing bug. The same applies to managed runs going through `server.js` HTTP, and to anything else the wrappers do over `http://localhost:8800`.

**Fix (shipped, commit `71f2576`).** `claude-channel.js` and `mcp/stdio/server.js` now coerce `http://localhost` URLs to `http://127.0.0.1` before fetching. Defensive — works regardless of what env vars or wrappers pass. No-op on Linux/macOS (same loopback address). The wrapper template still uses `127.0.0.1` directly in the generated MCP config so operators on Linux don't notice anything; the bridge-level fix protects against custom `AIFY_SERVER_URL=http://localhost:...` configs and stale wrappers that predate the install regeneration. Run `install.sh --client claude` and restart `claude-aify` after pulling — verify with the smoke test above.

**Manual quick-fix while you wait to update.** Set `AIFY_SERVER_URL` / `CLAUDE_MCP_SERVER_URL` to `http://127.0.0.1:8800` in the wrapper's MCP env block (or `~/.claude/settings.local.json`) before launching the wrapper.

## Stale session handle causing prompt.submit failures (Plan 6 A)

**Symptom.** Dispatch fails at delivery time with `prompt.submit failed: session not found` (hermes) or analogous "session not found" / GC'd-rollout warnings on codex / pi / claude. Bridges look alive, heartbeating, and the dispatch row reports `delivered` — but the runtime rejects the handle. `agents.session_handle` matches a session that no longer exists in the runtime.

**⭐ ROOT CAUSE for managed HERMES + FIX (`9a71b72`, 2026-06-04) — the recurring one.** Hermes has TWO ids per session: a **durable `session_key`** (timestamp form `20260604_215845_395891`, persisted in the SessionDB, what `--resume`/`session.resume` REQUIRE) and an **ephemeral `sid`** (`uuid4().hex[:8]`, e.g. `8b821120` — the gateway's in-memory `_sessions` key, **regenerated on every gateway restart**). `session.active_list` rows carry both: row `id`/`session_id` = ephemeral, `session_key` = durable. The bridge was capturing/persisting the **ephemeral** id as the agent→session marker/handle (`rowRealId` read `r.id`; the TUI also writes the ephemeral id to the active file on a FRESH session), so the next launch did `hermes --tui --resume <ephemeral>` → the sid was gone → gateway **4007 "session not found"**, with **no resume-or-fresh fallback**. This regressed ~2026-06-03 when the native-session-id rework retired the always-resumable `aify-<id>` pre-seed and replaced it with capturing the wrong id. **Fix:** `resolve-session` now persists/resumes the **durable `session_key`** (new `rowResumeKey()` in `hermes-gateway-protocol.js`; gateway delivery RPCs target the ephemeral sid, and normal managed delivery uses `prompt.submit` only), and on **no resumable session it returns "" (start fresh) + clears the dead marker** so a stale id stops being replayed by send-driven spawns. A poisoned marker (`8b821120`/`${...}`) now self-heals to a fresh session instead of erroring. **Deploy:** `./install.sh --client hermes` + relaunch. If you still see 4007 after that, the wrapper/native copy is pre-`9a71b72`.

**⭐ SECOND root cause — "FRESH session / lost history on EVERY restart" + FIX (`3a38d30`, 2026-06-04).** `resolve-session` decided what to resume from the gateway's live **`active_list`**, which is **EMPTY after any gateway/aify-comms restart** (sessions live in the SessionDB but aren't "loaded"). So the marker's real session was never found "live" → it fell to **fresh**, and the `9a71b72` fresh-fallback then **cleared the marker** → the agent abandoned its history and minted a brand-new session every launch. Verified live: a fresh gateway reports `active_list`=0 while `session.list`=69 (incl. the agent's real, resumable session). **Fix:** `resolve-session` now also queries **`session.list` (the SessionDB)** and PREFERS a marker that is **resumable-from-DB** (stable across restarts); it falls to `active_list`-most-recent only when there's no marker, and the dead-marker clear fires **only when the DB positively confirms the marker is gone** (`dbConsulted` guard — a transient `session.list` failure never clears a still-resumable marker). If an agent keeps coming up on a fresh/empty session after restarts, the wrapper/native copy is pre-`3a38d30` — `./install.sh --client hermes`. To restore a specific prior conversation, seed its durable `session_key` (from `hermes sessions list`, e.g. `aify-sc-coder #101` → `20260603_114935_8f7b7a`) into the marker / via dashboard Set handle.

**Detection.** Compare the stored handle against the runtime's actual current session id.

1. Get the stored handle from the server:
   ```bash
   curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID | python -m json.tool | grep -E '"sessionHandle"|"runtime"'
   ```

2. Get the runtime's actual current session id, per runtime:

   - **hermes**: do **not** use gateway `session.most_recent` as the current visible session — it can be historical DB state. The visible-TUI runs on the agent's **native hermes session id** (a normal timestamp id stored as the `sessionHandle`, symmetric with claude/codex) — there is no synthetic `aify-<agentId>` session. The PRIMARY id source is the per-agent active-session file (`HERMES_TUI_ACTIVE_SESSION_FILE` / `AIFY_HERMES_ACTIVE_SESSION_FILE`), bound to the agent by the `aify-hermes-session-<agentId>` marker. To find the live runtime sid, read that file (or ask the gateway `session.active_list` for the agent's stored real id), or just use `comms_agent_info`:
     ```bash
     curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID | python -m json.tool | grep -E '"sessionHandle"|"sessionId"'
     ```
   - **codex**: for a fresh `codex-aify`, do **not** scan `~/.codex/sessions`; the newest rollout may be an unrelated historical thread. Use `$CODEX_THREAD_ID` only if this exact session exported it, usually after `codex-aify --resume <id>`.
   - **pi**: `~/.omp/agent/sessions/<project-key>/...`, OR ask the bridge directly:
     ```bash
     curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID/pi-session-state | python -m json.tool
     ```
   - **claude**: list transcripts the operator's session might have created:
     ```bash
     ls -t ~/.claude/projects/*/*.jsonl | head -5
     ```
     If the stored `sessionHandle` (an `<id>.jsonl` basename) doesn't appear, it's stale.

3. If the runtime's truthful id differs from `agents.session_handle`, this is the Plan 6 A gap.

**Fix.**
- With Plan 6 A1 in place (`mcp/stdio/session-handle-heartbeat.js`, commit `3167423`) the bridge auto-corrects within one heartbeat tick (~60s). Wait 60s and re-check the stored handle — it should now match the runtime.
- With Plan 6 A2 in place (`mcp/stdio/server.js` `computeInitialSessionHandle`, commit `edbc374`) the FIRST register call also uses discover over env — fresh agents are correct on first dispatch.
- If you're running pre-Plan-6 bridge code (verify with `git log mcp/stdio/session-handle-heartbeat.js | head -3` showing the Plan 6 A1 commit), pull + restart the wrapper.
- One-shot manual recovery without waiting for the heartbeat: re-register the agent with an empty `sessionHandle` and let the bridge's discover fill it:
  ```
  comms_register(agentId="YOUR-AGENT-ID", role="...", runtime="...", cwd="...", sessionHandle="")
  ```
  OR unset the runtime's session env var in your shell before relaunching the wrapper:
  ```bash
  unset HERMES_SESSION_ID    # hermes
  unset CODEX_THREAD_ID      # codex
  unset PI_SESSION_ID        # pi
  unset CLAUDE_SESSION_ID    # claude
  hermes-aify --aify-agent YOUR-AGENT-ID   # or codex-aify / pi-aify / claude-aify
  ```
  Current `codex-aify` and `hermes-aify` deliberately do not rediscover from historical runtime state on fresh launch; explicit `--resume <id>` is the only wrapper-side handle export. For Hermes, a fresh visible session becomes wakeable after the TUI writes the active-session file and the live bridge registers/heartbeats it.

## Fixed check: wrapper-backed channel claim must be delivery-owner-owned

**Symptom.** A wrapper-backed Codex/Hermes dispatch is routed through `executionModes=["channel","resident"]`, but the environment bridge claims the run before the bridge-spawned delivery owner is fully registered. The dashboard may show the run as claimed/running while the visible wrapper terminal never receives the message.

**Cause.** Old builds allowed a generic environment bridge to claim wrapper-backed channel work. That bridge lacks the local app-server/gateway context and can only fail or fork hidden work.

**Fix.** Current builds require Codex's `bridge_kind='managed-wrapper-child'` or Hermes's `bridge_kind='channel-sidecar'`, plus the current active wrapper `terminal_id`, before the runtime's delivery owner can claim channel work. If you see this symptom, rebuild/redeploy the service, restart the environment bridge, then restart the managed session so a fresh delivery owner registers.

## Managed claude freezes on boot at a prompt (resume / compaction / permissions)

**Symptom.** A freshly-spawned or restarted managed claude sits at an unanswered TUI prompt
(the "Resume from summary / Resume full session" menu, a compaction question, the bypass-
permissions accept, the dev-channels acknowledgment, or a channel-enter prompt) and never
reaches a usable turn. The tell-tale downstream symptom is **"up-but-deaf"**: the agent
registers `online`/`available` but never claims any dispatched run (sends bounce with "no live
claimer", even the coldstart rescue re-hits the same stall) — because the worker never reached
its in-process MCP to register a wrapper-child / channel-sidecar bridge. Read the console tail
(`comms_console_tail`) to see which prompt it's stuck on.

**Fix (2026-06-05, updated 2026-07-25).** The host bridge auto-answers these via a centralized rules layer
(`claude-console-prompts.js`): resume and the three-option compaction recommendation → **full session** (cursor-aware ↓+Enter from the default), simple confirmation dialogs → Enter. Gated
to **managed claude only** (never a resident/operator session), requires an interactive menu
cursor (`❯`) and that claude is NOT mid-turn, fires once per appearance. If a NEW prompt
appears after a claude update, capture the frame into `mcp/stdio/tests/fixtures/claude-console/`
and add a rule. Kill-switch: `AIFY_NO_AUTO_ANSWER=1` (set in the wrapper env) disables it.

**Hardened (2026-06-12, `aca7562`) — the silent auto-compact-on-resume.** The channel-enter
rule once matched the bare substring `development-channels`, which also appears in the
worker's own BOOT OUTPUT (`--dangerously-load-development-channels …`) — at the moment the
resume menu rendered, the blind Enter accepted the highlighted "Resume from summary
(recommended)" and silently summarized the session away on EVERY cold start (operator: "it
auto compacts each time"). Now: channel-enter matches only the dialog's own question line
(`Enter channel to receive …`); any visible resume-menu text suppresses ALL blind-Enter rules
until the cursor-aware resume rule can answer; matching is recency-first (the latest dialog
text in the stream wins, so a scrolled-away menu can never re-claim a live dialog). If a
managed claude still loses context on restart, its PTY-hosting environment bridge predates
this fix — restart the `aify-comms` wrapper.

**Dev-channels acknowledgment (2026-07-03, `c1e1704`) — up-but-deaf on FIRST spawn.** The
wrapper launches claude with `--dangerously-load-development-channels server:aify-comms-channel`,
which triggers a first-run confirmation menu (`❯ 1. I am using this for local development / 2.
Exit`). No rule matched it (the `channel-enter` rule is the LATER "enter channel to receive"
prompt), so the worker booted, sat at the menu forever, and never registered a claimer =
up-but-deaf. The `dev-channels-accept` rule now blind-Enters the highlighted accept option
(matched on the acknowledgment's own question line, so a boot-log mention of the flag can't
trip it; subject to the same cursor + resume-menu-interlock gates). **Deploy:** this is an
`mcp/stdio/` change — re-run `install.sh` on each host (re-copies the bridge into
`~/.aify-comms/`) AND restart the wrapper; until then newly-spawned workers keep stalling. To
un-stick an already-stuck worker without redeploying, type a bare Enter into its console
(dashboard Console, or `POST /terminals/{id}/input` with body `"\r"`).

## Runtime "not launchable" / up-but-deaf on a Windows host with a non-ASCII profile path

**Symptom (fixed 2026-07-20, fix-non-ascii-paths).** On a Windows host whose user profile
contains a non-ASCII character (`C:\Users\KertMõttus`), every managed spawn dies before a
worker launches: the agent goes up-but-deaf, runs fail at the 180s backstop, and the
environments API shows the runtime as not launchable with a diagnostic like
`[where: stdout="C:\Users\KertMottus\..."] [rejected C:\Users\KertMottus\...: not a real
executable file]` — note the missing `õ`.

**Cause.** The bridge resolved wrappers by shelling `cmd /c where claude-aify` and decoding
stdout as UTF-8. `where.exe` writes the console's OEM codepage, which lossily transcodes
non-ASCII path characters (`õ` → `o`, or mojibake `├╡`), so the "resolved" path did not
exist on disk. The same OEM-vs-UTF-8 mismatch affected the PowerShell `Win32_Process`
inspectors used by the orphan reapers — command lines containing non-ASCII workspace or
wrapper paths were mangled before matching.

**Fix.** `resolveExecutable` now walks PATH+PATHEXT in-process (`resolveOnWindowsPath`,
plain `fs` — no codepage round-trip), prefers the `.cmd` shim over the extension-less
Git-Bash wrapper script (`where` listed the script first and the old code blindly took line
one — broken even on ASCII-only hosts), and treats `where` output as a fallback hint that
must exist on disk. The PowerShell inspectors now lead with `PS_UTF8_PRELUDE`
(`win32-text.js`) forcing UTF-8 stdout. **Deploy:** `mcp/stdio/` change — re-run
`install.sh` on each host AND restart bridges/wrappers. **Workaround on an old bridge:**
set `AIFY_CLAUDE_COMMAND` (or `AIFY_CODEX_AIFY_COMMAND` / `AIFY_HERMES_AIFY_COMMAND`) to
the ABSOLUTE wrapper path (`C:/Users/<user>/.local/bin/claude-aify.cmd`) and restart the
bridge — absolute paths skip `where` entirely.

## Resident relaunch goes offline + deaf (auto-register refused by the race guard)

**Symptom.** Close a resident wrapper and relaunch it quickly. The session works for
SENDING, but: status reads `offline`, inbound messages never arrive (runs queue/fail with
no claimer), the sidecar bridge row stops heartbeating at the relaunch moment, and the
bridge boot log shows `auto-register for "<agent>" was refused — another live wrapper
owns this session (HTTP 409 … seen Ns ago)`.

**Cause (fixed 2026-06-13).** Kill-prior kills the old session seconds before the new
bridge boots, but the dead bridge's heartbeat lease (~150s) makes it look like a LIVE
owner — the Phase-4 race guard 409'd the auto-register, which never retried. No binding
file → `claude-channel.js` never binds (mute: no claims, no liveness) and
`runtime_state.bridgeInstanceId` stays pinned to the dead bridge (→ `offline`).
**Fix:** the server now allows a SAME-session-handle relaunch to take over an IDLE
prior bridge (supersedes it; a prior with an in-flight claimed/running run still 409s —
the Phase-4 in-flight protection stands), and the bridge retries a refused auto-register
every 30s for ~4 min. **Recovery on an old bridge:** run `comms_register` inside the
session (binds immediately, no restart) or relaunch once more after updating.

## Resident sends say "sent" but the agent never receives them (post mode-switch)

**Symptom.** An agent was switched managed→resident (operator launched the resident
terminal FIRST, clicked "switch to resident" SECOND). Sends report "sent", runs sit
`queued` with `claim_bridge_id=''`, and the agent's `channel-…` sidecar bridge row stops
heartbeating at the exact switch moment.

**Cause (fixed `9d81ea8`, 2026-06-12).** The switch clobbered `driver_state` to 'idle',
so the server answered the resident session's OWN channel sidecar with the mode-FSM
`release` — and the sidecar permanently exited its poll loop. Fix: the switch keeps
'driving' when adopting a live resident candidate; the release sites self-heal (a fresh
resident bridge ⇒ adopt driving, never release); claude-channel.js treats `release` as a
60s dormant re-check instead of a permanent stop. **Recovery on an old bridge:** restart
the agent's terminal — queued runs deliver as soon as a live sidecar claims.

## Send to an `available` managed claude FAILED after ~180s instead of cold-starting

**Cause (fixed `9d81ea8`, 2026-06-12 — root-cause-G parity).** The channel-mode claude
branch never fell back to `_coldstart_spawn_request_for_dispatch` when
`_ensure_managed_pty_for_dispatch` had no usable session row (the post-env-restart
state); the run sat queued with a claimer that could never exist until the queued-run
backstop failed it. hermes/codex always had the fallback; claude now does too
(`test_dispatch_claude_coldstart.py`). Re-send after deploying — the message cold-starts
a worker.

## Run failed with a "provider rate-limiting, not your request — retry shortly" notice

**Symptom.** A dispatch run you sent comes back FAILED and the sender notice says something like
*"provider rate-limiting, not your request — retry shortly"* rather than a raw API error.

**This is expected, not an aify bug.** As of 2026-06-07 (`11e7a5a`), when a run fails because the
underlying provider throttled the worker (an Anthropic "temporarily limiting requests" / "hit your
limit" message, an HTTP 429/529, or an "overloaded" error), the failure mirrored back to the SENDER
is rewritten into a clear, human notice instead of surfacing the raw provider/API error text. It
means: the request was fine, the provider is rate-limiting the model right now, and you should
**retry shortly**. The agent itself is healthy.

**What to do.** Wait a short while and re-send — there is nothing to repair on the aify side. If the
notice persists across many minutes, the provider throttle is sustained (check the agent's Console
for the upstream provider message), but the run-failure path is working as designed.

1. Capture the exact symptom (dispatch run ID, agent ID, error text).
2. Hit `curl http://localhost:8800/api/v1/dispatch/runs/<id>` to get the raw run state.
3. Hit `curl http://localhost:8800/api/v1/agents/<id>` for the agent state.
4. Forward those three pieces to whoever is debugging aify-comms. A fresh repro against current code (post-hard-reset) is worth 10× more than a trace against stale state.

## Bridge log lines: `claim timed out` / `503 database is locked` / `fetch failed` — triage (2026-07-01)

Three different signatures, three different meanings — don't conflate them:

- **`fetch failed` / `transient HTTP error … will retry on next poll` … `recovered after N failure(s)`** — the bridge aggregates the first two consecutive failures, warns on the third with the nested socket cause, reports a sustained outage at most every 30 seconds, and emits one recovery summary. A deploy or network interruption is a common cause, not proof; use the nested cause and service/container state. If no recovery arrives, check the service and network path.
- **`HTTP 503 … database is locked`** — write-lock contention under load. As of `d069f51` the service RETRIES the write (3×, 0.1/0.25/0.5s backoff) before ever surfacing a 503, so this should be rare; if it appears it's genuine sustained overload (correct backpressure), not a transient. The claim endpoints never 503 on contention — they return an empty claim (200) and retry next poll (`6eb3263`).
- **`claim … timed out after 28000ms`** — the old long-poll lock-overshoot, FIXED (`6eb3263`): claim probes now open with a short busy_timeout (`SQLITE_CLAIM_BUSY_TIMEOUT_MS=1200`) and fail fast, and `longpoll.MAX_WAIT_S` is 25s (below the bridge's 28s HTTP timeout). If you still see it, the host's service predates the fix — `git pull && docker compose up -d --build`.

All three are server-side; a host running its own service must pull + rebuild to get the fixes. See DECISIONS.md, "Claim probes fast-fail; writes retry the lock before 503."
