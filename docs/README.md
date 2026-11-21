# What is in `docs/`, and which of it is still true

Most of `docs/` is a **record of work already finished**; a smaller part is **reference you need to
do work now**. This index tells them apart. Nothing is deleted for being history: a finished-work
record is evidence, and this project argues from evidence. It just needs a label.

**How each file was placed:** its last commit date, whether an entry point (`README.md`, `CLAUDE.md`,
`docs/ARCHITECTURE.md`) links it, and what its own first heading claims to be. Where those disagree
the file is listed as UNCLASSIFIED rather than guessed at.

---

## Start here

| file | what it answers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How it is built: the tiers and what reloads each, the service layering, the message-to-work path, and the layer rules with their gates. |
| [`TARGET_ARCHITECTURE.md`](TARGET_ARCHITECTURE.md) | The shape it is heading for, as the operator specified it. Anything disagreeing with this is the thing that is wrong. |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) · [`COMMUNICATION_GUIDE.md`](COMMUNICATION_GUIDE.md) | Using the thing as an agent. |
| [`SESSION_MODEL.md`](SESSION_MODEL.md) | What a session, an agent and a handle actually are. Read before any lifecycle work. |
| [`OPERATING_MODES.md`](OPERATING_MODES.md) | Managed and resident agents, delivery per runtime, compaction and runtime settings, as the operator meets them. |

## Live reference — current design, read before changing that area

| file | area |
|---|---|
| [`ENVIRONMENT_ADVERTISEMENT.md`](ENVIRONMENT_ADVERTISEMENT.md) | Who tells the service what a host can do, and why exactly one tier may. |
| [`SERVICE_ADAPTER_CONTRACT.md`](SERVICE_ADAPTER_CONTRACT.md) | How aify-env supervises a service. |
| [`HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md`](HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md) | Which repo owns harness-driver semantics, and why. |
| [`AIFY_ENV_BOUNDARY.md`](AIFY_ENV_BOUNDARY.md) | What moved to aify-env, what stayed, which doctor owns which check. |
| [`BRIDGE_SETUP.md`](BRIDGE_SETUP.md) | The host tier (aify-env) and workspace roots. |
| [`TRANSPORT_PLAN.md`](TRANSPORT_PLAN.md) | What carries keystrokes and console output, measured per transport, and the rule that nothing runs for an agent nobody is watching. Read before adding a socket or a poll. |
| [`HTTPS.md`](HTTPS.md) | Current HTTPS routing, isolated validation, certificate trust requirements and unverified deployment paths. |
| [`INSTALL_ONBOARDING.md`](INSTALL_ONBOARDING.md) | Agent-led installation and updates across the owning repositories, optional components and approval-bound verification. |
| [`HERMES_INTEGRATION.md`](HERMES_INTEGRATION.md) · [`HERMES_AIFY_PLUGIN.md`](HERMES_AIFY_PLUGIN.md) | The hermes runtime and its plugin. |
| [`SKILLS.md`](SKILLS.md) | What the skill trees are and where they install to. |
| [`TEAMWORK_STRATEGY.md`](TEAMWORK_STRATEGY.md) | How a multi-agent team is meant to work here. |
| [`UNINSTALL.md`](UNINSTALL.md) | Removing it cleanly. |
| [`CODEX_CATCHUP.md`](CODEX_CATCHUP.md) | What Codex needs to drop its local comms process, deferred from 0.9.11 until Claude and hermes are proven on it. |
| [`ROADMAP.md`](ROADMAP.md) | The stack roadmap across all six repositories: goals, groups and the gates between them. [`roadmap/`](roadmap/) holds its issue map, the owners' approvals, issues opened since, and the checklist for [working an issue](roadmap/WORKING-AN-ISSUE.md). Earlier roadmaps are in the finished-work archive. |

## Finished work

[Finished work](archive/README.md) is evidence, not current instruction. Default dashboard file search leaves that directory out.

## Unclassified — the signals disagree, so read the file

| file | why it is here |
|---|---|
| `V0.4_SPEC` | Its heading says "design, awaiting review. Nothing is implemented", while `service/ntfy.py` ships the ntfy alerts it designs. |

## Where the rest of the writing lives

`docs/superpowers/plans/` and `docs/superpowers/specs/` hold dated working documents, one per piece
of work, named by date. They are records by construction and are not indexed here.
`docs/audit-2026-11/` holds the November 2026 audit's record: the owner-confirmed roles and decisions,
the guidance the owner gave, and a dated decision ledger. The `audit-2026-11` issues cite it
(index: issue #46).
`.claude/skills/` and `.agents/skills/` hold the agent-facing skills, which are loaded into context
rather than read on demand and are governed by a size ratchet.

## Keeping this honest

A Markdown file added directly to `docs/` or `docs/archive/` must be named by that folder's index. See `service/tests/test_every_doc_is_in_the_index.py`.
The gate checks only that each file is named; whether it sits in the right section is a reading. If
a section here disagrees with the file it names, the file wins and this index is the thing to fix.
