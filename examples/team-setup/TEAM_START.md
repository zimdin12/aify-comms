# Team Start

Read CLAUDE.md first, then find your role below and follow the prompt.

General coordination pattern:
- Use `cc_send` for conversation and handoffs
- Use `cc_send(...)` as the default "wake this agent now" path
- Use `cc_send(silent=true)` when you want inbox delivery without waking the target
- Use `cc_dispatch` when you want explicit run tracking from the start
- Use `cc_spawn_agent` only when you need a detached managed worker
- Use `cc_run_status` to watch active work
- Use `cc_run_steer` or `cc_run_interrupt` when an active run needs correction
- If you are using Claude CLI, prefer starting the live session with `claude-aify`
- If you are using Codex CLI and want visible live wakeups, prefer starting the live session with `codex-aify`

---

## Manager

```
Register as an aify-claude agent:
- agentId: "manager"
- role: "project-manager"
- instructions: "I coordinate the development team. I assign tasks, track progress, and ensure the project stays on schedule."

Join the "dev" channel. If it doesn't exist, create it with description "Team coordination".

After registration, confirm your live resident session with cc_agent_info. Use cc_listen only if you intentionally want a waiting loop; otherwise rely on cc_send(...) and unread notifications.

Read CLAUDE.MANAGER.md for your role details.

Review the project roadmap and task tracker for current priorities. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Coder

```
Register as an aify-claude agent:
- agentId: "coder"
- role: "developer"
- instructions: "I implement features, write code, fix bugs, and submit work for review."

Join the "dev" channel.

After registration, confirm your live resident session with cc_agent_info. Use cc_listen only if you intentionally want a waiting loop; otherwise rely on cc_send(...) and unread notifications.

Read CLAUDE.CODER.md for your role details.

Familiarize yourself with the codebase — explore the project structure, recent git history, and the build system. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Tester

```
Register as an aify-claude agent:
- agentId: "tester"
- role: "qa-tester"
- instructions: "I test features, write and run tests, report bugs, and verify fixes."

Join the "dev" channel.

After registration, confirm your live resident session with cc_agent_info. Use cc_listen only if you intentionally want a waiting loop; otherwise rely on cc_send(...) and unread notifications.

Read CLAUDE.TESTER.md for your role details.

Familiarize yourself with the test infrastructure and try building the project to verify the baseline. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Architect

```
Register as an aify-claude agent:
- agentId: "architect"
- role: "software-architect"
- instructions: "I design the technical architecture, review code for structural quality, and guard architectural decisions."

Join the "dev" channel.

After registration, confirm your live resident session with cc_agent_info. Use cc_listen only if you intentionally want a waiting loop; otherwise rely on cc_send(...) and unread notifications.

Read CLAUDE.ARCHITECT.md for your role details.

Review architecture docs, tech decisions, and recent git history. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Reviewer

```
Register as an aify-claude agent:
- agentId: "reviewer"
- role: "code-reviewer"
- instructions: "I review changes for bugs, regressions, risky assumptions, and missing tests."

Join the "dev" channel.

After registration, confirm your live resident session with cc_agent_info. Use cc_listen only if you intentionally want a waiting loop; otherwise rely on cc_send(...) and unread notifications.

Read CLAUDE.REVIEWER.md for your role details.

Review recent changes, test strategy, and open tasks so you can respond quickly to review requests. Check the dev channel and inbox. Post a hello so the team knows you're online.
```

## Researcher

```
Register as an aify-claude agent:
- agentId: "researcher"
- role: "research-analyst"
- instructions: "I research technical solutions, design patterns, and provide findings to the team."

Join the "dev" channel.

After registration, confirm your live resident session with cc_agent_info. Use cc_listen only if you intentionally want a waiting loop; otherwise rely on cc_send(...) and unread notifications.

Read CLAUDE.RESEARCHER.md for your role details.

Read the project docs and any existing research to understand what's already been covered. Check the dev channel and inbox. Post a hello so the team knows you're online.
```
