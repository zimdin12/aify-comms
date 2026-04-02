# Project — AI Dev Guide

## Team

You are part of a multi-agent team on the `dev` aify-claude channel. Read your role file before working:

| Role | File |
|------|------|
| manager | `CLAUDE.MANAGER.md` |
| coder | `CLAUDE.CODER.md` |
| tester | `CLAUDE.TESTER.md` |
| architect | `CLAUDE.ARCHITECT.md` |
| researcher | `CLAUDE.RESEARCHER.md` |

Any agent can DM any other agent directly. Use the channel for team-wide updates.

## Communication rules

- **When you finish a task and have nothing to do, call `cc_listen` to wait for messages.** Do not idle without listening.
- Use `cc_agent_info` to check if someone has seen your message before following up.
- Keep acknowledgments brief — "on it" is fine, no need for paragraphs.
- Share files, logs, and screenshots via `cc_share` when handing off work.

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
