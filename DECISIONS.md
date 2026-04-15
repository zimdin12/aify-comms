# aify-comms: Design Decisions & Current Limits

Short rationale log for non-obvious choices, plus the current runtime limits. If you're wondering *why* the service behaves a certain way, this file beats guessing from the code.

## Runtime limits

| Capability | Claude Code | Codex | OpenCode |
|------------|-------------|-------|----------|
| Managed workers | yes | yes | yes |
| Resident visible-wake | `claude-live` (via `claude-aify`) | `codex-live` (via `codex-aify`) | not yet |
| Resident background resume | — | `codex-thread-resume` | `opencode-session-resume` |
| Interrupt | yes | yes | yes |
| In-flight steering | no | yes | no |
| Active dispatch default timeout | 2 h | 2 h | 2 h |

**One active dispatched run per agent.** Later dispatches from the same sender merge into a buffered pending run (see below).

**SSE clients** can message, inspect runs, and request dispatch — but they cannot host triggerable sessions or be local launchers.

## Wake modes

Every agent registration resolves to one of these wake modes. `comms_agent_info` reports the current one:

| Wake mode | Meaning |
|-----------|---------|
| `claude-live` | Resident Claude session started via `claude-aify`; woken through the local aify channel bridge. |
| `codex-live` | Resident Codex session started via `codex-aify`; woken through the shared local WebSocket app-server that the visible TUI uses. |
| `codex-thread-resume` | Resident Codex session started with plain `codex`; woken by resuming the bound `thread.id` in a separate background app-server. |
| `opencode-session-resume` | Resident OpenCode session with a bound `sessionHandle`; resumed in a background worker. |
| `managed-worker` | Detached managed worker created by `comms_spawn_agent`. Not visible to a live user. |
| `message-only` | No live wake path available. Messages still land in the inbox; dispatch cannot execute. |
| `claude-needs-channel` | Claude agent is registered but no alive `claude-aify` wrapper exists on this machine. Fix: launch one. |

## Dispatch buffering (cap 10)

**Decision.** When an agent is already running a dispatch and the same sender tries to queue another, new dispatches are merged into one pending buffered run instead of stacking. The buffer caps at 10 items; past that, new dispatches are rejected with `reason: "buffer_full"` in `notStarted`.

**Why.** Without it, a sender that panic-retries (or a channel that fans out aggressively) can pile up 50+ queued runs on a stuck agent. Those runs all claim to be "queued" but there is nothing the operator can do except cancel them one by one. Merging collapses panic-retries into a single growing envelope with per-item timestamps; the cap prevents unbounded body growth.

**Why per-sender.** Different senders are different conversations; merging across senders would lose the thread. The cap is per (sender, recipient) pair.

**Why 10.** Picked to be high enough that normal bursty workflows never hit it, low enough that a buggy sender can't grow a single run body past ~100 KB.

## Re-register is a full state refresh (except description)

**Decision.** `comms_register` on an existing agent overwrites `sessionHandle`, `runtime_state`, `cwd`, `role`, `runtime`, `machineId`, `runtimeConfig`, and capabilities with whatever the new request contains. The only exception is `description`: omitting it preserves the existing value; passing `""` clears it.

**Why not preserve everything.** Earlier versions preserved `sessionHandle` and `runtime_state` across re-register. That let stale Codex thread IDs survive a fresh `codex-aify` start and broke `thread/resume` with `AbsolutePathBuf` or `no rollout found`. Making re-register authoritative is simpler and matches the user's mental model: "I just re-registered, the record should reflect *this* session".

**Why keep description.** Description is human-facing team context ("I work on the NRD ingest pipeline"). It changes on a slow cadence and should survive the common "kill + restart + re-register" loop. The explicit `""` clear is there for when you genuinely want to reset it.

## Codex requires exact wrapper binding; Claude falls back to any alive wrapper

**Decision.** When resolving the runtime marker for an agent's cwd:
- **Claude Code** falls back to *any* alive `claude-aify` wrapper on the machine if there is no per-cwd marker. Registration succeeds with `claude-live` as long as at least one wrapper is running.
- **Codex** requires an exact match. If there are multiple live markers for different cwds, the bridge refuses to pick one and falls back to `message-only` unless the caller passes explicit `sessionHandle` + `appServerUrl`.

**Why the asymmetry.** Claude's resident-wake path only needs the channel bridge to be loaded into *any* Claude session — it's a process-level wake, not a per-thread one. Codex's resident-wake path binds to a specific `codex app-server` WebSocket URL owned by a specific `codex-aify` wrapper; picking the wrong wrapper means the wake goes to a different Codex session than the one the user registered.

**Practical consequence.** In multi-tab Claude setups on the same machine, everything Just Works. In multi-tab Codex setups, you need to register each tab with explicit `sessionHandle="$CODEX_THREAD_ID"` and `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` from inside that tab.

## Superseded bridges are blocked at claim time

**Decision.** When an agent re-registers, the server marks the old bridge instance as `superseded_by: <new bridge id>`. The `/dispatch/claim` endpoint rejects claims from any superseded bridge with `blockedBy: {reason: "bridge_superseded"}`.

**Why.** Without this, an old `codex-aify` process that didn't exit cleanly would keep polling, keep claiming fresh runs, and keep failing them with its stale in-memory state — even though the code on disk had been updated and a new bridge was ready to handle the same work. Blocking old bridges at claim time makes re-register a definitive handoff.

The old bridge stays alive and keeps polling (that's fine — polling is cheap) but can no longer steal work.

## Notifications fire on `PostToolUse` for `Bash`

**Decision.** The unread-notification hook is installed on Claude and Codex with the `PostToolUse` hook, matcher `Bash`. It's not installed on OpenCode at all.

**Why `Bash` specifically.** Codex's current hooks runtime only fires `PostToolUse` for `Bash`, not for every tool. Using the same matcher on Claude keeps the two runtimes consistent so team-wide guidance applies to both.

**Why not OpenCode.** OpenCode doesn't expose a hook path the notification script can bind to yet.

**Consequence.** If an agent never runs a Bash tool call, it never checks for unread messages from the hook path. Agents should call `comms_inbox` explicitly at natural check-in points (start of a task, between major steps).

## Dispatched runs do not auto-reply

**Decision.** When a dispatched run completes, the server records `status` and `summary` on the run — it does not send a message back to the requester. If the requester wants a reply, the target has to explicitly call `comms_send(...)`.

**Why.** Auto-reply on completion sounds convenient but creates two problems: (1) the "summary" is often just a short status line that adds noise to the requester's inbox, and (2) it hides the choice of what to report. Forcing the target to call `comms_send` explicitly means the target decides what's worth reporting and the requester's inbox only carries intentional replies.

## Channel messages land in inbox

**Decision.** `comms_channel_send` delivers the message to every member's inbox. There is no separate "channel view" the agent has to poll.

**Why.** Coding agents don't keep long-lived UI windows open on channels. If channel messages lived only in channel history, agents would miss them unless they remembered to poll. Delivering to the inbox means the normal unread-notification flow covers channel traffic automatically.

## Identifier name constraints

**Decision.** Agent IDs, channel names, and shared-artifact names are `[A-Za-z0-9._-]{1,128}`.

**Why.** These end up in URLs (`/agents/{id}/...`), filesystem paths (shared artifacts), and shell arguments. The strict regex prevents path traversal, URL escaping issues, and shell injection without having to sanitize at every call site.

## Container name, repo name

The repo is `zimdin12/aify-comms` and the Docker container is `aify-comms-service`. Earlier versions used `aify-claude`; the rename is cosmetic and GitHub auto-redirects old URLs. If you see `aify-claude` in a log or filesystem path on an older install, it's the same project.
