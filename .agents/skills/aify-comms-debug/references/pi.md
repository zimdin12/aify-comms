# aify-comms troubleshooting: Oh My Pi / OMP

## Managed Oh My Pi / OMP reply is `(no output)`

**Symptom.** A dashboard-managed OMP (`runtime="pi"`) run reaches `agent_end`, but the dashboard stores `(no output)` as the human-visible reply.

**Cause.** Older OMP RPC adapters only captured streaming `text_delta` events. OMP can also provide the final assistant text on completion events such as `message_end`, `turn_end`, or `agent_end`.

**Fix.** Pull current `aify-comms` and restart the affected `aify-comms` / `omp-aify` bridge process (`pi-aify` is an alias). Current builds capture streamed deltas and final completion-event text before deciding that a managed run produced no reply. Verify the bridge checkout with `npm test` from `mcp/stdio/`.

## Managed Oh My Pi / OMP fails: `Session ... is in another project`

**Symptom.** A managed Pi run fails immediately with an OMP error like `Session "..." is in another project (C:\tmp)`.

**Cause.** The saved OMP/Pi `sessionHandle` belongs to a different project directory than the workspace where the bridge is trying to resume it. This can happen after workspace changes, resident-to-managed lease expiry, or an old session record being reused across projects.

**Fix.** Current bridge builds treat this as a stale managed handle: they clear the saved Pi handle and retry once with a fresh managed session. Resident Pi sessions still fail loudly because auto-swapping a visible CLI session would hide native memory changes. Pull current `aify-comms`, restart the affected bridge, and retry. If it still fails, use Dashboard **Sessions -> Actions -> Recreate** for that Pi agent.

## Managed Oh My Pi / OMP fails with Cursor API key when model is `default`

**Symptom.** A managed OMP run is cancelled before a chat reply and reports `No API key found for cursor`, even though `~/.omp/agent/agent.db` exists.

**Cause.** Older adapters treated stored `model: "default"` as a concrete model and launched `omp --mode rpc --model default`. OMP resolves that literal model name through the Cursor provider, which requires Cursor credentials.

**Fix.** Current OMP runtime handling treats blank model values and case-insensitive `default` as no explicit override, so the bridge launches `omp --mode rpc` and lets OMP use `~/.omp/agent/config.yml`. Pull current `aify-comms`, restart the host-side bridge/wrapper, and retry the managed run.

## Pi-aify wrapper exits mid-turn / "terminal failed before reply"

**Symptom.** Dashboard chat to a managed pi agent starts the wrapper PTY, the run goes to `running`, then `term_*` status flips to `stopped` and the run fails with "Terminal failed before an explicit reply was recorded".

**Cause.** Most common: `omp --mode rpc` child accidentally launched a nested `mcp/stdio/server.js` (because `pi-aify` exports the full aify env) that registered as a sibling bridge for the same agent. The nested bridge's registration superseded the parent bridge, the parent's RPC child died, and the wrapper exited.

**Fix.** Already fixed in commit `59c66ff` — `PiController` (`mcp/stdio/controllers/pi-controller.js`, originally `createPiController` factory before the Plan 3 extraction) spawns the pi RPC child with an explicit per-call env `{AIFY_BRIDGE_DISABLED:"1", AIFY_AGENT_ID:""}` so the nested `mcp/stdio/server.js` exits at startup. The fix is per-spawn, not global, so other wrapper children (claude-aify, codex-aify) keep their full aify env. If you still see this, verify the bridge log shows `AIFY_BRIDGE_DISABLED=1 exit at startup` from the omp RPC child.

## Managed pi: synthesized terminal stream vs. real PTY

**Symptom.** Operator opens the Console pane for a managed pi agent and sees a `command='aify://virtual-rpc/pi'` row with output that looks like `[pi rpc ready]`, `[turn started]`, `[tool] bash ...`, the assistant's streamed text, and `[turn ended]` rather than a real shell prompt. There is no input cursor in the traditional shell sense, but typing into the console DOES work — the operator's line is echoed back as `> ...` and the agent runs a new turn.

**Cause (not a bug).** Phase 2 swapped per-dispatch `omp --mode rpc` spawn for a persistent child per agent, and surfaces the child's `AgentSessionEvent` stream as a synthesized terminal row. The `runtime_state.virtualTerminal=true` flag on the agent marks this as a bridge-driven feed, not a PTY — there is no shell. Operator input typed in the dashboard buffers until `\r`/`\n` and dispatches a new RPC turn through the persistent child. See DECISIONS.md "Managed pi uses persistent RPC + synthesized terminal stream" and `docs/plans/pi-persistent-rpc.md`.

**What to expect.**
- One `terminal_sessions` row per agent for the lifetime of the persistent child (default idle timeout 24h via `AIFY_PI_IDLE_TIMEOUT_MS`).
- No resize semantics (the synthesized stream has no PTY dimensions).
- Stopping from the dashboard tears down the persistent RPC child + the virtual terminal row. Next dispatch respawns.
- Real PTY managed pi (the old `terminal_sessions` rows with `command='pi-aify --aify-agent ...'`) no longer exists for managed dispatches under the persistent RPC path. If you see one, it's a stale leftover from a pre-Phase-2 deployment — clear it the same way as below.

**Cleanup of legacy real-PTY rows (only relevant if upgrading from a pre-persistent-RPC build).**
```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
c.execute(\"UPDATE terminal_sessions SET status='stopped', error='superseded_by_virtual_rpc' WHERE agent_id='YOUR-AGENT-ID' AND command != 'aify://virtual-rpc/pi' AND status IN ('attached','running','starting')\")
c.execute(\"UPDATE agent_sessions SET terminal_id='', terminal_status='' WHERE agent_id='YOUR-AGENT-ID'\")
c.commit()
"
```

## `omp-aify` / `pi-aify` refuses to start: "currently driven by aify-comms"

**Symptom.** Operator runs `omp-aify --aify-agent X` (or `pi-aify ...`) and the wrapper prints:

> Agent 'X' is currently driven by aify-comms (visible in dashboard terminal). Stop it from the dashboard or use `omp-aify --standalone --aify-agent X` to launch a parallel session on a different session-id.

…and exits 1.

**Cause (not a bug).** Phase 4 watchdog. The bridge's persistent `omp --mode rpc` child currently holds this agent's session-id; an external omp on the same handle would corrupt the session file (OMP's RPC channel has no multiplexing, upstream [#436](https://github.com/can1357/oh-my-pi/issues/436)). The wrapper queries `GET /agents/{id}/pi-session-state` before exec'ing omp.

**Choices.**
- Stop the bridge session from the dashboard (Console pane → Stop). Then re-run the wrapper.
- Pass `--standalone` AND a different `--resume <other-handle>`. The bridge keeps driving its session-id; you get a parallel omp on a separate handle. They will not contend.
- If `AIFY_COMMS_URL` is missing, the curl times out, or the runtime isn't pi, the check fails open and the wrapper proceeds normally — so this only fires when the bridge actually claims ownership.

**Quick check from the host:**
```bash
curl -sS http://localhost:8800/api/v1/agents/YOUR-AGENT-ID/pi-session-state | python -m json.tool
# {"ok": true, "bridgeOwned": true|false, "virtualTerminalId": "vterm_..."}
```
