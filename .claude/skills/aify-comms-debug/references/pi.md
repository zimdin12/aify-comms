# aify-comms troubleshooting: Oh My Pi / OMP

Pi is **deprecated**: `install.sh --client pi` is disabled and its tests are off by default
(`AIFY_TEST_DEPRECATED=pi` turns them on). The code still ships, so these entries stay until it is
removed. `pi-aify` and `omp-aify` are aliases.

## Managed Pi run hangs or fails on auth

Pi RPC fails fast on an auth or provider failure (`Pi authentication failed fast: ...`) and on
startup silence. Run `omp` by hand in that environment to log in again. A dead saved Pi session
heals to a fresh one and clears the stored handle; a resident Pi fails with a clear message instead,
by design.

## Managed Pi fails: `Session ... is in another project`

The saved handle belongs to another project directory. Managed Pi clears it and retries once with a
fresh session; resident Pi fails loudly, because swapping a visible CLI session would hide the
memory change. If it still fails, use Dashboard **Sessions -> Actions -> Reset (fresh context)**.

## Managed Pi fails with `No API key found for cursor` when the model is `default`

`default`, `unknown`, `auto` and a blank model are treated as "no override", so the bridge launches
`omp --mode rpc` and OMP reads `~/.omp/agent/config.yml`. Seeing this means the running bridge
predates that: check `aify-comms doctor` `bridge-installed`, then restart the agent from the
dashboard.

## Reply is `(no output)`

The adapter takes the final text from `message_end`, `turn_end` or `agent_end` as well as the
streamed deltas. An empty reply on current code means OMP produced no text.

## The Console shows `[pi rpc ready]` / `[turn started]` instead of a shell

Expected. Managed Pi keeps one persistent `omp --mode rpc` child per agent (`PiController`,
`controllers/pi-controller.js`) and renders its events as a virtual terminal
(`command='aify://virtual-rpc/pi'`, `runtime_state.virtualTerminal=true`). Typing a line and Enter
starts a new turn; there is no resize. Stop tears the child down and the next send respawns it. The
child is started with `AIFY_BRIDGE_DISABLED=1` so a nested MCP bridge exits instead of superseding
its parent.

## `omp-aify` refuses to start: "currently driven by aify-comms"

The bridge's persistent RPC child holds that session, and OMP's RPC channel cannot share it. Stop the
bridge session from the dashboard Console first, or run `omp-aify --standalone --aify-agent X` with a
different `--resume <other-handle>`. The wrapper asks the service first:

```bash
curl -sS -H "X-API-Key: $AIFY_API_KEY" http://127.0.0.1:8800/api/v1/agents/YOUR-AGENT-ID/pi-session-state | python -m json.tool
# {"ok": true, "bridgeOwned": true|false, "virtualTerminalId": "vterm_..."}
```
