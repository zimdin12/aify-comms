# Audit 2026-11: what was done, and where it stands

A record of the November 2026 review of the aify stack, from 2026-11-03 to 2026-11-04. The work itself is tracked
as GitHub issues labelled `audit-2026-11`, indexed in
[aify-comms#46](https://github.com/zimdin12/aify-comms/issues/46). The owner's roles and decisions are in
[ROLES.md](ROLES.md), the guidance given to the reviewers is in [OWNER-GUIDANCE.md](OWNER-GUIDANCE.md), and the decision
ledger is in [INTENT.md](INTENT.md).

## Scope

| Repo | What it is (owner-confirmed) |
|---|---|
| aify-comms | Message store-and-deliver: the owner's comms UI, history, delivery, statistics, the work loop. Home of stack-wide tracking |
| aify-env | Platform: host truth for processes, PTYs and spawns, and the agent registry and agent status served to every service |
| aify-wrapper | Platform: launchers that start any service's agents with identity and MCP config |
| aify-dashboard (private) | The second brain: project knowledge for agents, owner and agent questions and events, manual fleet controls |
| aify-project-graph | Code-structure producer. As of 2026-11-04 it is a provider for aify-dashboard only |
| agents-setup (private) | The owner's doctrine, skill selection and machine bootstrap |

A private game repo was reviewed the same way. Its issues and its launch assessment live in that repo.

## How it was done

1. **First audit (2026-11-03).** One reviewer per repo plus one for the whole stack: architecture, bugs, duplication,
   data-driven design, security, Docker and WSL2 operability, tests and docs. This filed 89 issues.
2. **Owner guidance, given during the work:**
   - understand each part's purpose before judging it
   - DRY, with copying only where it is clearly smarter
   - on demand, not time-based
   - the smallest change, with no overbuilding
   - minimal agent context
   - aify-env and aify-wrapper are a reusable platform for future services
   - no monorepo
   - removal only where a counterpart exists and is used
3. **More reviews (2026-11-03):**
   - skills and their descriptions
   - the agent tool surface
   - agents-setup as engineering
   - runtime cost, measured with the containers and daemons running
   - a paper walk-through of onboarding a third service
   - aify-dashboard judged as a second brain
4. **A decision ledger** (INTENT.md), rebuilt from all six repos' records: which recorded decisions are current,
   superseded or still migrating.
5. **A live end-to-end run** on a clean machine, using a stand-in agent runtime and no real model. It installed and
   ran the stack, and confirmed or refuted the main claims by observing behaviour.
6. **A second pass** over all first-audit issues against the roles, the ledger and the live results. Every issue was
   rewritten into eight sections: Purpose, Current behaviour, Evidence, Problem and impact, Options, Recommendation,
   Acceptance criteria, Open questions / confidence. Bad findings were closed with a reason.
7. **A final adversarial review** by fresh reviewers, asking whether each issue is correct, reasonable and worth
   doing, and whether it makes the stack harder to use. A cross-issue check covered conflicts, ordering, merges and
   cumulative usability cost.
8. **Builder review (2026-11-04).** The coding agents that built each repo commented on every issue. Each comment
   was checked against the code, applied or declined with a reason, and answered. Every open issue got a readiness
   label.

## Results

- **106 open issues across the six repos, plus 16 closed with a reason** (withdrawn, merged or moot). Readiness
  labels: `ready-to-build`, `blocked` (each names the issue it waits on) and `needs-owner`. Current counts are in the
  index.
- **130 builder comments, all answered**, across the six repos and the game repo. 114 were approved and 16
  approved in part. Where a builder was wrong the reply says so, with the code reference.
- **The index orders the work in batches**, so the plan needs about two aify-env restarts and two agent relaunches,
  not one per issue.

### What the audit found, in short

- **The design is careful and the tests are extensive, but the stack had drifted from its intended shape.**
  - The contracts between repos were in prose, with hand validators on each side, and the copies disagreed.
  - There was no CI anywhere, and several suites were red on Linux or WSL2.
  - Moves from aify-comms into aify-env and aify-wrapper were half finished, which left duplicated logic.
  - Docs and committed evidence had sprawled.
- **Runtime cost sits per agent session, not in the containers.** The containers idle at about 50–70 MB. One
  agent session added about 6 Node processes and 460 MB. A full tool load was about 82.6 KB of listings before the
  first call. Several flows waited on timers where an event already existed.
- **Fixes that were caught before they could do harm:**
  - Windows installs that would have broken.
  - Your own installer's commands that would have been deleted.
  - Launchers that would have read as stale forever.
  - Fleet tools that would have been removed from lead agents.
  - Channel runs closed against a recorded decision.
  - A key prompt on the owner's most frequent action.
  - Claude Code's root check silently overridden.
- **Agent-usage figures from a transcript scanner were withdrawn.** The scanner double-counts.

## Owner decisions taken during the audit

All of them are in [ROLES.md](ROLES.md). The main ones:

- No monorepo, and no aify-env and aify-wrapper merge. Repo boundaries keep separation of concerns enforceable for
  agents.
- aify-env owns agent status, and its core observes agent screens. aify-comms and aify-dashboard read status. One
  publisher conflict rule is stated in aify-env's contract.
- Fleet controls stay in both UIs, calling the same endpoints. aify-dashboard is the second brain.
- aify-project-graph is a dashboard-only provider, run once per host by aify-env. It has no agent tools or skills.
- Service plugins live with their services. Evidence stays in the repos, fenced out of packages, images, search and
  agent docs.
- Root installs are supported, but bypass-permissions as root is never forced. The containers subsystem is deleted.
  Codex follows the Claude Code and Hermes patterns.
- Skill sources are pinned and drift-checked, the BLEI marketplace included. ponytail is kept as upstream ships it.

## Still open

- Three game-repo issues wait on the owner lifting a performance hold.
- [aify-comms#23](https://github.com/zimdin12/aify-comms/issues/23) describes work in aify-dashboard and could be
  transferred there.
- This folder is a record of the audit. Later decisions belong in each repo's DECISIONS, not here.
