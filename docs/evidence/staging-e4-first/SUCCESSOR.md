# E4 switch acceptance

2026-11-08. **PROVEN on the synthetic Linux/container fleet: actual exit 0, PASS E4.** This is the successor to the retained first attempt, not a relabelling of its exit2. Exact source review and push are still pending.

## Run and scope

The same command documented in README.md ran with baseline `1c9611b3/dde9d6d/dd04bd4` and candidate `b40c1b88/4e10bc54/d07ba67`; refs.json carries full SHAs. Runtime versions were Node22.20.0, Codex0.166.0 and Claude2.1.316. Both client integrations were installed through their owners, as the real joint switch will do. The managed work loop used the maintained no-provider Codex runtime double. No live data, credentials, processes or provider calls were used.

Actual output is `complete-run.txt`. Full receipts and executed-source/input hashes are retained in:

`C:/Users/Administrator/projects/comms-evidence-fixes-20261106/staging-e4/e4-complete-1/`

All42 recorded checks passed. Baseline and candidate actual isolation/auth controls, definition publication, managed start and exact run-linked reply passed. The second agent was actually started/stopped before the switch and remained stopped afterward. Candidate takeover replaced the still-existing predecessor in the same host container/home/machine. The old owner and observed owned children became OS-absent; an unrelated process kept its birth identity and survived. Definition contents/incarnations/revisions and API agent identities remained. Candidate stop reached OS-confirmed exit and the owner listed no remaining managed processes.

All ten owned Docker resources were present before removal, removed with status0 and read back ABSENT. Commands settled and executed input hashes were unchanged. Check details are now copied at recording time; the observed alias regression was watched failing with two healthy controls, then passing.

## Doctors and explicit exclusions

- Comms doctor:16 applicable rows passed, raw exit0. `usage-openai` was explicitly NOT_RUN for provider qualification.
- `aify-env doctor`:19 applicable rows passed. Raw exit1 is preserved because `claude-login` reports not-set-up in this deliberately unlogged-in fixture. `claude-login` and `context-window` are explicitly NOT_RUN for E4, not passes. No other failed, skipped or unanswered row was accepted.
- The cold fixture has a readable `projects:{}` Claude config, without trust flags or login data. Its real trust-roots checker reports the existing automatic-trust policy. This does not qualify native Claude onboarding, trust-dialog execution or model behavior.

Acceptance was explicitly amended by lead1794104747508-3ac9eabc after the first result: remove constructed wrong-parent/corrupted-takeover controls, require the real doctors, actual stopped-agent preservation and snapshot correction. Lead1794105637274-a0a95c85 selected the real two-client install population and bounded bridge settling. Lead1794106277653-f2e25fec approved the empty project-map fixture and context-window NOT_RUN. The first report's limits remain historical; dropped controls were not run or passed.

## Operator-visible settling and known inconsistency

The API's `liveWithinSeconds` was120. It still listed the stopped baseline bridge when first queried after the switch. The scenario waited76882ms until that row expired while requiring the active candidate bridge as a positive control; no row or timestamp was edited. A doctor run immediately after the real switch may therefore report a stopped old bridge as stale/live until the120-second heartbeat window expires.

Candidate bind to first observation of its accepted new bridge was645ms. This is a polling upper bound, not exact first-heartbeat receipt time or a pass threshold.

Known inconsistency only: the doctor's Claude skill destination is unconditional while its comment says destinations require a runtime home; no Codex-only host was qualified or product change made for that case.

## Verification and remaining review

Focused staging tests passed184/184 after the snapshot correction, with no failure/cancellation/skip. The final composed focused and selected governing runs are reported with the review submission; prior runs are not relabelled as later source evidence. The first output and failed doctor attempts remain in the planning directory.

The entry names baseline, service switch, owner install, takeover, preservation, work loop, doctors and stop; finally cleanup has one owner. OS ending predicates are pure. Allowed resources/versions derive from the frozen manifest/layout; NOT_RUN rows are the explicitly approved qualification exceptions. Unknown observations and unexpected doctor rows fail rather than becoming a pass.

No new shared class was added after the scope decision. Each staged source module is used by the E4 command or is its direct regression test. This qualifies only the admitted synthetic Linux switch. Windows, native Codex auth, real models, production-data migration, E2's dashboard receiver and the live switch remain outside this run. No push or live activation is implied.

## Independent re-run

2026-11-08, by the comms lead from main e45e43c9, same refs.json: actual exit 0, PASS E4, 46 PASS lines,
with `usage-openai`, `context-window` and `claude-login` NOT_RUN as above. 4 min 27 s from invocation to
result. All ten owned Docker resources (3 containers, 2 volumes, 1 network, 4 images) were removed and read back
absent; a by-name search afterwards found none of the run's containers, volumes or network. The env and
wrapper checkouts are not beside the comms checkout on this host, so the command needs
`--env-repo <path> --wrapper-repo <path>`; without them it stops at staging with ENOENT before building anything.
Receipts: `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/staging-e4/lead-independent-run-3/`.
