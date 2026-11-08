# First runnable E4 switch attempt

2026-11-08. **NOT QUALIFIED, actual exit 2.** The one command completed its implemented switch steps, then refused overall success because required acceptance remains NOT_RUN. No live activation is authorized by this result.

The executed population was a synthetic fleet in a Linux container on Docker Desktop, using the real owner installers, env daemons, wrapper, MCP bridge and service. A maintained Codex app-server double drove replies without a provider. This is not native Codex compatibility, Windows takeover or live-data migration evidence.

## Reproduce

From the comms checkout, with Git Bash on Windows, Docker/Compose already installed and sibling env/wrapper checkouts containing the selected objects:

```bash
bash scripts/staging-e2e.sh E4 --refs docs/evidence/staging-e4-first/refs.json --out /absolute/path/to/new-run-directory
```

On Windows use the actual Git Bash executable, not the WSL `bash` selected by PowerShell. The first invocation used bare bash and exited127 before the entry could read a C:/ path. The corrected invocation used `C:/Program Files/Git/bin/bash.exe`.

The actual sealed invocation, direct exit and PASS/NOT_RUN output are retained as `first-run.txt`. The full run record is in the declared planning directory:

`C:/Users/Administrator/projects/comms-evidence-fixes-20261106/staging-e4/e4-first-full-2/`

That directory holds refs, executed source, input hashes, clean source snapshots, Docker receipts, actual rendered configurations, definition/agent snapshots, linked reply records, PID witnesses, runtime archive and final result. It is not copied into this compact report. No tests were rerun after this first command before its requested checkpoint commit.

## Observed result

- 35 recorded checks passed. The command still exited2, not0.
- Baseline `1c9611b3 / dde9d6d / dd04bd4` switched to candidate `b40c1b88 / 4e10bc54 / d07ba67`. Full SHAs are in refs.json. The harness ran from the then-uncommitted source captured in the run, separately from these product SHAs.
- Actual isolated mounts, environment, own internal network and no published ports were checked before/after start in both phases. Correct-key200 and wrong-key401 controls passed.
- Baseline real managed start and a correctly linked MCP reply passed. Fixing the verifier to read `/messages/inbox/dashboard?filter=all&peek=true&messageId=...` removed the earlier HTTP405 apparatus failure.
- Candidate start without force exited69 while the predecessor/managed worker remained alive. Candidate `--force` then took the same container-local port and persistent home. Owner PID812 was replaced by PID2200; the service accepted a new bridge for the same environment and machine.
- The old owner and four observed wrapper/runtime/MCP PIDs were OS-confirmed absent. An unrelated PID1479 retained its startTicks and stayed alive.
- Three definition contents/incarnations/revisions compared equal across the switch, and the three API agent identities remained. The definition named `stopped` was not actually driven through stop before the switch; that original fixture condition remains unproven.
- Candidate managed start, exact request/run/reply relation, real stop and OS-confirmed owned-process exit passed. The owner then listed zero managed processes.
- All ten owned Docker resources were present before removal, removed with status0 and read back absent. Input hashes were unchanged and Docker commands settled. An independent post-run hash comparison also found no mismatch.
- Bind event to first observation of the candidate's accepted new bridge was1222ms. This is a polling upper bound, not the exact first heartbeat-receive time and not an acceptance threshold.

## What prevents qualification

Applicable doctors and the native wrong-parent reply control were not implemented/run. The retained-owned-child control only demonstrated that the actual exit predicate rejects still-live managed children before takeover, not that an intentionally broken takeover is caught. The `stopped` fixture condition above is also incomplete. GATES-B.md is not met and the joint switch is not cleared.

One receipt limitation was noticed after the run: the scenario attaches `environment` to the baseline health object already retained in an earlier check. That changes the later serialization of that earlier check. Command argv are copied and the source hashes still match, but check detail objects are not immutable snapshots. The original separate health/definition/reply files remain available. This is a staging-record defect, not a proven fleet defect, and was not polished before this checkpoint.

Next work stays inside E4: close those acceptance gaps and the receipt snapshot defect, then run the same command again. Later E2, E3 and E1 use plain scenario functions; no new shared framework is needed.
