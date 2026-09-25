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
| [`ROADMAP.md`](ROADMAP.md) | What shipped and what is next. Part record, part plan: the live half is the "next" section. |

## Finished work — kept as evidence, not as instruction

These describe decisions already made and work already done. They are accurate about their own
moment and are not a description of how the system behaves now. Read one when you want to know **why**
something is the way it is, never to find out **what** it currently does.

**Moved history** — [`history/`](history/) holds the dated narrative moved out of the root documents,
so those stay current: [`history/CLAUDE-history.md`](history/CLAUDE-history.md) (from `CLAUDE.md`),
`history/DECISIONS-archive.md` (superseded `DECISIONS.md` entries) and
`history/KNOWN_ISSUES-archive.md` (resolved `KNOWN_ISSUES.md` entries).

**Release specs and plans** — `V0.2_ROADMAP`, `V0.3_SPEC`, `V0.5_SLICE1`, `V0.5_SLICE2`,
`V0.5_SLICE3`, `V0.6_PLAN`, `V054_REMAINING_FIVE_PACKET`, `PHASE8_STATUS` (spawn delegation to
aify-env; reduced to a banner, full text at tag `v0.6.22`).

**Review rounds and ledgers** — `V0_7_WEAK_POINTS`, `V0_7_UNDEPLOYED`, `V0_7_REVIEW_DOSSIER`,
`V063_ACCEPTANCE_LEDGER`, `FINDINGS_LEDGER_2026-08`, `PHASE3_DASHBOARD_LEDGER`,
`PHASE4_BUGHUNT_LEDGER`, `PHASE5_END_TO_END_STATUS`, `OVERSIZED_SCOPE_BLIND_SPOT`.

**Point-in-time traces** — `CONNECTION_TRACE` (dated in its own title), `MULTI_SERVICE_STACK_TRACE`.

**Decomposition proof packets** — each proved one file could be split safely. The splits landed; the
packets are the working, not the result. Named individually so a new one cannot hide behind a
wildcard:
`APP_JS_APIBASE_PACKET`, `APP_JS_STATE_MODULE_PACKET`, `JS_BRIDGE_AGENT_STATE_PACKET`,
`JS_CHANNEL_READ_WRITE_SPLIT_PACKET`, `JS_COMMS_REGISTER_PACKET`, `JS_DECOMPOSITION_PROOF_PACKET`,
`JS_DELIVERY_LOOP_SEAM_PACKET`, `JS_DETECTOR_TEARDOWN_PACKET`, `JS_DISPATCH_LOOP_SEAM_PACKET`,
`JS_HERMES_HOST_MINI_PACKET`, `JS_PI_SESSION_PACKET`, `JS_SERVER_JS_PROOF_PACKET`,
`JS_SERVER_REMAINDER_PACKET`, `JS_SERVER_URL_PACKET`, `JS_SPAWN_TRIGGERED_AGENT_PACKET`,
`STATUS_CACHE_COMPONENT_PACKET`.

**Dashboard programme** — `DASHBOARD_ARCHITECTURE_PLAN`, `DASHBOARD_REBUILD_PLAN`,
`DASHBOARD_OVERHAUL_ROUND2`, `DASHBOARD_PARITY_COMPLETION`, `DASHBOARD_8801_PARITY`,
`DASHBOARD_8801_UX`, `DASHBOARD_8801_BUTTON_AUDIT_2026-06-18`, `DASHBOARD_CRITIQUE_2026-08-19`,
`DASHBOARD_SPEC`, `WEB_APP_DESIGN`.

**Earliest planning** — `PLAN_REVIEW`, `PRODUCT_BRIEF`, `IMPLEMENTATION_ROADMAP`, `ARCHITECTURE_PLAN`,
`FIRST_CODING_AGENT_TASK`, `RUNTIME_DELIVERY_TARGET` (superseded by `TARGET_ARCHITECTURE.md`),
`DASHBOARD_REVIEW`.

## Unclassified — the signals disagree, so read the file

| file | why it is here |
|---|---|
| `V0.2_SPEC` · `V0.2_PLAN` | Their own headings say shipped ledger, which is history, but `KNOWN_ISSUES.md` links them as where the v0.2 backlog lives. |
| `V0.4_SPEC` | Its heading says "design, awaiting review. Nothing is implemented", while `service/ntfy.py` ships the ntfy alerts it designs. |

## Where the rest of the writing lives

`docs/superpowers/plans/` and `docs/superpowers/specs/` hold dated working documents, one per piece
of work, named by date. They are records by construction and are not indexed here.
`.claude/skills/` and `.agents/skills/` hold the agent-facing skills, which are loaded into context
rather than read on demand and are governed by a size ratchet.

## Keeping this honest

A file added to `docs/` and not listed here fails `service/tests/test_every_doc_is_in_the_index.py`.
The gate checks only that each file is named; whether it sits in the right section is a reading. If
a section here disagrees with the file it names, the file wins and this index is the thing to fix.
