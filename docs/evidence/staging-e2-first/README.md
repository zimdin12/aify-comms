# E2 first attempt

E2 runs the managed work loop and env recovery on one candidate stack: comms 29b91cd3, env 45e76f77,
wrapper 2da3381 (`refs.json`). Run it from the comms checkout with Git Bash:

```bash
bash scripts/staging-e2e.sh E2 --refs docs/evidence/staging-e2-first/refs.json --out <new directory>
```

`first-run.txt` is the raw output of the first run, exit 1 after 330 s. It passed 23 checks, then failed
`piped_stop_answered`: `DELETE /processes/<id>` answered 500, `execution-unknown: POSIX group 2347 is still present`.
Every owned resource was removed (`PASS cleanup`).

Cause: the staging host container's PID 1 was `sleep infinity`, which never reaps. When the group TERM ended the
marker launcher before its `sleep` child, the child was reparented to PID 1 and stayed a zombie. A zombie is still a
group member, so env's ESRCH-only confirmation correctly refused to report the group gone. A disposable-container
probe showed it: without an init, 5 of 5 orphaned children stayed `Z` and `kill(-pgid, 0)` succeeded; with Docker's
init, 5 of 5 were gone and the probe returned ESRCH. A real Linux host's init reaps orphans, so this is a staging
defect, not an env one. The fix gives the host container an init.

## After the fix

`complete-run.txt` is the run on the same refs with the host init: exit 0 after 334 s, 28 recorded checks
(32 PASS lines with the input, cleanup and final lines), `PASS E2`, cleanup PASS. Each issue maps to these checks:

| Issue | Checks |
|---|---|
| comms#53 | `mixed_targets_refused_unstored`: a `to` plus `toRole` send answers 400 and reaches none of the three same-role inboxes, while the direct probe is found in the first |
| env#13 | `piped_stop_answered` (204), `piped_stop_ended_group`: both members of the launcher's group are gone |
| env#15 | `raw_start_outside_roots_live`: the raw local route still starts outside the roots, as its documented exemption says |
| env#17 | `orphan_outlived_crash`, then `successor_reaped_orphan_group` and `recovery_spared_unrelated` after a `kill -9` of env and a fresh start |
| env#19 | `spawn_failure_names_errno`: a non-executable launcher answers 422 `spawn failed: EACCES` and lists nothing |
| env#21 | the extracted boot composed end to end: the successor answers from the process that bound the port, reaps the orphan group, reports `ownershipRecovery` ready and owns nothing (`successor_owns_nothing`). The order of bind and reap is not observed here; the unit tests own it |
| work loop | `e2_managed_worker`, `e2_linked_reply`, `e2_stop_os_exit`, `e2_stop_owner_readback` |

NOT_RUN, printed by the run: env#31 and comms#31 have no fix at these shas, and env#15's service-start bound cannot
be reached from outside, because the comms plugin refuses an outside-root start before core sees it. Its unit tests
remain the evidence for that half.

The piped path runs with the optional `node-pty` moved aside, the supported fallback env#13 is about. Linux only:
no Windows ConPTY, no model provider.

The host init also applies to E4, which shares the layout. E4 reran on its published refs with it: exit 0, 42 checks,
the same three provider NOT_RUN rows, cleanup PASS, 457 s.

An independent run from this checkout at cb26fab4, with its own output directory and the main env and wrapper
checkouts as sources, recorded the same 28 checks and the same three NOT_RUN rows: exit 0, 5 min 38 s, all ten owned
resources absent afterwards.
