# Project — AI Dev Guide

## Team

You are part of a multi-agent team on the `dev` aify-comms channel. Read your role file before working:

| Role | File |
|------|------|
| manager | `CLAUDE.MANAGER.md` |
| coder | `CLAUDE.CODER.md` |
| tester | `CLAUDE.TESTER.md` |
| architect | `CLAUDE.ARCHITECT.md` |
| researcher | `CLAUDE.RESEARCHER.md` |

Any agent can DM any other agent directly. Use the channel for team-wide updates.

## Communication rules

- **Stay registered and triggerable.** Use `comms_send(...)` or `comms_channel_send(...)` as the normal wake-up paths across the team. An `available` managed teammate auto-starts; busy steer-capable targets receive a mid-turn update and busy non-steer targets queue/merge for the next turn. Only offline/stopped/no-wake targets reject the send; inspect and recover those before retrying.
- **Use `comms_listen` only when you intentionally want a waiting loop.** Do not assume resident sessions depend on `comms_listen` to be reachable.
- Use `comms_agent_info` to check if someone has seen your message before following up.
- Send a progress acknowledgement only when it affects coordination; do not answer closed `info`, `response`, `approval`, final, or acknowledgement messages with a courtesy acknowledgement.
- Keep result messages concise too: short summary first, then deeper detail only if it helps the receiver act.
- After a bounded dispatched result, send an explicit reply to the requester or acting manager even if the run summary already contains the detail.
- If the details are large, send a short message and attach the rest with `comms_share`.
- Share files, logs, and screenshots via `comms_share` when handing off work.

## Build

```bash
# Add your build commands here
```

## Architecture — LOCKED

<!-- Add your project's architectural constraints here. Example: -->
<!-- - No X without Y -->
<!-- - Always use Z for ... -->

## Known traps

<!-- Add project-specific gotchas that agents would hit without warning -->

## Git coordination

Use feature branches for non-trivial work. **Merging to main requires tester verification.** Manager owns the push. Coordinate branch usage via the team channel.

## Commits

`feat:`, `fix:`, `perf:`, `refactor:`, `docs:`, `test:` — include task ID when closing a task.

## Task tracking

Keep task statuses current. Comment with commit hashes when done. Create tasks for bugs found.
