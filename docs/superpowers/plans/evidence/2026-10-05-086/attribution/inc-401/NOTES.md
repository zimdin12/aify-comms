# INC-401: apgtest-hm-3/4 delivery loops 401 — investigation notes (2026-10-05, read-only)

## Key resolution path (installed copy C:/Users/Administrator/.aify-comms/mcp/stdio, all files mtime 2026-10-05 01:53:13 +0300 = 22:53Z 10-04)
- hermes-managed-host.js `run` -> hermes-delivery-loop.mjs:111 `httpCall = makeAifyHttpCall(AIFY_SERVER_URL, AIFY_API_KEY)`
- aify-http.mjs:43 `AIFY_API_KEY = API_KEY` (re-export) from aify-service-endpoint.mjs
- aify-service-endpoint.mjs:51 env names CLAUDE_MCP_API_KEY, AIFY_API_KEY; :94 endpoint env CLAUDE_MCP_SERVER_URL, AIFY_SERVER_URL
- :139-146 destinationKeyResolver: env key wins; else keyForEndpoint(registry) only if sameEndpoint(registry endpoint, url)
- :169 `const API_KEY = keyForUrl(SERVER_URL)` — resolved ONCE at module load, never retried.
- registry-credential.mjs:173-211 keyForEndpoint: registry read -> ref check -> sameEndpoint -> realpath check -> custody -> read+decode. Every failure returns "" (fail closed).
- credential-custody.mjs custodyProblemFor: lstat + `icacls <file>` via execFileSync timeout 5000 ms; failure -> stdout "" -> aclProblem "no access-control entries could be read" -> no key.
- loadSettingsEnv() (hermes-managed-host.js:57) runs AFTER static imports, so it cannot influence API_KEY.
- launcher ~/.local/bin/hermes-aify (mtime 01:54:04 +0300): :167 HARNESS_ENDPOINT default http://127.0.0.1:8800; :440/:443 export AIFY_SERVER_URL / CLAUDE_MCP_SERVER_URL; :933 nohup loop; no key exported.
- service launch overlay service/api_core/launch_env.py managed_launch_env: no key, no URL. aify-env terminal-controls.mjs:142 launchEnv = host env minus unsetEnv + overlay.

## Facts observed
- services.json aify-comms: endpoint "http://127.0.0.1:8800", credentialRef aify-comms-6b1e649c84bd.key, keyEnv [CLAUDE_MCP_API_KEY, AIFY_API_KEY]. mtime 22:53:21Z 10-04. Credential file mtime 22:52:52Z, 7 bytes, ACL Administrator(F)+SYSTEM(F).
- Container StartedAt 2026-10-04T22:49:23Z, restarts 0.
- Loop env (PEB read, pids graph 22524, hm-3 73832, hm-4 28376, dashboard 84288, comms 58220): all AIFY_SERVER_URL=CLAUDE_MCP_SERVER_URL=AIFY_COMMS_URL=http://127.0.0.1:8800; none has CLAUDE_MCP_API_KEY/AIFY_API_KEY/AIFY_SERVICE_REGISTRY; USERNAME/USERDOMAIN/USERPROFILE/HOME identical; PATH identical (92 segs, diff empty); TEMP identical.
  Differences: hm-3/4 have AIFY_MANAGED_MODEL, lack AIFY_SESSION_HANDLE + AIFY_EXPLICIT_SESSION_HANDLE (fresh vs --resume). Not on the key path.
- Replay (resolve.mjs, read-only) of keyForEndpoint now: 127.0.0.1:8800 -> key, hash matches file (50 ms); localhost:8800 -> key (678 ms, icacls variance); :8801 / "" -> none. custody now "". Empty icacls text -> refuses (negative control).
- Memory scan (memscan.ps1, read-only, counts only): key bytes count — graph 37, dashboard 41, comms 88; hm-3 1, hm-4 1; unrelated node controls 44296/71744/106980: 1 each (baseline: the 6-char key is a dictionary word present in node itself). Random 6-char control: 0 everywhere. => hm-3/4 loops never hold the key (baseline only), working loops hold many per-request copies.
- bridge_instances: graph has `hermes-managed-host-win32:stevenz-l-graph-senior-dev` registered 03:29:44Z, last_seen 04:56:36Z. No hermes-managed-host-* row for hm-3/4 => loop never made one authenticated call.
- Loop logs: hm-3 1061 lines, 1060 are 401, last 'fetch failed'; hm-4 1052/1052 401. All other loop logs 0 401s.
- Loop start (nohup / node, UTC): pc-manager 21:35:24 (pre-0.8.5), golf 23:44:11, sc-lead 23:44:27, sc-coder 23:48:56, comms 01:43:54, dashboard 02:13:46, graph 03:29:42, hm-3 03:57:36.8/03:57:38.6 (1.7 s), hm-4 03:58:01.3/03:58:02.3 (1.0 s). Others' nohup->node gap 0.05-0.7 s.
- terminal created -> loop start: comms 20 s, dashboard 23 s, graph 2m09s, hm-3 4m31s, hm-4 4m55s (that interval includes ensure-host gateway bring-up).
- All five spawns claimed by aify-env bridge bc96a8dc (process 47240); spawn specs env_vars empty for all.
- Odd side row: bridge_instances id bc96a8dc-... (aify-env's claimer id) attributed agent apgtest-hm-4, registered 03:53:06Z, bridge_build ''. Not explained.

## Conclusion
- NOT an endpoint/credential spelling mismatch (PROVEN by env read + registry).
- The two loops resolved NO key at module load and send none (PROVEN by memory baseline + no heartbeat row). The service answers "Invalid or missing" for both cases, so the log text cannot distinguish.
- Inputs are identical to working loops and resolve correctly now, so the one-shot resolution failed transiently at 03:57:38Z/03:58:02Z. Which step: UNATTRIBUTED. Leading candidate: icacls subprocess (5 s timeout) under load — ASSUMED.
- Not every new spawn: 6 post-0.8.5 managed loops work.

## Disclosure
- A PowerShell alias collision (my function `H` shadowed by alias h=Get-History) made an error message echo the credential value into my own tool output once. It went nowhere else. Fixed in later scripts (no H function used in memscan).
