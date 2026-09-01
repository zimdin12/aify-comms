# What is in `docs/`, and which of it is still true

70 files, ~17,000 lines. Most of it is a **record of work already finished** and a few of it is
**reference you need to do work now**, and until this file existed nothing told them apart. A new
agent opening the directory saw `V0.3_SPEC.md` and `ARCHITECTURE.md` side by side with equal weight.

**THE PROBLEM WAS NOT CLUTTER, WHICH IS WHY THIS IS AN INDEX AND NOT A DELETION.** Measured
2026-09-01: only 23 of these 70 are reachable from the files a newcomer actually opens (`README.md`,
`CLAUDE.md`, `docs/ARCHITECTURE.md`, `DECISIONS.md`, `KNOWN_ISSUES.md`) — and the three most recent,
most substantial design documents in the repo were **not** among them, while `V0.2_SPEC.md`, which
announces itself as "what shipped, and the ledger behind it", was. Live design was invisible and
history was signposted. Nothing here is deleted: a finished-work record is evidence, and this project
argues from evidence constantly. It just needs a label.

**HOW EACH FILE WAS PLACED**, so you can disagree with a specific call rather than the whole list:
its last commit date, whether any entry point links it, and what its own first heading claims to be.
Where those three disagree the file is listed as UNCLASSIFIED rather than guessed at.

---

## Start here

| file | what it answers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How it is built: the three processes, the service layering, the message-to-work path. |
| [`TARGET_ARCHITECTURE.md`](TARGET_ARCHITECTURE.md) | The shape it is heading for, as the operator specified it. Anything disagreeing with this is the thing that is wrong. |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) · [`COMMUNICATION_GUIDE.md`](COMMUNICATION_GUIDE.md) | Using the thing as an agent. |
| [`SESSION_MODEL.md`](SESSION_MODEL.md) | What a session, an agent and a handle actually are. Read before any lifecycle work. |

## Live reference — current design, read before changing that area

**The first three are the ones this index exists for.** All were written in the last week, all
describe live behaviour, and none was reachable from any entry point.

| file | area |
|---|---|
| [`ENVIRONMENT_ADVERTISEMENT.md`](ENVIRONMENT_ADVERTISEMENT.md) | Who tells the service what a host can do — and why exactly one tier may. |
| [`SERVICE_ADAPTER_CONTRACT.md`](SERVICE_ADAPTER_CONTRACT.md) | How aify-env supervises a service. |
| [`HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md`](HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md) | Which repo owns harness-driver semantics, and why. |
| [`AIFY_ENV_BOUNDARY.md`](AIFY_ENV_BOUNDARY.md) | What moved to aify-env, what stayed, which doctor owns which check. |
| [`PHASE8_STATUS.md`](PHASE8_STATUS.md) | Spawn delegation as it stands, including the three defects the first real spawn exposed. Read before touching spawn or terminals. |
| [`BRIDGE_SETUP.md`](BRIDGE_SETUP.md) | Setting up the environment bridge. |
| [`HERMES_INTEGRATION.md`](HERMES_INTEGRATION.md) · [`HERMES_AIFY_PLUGIN.md`](HERMES_AIFY_PLUGIN.md) | The hermes runtime and its plugin. |
| [`SKILLS.md`](SKILLS.md) | What the skill trees are and where they install to. |
| [`TEAMWORK_STRATEGY.md`](TEAMWORK_STRATEGY.md) | How a multi-agent team is meant to work here. |
| [`UNINSTALL.md`](UNINSTALL.md) | Removing it cleanly. |
| [`ROADMAP.md`](ROADMAP.md) | What shipped and what is next. Part record, part plan — the live half is the "next" section. |

## Finished work — kept as evidence, not as instruction

These describe decisions already made and work already done. They are accurate about their own
moment and are not a description of how the system behaves now. Read one when you want to know **why**
something is the way it is, never to find out **what** it currently does.

**Release specs and plans** — `V0.2_ROADMAP`, `V0.3_SPEC`, `V0.5_SLICE1`, `V0.5_SLICE2`,
`V0.5_SLICE3`, `V0.6_PLAN`, `V054_REMAINING_FIVE_PACKET`.

**Review rounds and ledgers** — `V0_7_WEAK_POINTS`, `V0_7_UNDEPLOYED`, `V0_7_REVIEW_DOSSIER`,
`FINDINGS_LEDGER_2026-08`, `PHASE3_DASHBOARD_LEDGER`, `PHASE4_BUGHUNT_LEDGER`,
`PHASE5_END_TO_END_STATUS`, `OVERSIZED_SCOPE_BLIND_SPOT`.

**Point-in-time traces** — `CONNECTION_TRACE` (dated in its own title), `MULTI_SERVICE_STACK_TRACE`.

**Decomposition proof packets** — each proved one file could be split safely. The splits landed; the
packets are the working, not the result. Named individually rather than as `JS_*_PACKET`, because an
index that covers fifteen files with a wildcard cannot tell you when a sixteenth arrives, which is
the failure this whole file exists to fix:
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
| `V0.2_SPEC` · `V0.2_PLAN` · `V0.4_SPEC` | Their own headings say ledger and shipped-spec, which is history — but an entry point links each of them as current reference. One of those two things is wrong and it is not this file's call. |

## Where the rest of the writing lives

`docs/superpowers/plans/` and `docs/superpowers/specs/` hold dated working documents — one per piece
of work, named by date. They are records by construction and are not indexed here.
`.claude/skills/` and `.agents/skills/` hold the agent-facing skills, which are loaded into context
rather than read on demand and are governed by a size ratchet.

## Keeping this honest

A file added to `docs/` and not listed here is invisible in exactly the way that prompted this index.
The three signals above are cheap to re-run: last commit date, whether an entry point links it, and
what its own first heading claims. If a section here disagrees with the file it names, the file wins
and this index is the thing to fix.
