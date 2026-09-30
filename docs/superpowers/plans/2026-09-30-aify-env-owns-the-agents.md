# aify-env owns the agents

The next tag after v0.7.7. The operator's shape (docs/ROADMAP.md, "Next"), 2026-09-30:

> aify-wrapper is like wrapper for harnesses, aify-env registers these harnesses, basically for
> management, aify-comms connects with aify-env and gets agents from there, same with aify-dashboard,
> aify-env would be agents management, aify-wrapper is one agent side of it, but it would be cool if
> aify-wrapper could work directly with services also, so each service gets its agents from aify-env.
> aify-env would have plugins that turn on when service is detected. ... would aify-env have to have
> its own db then ? or should it be config files based ? ~/.aify ?

Built on branches `next/env-owned-agents` in all three repos (worktrees beside each checkout), because
aify-env and the launchers on this host run straight from the checkouts, and the operator restarts
aify-env for v0.7.7 before this lands.

## The problem, measured

Today nobody owns "which agents exist on this host and how they launch". aify-comms holds it in four
places that overwrite each other (all code-read on 2026-09-30, b8addd47):

| where | what it holds | how it goes wrong |
|---|---|---|
| `agents` row | role, name, cwd, model, instructions, runtime, session_mode, runtime_config.effort, herdr_space | every boot's auto-register sends `role` from env, `name = id`, no model/instructions, so a boot clears `instructions` and `managed_by` and resets `name` (`agent_registration_writes.py:229,235`, `auto-registration.mjs:160-189`) |
| `spawn_specs` | env_vars, instructions, model, mode, runtimeConfig | a NEW row per spawn and per cold-start; readers pick one three different ways (`spawn_spec_assignment.py:41`, `terminals.py:250`, `dispatch_start.py:197`); `DELETE /agents` leaves them behind |
| newest `agent_sessions` row | environment, workspace | a cold-start reads placement from here (`dispatch_start.py:167-201`); environment assign rewrites every historical session (`environment_assignment.py:126-140`) |
| `spawn_requests` | role, name, workspace, mode | a cold-start writes `role='coder'`, `name=agent_id` (`dispatch_start.py:322-323`) and the running transition copies them into `agents` (`running_spawn.py:80-81`): **starting a stopped agent from the dashboard, or messaging it, resets its role to coder** |

aify-dashboard keeps its own `agents` table, filled by bridge self-registration, plus project placement
(`registry.ts:174`, `registration/agents.ts`). aify-env reads aify-comms' roster to offer starts
(`startable-agents.mjs`). aify-wrapper reads no definition at all: identity comes from flags and env,
and `HARNESS_CWD` is resolved but never applied.

## What changes, in one table

| | owns after this tag | reads |
|---|---|---|
| **aify-env** (host core) | agent **definitions**: one file per agent in `~/.aify/agent-definitions/`, written only through `DefinitionStore` | the installed launchers (harness registration, already scanned for the advertisement) |
| **aify-env service plugins** | carrying definitions to their service, and applying the service's change requests | the store, their service |
| **aify-comms** | live and historical state: sessions, messages, runs, status, spawn history; plus a **projection** of each defined agent | definitions pushed by each host's aify-env |
| **aify-wrapper** | the harness side of one agent run | its own agent's definition file, as defaults |
| **aify-dashboard** | projects, tasks, docs (unchanged this tag) | contract published; its plugin is its owner's work |

An agent with no definition keeps working exactly as today (self-registered, spawn form, residents on
hosts without the new aify-env). Nothing is migrated without the operator running the import.

## Decisions

**D1. A definition is the desired state the operator sets, and only fields something reads.**

```json
{
  "version": 1,
  "revision": 7,
  "agent": {
    "id": "comms-senior-dev",
    "name": "comms-senior-dev",
    "role": "reviewer",
    "harness": "hermes",
    "mode": "managed",
    "workspace": "C:/Docker/aify-comms",
    "model": "",
    "effort": "",
    "instructions": "",
    "env": {},
    "herdrSpace": true
  },
  "updatedAt": "2026-09-30T19:00:00Z"
}
```

- `harness` is the launcher client name (`claude`, `codex`, `hermes`), the vocabulary aify-env already
  advertises (`advertise.mjs:25-28`); the service maps it to its runtime names as it does today.
- Not carried: `systemPrompt`, `profile`, `channelIds`, the three policies. They are stored in
  `spawn_specs`, serialized and copied by cold-start, but no consumer in the current launch path uses
  them (P0 C1 states the legacy policy). `description` stays in the service (the agent writes it,
  `comms_describe`); `favorited` stays in the service (a viewer's preference).
- The exact schema, identifier rule and file admission are P0 C1; revisions are P0 C2 and C3.

**D2. `~/.aify/agent-definitions/<id>.json`, not `~/.aify/agents/`.** That directory is aify-wrapper's
lease files (live state: pid, startedAt, attached; `agent-lease.mjs`). One writer per file, so
definitions get their own directory. Ids follow the lease rule `^[A-Za-z0-9._-]+$`, no leading dot.

**D3. One writer at a time: `DefinitionStore` (aify-env, host core).** Lock file (`wx`), temp file,
fsync, rename, the pattern aify-wrapper's lease already proves. The daemon and the CLI both go through
it. It reads the directory on every call (no cache to go stale), refuses an unknown `version`, and
reports an unreadable file as invalid rather than rewriting it. A hand edit is allowed: the next read
sees it, and an invalid one shows in `aify-env doctor` and is neither offered nor pushed.

**D4. A single definition owner per agent, for this tag.** The definition lives on the host where the
agent runs, and a service's fleet view is the union of what every host's aify-env pushes. Two hosts
defining one id: the first accepted owns it, the second is refused with the reason. Ownership moves
only by the owner's withdrawal or an explicit operator release (P0 C3). This is a limitation of this
tag, not an answer to the operator's open multi-machine question, which stays open.

**D5. Harness registration is the launcher scan that already exists.** `installedHarnesses()` finds
`<client>-aify` files carrying `HARNESS_WRAPPER_VERSION` (`advertise.mjs:84-97`). `DefinitionStore`
refuses a harness that is not installed on this host; a definition whose launcher is later removed stays
and is reported, never deleted.

**D6. Plugins switch on when their service is detected.** Today plugins are built once at boot from
`services.json` and never again (`bin/aify-env.mjs:717-760`). The daemon re-reads the registry on the
advertiser's beat and starts a plugin for a new entry. Removing or re-pointing one is a configuration
detach, not a host shutdown, and is held while the plugin still serves workers (P0 C8). "Detected"
means registered; reachability stays the plugin's own concern, as now.

**D7. Synchronization is a push.** The aify-comms plugin sends this host's complete, ordered, fenced
snapshot (P0 C3). This keeps the 2026-09-08 ruling: the host does not fetch the service's domain to
display it; the plugin carries the host's own data to its service. Local list, show and validation
work with no service reachable. The one pull is the operator's explicit `import` (P0 C10).

**D8. aify-comms keeps desired, descriptive and effective apart.** New table `agent_definitions`
holds the desired state. Descriptive fields (name, role, instructions, herdr space) apply to the agent
at once; execution fields (harness, mode, workspace, model, effort, env) are desired until the next
start and never rewrite what a live run is. Every existing writer has a stated disposition, enforced in
its own transaction (P0 C5). `spawn_specs` rows are still written per spawn, as history.

**D9. The service asks for a change; aify-env decides it.** A dashboard edit of a defined agent
(herdr space, workspace/model/harness via environment assign, session mode, remove) becomes a row in
`definition_requests` and returns 202 with the request id. The aify-comms plugin claims its machine's
requests, applies each through `DefinitionStore` with compare-and-set on the definition revision and a
recorded request id, reports done or refused, and pushes (P0 C4). Withdrawal is not removal (P0 C6).
Rename of a defined agent is refused (409, "rename it in aify-env") this tag: it renames a file and
every row keyed by the id, and that is its own piece of work.

This is what lets the operator set herdr space from both sides, which they asked for, without two
owners.

**D10. aify-env's own surfaces.** `aify-env agents list | show <id> | set <id> key=value ... | remove <id>
| import`, and daemon routes `GET /definitions`, `GET /definitions/:id`. The TUI start list comes from
definitions, annotated with the status the service reports (the host still derives no status of its
own). Starting stays a request to the service (it owns sessions, dispatch and the launch payload).

**D11. The launcher reads its own definition as defaults.** Role, model and effort, below flags and
env. A harness mismatch or an invalid file is refused unless `--aify-ignore-definition`; a missing
file is today's behaviour (P0 C9).

**D12. Migration is the operator's command, dry-run by default,** showing sources, conflicts and what
the service cannot report (P0 C10). `aify-env doctor` reports agents the service knows on this host
that have no definition.

**D13. aify-dashboard is its owner's.** This tag publishes the contract (the file schema, the daemon
routes, the push route) and tells dashboard-manager. D6 means its plugin, when written, switches on the
same way.

## Phases, each reviewed before the next

| phase | repo | what | proves |
|---|---|---|---|
| P0 | plan | the contracts: [P0](2026-09-30-aify-env-owns-the-agents-P0.md) C1-C11 | reviewed before any code |
| P1 | aify-env | `lib/agent-definitions.mjs` (C1 schema, C2 store, C3 snapshot), the shared fixture, `aify-env agents` list/show/set/remove | C1/C2 witnesses: identifiers, case collision, symlink, torn write, concurrent writers, live-holder lock, hand edit adoption, invalid file, trash |
| P2 | aify-env | plugins follow the registry (C8) | C8 witnesses with the real comms plugin and fakes |
| P3 | aify-comms | `agent_definitions`, the push route (C3), dispositions (C5), withdrawal (C6), revision binding (C7), `definition_requests` (C4), dashboard | the role reset RED first; C3, C4, C5, C6 witnesses |
| P4 | aify-env | the plugin pushes, applies requests, checks at the start boundary (C7); start list from definitions | an offline request -> file -> push -> service readback test across both repos |
| P5 | aify-env | `agents import` (C10) and the doctor row | dry run writes nothing; conflicts listed; env reported unavailable |
| P6 | aify-wrapper | C9 | precedence, missing, invalid, mismatch |
| P7 | all | docs (TARGET_ARCHITECTURE, AIFY_ENV_BOUNDARY, DECISIONS, ROADMAP, READMEs, skills), version bumps, mixed-version check (C11), whole-diff review | every suite in all three repos; deploy, migration and the operator's restart stay separate |

## Not in this tag

Rename of a defined agent (D9). Starting an agent while its service is down. aify-dashboard's plugin
(D13). Harness-swappable transcripts (the tag after). Projects as aify-env data: the operator mentioned
"agents and projects"; projects are aify-dashboard's today and stay there until that is decided.

## What would prove this wrong

- A definition field something reads that is not in D1: found by grepping every reader of the
  projected columns and of `spawn_specs` before P3 lands.
- Two writers: `DefinitionStore` is the only code that writes the directory (a test derives the writer
  set from the source, as the doctor sources are derived).
- The role reset surviving P3: the P3 test is written first, against today's code, and must go red.
