# aify-comms debug: Bridges, sidecars, wake modes and session ownership

The `curl` examples read the service key from `$AIFY_API_KEY`; drop the header on a service
running without `API_KEY`.

## A whole managed fleet dies at once, with no deploy

**Cause.** A second host tier started for the same environment. The newcomer **supersedes** the
incumbent aify-env, and the incumbent reaps the managed workers it hosted on the way out. Seconds of
overlap are enough, and stopping the newcomer does not bring them back. Treat it as this before
suspecting an install or deploy fault.

**Ask, never start:** `aify-env doctor`, and `aify-comms doctor` (`env-bridge` confirms a host is
actually online, not merely registered). Bringing the host tier back and re-spawning the agents is
the operator's action.

## `comms_spawn` returns 409 on a host that is up

**Symptom.** *"nothing is CLAIMING spawns on it"* while `comms_envs` shows the host online.

**Cause.** Being described and being able to claim are different facts. `status` / `lastSeen` come
from aify-env advertising the host; `/spawn` reads `metadata.bridgeLastSeen`, stamped only by a
heartbeat carrying a `bridgeId`, which is aify-env's aify-comms plugin claiming.

```bash
curl -s -H "X-API-Key: $AIFY_API_KEY" http://127.0.0.1:8800/api/v1/environments   | python -c "import json,sys;[print(r['id'],r['status'],(r.get('metadata') or {}).get('bridgeLastSeen')) for r in json.load(sys.stdin)['environments']]"
```

Older than 90s means nothing can claim there, whatever `status` says. Tell the operator: the host
needs an aify-env running its aify-comms plugin, and starting one is theirs.

## Channel-routed claude dispatches stay queued forever

**Cause.** Claude Code's MCP init race
([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462)): with many
stdio MCP servers, `aify-comms-channel` can stay `still connecting`, so the
`notifications/claude/channel` listener never registers and deliveries are dropped while the bridge
reports `delivered`.

**Fix.** Launch with `AIFY_CLAUDE_STRICT_MCP=1 claude-aify ...`: it passes `--strict-mcp-config` with
only `aify-comms` + `aify-comms-channel`. Your other MCP servers are then absent from that session.

## Bridge cannot reach `localhost:8800` on Windows

Docker Desktop's IPv6 forwarding can hang connections to `::1`, and `localhost` resolves there
first. The bridge rewrites `localhost` to `127.0.0.1` before every `fetch()`
(`coerceLoopbackToIPv4` in `aify-http.mjs`). A custom config still pointing elsewhere at
`localhost` hits it; use `http://127.0.0.1:8800`. Test: `curl --max-time 5` against both hosts.

## Stale session handle: "session not found" at delivery

**Symptom.** The runtime rejects the stored handle (`prompt.submit failed: session not found`,
codex "no rollout found") while bridges heartbeat and runs read `delivered`.

**Compare** the stored handle with the runtime's real one:

```bash
curl -s -H "X-API-Key: $AIFY_API_KEY" http://127.0.0.1:8800/api/v1/agents/YOUR-AGENT-ID | python -m json.tool | grep -E '"sessionHandle"|"runtime"'
```

- **hermes:** see hermes-session.md, "Hermes starts a FRESH session".
- **codex:** use `$CODEX_THREAD_ID` only when this exact session exported it (after
  `codex-aify --resume <id>`); the newest rollout under `~/.codex/sessions` may be unrelated.
- **claude:** `ls -t ~/.claude/projects/*/*.jsonl | head -5`; a stored handle with no matching
  `<id>.jsonl` is stale.

**Fix.** `session-handle-heartbeat.js` corrects a stored handle from the runtime within a heartbeat
tick, so re-read after a minute. To force it now, re-register from inside the live session with an
empty handle and let discovery fill it:
`comms_register(agentId="...", role="...", runtime="...", cwd="...", sessionHandle="")`.
If you know the right handle, Dashboard **Set handle** writes it directly.

## Who may claim wrapper-backed channel work

Codex's `bridge_kind='managed-wrapper-child'` or Hermes's `bridge_kind='channel-sidecar'`, plus the
current active wrapper `terminal_id`. Only the delivery owner holds the local app-server or gateway
context, so a run claimed by anything else fails or forks hidden work. If a run reads
claimed/running while the visible terminal never receives it, restart the managed session so a
fresh delivery owner registers.

## Managed claude freezes on boot at a prompt

**Symptom.** A fresh or restarted managed claude sits at a TUI prompt (resume menu, compaction
question, permissions accept) and never claims work: "up-but-deaf". `comms_console_tail` shows which.

The service answers exactly one dialog, the development-channels acknowledgment
(`service/api_core/console_prompts.py`, rule `dev-channels-accept`), matched on the rendered screen
and answered once per terminal. It refuses resume menus wholesale, because a wrong key there is
unrecoverable, and leaves compaction and permission dialogs for a person to answer. It is service
code: a change deploys by rebuilding the container.

## Resident relaunch reads `offline` and deaf

**Symptom.** A quickly relaunched resident sends fine but receives nothing, and its boot log says
`auto-register for "<agent>" was refused — another live wrapper owns this session`.

The previous bridge still looked live. The bridge retries the auto-register every 30s for a few
minutes; to bind now, run `comms_register` inside the session. After a managed→resident switch
where the sidecar stopped claiming, relaunch the resident terminal.

## Bridge log lines: `fetch failed` / `503 database is locked` / `claim timed out`

- **`fetch failed` … `recovered after N failure(s)`**: transient network or service interruption,
  aggregated and summarised on recovery. No recovery line → check the service and network path.
- **`HTTP 503 … database is locked`**: sustained write contention. Writes retry the lock before a
  503, and claim endpoints return an empty claim instead, so a 503 here means real overload.
- **`claim … timed out after 28000ms`**: claims open with a short `SQLITE_CLAIM_BUSY_TIMEOUT_MS` and
  long-polls stop at `MAX_WAIT_S` (25s, `service/longpoll.py`), below the bridge's 28s timeout. Seeing
  it means the service predates that; rebuild it.
