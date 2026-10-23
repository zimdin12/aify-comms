# SW6 qualification preregistration on the final pair

Frozen by the commit containing this file, before q1. Authority is gate-owner message `1792766778695-3af2ed3a`, October 23, 2026, which approves the draft associated with `1792765958917-f7a0b545` and prospectively replaces its whole-host quietness condition. The earlier SW3d attempts remain historical and provide no execution credit to this set.

## Pair, apparatus and attempts

- comms `8c4788f620fa6075a9b580246532c52872ec992e`.
- aify-env `34c31d02e9af81a6e128a2051d3856431828e614`.
- wrapper `e103f3d6dbfe0173352671eb76642a575b05cdd8`, through comms' committed package lock.
- Gate `b70699c437105788302652bc34d2482b1f10693f`, unchanged in the kept `C:/Docker/aify-comms-wt-sw3d` checkout on `098/sw3d`. Its carrier before this evidence commit is `343a77d05a4c0bc65541192284940d94413723d0`.
- Exactly q1, q2 and q3, serially. Build both images in every attempt. Run the entire frozen `CASES` roster without a scenario filter. Unset `SANDBOX_GATE_IMAGES`. A1-A9 apply, with A9 replacing the surviving/drained seed of A6. Record `daemon-lost`, never judge it.

`source-manifest.json` derives the case roster and the gate's source population from the frozen source. It binds the gate, this preregistration and `campaign.py` by SHA-256. The manifest's own identity comes from the freeze commit, not a self-containing hash. The coordinator rechecks working bytes against both the manifest and committed blobs before and after execution. It never edits the gate or the product trees.

Run root: `C:/Docker/aify-comms-wt-sw3d/docs/evidence/2026-10-23-sw6-quiet-host`. Preflight and run directories must not already exist. Each attempt's output directory must be new; failed attempts are never overwritten, retried or replaced. Safety-unknown cleanup stops later attempts and leaves the set failed/incomplete.

After this commit:

```text
python docs/evidence/2026-10-23-sw6-quiet-host/campaign.py preflight
python docs/evidence/2026-10-23-sw6-quiet-host/campaign.py run
```

The first command controls the observer and runs both existing self-check entrypoints outside the qualification window. The second requires green preflight on this exact freeze commit, then starts the observer before q1 and keeps it through the final cleanup. Each attempt invokes the accepted gate with the full comms/env hashes, its q label and its new absolute output directory. The existing 120-minute primary budget, bounded child commands and exact-owned-resource cleanup remain unchanged. Capture the real child exit, raw combined console output, UTC boundaries, source hashes, attempt JSON and every scenario's evidence.

## Gate-owner ruling, verbatim

The following is the complete body of message `1792766778695-3af2ed3a`. Its rationale is the gate owner's statement, not evidence that extra load cannot influence a pass.

```
Approved: the sources (comms 8c4788f6, env 34c31d0, wrapper e103f3d via the pin, the gate b70699c4 unchanged from the kept C:/Docker/aify-comms-wt-sw3d), q1-q3 serially, fresh builds, no filter, SANDBOX_GATE_IMAGES unset, the full CASES roster, A9 as amended, daemon-lost observed and never judged, every attempt retained, the green rule otherwise as written, and the limits section.
One change, as gate owner. I wrote the quiet-host requirement on 10-22, and I'm narrowing it now.
- Why: quietness exists so a FAILURE can be attributed. Extra load only adds failure risk. Freezing the operator's whole fleet for about 2 h costs more than it buys right now; he is time-crunched and wants focus.
- Ruling: no host reservation, and nobody else's work is paused. Our own lanes stay quiet: no tests, builds or Docker from you, me or my builders from q1 start to q3 cleanup.
- Record, don't require: sample co-located load across the whole window, read-only (process table plus `docker ps`, about every 60 s), with the gaps recorded. Control the observer once before q1 against a known present process and a known absent id.
- Green rule: all three exit 0, every case passes, cleanup is complete, sources match, our lanes are quiet, and the load record is complete. Load from others does NOT block green. It is reported beside the result.
- A failure under recorded load is attributed with that record, and is never waved off as load. One residual: a pass that depends on slowness is theoretically possible. Say that in the limits.
Put this ruling and its reason into the preregistration verbatim. Then freeze it, commit it with the source manifest on the sw3d apparatus branch, run the self-checks outside the window, and start q1 when the self-checks are green. You run it; I review the results.
```

## Load record and own-lane boundary

No host reservation and no pause of anyone else's work. The executor, gate owner and the gate owner's builders run no unrelated tests, builds or Docker jobs from q1's start through q3's cleanup. The qualification's own required builds and read-only observation are the admitted work. The executor's earlier suite jobs have exited; the gate owner's message commits their lanes. Final owner readback remains part of result review, not something process names alone can prove.

Before q1, control the actual process-table reader with one bounded live child and a separate child that has exited. Require the live PID present and exited PID absent, refuse swapped expectations and a missing table, and retain the observed table and both child identities. Then run the existing gate and sandbox self-checks. Controls and self-checks occur outside the window.

The observer reads Windows CIM process metadata and `docker ps` at the boundaries and every 60 seconds. It records each read's start, end, exit, selected rows and errors, with nominal scheduling and actual capture times. It excludes process command lines, environments and container commands to avoid collecting credentials. A failed read is a gap, not an empty process/container population. Retain delayed or failed samples and the intervals between samples; these samples do not prove continuous host quietness. They describe recorded co-located load. An observer failure or missing boundary cannot be silently called a complete record. The owner reviews coverage and all recorded gaps beside the results.

## Fixed outcome rule and falsifier

Green requires all three actual exits zero, every case PASS, complete cleanup, matching sources, our lanes quiet and a complete load record under the ruling above. Any nonzero attempt, FAIL, UNKNOWN, ERROR, missing case, reused image, changed source, unproven cleanup or unclosed required own-lane/load-record condition prevents a green qualification. Preserve every attempt regardless of cause. A failure stays a failure under recorded load. Record context but leave causation UNATTRIBUTED unless separate evidence establishes it. No post-result case, threshold, source or attempt substitution is allowed.

The coordinator records execution, not an independent acceptance verdict. Its result stays `PENDING_REVIEW` for the gate owner's review. The owner reviews the actual case roster and outcomes, cleanup, load coverage, source identity and own-lane compliance.

## Limits that remain after a green set

- A pass that depends on slowness is theoretically possible. This campaign does not establish timing robustness or prove other load had no effect.
- This is the frozen Linux-container qualification with the gate's stand-in launcher. It does not certify native installed channels, actual Claude config persistence, S2's install boundary or the live stack.
- Fresh builds pin product Git source, not every external dependency. Preserve build logs and resolved image identities; the accepted Dockerfiles may fetch dependencies.
- SW6 still requires the daemon's actually loaded build mapped through PackageBuild to the qualified artifact and installation provenance, raw accepted interval announcements from every identified live publisher in both receivers, all remaining 0.9.8 release gates and the ordinary-next-restart authority. This sandbox run installs, activates and restarts nothing live.

Governing semantics remain `docs/superpowers/plans/s4-hard-switch/2-sandbox-gate.md` plus `docs/evidence/2026-10-21-sw3a-gate/AMENDMENTS.md` at the accepted gate. The historical source-only/quiet-host disposition is `343a77d0:docs/evidence/2026-10-22-sw3d/RESULTS.md`; the prospective ruling quoted above controls this new set. Neither is backdated into the old results.
