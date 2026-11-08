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
