# Incident notes 2026-10-05, apgtest spawns (read-only investigation)

Local TZ is UTC+3 (FLEDT). File mtimes below converted to Z.
DB read via `docker exec -i aify-comms-service python` with sqlite URI mode=ro (container has no sqlite3 binary); q.py asserts SELECT/PRAGMA/WITH only.
Gateway tokens appear in agents.runtime_config; deliberately NOT copied here.

## A. spawn_1791172292730_3eacb1d5 (apgtest-cc-4)

spawn_requests row (spawn_requests.txt in this dir):
- created 03:51:32Z, claimed_at 03:51:56Z, started_at 03:51:56Z, status starting, process_id '', error '', finished_at NULL, claimed_by bc96a8dc-..., claim_machine win32:stevenz-l.
- started_at is set ONLY by a `starting` PATCH (service/routers/spawn_requests.py update_spawn_request: `if status_value == "starting" and not started_at: started_at = now`). So the starting report landed.
- process_id is written from req.processId; claim.mjs sends processId only on the `running` PATCH. Empty => running never committed.
- No agents row for apgtest-cc-4 (all 7 others have one). No terminal_sessions row for cc-4. No spawn events table exists in schema.
- aify-env /health (GET 127.0.0.1:60133): pid 47240, build fe14c700 == codeOnDisk; processes p12..p18 = cc-1,cc-2,cc-3,(p15 hm-1 exited),(p16 hm-2 exited),hm-3,hm-4. NO process for cc-4. plugin state claimedTotal=19.
- spawn_requests claimed by bc96a8dc since aify-env start (23:41:10Z): 20 rows; 19 have process_id=47240 (=running landed); 1 (cc-4) has none. claimedTotal counts outcome "registered" only, i.e. after running report succeeded => 19 = 19. The cc-4 pass did not end "registered".
- claim.mjs (aify-env lib/plugins/aify-comms/claim.mjs; installed path C:/nvm4w/nodejs/node_modules/aify-env is a symlink to ~/projects/aify-env): on running-report failure it logs `spawn <id> was claimed but could not be reported: <detail>` and returns {outcome:"failed"}; NO retry and NO failed report.
- That log goes to host.log -> bin/aify-env.mjs logLine -> NOTICES (in-memory TUI) when dashboard owns the screen, else stderr of the herdr pane. No file. ~/.aify/aify-env.log is from Sep 1. Not exposed over HTTP (/health has no notices). => cause text not visible to me.
- api.mjs: REQUEST_TIMEOUT_MS = 10_000 for PATCH report (AbortSignal.timeout). CLAIM_TIMEOUT_MS 35s.
- Service logs 03:45-04:05 (svc-0345-0405.log, 60 lines): no line for 3eacb1d5 / apgtest-cc-4; no REQ-ERROR, REQ-5XX, DB-LOCK, no SLOW-REQ PATCH /spawn-requests. Access log is off. RequestTimingMiddleware (service/main.py:72) logs Exception subclasses and >=1s work and 5xx; NOT 4xx under 1s, NOT asyncio.CancelledError (BaseException).
- PATCH handler raises only 400/404/409/403/500 before the commit; _settle_running_spawn (api_core/running_spawn.py) has no raise. Same body as 19 successes -> 4xx implausible (ASSUMED reasoning).
- Remaining candidates (UNATTRIBUTED): PATCH never reached service, or it exceeded the 10s client timeout and was abandoned/cancelled server-side without commit.
- Claim cadence: claims land in same-second pairs 30-38s apart: cc-1 03:50:52; cc-2 03:51:22; cc-3+cc-4 03:51:56; hm-1+hm-2 03:52:27; hm-3+hm-4 03:53:05 although requests were queued 16-38s earlier. Not explained.
- Why it stays `starting` forever: service/reconcilers/spawn_lifecycle.py:173 sweeps only `status IN ('claimed','running')`. `starting` is never reaped.
- Side finding: all 19 successful claims since 23:43Z were later failed "Abandoned: claimed by a live bridge but never settled within 30 minutes" although their agents run (sc-lead, cc-1..3, hm-3/4 ...). The wall ceiling fails every live managed-warm `running` row.

## B. vanished apgtest definitions

- ~/.aify/agent-definitions/.collection.json: revision 60, nextIncarnation 36, lastOperation 4be60290-..., mtime/birth 2026-10-05 01:49:28 local = 2026-10-04T22:49:28Z. 24 ids, none apgtest.
- Incarnations 1..35 all accounted: ledger (2-10,12-17,23-30,33) + .trash (1 codex-probe, 11 gov-tui, 18-22, 31, 32 e2e-check-084, 34 e2e-cli-084, 35 e2e-cli-084). No incarnation was ever minted for an apgtest id.
- .trash newest file 2026-10-04 02:12 local; no apgtest there. .recovered empty.
- service definition_stores: revision 60, updated_at 2026-10-04T22:49:28Z (moves only when the snapshot changes), pushed_at 04:34:59Z, outcome refused/invalid/kept all [].
- agent_definitions: 24 rows, max updated_at 22:49:28Z, none apgtest. definition_requests: 3 rows total, newest 2026-10-03T23:14:38Z, none apgtest. agents.definition_state/definition_withdrawn empty for all apgtest.
- `aify-env agents list`: 24 rows, no apgtest.
- Store code: adoptionPlan (lib/agent-definition-recovery.mjs:64) mints a NEW incarnation for any valid unknown file in the dir; invalid files are skipped (not deleted); hand-removal applies only to ledger ids; remove() moves into .trash with a ledger op. Push pass every 60s (definition-sync.mjs PUSH_INTERVAL_MS). So a valid apgtest-*.json present during any store session would have bumped nextIncarnation above 36; the store has no path that deletes a file it never adopted.
- ~/.aify/agents/ (instance records, not definitions) holds EXACTLY apgtest-cc-1, cc-2, cc-3, hm-3, hm-4 (the five named in the question), mtimes 03:51:59Z-03:58:19Z, still present at 04:33Z. These are the five apgtest agents with live processes.
- Conclusion: definitions store never held apgtest-*; the reported set matches ~/.aify/agents. What the observer actually listed at 03:56 is not visible to me.
