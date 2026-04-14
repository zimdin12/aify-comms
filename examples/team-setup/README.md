# Multi-Agent Team Setup Example

Example CLAUDE.md files for a 5-agent development team using aify-claude for coordination.

## Agents

| Role | File | Responsibility |
|------|------|---------------|
| Manager | `CLAUDE.MANAGER.md` | Task assignment, progress tracking, push approval |
| Coder | `CLAUDE.CODER.md` | Implementation, commits (no push until verified) |
| Tester | `CLAUDE.TESTER.md` | Verification, testing, bug reporting |
| Reviewer | `CLAUDE.REVIEWER.md` | Code review, regression spotting, test-gap detection |
| Architect | `CLAUDE.ARCHITECT.md` | System design, architecture enforcement |
| Researcher | `CLAUDE.RESEARCHER.md` | Research, state-of-the-art analysis |

## How to use

1. Copy the role files to your project root
2. Edit each file to include your project-specific details (build commands, architecture rules, task tracker IDs)
3. Copy `TEAM_START.md` to your project — agents use it to register and orient themselves
4. Start each agent with: `Read TEAM_START.md — you are the <role>.`

## Key patterns

- **Push workflow**: Coder commits locally → Tester verifies → Manager tells coder to push
- **Communication**: `game-dev` channel for team updates, DMs for direct collaboration
- **Status awareness**: Agents use `cc_agent_info` to check before messaging
- **File sharing**: `cc_share` for handoffs (logs, screenshots, test results)
- **Active starts**: use `cc_spawn_agent` to create triggerable workers, then use `cc_dispatch` when another worker should begin now instead of waiting on inbox polling alone
- **Run correction**: use `cc_run_steer` or `cc_run_interrupt` when active work needs intervention
- **Brief acks**: "on it" instead of paragraphs — reduce noise

## Customization

These files are from a game development project. Adapt them to your domain:
- Replace architecture rules with your project's constraints
- Replace build commands with your toolchain
- Replace task tracker references with your system (Jira, Linear, GitHub Issues, etc.)
- Add or remove roles as needed
