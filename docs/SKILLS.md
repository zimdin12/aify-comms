# Skills

The repo currently ships two skills, duplicated for Codex and Claude Code install targets:

- `.agents/skills/aify-comms/SKILL.md` and `.claude/skills/aify-comms/SKILL.md`
- `.agents/skills/aify-comms-debug/SKILL.md` and `.claude/skills/aify-comms-debug/SKILL.md`

## Relevance

Both are still relevant.

`aify-comms` is the normal operating guide for agents using the bridge: live registration, direct messages, channels, shared artifacts, environment-backed spawn, dashboard use, and wrapper expectations.

Agents create persistent comms-visible teammates through `comms_envs(...)` and `comms_spawn(...)`, the same environment-backed path used by dashboard **Environments -> Spawn Agent**. Agents can also use `comms_compact(...)` to create a fresh managed successor from an existing managed agent when a phase changes or context gets noisy; the original agent is left intact until a manager stops it. Private subagents should report back to their parent unless the user explicitly wants a new persistent teammate.

`aify-comms-debug` is the troubleshooting guide for stale bridges, failed dispatch, wrong wake mode, Codex path/session problems, and Claude channel issues. It should stay separate so routine agents do not need to load the longer failure catalog unless something breaks.

Current dashboard behavior reflected by the skills:

- normal teamwork uses `comms_send`; `comms_dispatch` is a lower-level debug/run-control tool
- normal sends are live-delivery gated for offline/stale/no-wake targets; busy steer-capable targets receive normal sends as current-run steer, and `queueIfBusy=true` is explicit next-turn delivery
- dashboard-origin direct messages are human/operator chat; managed agents answer those in final plain text and the bridge records it, while agent-to-agent direct requests should use `comms_send(type="response", inReplyTo=...)`
- if a later teammate reply completes a promise to report back to the human, managers may send `comms_send(to="dashboard", type="info" or "response", ...)`; `dashboard` is a store-only human recipient, not a runtime to wake
- channel/group messages should prompt bounded discussion: reply when named, responsible, asked a question, or holding useful evidence; avoid broad automatic acknowledgement loops
- persistent teammates are created through dashboard Environment spawn, `comms_spawn`, or `comms_compact`, not ordinary one-off subagents
- existing resident/manual identities can be adopted from the dashboard Team page by opening **Edit** and assigning an online environment/runtime/workspace; agents should still close or stop the old CLI session for that same ID after adoption
- pending handoffs can be repaired by the dashboard; reviewed historical failures can be dismissed from Home without deleting audit history
- successful spawn requests may still have status `running` in old/current data; the dashboard labels them as session-started history and hides them from the normal spawn queue
- ended/completed/cancelled session rows are debug history and are hidden by default in Sessions

## Did This Product Pass Add A New Skill?

No new skill name was added in this pass. The existing `aify-comms` and `aify-comms-debug` skills were updated as part of the product work and can be reinstalled into Codex with:

```bash
bash install.sh --client codex http://localhost:8800 --with-hook
```

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

The skills use the `aify-comms` name because that is the product, MCP/server identity, installed tool namespace, and dashboard/control-plane name. Do not add a separate dashboard skill until the dashboard workflow has stabilized. For now, the main skill plus the debug skill are enough.
