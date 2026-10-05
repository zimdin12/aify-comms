# 0.8.6 resubmission packet (2026-10-05)

Answers comms-senior-dev's REVISE of wrapper 38f3e20 (1791170343120-349981e8) and the managed-spawn handoff
(1791174645897-97f09b60, 1791175190147-fc2429c5). Labels: PROVEN = run or read on the real object; PASSES IN
TESTS = green, never run for real; ASSUMED / UNATTRIBUTED as stated.

## Identities

| repo | base (commit / tree) | candidate (commit / tree) |
|---|---|---|
| aify-wrapper | 237c0c553ab6cb29ab13a5010c40fe5df83bbd17 / 46fdb77c5096e86c4ae63c83e01def0fadc2b96b | 866865545b7cba5e766f86b431eac698bc4659ab / 77729646f4b5de5228a198557e2b9d46b7fb441c |
| aify-comms | 23a77ee4cd542d7cf1d2b141ba78d618c6b46bf1 / c49181d4d0500e0904de761407a53e5224433624 | the commit that adds this file (child of a9f923c7d3d9c5c20b1b4a47911f605d6ae5ecb6, tree 8c1b0e1a7a1d6cb814582c375799b9109722b4bf); it adds only `docs/superpowers/plans/evidence/2026-10-05-086/` |
| aify-env | 66c9968 (v0.8.5), unchanged | — |

## Manifests

Wrapper, release range 237c0c5..8668655: A .gitattributes; M HERDR.md, README.md, VERSION, bin/herdr-aify.mjs,
lib/herdr-owner.mjs, lib/herdr-resident.mjs, lib/herdr-supervisor.mjs, package.json, package-lock.json,
tests/herdr-aify-attaches-a-tui.test.js, tests/herdr-supervisor.test.js, tests/launch-to-a-stub-runtime.mjs,
wrappers/hermes-aify.sh.in; A lib/herdr-stop.mjs, tests/herdr-aify-env-joins-and-stops-by-name.test.js,
tests/the-gateway-token-is-never-printed.test.js, docs/evidence/2026-10-05-env-detach/{detach-mutants.mjs,
detach-mutants.out, detach-proof.sh, detach-proof.out}.

Wrapper, parent-relative:
- 388cd55 (detach): the release-range set minus .gitattributes, launch-to-a-stub-runtime.mjs, the token test, hermes-aify.sh.in.
- e51b83e: bin/herdr-aify.mjs, the 0.8.6 test, detach-mutants.{mjs,out}.
- 38f3e20 (R1-R3): .gitattributes, bin/herdr-aify.mjs, lib/herdr-owner.mjs, lib/herdr-stop.mjs, the 0.8.6 test, detach-mutants.{mjs,out}, detach-proof.out.
- 383b9df (R1-ABA, R3-LATE): lib/herdr-owner.mjs, lib/herdr-stop.mjs, the 0.8.6 test, detach-mutants.{mjs,out}, detach-proof.out.
- 8668655 (token): wrappers/hermes-aify.sh.in, tests/launch-to-a-stub-runtime.mjs, tests/the-gateway-token-is-never-printed.test.js, detach-mutants.{mjs,out}.

Comms, release range 23a77ee4..a9f923c7: M .claude-plugin/plugin.json, KNOWN_ISSUES.md, VERSION,
mcp/stdio/package.json, mcp/stdio/package-lock.json, mcp/stdio/version.js, service/reconcilers/spawn_lifecycle.py,
service/tests/test_a_live_bridge_does_not_shelter_an_abandoned_spawn.py. Parent-relative: a2467ca7 version + pin;
a0320735 pin; 8da5f108 and 4b45b243 the reconciler + its test; 827429bb and 2992bc5d KNOWN_ISSUES; a9f923c7 pin
8668655. **The comms candidate is no longer only a version and pin change: it carries the reconciler delta.**

## What changed since 38f3e20

- **R1-ABA** (lib/herdr-owner.mjs). A claim is `${pid} ${randomUUID()}`; a reclaim, like a release, removes only
  the exact bytes it judged dead, re-read under `<lock>.reclaim`. Test: your schedule (C reclaims and releases, a
  live B takes the lock under the reused pid 111, A's liveness verdict predates B); A refuses, B's bytes survive.
- **R3-LATE** (lib/herdr-stop.mjs). The time left is checked again after the sleep; no question starts at or after
  the deadline. The comment now separates what the five seconds bound (question start, each question's own
  timeout) from what they do not (a late timer wake, a CLI process still ending after its timeout). Test: a clock
  whose sleep wakes 5 s late; no question starts at or after +5000 ms.
- **Gateway token** (wrappers/hermes-aify.sh.in). The success line and the parse-failure line print the host's
  answer with `"token":"<redacted>"` and `?token=<redacted>`; the answer the launcher parses is unchanged, and the
  TUI still gets the URL with its token. This is a logging correction only; it does not rotate or contain tokens
  already printed (Steven's).
- **Stranded `starting`** (service/reconcilers/spawn_lifecycle.py). The orphan query admits `starting` under
  `running`'s rules, with the existing owner predicates unchanged. The UPDATE now compares the status, the
  claimer and an empty finished_at as they were judged, and counts only rows it settled.

What the wall ceiling permits, exactly: the reconciler runs in the 60 s sweep. For a `starting` or `running` row
whose claimer (`claimed_by_bridge_id`) is a live environment bridge, it fails the row once
`now - (claimed_at or created_at) >= active_managed_run_wall_ceiling_minutes` (setting, default 30 min); for a
claimer not live, once that age >= SPAWN_ORPHAN_GRACE_SECONDS (180 s); with no determinable age, never. A
`claimed` row has no live-claimer shelter, as before. For spawn_1791172292730_3eacb1d5 (claimer bc96a8dc, live)
that means the first sweep after this build is deployed; that is the recovery of the exact request, under the
reconciler's own rules. ASSUMED until deployed, which is Steven's: no deploy, restart, console input or row edit
was made.

## Proof

| claim | instrument | result |
|---|---|---|
| wrapper herdr + token behaviour | `node --test` on the four files in detach-mutants.mjs | 49/49 (PASSES IN TESTS) |
| each guard is load-bearing | docs/evidence/2026-10-05-env-detach/detach-mutants.out (wrapper) | 27 mutants, each killed at its named test, sources sha256-identical after; runner uses --test-force-exit |
| token absent from every printed line | the token test through the rendered launcher, stdout+stderr captured | sentinel absent; mutants T1-T3 (each line unredacted, the URL rule removed) expose it through the same capture |
| reconciler | test_a_live_bridge_does_not_shelter_an_abandoned_spawn.py | 12/12; mutants by hand, restored with cp + cmp: old query (2 starting tests fail), `starting` given `claimed`'s rules (shelter control fails), status compare removed (late-report race fails), claimer compare removed (new-claimer race fails) |
| herdr-aify env lifecycle, real | detach-proof.sh, sealed (env -i, temp HOME, real herdr.exe), detach-proof.out | run on the 383b9df tree; 8668655 changes no bin/ or lib/ file, so the herdr code it ran is the candidate's. Attached-window close and job-object terminals UNVERIFIED |

## Suites, every run on the final trees (receipts/ holds the raw logs, gzip)

- wrapper 8668655: wrapper-086g.log, 722 tests, 656 pass, 0 fail, 66 skipped.
  Earlier runs kept: wrapper-086.log (29 fail), wrapper-086f.log (63 fail, on 383b9df). Every failure was a render
  or launch subprocess killed at its timeout (`render failed:` with empty output, `null !== 0`, or a rendered
  launcher never written). The 19 failing files of 086f, run sequentially on 383b9df: wrapper-086f-alone.log,
  146/146 in 1797 s (per-file list: failing-086f.txt). wrapper-086-rerun{,2}.log: the 16 failing files of the first
  run, 37/37 and 77/77. Load is NOT proven as the cause: non-reproduction alone is all these show. One
  discriminating fact: no render path imports the modules changed after the green run at 38f3e20 (grep of
  install.sh, render.sh, wrappers/, lib/, bin/ for herdr-owner|herdr-stop; control: bin/herdr-aify.mjs found).
- comms a9f923c7: comms086e-py.log 5112 passed / 27 skipped; comms086e-bridge.log 371/371; comms086e-dash.log
  1894/1894. Earlier on pin 38f3e20: comms086c (1 py: test_install_hermes_prebuild 60 s subprocess timeout; 1
  bridge: session-fixes EBUSY rmdir), both green alone and in the full re-run comms086d.
- aify-env 66c9968 (unchanged) with AIFY_WRAPPER_REPO at the candidate: env086e-npm.log 2463 pass, 0 fail, 4 skipped,
  1 cancelled -- tests/the-dashboard-plugin-reads-no-object-store-out-of-the-grant.test.js hit the runner's 180 s
  file budget. Alone (env086e-alone.log) 10/10 but in 190 s: its cost exceeds its budget on this host, a
  pre-existing aify-env test-budget issue outside 0.8.6.

## Steven's rulings this packet relies on

Given in the comms-tech-lead session, 2026-10-05 about 03:40Z, answering a question whether overnight restarts
were allowed: "No. other teams are working on 0.8.5 currently. but i guess you could move my claude code
credentials and hermes credentials over to wsl and install stuff there and test there if needed. or container if
you wish. but not this live work env." Credentials were not copied (a refresh elsewhere may rotate the shared
grant: ASSUMED, not tested). Earlier, "actually finish it as full correct thing in 0.8.6" (the detach scope).

## Attribution (attribution/, read-only)

- apgtest-cc-4: the `running` report never committed (PROVEN: empty process_id; 19 of the claimer's 20 rows carry
  pid 47240). Why: UNATTRIBUTED -- aify-env's "claimed but could not be reported" notice is not written to disk,
  and the service access log is off.
- "Vanished definitions": the store never held an apgtest definition (PROVEN: ledger revision 60 and incarnations
  1-35 all accounted for, none apgtest). The files seen were ~/.aify/agents records, still present.
- hm-1/2: gateway boots of 58-72 s against the 60 s readiness wait (PROVEN timeline); load as the cause ASSUMED.
- hm-3/4: their delivery loops hold no API key (PROVEN by a memory count against controls) and get 401 on every
  poll; the key is resolved once at module load and any credential-path failure is an empty key with no log.
  Which step failed: UNATTRIBUTED. Logged in KNOWN_ISSUES with the fix owed; not in this release.
- Side finding, logged: every healthy spawn's row ends `failed` after 30 minutes (no success state).
