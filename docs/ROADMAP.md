# Stack roadmap

**Revision: STACK-2026-11-04-R2 · Planning only · 2026-11-04.**

This is the shared goals, dependency and sequencing map for aify-comms, aify-env,
aify-wrapper, aify-dashboard, aify-project-graph and agents-setup. It integrates the
owners' existing plans and the current issue discussions; it does not authorize fixes,
installations, public issue edits, releases, resets or fleet lifecycle actions.

The [issue map](roadmap/2026-11-04-issue-map.md) covers **109 current open issues**:
comms 29, env 18, wrapper 17, dashboard 20, project-graph 18 and setup 7. It includes
non-audit comms #53 and env #11. Every issue has an owner phase, disposition,
dependencies and an exit criterion. **HP-1** below is an additional required planned
integration item, not an existing GitHub issue. The [approval record](roadmap/2026-11-04-approvals.md)
binds all six owners to the same roadmap and issue-map bytes; contributing a slice is
not final approval. No issue label or comment saying “Applied” is proof of delivered code.

The [previous roadmap](history/ROADMAP-before-STACK-2026-11-04-R1.md) is retained
byte-for-byte. Its old tag targets are not a new release queue. Unfinished commitments
are carried below; older untriaged goals remain discoverable, not silently deleted.

## Goals

1. **Reliable everyday use and installation.** Useful small fixes ship without waiting
   for the whole architecture migration. Refusals are truthful; maintenance preserves data.
2. **One platform truth, explicit contracts.** Env owns processes, definitions and status;
   wrapper owns harness launch integration; services consume published contracts.
3. **One place agents ask for project knowledge.** Dashboard consumes APG's code facts
   through a shared host provider. Replacements must work and be used before removal.
4. **Lower per-session cost without capability loss by accident.** Qualify shared HTTP,
   host-wide mechanisms and their local/identity/lifecycle behavior before retiring copies.
5. **Predictable machine setup and findable guidance.** Declared owners, pins, discovery
   and bounded instructions replace contradictory lists; history and evidence stay retained.

## What is and is not finished

- The audit comment pass and correctness recheck are finished; the issue fixes are not.
  This roadmap uses the newer discussions and recorded amendments, not the old issue counts.
- The separately authorized one-time model correction is finished for the nine verified
  open sessions in its snapshot: seven definitions changed, two already matched, no
  unresolved target. Model-only persistence was checked; running sessions were not switched.
- **Automatic model sync, comms dashboard task #23, remains blocked.** Full re-selection
  and last-actual-selection semantics stay. [Upstream #107122](https://github.com/anthropics/claude-code/issues/107122)
  requests the missing producer contract; filing it is not implementing the capability.
- No implementation or activation follows from this planning round. Dashboard's separate
  documentation commit is not evidence that its milestone fixes are implemented.

## Decision basis

Use the [audit record at 8c2697ee](https://github.com/zimdin12/aify-comms/tree/8c2697eedad25622829d75f0dba00a592c925ad7/docs/audit-2026-11):
ROLES' later amendments outrank OWNER-GUIDANCE and the older INTENT ledger. These are
coordinator-written decision records, not verbatim operator messages or runtime proof.
Later operator decisions belong in their owning repo's decision record.

Recorded targets are not reopened by stale prose: separate repos; service-owned plugins
loaded by env; env-core screen evidence; both UIs retain manual controls through shared
endpoints; comms maps idle to online; root installs work without a silent Claude root
bypass; one env-owned lease watcher; and APG becomes a dashboard-only provider, one process
per host, no idle shutdown and no new feature-export writer. Keep Pi's actual existing
installer support; a stale header is not a reason to schedule re-enablement. Codex is
unparked, with real capability qualification rather than a historical parking gate.

## Delivery groups and useful exits

These are dependency groups, **not a waterfall**. Safe local work and early usage recording
can proceed in parallel after their specific decisions are settled. Each coherent approved
slice has its own release gate; G4 does not hold G0 hostage.

| Group | Outcome and first useful slice | Exit before the next dependent change |
|---|---|---|
| **G0 — Reliability and protection** | Comms install invocation/data-preserving clean/auth/transport edges; env Host and honest spawn/credential-readiness fixes; wrapper value/render/lock/refusal correctness; dashboard daily reliability; APG foreign-link/trust/HTTP/unsupported-language safeguards. Start with small independent fixes, not a platform rewrite. | Exact bad-input cases refuse before effects with healthy controls; failed writes preserve data; no hidden runtime/profile actions. The issue-map boundaries determine each fix, not every item in G0. |
| **G1 — Contracts, identity and test foundations** | Producer-owned schemas/examples; one exact process identity; verified process-tree cleanup; shared harness facts; neutral client/wire contract and staged consuming pins. Fix actual Linux defects before calling suite qualification green. | Real producer/consumer examples agree at named revisions; receivers accept new versions/fields before publishers send them; strict unknowns stay honest; current installed callers remain compatible. |
| **G2 — Platform ownership and shared controls** | Generic lifecycle composition, core screen/status evidence, event-held claims, service-owned plugin loading, one asset resolver and one registry publication path. Both UIs consume one status/control contract. | Empty/non-comms registry paths work; no duplicate starts or observers; publisher silence/conflict/restore rules survive; request identity/replay/lifetime and queued/refused/unknown outcomes remain intact; old writers retire only after adoption. |
| **G3 — Useful dashboard code service** | APG's versioned provider and explicit indexing; env-owned shared host lifecycle; dashboard_code for local and HTTP callers; early bounded usage recording. Improve PHP/Laravel/Rust facts alongside integration where independent. | Seven initial calls share the real provider; two sessions/two projects share one host process without leakage; no silent graph bootstrap; correct no-host/no-index/cancel answers; actual useful PHP/C++/TS session calls recorded before agent-surface retirement. |
| **G4 — Reduce per-agent machinery, then retire** | Qualified shared HTTP/Codex paths, dashboard startup/shim equivalence, host lease watcher, generic launchers; migrate triggers/discovery before retiring APG's agent MCP/skills and copied host code. | Named installed consumers use the tested counterpart. Preserve local docs/WSL placement/headers/custody/stdout/EOF/exit/signals. Real supported-runtime tests establish auth/capability and cost claims. No interim APG skill rename. |
| **G5 — Doctrine, context and evidence hygiene** | Setup declarations/drift/pins/composer; thin current guidance; package/image/search fences; current decision index and accurate usage evidence. These start where independent and integrate with G1/G2/G3. | Effective discovery and selected trees match declarations; unmanaged bytes survive; stale history stays explicitly reachable but out of default context; telemetry cannot fail a useful call. Invalid or zero-use figures never authorize retirement. |

**Usage recording starts early**, alongside G0, once its data-shape decision is resolved.
A paid usefulness trial is a separate operator choice. It is not a prerequisite for
reliability fixes or ordinary real-session use of the code service.

## Cross-repo gates: producer before removal

| Gate | Producers and consumers | Required ordering |
|---|---|---|
| State/definition contracts | env contracts and examples → comms receivers → both UIs | Receiver/schema support precedes new publisher fields, including exit diagnostics. Do not place all receiver acceptance after the host update that needs it. |
| Shared client/pins | wrapper client API + env authority agreement → actual comms/dashboard consumers | Preserve sync callers and async/deadline paths plus launch/per-turn custody distinctions. Switch each actual consuming pin and its candidate-sensitive tests before deleting copies or parity gates. Comms GitHub #23 is dashboard pin work; #52 owns writer migration. |
| Plugin/assets custody | env lifecycle/screens/loader → service plugins → env asset resolver → comms/dashboard/setup adoption | Wrapper rendering/connect and registry publication remain single owned paths. The installed-skills record is a deliverable, not an already-existing fact. No APG agent assets are installed by this service chain. |
| Shared APG host contract (HP-1) | APG package/dispatch/schema + dashboard spec/plugin + env process lifecycle | Agree the exact approved artifact/installed-path, acquire/start/connect, request correlation, project/LSP isolation, cancellation and owned stop contract. Env's host-provider adapter has no open env issue in the frozen inventory: assign an explicit env-owned tracker before G3 implementation, covering first-request singleton start, no idle stop, owned stop and cancellation. A new public issue requires separate posting authority. Reuse existing service acquisition/connect and process ports; no APG pseudo-agent, service endpoint or second installer. |
| Provider introduction before relocation | current dashboard plugin + env host capability → later external-plugin placement | The shared provider may be proved behind the current plugin first. Do not create a cycle requiring an already-moved plugin before the provider can be integrated. Both local and HTTP queue work use the same host provider, not one cold LSP per claim. |
| APG agent-surface retirement | provider + dashboard #16/#17/#21 → real use → setup triggers/discovery → APG #8/#21 retirement | Foreign-link guard first; full safety-reference process reaches the dashboard before APG's copy disappears; restore a foreign skill only after its automatic trigger is migrated and repair is authorized. Needed impact/hierarchy/diagnostic capabilities get a replacement or an explicit loss decision, not assumed seven-call parity. |
| Independent viewer/overlay retirement | dashboard view and project imports → explicit acceptance | Viewer retirement requires recorded operator use/acceptance. Each affected project's overlay is imported with associations preserved or explicitly dropped before removal. No export writer, no blanket data loss. |
| HTTP/process budget | comms #47 + wrapper #24/#25 + dashboard code/local capability | Guidance/schema corrections can land early. Actual Codex headers/auth and shared transport need real version-qualified evidence. A bare import does not start an entry-guarded bridge; a generic forwarder is not automatically a local bridge replacement. |

**Provider provision/update custody proposal for approval:** APG owns its package/bin/version.
Dashboard owns the dependency declaration (which provider calls and schema versions it needs)
through its existing approved service install/plugin path. The `providerCheckout` location
stays an operator-granted host execution setting that env reads fail-closed; no service
installer infers or silently writes it. Env owns the single process and lifecycle; setup
invokes one declared component entry point and reports installed ownership. The exact artifact,
installed-path and explicit host-grant contract must be agreed before G3 implementation.
Runtime ownership is not installer custody. This proposal must not create a second installer.

Status conflict is stated once in env's contract: only proven silence under its announced
intervals and qualifying same-machine peer evidence permits yielding; freshness alone does
not override a live conflict, and restored data is not fresh. Retain the actual recovery and
silence deadlines when replacing clocks with events. A reported turn-end is not proven
native quiescence, useful work, or fulfillment of an owed reply.

## Existing work retained, not duplicated

Numbers here are **comms dashboard tasks**, not GitHub issue numbers.

| Existing task | Roadmap disposition |
|---|---|
| #4 Hermes host separation / WSL proof | Preserve remaining delivery/integration qualification; do not rebuild the already-moved gateway or replay old version targets. |
| #6 reported reliability / test hygiene | Keep the actual unresolved cases; new CI does not close host-specific problems by itself. |
| #8 stopped-agent explicit-start behavior | Preserve its scoped adoption gate; do not silently widen it to the newly unparked Codex producer. A distinct runtime follow-up needs its own source/proof. |
| #12 fresh managed-agent unknown state | Keep its host-activation/qualification gate; do not treat source or new status vocabulary as live proof. |
| #15 Hermes resident start acknowledgment | Keep the owned visible-TUI activation/receipt gate. Gateway readiness, preseeded session markers and turn-end are not startup receipts. |
| #20 no local tool bridge copy | Unify code work with comms #47 and wrapper #25. Preserve its separately measured memory objective without claiming old figures are current. |
| #21 Make managed | Use the generic lifecycle and structured request contract; do not copy fleet rules into the UI. |
| #22 launch-roots presentation | Preserve the host's actual roots/adoption gate and operator roots action; no restart request is implied. |
| #23 automatic model sync | Capability-blocked; full re-selection/actual-last-choice semantics retained; upstream #107122 is not an implementation. |
| #24 one-time current-model pins | Completed separately for the verified nine-session snapshot; not automatic-sync qualification. |
| #25 this roadmap | Planning only; complete after the same named revision receives all six owner approvals and is delivered. |

Dashboard's canonical [PLAN at bb3b26a5](https://github.com/zimdin12/aify-dashboard/blob/bb3b26a56aed4955422144f1f0f273f0616d84f1/docs/PLAN.md)
keeps its existing task/proof/trial backlog. APG's current amendment to
`docs/PLAN-2026-09-20-dashboard-seam.md` preserves its active-plan and NO-RUN contracts.
Env's README-linked original plan is DONE, not a new backlog; later separation,
bootstrap/view/instructions, status and activity contracts remain the relevant commitments.
Wrapper and setup use this shared home rather than inventing another global queue.

## Decisions and activation gates still explicit

These do **not** reopen the recorded targets above. They are scoped choices before the
particular affected change, not blockers for every independent fix.

- **Audit defaults versus operator decisions:** keep the standalone path, no new layering
  ratchet, narrow explicitly-superseded history moves and staged host-guidance migration as
  the recorded proposed defaults. Do not recast them as verbatim operator rulings. Terminal
  `completed` wording and the ownership of usage-limit `blocked` require explicit semantics
  while keeping reply obligations and host state distinct.
- **Usage data shape:** dashboard #21's proposed identity-bearing per-call rows versus the
  smaller daily aggregate alternative is an explicit policy/data choice before implementation.
  Either must support the agreed actual-use gate, deduplicate logical calls, retain unknown
  attribution honestly, bound retention, store no content and isolate telemetry failures.
- **Destructive install reset:** the setup thread records a claimed reset policy without the
  original named targets/scope. Preserve current data until that authority is verified;
  keep dirty-tree refusal for `--bump` distinct from any approved install-time reset.
- **Control labels:** reconcile Stop/kill versus Hold/stop and Release hold across both UIs;
  never silently change request semantics. Delete remains held key OR explicit gate-off,
  with unknown unavailable.
- **Import/loss and manual activity:** declare overlay association mapping or explicit loss
  per project; use the existing managed activity path before considering a manual-launch
  wrapper default. Viewer acceptance, runtime installs, paid trials and live repairs stay
  separately authorized.
- **Capability choices:** the optional toolset selector's continuing purpose after APG
  removal and every needed code capability's replacement/loss are explicit, not guessed
  from a reduced listing or a withdrawn scanner result.

## First build recommendation and release discipline

After a build order, start with independent safety/install/daily-use slices: comms safe
clean/install invocation, wrapper missing-value/refusal fixes, env authority/spawn/readiness
fixes, dashboard's first reliability slice, and APG's interim foreign-link/safety guards.
In parallel settle G1 contracts, the telemetry data choice and the host-provider seam.
Do not wait for every G3/G4 migration to make the first useful release.

Owners supplied **effort estimates, not calendar commitments**. Env's named small fixes are
about half to one focused day each; wrapper's bounded small slices range from half to one;
dashboard's first reliability slice is about one; APG's initial protection group is 1.5–3.
These are assumptions, overlap and exclude external waits; do not sum them into a promised
stack deadline. APG's larger 16–33 focused-day envelope is not a prerequisite to those first
releases. Re-estimate contracted migration slices before implementation dates are promised.

During implementation, use targeted tests and real red controls; no run over ten minutes
on the shared host. Final exact release/pin candidates get the governing composed suites,
with skipped/not-run separate from passed. Public repos use CI on push; private dashboard
keeps its pre-push gate (`npm test` and `npm run check` on the pushing host) without Actions
minutes. Where its Linux run executes, and its cost, is chosen explicitly under dashboard #5;
that gate does not exist merely because this plan requires Linux qualification. Source-text
cleanup is not permission to delete behavior or frozen evidence/measurement contracts.

Batch compatible host/bridge adoption rather than restarting per issue. The audit's
“about two host restarts and two relaunches” is a planning estimate, not a guarantee or
permission. Env restarts and fleet relaunches remain ordinary operator lifecycle actions;
no agent starts/restarts env or asks for it merely to complete a roadmap row. Build, test,
pin, install, running adoption and live proof remain separate stages.

## Later goals, not part of the fix programme

The prior roadmap's console/transport, delivery/loop safety, one-shot idle notification,
verification and neutral-transcript ideas remain in the retained history for fresh triage.
The idle-notification idea is not a new monitoring daemon. Neutral transcripts/Continue as
remain experimental, with no native session-file or signed-thinking impersonation.
Larger memory/document/file-tree work remains later; transcript storage in dashboard is
not decided. A structural project must name the artifact or workflow it retires and the
concrete unmet requirement, not become another framework before the local fixes ship.
