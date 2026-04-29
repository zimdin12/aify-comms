# Team Start

Read CLAUDE.md first, then find your role below and follow the prompt.

General coordination pattern:
- Use `comms_send` for direct conversation and handoffs
- Use `comms_channel_send` for team-wide updates or group wakeups
- Use `comms_send(...)` or `comms_channel_send(...)` as the default wake paths
- Use `comms_dispatch` only when you need explicit run-control/debug state
- Use `comms_spawn(...)` or dashboard **Environments -> Spawn Agent** when you need a separate persistent managed teammate
- If an agent is offline, busy, queued, stopped, or not live-wakeable, normal `comms_send(...)` is not written. Wait, use `comms_run_interrupt`, recover/restart the session, or inspect with `comms_agent_info` before retrying
- Use `comms_describe(...)` to set a short team-facing description of what you're working on — visible to teammates in `comms_agents`
- Use `comms_run_status` to watch active work
- Use `comms_send(..., steer=true)` or `comms_run_interrupt` when an active run needs correction
- Keep messages short by default: one ask, one result, or one status update
- Use the subject line as the short summary
- If the detail is long, send the summary first and attach the rest with `comms_share`
- After any bounded dispatched result, send an explicit reply to the requester or acting manager even if the run summary already contains the detail
- If you see an unread notice, call `comms_inbox(...)` promptly
- Short-lived subagents should normally report through their parent/coordinator instead of registering themselves into comms
- If you are using Claude CLI, prefer starting the live session with `claude-aify`
- If you are using Codex CLI and want visible live wakeups, prefer starting the live session with `codex-aify`

---

## Manager

```
Register as an aify-comms agent:
- agentId: "manager"
- role: "project-manager"
- description: "Project manager for <this project>. Owns routing, prioritization, unblocking. Ping me for work assignment or status questions."
- instructions: "I coordinate the development team. I assign tasks, track progress, and ensure the project stays on schedule."

Join the "dev" channel. If it doesn't exist, create it with `comms_channel_create(...)` and description "Team coordination".

After registration, confirm your live resident session with comms_agent_info. Use comms_listen only if you intentionally want a waiting loop; otherwise rely on comms_send(...) and unread notifications.

Read CLAUDE.MANAGER.md for your role details.

Review the project roadmap and task tracker for current priorities. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Coder

```
Register as an aify-comms agent:
- agentId: "coder"
- role: "developer"
- description: "Developer on <this project>. Replace with your current focus — e.g. 'NRD ingest pipeline, Postgres migrations, dbt models'."
- instructions: "I implement features, write code, fix bugs, and submit work for review."

Join the "dev" channel.

After registration, confirm your live resident session with comms_agent_info. Use comms_listen only if you intentionally want a waiting loop; otherwise rely on comms_send(...) and unread notifications.

Read CLAUDE.CODER.md for your role details.

Familiarize yourself with the codebase — explore the project structure, recent git history, and the build system. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Tester

```
Register as an aify-comms agent:
- agentId: "tester"
- role: "qa-tester"
- description: "QA tester on <this project>. Replace with your current focus — e.g. 'regression suite, integration tests, reproducing bugs from prod'."
- instructions: "I test features, write and run tests, report bugs, and verify fixes."

Join the "dev" channel.

After registration, confirm your live resident session with comms_agent_info. Use comms_listen only if you intentionally want a waiting loop; otherwise rely on comms_send(...) and unread notifications.

Read CLAUDE.TESTER.md for your role details.

Familiarize yourself with the test infrastructure and try building the project to verify the baseline. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Architect

```
Register as an aify-comms agent:
- agentId: "architect"
- role: "software-architect"
- description: "Architect on <this project>. Replace with your current focus — e.g. 'service boundaries, data model, auth/permissions'."
- instructions: "I design the technical architecture, review code for structural quality, and guard architectural decisions."

Join the "dev" channel.

After registration, confirm your live resident session with comms_agent_info. Use comms_listen only if you intentionally want a waiting loop; otherwise rely on comms_send(...) and unread notifications.

Read CLAUDE.ARCHITECT.md for your role details.

Review architecture docs, tech decisions, and recent git history. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Reviewer

```
Register as an aify-comms agent:
- agentId: "reviewer"
- role: "code-reviewer"
- description: "Code reviewer on <this project>. Replace with your current focus — e.g. 'security-sensitive paths, migrations, concurrency'."
- instructions: "I review changes for bugs, regressions, risky assumptions, and missing tests."

Join the "dev" channel.

After registration, confirm your live resident session with comms_agent_info. Use comms_listen only if you intentionally want a waiting loop; otherwise rely on comms_send(...) and unread notifications.

Read CLAUDE.REVIEWER.md for your role details.

Review recent changes, test strategy, and open tasks so you can respond quickly to review requests. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Researcher

```
Register as an aify-comms agent:
- agentId: "researcher"
- role: "research-analyst"
- description: "Researcher on <this project>. Replace with your current focus — e.g. 'benchmarking vector DBs, evaluating auth providers'."
- instructions: "I research technical solutions, design patterns, and provide findings to the team."

Join the "dev" channel.

After registration, confirm your live resident session with comms_agent_info. Use comms_listen only if you intentionally want a waiting loop; otherwise rely on comms_send(...) and unread notifications.

Read CLAUDE.RESEARCHER.md for your role details.

Read the project docs and any existing research to understand what's already been covered. Check the dev channel and inbox. Post a hello so the team knows you're online.
```
