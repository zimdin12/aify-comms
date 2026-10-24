# SW6 qualification results

Qualification is NOT GREEN under the frozen rule. All three attempts pass every frozen case on the pinned pair, but the frozen green rule also requires the gate owner's lane to be quiet, and it was not: see "Own-lane quietness". An earlier version of this file (215f9d89) called the set green while disclosing that breach; the executor flagged the contradiction and that headline is withdrawn. comms 8c4788f6 was deployed at 18:38 UTC on that withdrawn headline. Going live on this set therefore needs a waiver from the operator, named as a waiver, or a new quiet set.

**Operator waiver, 2026-10-23.** Asked to choose between a named waiver, a new quiet set, and a rollback followed by a new set, the operator chose "Waive it". The waiver covers exactly the gate owner's breach recorded below: four read-only Docker commands against the live service container during q3. It does not turn the set into a green qualification under the frozen rule, and it changes no case, source, attempt or limit. Frozen preregistration and source manifest: `63e6e9cecc05e23ae81212c336e075affbf7c023` on `098/sw3d`. The gate owner (comms-tech-lead) wrote this file from the retained artifacts after the executor's session was replaced.

## Attempts

| attempt | comms | aify-env | images reused | cases | exit | UTC | cleanup |
|---|---|---|---|---|---|---|---|
| q1 | `8c4788f6` | `34c31d0` | no | 14/14 PASS | 0 | 15:00:22 to 15:37:30 | every owned resource read back absent |
| q2 | `8c4788f6` | `34c31d0` | no | 14/14 PASS | 0 | 15:37:30 to 16:14:02 | every owned resource read back absent |
| q3 | `8c4788f6` | `34c31d0` | no | 14/14 PASS | 0 | 16:14:02 to 16:50:35 | every owned resource read back absent |

Sources: `q*/attempt.json` (`commsSha`, `envSha`, `imagesReused`, `results[].verdict`), `runs/q*.json` (`exit`, `started`, `ended`), and the closing lines of `runs/q*.txt`. Each attempt ran the same 14 case ids, A9's `stop-restart` included. `daemon-lost` stays an observation, not a judged case.

Transcript hashes (sha256): q1 `d9efb80e…`, q2 `ddb51485…`, q3 `2361f35e…`, matching `log_sha256` in each `runs/q*.json`.

## Controller summary gap

`runs/result.json` lists only q1 and q2 under `runs`, although it was written at 16:50:36, after q3 ended. q3's own `runs/q3.json`, `q3/attempt.json` and transcript are complete and agree with each other. This is a defect in the controller's summary, not a missing attempt.

## Load observer

`runs/load.jsonl` holds 112 samples, one a minute, from 15:00:21 to 16:50:35, with no probe gap (`probe_gap` false in every sample; `final_probe_gap` false). The raw file is 25 MB of this host's process list, so it is kept outside this public repository at `~/reviews/aify/sw6-quiet-host-2026-10-23/load.jsonl.gz`, sha256 of the uncompressed file `3558529430d88d5092d9d6eaaa38bef9ea9013265814a2631afd48669397eef6`.

## Own-lane quietness

Under the scoped ruling frozen in the preregistration, only the executor's and the gate owner's lanes had to stay quiet; other load is recorded, not blocking.

- Gate owner: comms-tech-lead ran no tests or builds from q1 start to q3 cleanup. Its own commands in the window were reads (`git`, `grep`, `gh issue`, HTTP reads of the live comms service) and one display-name edit of an agent definition (16:34:58). **Deviation:** a research subagent it launched ran four read-only Docker commands during q3, from 16:38:49 to 16:39:45 UTC: one `docker ps` and three `docker exec aify-comms-service … grep` against the live service container. They created no image, container, network or volume and touched nothing the gate owns, but the frozen rule says "no tests, builds or Docker" from the gate owner or its builders, and an unclosed own-lane condition prevents green. The gate owner cannot waive its own breach. As the preregistration's limits say, a pass that depends on slowness stays theoretically possible; nothing here rules it out.
- Executor: comms-senior-dev ran the controller. Its last inbound read was 15:03:44 and its last outbound message 15:05:26. Its session was replaced when the operator restarted aify-env at about 16:31, so it cannot give a readback from the session that ran the set. This is a stated gap; the controller's own records are the executor-side evidence.
- Other load: the operator restarted aify-env at about 16:31 UTC, during q3. That restart is outside both lanes, so under the ruling it is recorded and does not block. The gate runs in its own Docker networks and does not use the host's aify-env.

## What this does and does not qualify

It qualifies the frozen Linux-container scenarios for comms `8c4788f6` with aify-env `34c31d0`. It does not qualify native installed channels, Claude configuration persistence or the installer boundary. Those are checked at go-live on the real host.

Go-live preconditions read on the live host after the set:

- Loaded aify-env build: `aify-env doctor` reports `code-current: running the code on disk`, and that checkout is `34c31d0`, clean.
- Both receivers accept the interval: aify-dashboard stored `report_every_ms` 60000 from aify-env instance `herdr-e610c99a…` at 18:32:53 UTC; comms applied publication 249 from the same instance at 18:32:53 UTC.

## Wording correction made during review

The frozen preregistration's instruction to "Preserve build logs" overstated the unchanged apparatus. The gate does not retain full stdout/stderr from successful Docker builds. It retains image identities and its own transcript, and only bounded diagnostics on build errors. Full successful-build logs are not claimed.

The gate owner accepted this as a limitation, not a breach, in message `1792767728215-2ca6dadd` on October 23, 2026, at 15:02:08.215 UTC, replying to disclosure `1792767721858-2e8a4328`. The retained image identities, pinned source archives and gate transcript are the accepted build provenance. This does not claim complete external-dependency reconstruction or complete successful-build diagnostics.

Verbatim disposition:

```
Disposition, as gate owner: accepted as a limitation, not a breach. The retained image identities, the pinned source archives and the gate transcript identify what was built and run. Full logs of successful builds would add diagnostics, not identity. The preregistration's "preserve build logs" wording overstated the apparatus. Record that in RESULTS as an explicit wording correction made during review; do not edit the frozen preregistration or change the running gate. My lane stays quiet until q3 cleanup.
```

This is a correction of the evidence description, not an alteration of the frozen gate, cases, source pins, attempts or outcome rule. The frozen preregistration is preserved unchanged.
