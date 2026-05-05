# Skills

The repo currently ships two skill families, duplicated for Codex and Claude Code install targets:

- `.agents/skills/aify-comms/SKILL.md` and `.claude/skills/aify-comms/SKILL.md`
- `.agents/skills/aify-comms/references/*.md` and `.claude/skills/aify-comms/references/*.md`
- `.agents/skills/aify-comms-debug/SKILL.md` and `.claude/skills/aify-comms-debug/SKILL.md`

## Relevance

Both are still relevant.

`aify-comms` is the normal operating guide for agents using the bridge: live registration, direct messages, channels, shared artifacts, environment-backed spawn, dashboard use, and wrapper expectations.

The main `aify-comms/SKILL.md` is intentionally small. Routine chat and managed turns should load only the core protocol. Slower details live in reference files:

- `references/operations.md` — install/update, runtime policy, bridge/session ownership, dashboard operations, status meanings, and repair hints.
- `references/teamwork.md` — manager/tech-lead/coder workflow, work-contract shape, labels, review discipline, and compact/rebrief patterns.

This keeps normal agent turns cheaper while preserving the operational detail when an agent actually needs it.

Agents create persistent comms-visible identities through `comms_envs(...)` and `comms_spawn(...)`, the same environment-backed path used by dashboard **Environments -> Spawn Agent**. Agents can also use `comms_compact(mode="handoff", ...)` to create a fresh managed backing from an existing managed identity when a phase changes or context gets noisy; the default handoff identity is the same agent ID. Private subagents should report back to their parent unless the user explicitly wants a new persistent agent identity.

`aify-comms-debug` is the troubleshooting guide for stale bridges, failed dispatch, wrong wake mode, Codex path/session problems, and Claude channel issues. It should stay separate so routine agents do not need to load the longer failure catalog unless something breaks.

Current dashboard behavior reflected by the skills:

- managed mode is the default persistent-agent path: run the `aify-comms` environment bridge, spawn agents from the dashboard or `comms_spawn(...)`, and let the dashboard own lifecycle/restart/compact
- resident mode is a deliberate visible-terminal path: start `claude-aify --aify-agent <id>` or `codex-aify --aify-agent <id>` when the CLI should temporarily own a live session; if launched without an ID, register manually from that same session
- normal teamwork uses `comms_send`; `comms_dispatch` is a lower-level debug/run-control tool
- every message is treated as a small contract: owner, expected action or answer, evidence/result needed, and whether a reply or follow-up wake is owed
- agents and managers can use `comms_contracts(...)` to inspect computed Work Loop contracts before claiming an agent is silent, overdue, answered, or blocked by stale bookkeeping. It defaults to open direct contracts; channel, self-wake, missing-reply, failed, and answered audits are explicit filters.
- managed turns should not end silently: stdout/logs/tool output/run summaries are telemetry, not the team-visible answer. The turn should close with a final reply to the triggering sender, explicit `comms_send` updates for other owners/dashboard, or a self-scheduled wake when later self-work is required
- normal sends are live-delivery gated for offline/stale/stopped/no-wake targets; busy steer-capable targets receive normal sends as current-run steer, busy non-steer targets queue/merge as next-turn work, and `queueIfBusy=true` forces explicit next-turn delivery
- environment bridge roots are safety boundaries, not per-agent workspace defaults; current launchers/service builds reject or ignore flag-like roots such as `--help`
- managed sessions have real turn boundaries, but teamwork is not lockstep: agents may exchange messages mid-turn, run independent lanes in parallel, and continue bounded work inside a turn. Final text replies do not schedule later work. For autonomous project work, agents must send the next wake before finishing when more work should happen after the current turn. If they own the next bounded chunk, they self-schedule with `comms_send(to="<own-agent-id>", type="request", queueIfBusy=true, ...)`; if another teammate owns it, they message that teammate. Vague "need confirmation" is not a stop condition when docs/team roles can answer it.
- browser CLI is a planned dashboard ownership mode, not current behavior. Skills should not imply it works until the environment bridge advertises terminal/PTY attach. Current direct CLI access uses wrapper auto-registration (`claude-aify --aify-agent <id>` / `codex-aify --aify-agent <id>`) or manual `comms_register(...)`. Ownership changes only between turns: active managed work defers resident takeover, and stale resident leases can return to managed backing automatically.
- delivered dashboard-managed runs should answer the current message in final plain text; the bridge captures and stores/threads that answer into chat
- delivered dashboard-managed runs must not call `comms_register`; managed identities are already registered by the environment bridge, and current builds reject that call to prevent accidental resident/manual downgrades
- if a later teammate reply completes a promise to report back to the human outside the current delivered run, managers/operators should send `comms_send(to="dashboard", type="info" or "response", ...)`; dashboard is a store-only human recipient, and backend summary mirroring is only a safety net
- channel/group messages should prompt bounded discussion: reply when named, responsible, asked a question, or holding useful evidence; avoid broad automatic acknowledgement loops
- persistent agents are created through dashboard Environment spawn, `comms_spawn`, or `comms_compact(mode="handoff", ...)`, not ordinary one-off subagents
- existing resident/manual identities can be adopted from Dashboard **Sessions -> Identity Directory** by opening **Edit** and assigning an online environment/runtime/workspace; agents should still close or stop the old CLI session for that same ID after adoption
- pending handoffs can be repaired by the dashboard; reviewed historical failures can be dismissed from Home without deleting audit history
- Work Loop reminders should close the original contract. An agent should not merely acknowledge an automated reminder unless the reminder itself is the task.
- successful spawn requests may still have status `running` in old/current data; the dashboard labels them as started history and hides them from the normal spawn-request list
- ended/completed/cancelled session rows are debug history and are hidden by default in Sessions

## Install Or Update

No separate dashboard skill exists. The existing `aify-comms` and `aify-comms-debug` skills are the current agent-facing guidance and can be reinstalled into Codex with:

```bash
bash install.sh --client codex http://localhost:8800 --with-hook
```

For Claude Code installs, use:

```bash
bash install.sh --client claude http://localhost:8800 --with-hook
```

After either install/update, restart the relevant CLI wrapper/client and any long-running `aify-comms` environment bridge so both resident sessions and managed environment spawns load the updated skills and bridge code.

Dashboard operators can tune appearance and operations separately. Dashboard **Settings -> Appearance** controls the browser title/brand plus primary, secondary, and tertiary palette colors; preset tiles only load those picker values until saved. **Settings -> Runtime** controls global managed Claude/Codex model and effort policy. Home issue muting is dashboard-local: muted live/session/handoff notices stay visible as yellow context but stop counting as red active issues.

The installer copies the Codex skills from:

```text
.agents/skills/aify-comms
.agents/skills/aify-comms-debug
```

to:

```text
${CODEX_HOME:-~/.codex}/skills/aify-comms
${CODEX_HOME:-~/.codex}/skills/aify-comms-debug
```

Dashboard-managed Codex sessions use a separate managed Codex home:

```text
~/.local/state/aify-comms/managed-codex-home
```

The WSL/Linux bridge now copies the bundled Codex skills into that managed home whenever it prepares a managed Codex run. If a managed Codex agent says `aify-comms` or `aify-comms-debug` is not exposed as a skill, update/restart the relevant `aify-comms` bridge so it regenerates the managed Codex home, or copy `.agents/skills/aify-comms*` into the managed home's `skills/` directory as a temporary live repair.

Managed runtime defaults are documented in the main skill: Claude Code and Codex model fields are blank by default, which means runtime default/latest; both default to `high` effort/reasoning effort. Dashboard **Settings -> Runtime** sets global operator defaults; normal dashboard spawn/agent edit flows do not tune model/effort per agent.

Current bridge builds terminate the whole managed runtime process tree on timeout, interrupt, stop, and bridge shutdown. This is important for Codex/OpenCode app-server children and Windows Claude wrapper children; stale child processes should not keep agents falsely alive after the owning environment bridge is gone.

Claude installs from:

```text
.claude/skills/aify-comms
.claude/skills/aify-comms-debug
```

to:

```text
~/.claude/skills/aify-comms
~/.claude/skills/aify-comms-debug
```

## Naming

The skills use the `aify-comms` name because that is the product, MCP/server identity, installed tool namespace, and dashboard/control-plane name. Do not add separate always-on role/dashboard skills unless the trigger is clearly distinct; extra skills increase metadata and can over-trigger. Prefer references under `aify-comms` for role guides and operator details until a workflow proves it needs its own skill.
