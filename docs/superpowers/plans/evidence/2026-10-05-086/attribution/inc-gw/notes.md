# apgtest-hm-1..4 readiness incident, 2026-10-05 (read-only attribution)

Local time on host = UTC+3. hermes logs are local time.

## What "ready" means (code, installed copy identical to checkout: diff -q silent)
- mcp/stdio/hermes-gateway.mjs:36 READY_TIMEOUT_MS = AIFY_HERMES_GATEWAY_READY_MS || 60000
- ensureGatewayHost (:136): spawn `hermes dashboard --port P --host 127.0.0.1 --no-open --skip-build`,
  stderr -> ~/.local/state/aify-comms/hermes-gateway-host-P.log (truncated per spawn)
- waitForIndexToken (:75): GET http://127.0.0.1:P/ every 250 ms, scrape __HERMES_SESSION_TOKEN__,
  deadline = start + 60 s, checked only AFTER a failed fetch; error text
  "did not become ready within 60000ms: <its stderr ends: last log line | lastErr.message>"
- then verifyWsOpen: open ws /api/ws (5 s)

## Ports (TEMP\aify-hermes-port-apgtest-hm-N): hm-1 9427, hm-2 9570, hm-3 9189, hm-4 9284 -> distinct.

## Timeline (UTC)
| | spawn created | PTY start completed | PowerShell 1st paint | gateway log created (=ensure-host spawn) | fatal | web_server up ("Web UI"/lock warning) | boot |
|hm-1|03:51:50|03:53:05|03:54:10|03:54:48.15|03:55:52 (terminal_events)|03:55:57.95 watchdog / 03:55:57.97|69.8 s|
|hm-2|03:52:00|03:53:35|03:54:40|03:55:22.47|03:56:30|03:56:34.38 watchdog / 03:56:34.39|71.9 s|
|hm-3|03:52:18|claimed 03:53:05|-|03:56:07.01|ok, gateway marker 03:57:12.07|03:57:11.83|64.8 s|
|hm-4|03:52:35|claimed 03:53:05|-|03:56:38.06|ok, gateway marker 03:57:35.69|03:57:35.63|57.6 s|

hm-1/hm-2 error in spawn_requests.error: "... did not become ready within 60000ms: fetch failed" ->
explain() found the per-port log EMPTY at the deadline (else it would quote the line) => web server had
not started at all.

hermes errors.log (local):
- 06:55:57,953 web_server: Parent-death watchdog: desktop PID 86408 appears orphaned; shutting down.  (86408 = hm-1 terminal_sessions.process_id)
- 06:56:34,376 same for PID 36992 (= hm-2 terminal process_id)
- Host backend lock Permission denied on ~/.local/state/hermes/gateway-locks/host-serve.lock for ALL FOUR
  (06:55:57, 06:56:34, 06:57:11, 06:57:35) -> not the differentiator; "This is NOT another backend holding it."
agent.log: "Mounted plugin API routes" ~1 s before each.

Gateway process chain for 9189 (Win32_Process, still alive): hermes.exe 06:56:10 -> venv python 06:56:12 ->
.hermes-runtime cpython-3.11 06:56:21 -> tools python-3.14.7 03:56:37.7Z (listener pid 32564) -> serving 03:57:11.8.
9284: hermes.exe 06:56:40 -> 06:56:43 -> 06:56:50 -> 03:57:04.95Z (listener 109280) -> 03:57:35.6.
No process for 9427/9570 (netstat: only 9189, 9284 listen; 8800 positive control listens).

Host load in window: 10 spawns 03:20-03:52 (apgtest-cc-1..4 claude + apgtest-hm-1..4 + dashboard-senior-dev + graph-senior-dev);
PTY start control took 38 s (hm-1) / 68 s (hm-2); PowerShell first paint ~1m43s after request;
errors.log 06:50-06:57: aify-notify.sh shell hooks timing out 8-20 s repeatedly, execute_code 300 s timeouts,
terminal.wait 32/62 s deadlines, "snapshot bootstrap failed exit 124".
Baseline from memory hermes-update-elevated-locks-its-tools.md: uncontended dashboard launch served in 7 s (2026-10-02).

hm-3 succeeded at 64.8 s > 60 s: deadline is checked only after a failed fetch; an in-flight fetch at the
deadline can still win (ASSUMED mechanism; Windows retries SYN on a refused loopback connect ~2 s; deadline
also starts after spawn() returns, which on this host took ~3 s: log 03:56:07.0 vs hermes.exe created 06:56:10).

## Ruled out
- port collision: four distinct port files.
- source-completion-pending: absent in installs\bd3acbc64406c95b; dir mtime 2026-10-03T20:58:51Z => no entry created/deleted there since (NTFS dir mtime inference).
  Positive control: same search found .install.lock and pm-runtime\.prepare.lock.
- hermes update: update.log last write 2026-10-03 23:59 local.
- tools ACL lock: the tools\python-3.14.7 interpreter ran for all four gateways.
- state.db lock: no "database is locked"/state.db lines in errors.log/agent.log 06:5x (pattern matches 1 line elsewhere in errors.log).

## hm-3 / hm-4: no session handle, no turn
- spawn rows: session_handle "", status failed "Abandoned: claimed by a live bridge but never settled within 30 minutes" at 04:23:30.
- agent_sessions.session_handle "" for both.
- Local markers DO exist: TEMP\aify-hermes-session-apgtest-hm-3 = 20261005_065915_e3d023 (03:59:24Z), hm-4 = 20261005_065936_3c3903 (03:59:48Z).
- Delivery loop logs: hm-3 900/901 lines, hm-4 893/893 lines "poll cycle claim error: HTTP 401: Invalid or missing API key".
  Other loops (comms-senior-dev, graph-senior-dev started 03:29:42Z, sc-*, golf, dashboard, pc-manager): 0 x 401 in last 200 lines.
- hermes-active-session.mjs:575-580 writes the local marker FIRST then PATCH /agents/{id}/session-handle with the same key,
  error swallowed; next tick sees durable === marker and never retries => handle lost permanently once the PATCH fails.
- Registry credential ~/.aify/credentials/aify-comms-6b1e649c84bd.key == container API_KEY (equality only, not printed);
  registry endpoint http://127.0.0.1:8800. So the store is right; the apgtest loops' process carries a wrong/missing key
  or an endpoint the registry does not match. Spawn spec env_vars = {}. Cause UNATTRIBUTED (could not read process env).

## Procedural note
I ran `docker cp q.py aify-comms-service:/tmp/incgw_q.py` (a query helper) -> landed as /tmp/incgw_q.py in the container.
Removed it (`docker exec -u root ... rm`), verified /tmp empty. Later queries piped via stdin, python sqlite3 mode=ro
(container has no sqlite3 binary). SELECT / PRAGMA table_info only.
