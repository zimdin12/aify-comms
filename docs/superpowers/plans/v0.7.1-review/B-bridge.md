# v0.7.1 review, lens B: host side (bridge, doctor, installer, scripts, wrapper pin)

Reviewed at tag `v0.7.0` (`2daa3026`). Code under review is identical at HEAD `3f588c5c` (only the
finalization plan differs). Range: `git diff v0.6.22 v0.7.0 -- mcp/stdio ':!mcp/stdio/tests'
':!mcp/stdio/node_modules' install.sh redeploy.sh scripts`, plus aify-wrapper `1946a9c9..34b2a95`.

Evidence levels: **RAN** means executed in a sealed sandbox. **READ** means traced in source and not
executed. **ASSUMED** means a consequence inferred from reading.

Count: P1 0, P2 2, P3 6.

## Findings, ranked

### B1 (P2). The doctor can send the checkout's `.env` API key to a remote service, and 0.7.0 makes that routine

- **Where:** `mcp/stdio/doctor-api-key.mjs:103-113` returns the `.env` `API_KEY` without checking the
  endpoint. `mcp/stdio/doctor.js:107` (`SERVER_URL`) and `doctor.js:167-177` send it. 0.7.0 pointed
  the doctor at the installed endpoint: `install.sh:1367` (`aify-comms doctor` passes
  `AIFY_SERVER_URL="$SERVER_URL"`) and `install.sh:2712` (the `aify-doctor` shim bakes it), in
  `1a13abf5` (scan item B3).
- **Scenario:** A host is installed against a remote service (`install.sh --client claude
  http://team-server:8800`) and also has a local checkout with its own `.env` `API_KEY`. The operator
  runs `aify-comms doctor` from inside the checkout. `findRepo()` picks up the cwd (`doctor.js:154`),
  and the local service's key goes to `team-server` on every service-reading check, over plain http.
  Before 0.7.0 the doctor asked `localhost:8800` unless the shell exported a URL, so this needed an
  unusual shell. The store path beside it is endpoint-bound (R2). The `.env` path is not.
- **Evidence:** RAN. `resolveDoctorApiKey({repoDir: <dir with API_KEY=local-service-key>, endpoint:
  "http://team-server.example:8800"})` returned `{"key":"local-service-key","source":".env"}`. The
  control with no repo returned an empty key.
- **Introduced:** the `.env` branch is older (`dc7c9481`). 0.7.0 made it reachable on every run
  against a non-local install.
- **Smallest fix:** use the `.env` key only when `SERVER_URL` is loopback. That means
  `sameEndpoint(SERVER_URL, "http://127.0.0.1:<port>")`, or simply a loopback host check. Anywhere
  else, fall through to the endpoint-bound store. Add a test with a remote endpoint and a checkout
  `.env`, and assert an empty key.
- **Severity note:** I rated this P2 rather than P1. The receiver is a service the operator
  configured, not an arbitrary origin, and the leaked key opens a loopback service. It is still the
  R2 class the rest of the bridge refuses.

### B2 (P2). Running `install.sh` directly resets a custom aify-env endpoint, so the doctor goes red on a healthy host

- **Where:** `install.sh:18` defaults `AIFY_ENV_ENDPOINT_BAKED` to `http://127.0.0.1:8802`, and
  `install.sh:92-95` changes it only when `--env-endpoint` is passed. Nothing in install.sh reads the
  installed value back: `grep -c installed-env-endpoint install.sh` gives 0. v0.6.22's install.sh
  read it back through `installed-delegation.sh` (2 references). `redeploy.sh:44-45` carries it, and
  that fix (`9a0863a2`) was applied only there. Its test
  (`test_redeploy_keeps_the_installed_env_endpoint.py`) covers only redeploy.
- **Scenario:** aify-env runs with `--port 9000`, and the host was installed with `--env-endpoint
  http://127.0.0.1:9000`. The doctor's own fix line says "re-run install.sh", so the operator runs
  `install.sh --client claude`. The launcher is rewritten with 8802. After that:
  - `spawn-delegation` reports `unreachable` ("every managed spawn fails until it is") while spawns
    work, because aify-env claims through the service, not through this endpoint.
  - `env-processes` asks the wrong daemon.
  - The red row invites starting a second aify-env, which is the fleet-reaping action.
- **Evidence:** READ, with the grep counts above as controls.
- **Introduced:** 0.7.0 (`f047b6d1`/`1a13abf5` deleted the read-back).
- **Smallest fix:** when no `--env-endpoint` is given, set `AIFY_ENV_ENDPOINT_BAKED` from
  `bash scripts/installed-env-endpoint.sh "${EMIT_WRAPPERS_DIR:-$HOME/.local/bin}"`, falling back to
  the default. Add the install.sh twin of the redeploy test. While there, make a bare
  `--env-endpoint` with no URL an error. Today it silently does nothing (`*) shift ;;`).

### B3 (P3). The B11 fix changed what happens when the completion PATCH itself fails

- **Where:** `mcp/stdio/dispatch-loop.mjs:330-341` and `run-outcome.mjs:10-15`.
- **Scenario:** The fix is correct for what its commit claims: a throw after the run was PATCHed
  `completed` no longer appends a `failed` event. A transient failure of the completion PATCH itself
  now behaves differently:
  - The success branch has no retry.
  - The error is only logged.
  - The run stays `running`, batch extras stay `claimed`, and no reply handoff is ensured until the
    server reconciler acts (5 min or more).
  - Before, the `.catch` path reached a terminal status within about 3.5 s, though the status was the
    wrong one.
- **Evidence:** READ.
- **Introduced:** 0.7.0 (`1e4e2e80`).
- **Smallest fix:** give the completion PATCH the same 3-attempt backoff the failure branch has, then
  run `finalizeBatchedExtras` and the handoff.

### B4 (P3). The redirect gate stops reading a call at the first line that ends in `)`

- **Where:** `mcp/stdio/tests/a-fetch-with-headers-never-follows-a-redirect.test.js:20-31`
  (`callSites`).
- **Scenario:** A call like `fetch(url, {\n signal: AbortSignal.timeout(3000),\n headers: {...},\n})`
  ends its site at the `signal` line, so `headers` is never seen and the call is never judged. A new
  keyed fetch that puts `signal` first can follow redirects with the key, and the gate stays green.
- **Evidence:** RAN, using `callSites` copied verbatim from the test into scratch. The signal-first
  keyed fetch was flagged 0 times. The headers-first control was flagged 1 time. No current call
  site is affected: every keyed fetch in `mcp/stdio/*.{js,mjs}` has `redirect: "manual"` (read), and
  `controllers/` and `adapters/` have no fetch.
- **Introduced:** 0.7.0 (`d8b58ebc`).
- **Smallest fix:** extend the site to the matching close paren (count `(`/`)`) instead of the first
  line that ends in `)`. Add the signal-first case to the negative control.

### B5 (P3). `stamp.sh` repeats the doctor's runtime path list with nothing holding the two together

- **Where:** `scripts/stamp.sh:64-65` hard-codes `service mcp config Dockerfile` and three excludes.
  The owner is `mcp/stdio/doctor-predicates.js:205-237` (`SERVICE_RUNTIME_PATHS`,
  `SERVICE_RUNTIME_EXCLUDE_PATHS`). The comment says "the same paths". No test compares them: none of
  the stamp tests reference either constant.
- **Scenario:** A path added to the doctor's list is not counted as dirty, so a dirty build reads
  clean. This is the drift the doctor's own comments warn about.
- **Evidence:** READ.
- **Introduced:** 0.7.0 (`a50e6b69`).
- **Smallest fix:** add an agreement test that parses both lists, or have stamp.sh ask node for the
  exported arrays.

### B6 (P3). `hermes-managed-host.js` is 479 comment lines out of 667, and most describe functions that live elsewhere

- **Where:** `mcp/stdio/hermes-managed-host.js`, for example the blocks above "Flatten the many
  session.active_list envelope shapes", "Freshness stamp of the row", and `scrapeToken`.
- **What happened:** `0e035f0a` deleted the "X moved to Y" pointer lines. The descriptive paragraphs
  above them stayed, so they now read as documentation of code in this file, which has four
  top-level definitions.
- **Evidence:** READ (`awk` count: 479/667).
- **Introduced:** 0.7.0.
- **Smallest fix:** delete the orphaned paragraphs (about 350 lines), or move each one to the
  function it describes.

### B7 (P3, needs a live check). `bridge-current` can call aify-env's claim row "a bridge started before 0.7.0"

- **Where:** `service/api_core/running_spawn.py:129-145` inserts a `bridge_instances` row keyed by
  the reporter's `bridgeId`. That is aify-env's identity (`aify-env lib/plugins/aify-comms/api.mjs:286`),
  written with no `bridge_build` and `last_seen=now`. `service/routers/agents/bridges.py:28-44` lists
  it for `ACTIVE_RUN_BRIDGE_STALE_SECONDS` (120 s). `doctor-predicates.js:508-513` renders it as
  `partial`, with the advice "started before 0.7.0 — restart to start reporting".
- **Scenario:** For about 2 minutes after each spawn, the doctor names the spawned agent as running a
  pre-0.7 bridge. The advice is wrong: restarting the agent does not change aify-env's row.
- **Evidence:** READ for the writer and the route. ASSUMED that the row outlives the worker's own
  registration. The supersession pass on registration may retire it first. This was not observed on
  the live DB, and service ports were off-limits.
- **Introduced:** 0.7.0 (the new `/bridges` source dropped the old host-tier exemption).
- **Smallest fix:** in `/bridges`, exclude rows whose id is an environment's bridge id, or rows with
  an empty `bridge_kind` that were written by a spawn report. Alternatively, have the verdict skip
  them instead of counting them as silent. This belongs to the service lens.

### B8 (P3). Against a service older than 0.7.0, `bridge-current` blames the service for not answering

- **Where:** `mcp/stdio/doctor.js:373-378`. `get()` returns null on any non-2xx, so a 404 from a
  pre-0.7 service with no `/bridges` route renders as "the service did not answer, so no live bridge
  could be asked".
- **Scenario:** The bridge or installer is updated before the container is rebuilt. The `service` row
  is green, and this row says the service is silent.
- **Evidence:** READ.
- **Introduced:** 0.7.0.
- **Smallest fix:** keep the status in `get()` and word a 404 as "this service predates `/bridges`;
  rebuild it".

## Checked and sound

- **Notify hook (`notify-check.js`, `notify-notice.mjs`):**
  - It reads the inbox with `peek=1`. The route gates on `bool(peek)` over a string
    (`service/routers/dispatch_messages/inbox.py:51,133`) and accepts `offset`.
  - On Claude it reaches the model through `hookSpecificOutput.additionalContext`.
  - The body is fenced with ``` escaped, and the subject is quoted.
  - The seen set is per session and bounded at 200.
  - Paging terminates, including against a service that ignores `offset`.
  - The key is resolved per destination, the redirect is manual, and the down-marker is per server.
  - RAN: `notify-hook-leaves-messages-unread.test.js` drives the real script. It passed together with
    five other files: 35/35.
  - Caveat, not a finding: in the worst case the page walk makes 11 sequential reads inside the 3 s
    hook `timeout` (`install.sh:1935,2341`). That is fine against a local service. If the hook is
    killed, nothing is marked seen, so nothing is lost.
- **Keys and redirects:**
  - Every keyed `fetch`/`fetchImpl` in `mcp/stdio/*.{js,mjs}` sets `redirect: "manual"`. That covers
    `aify-http`, `aify-service-endpoint` (the key is re-resolved for each failover URL),
    `claude-channel` (the key is set or deleted per URL), `artifact-tools`, `inbox-tools`, the
    heartbeats, doctor, `agent-for-handle`, and the three `usage-collector` OAuth calls.
  - The hermes gateway probe sends no headers.
  - `destinationKeyResolver` binds the store key to `sameEndpoint`.
- **Fallback URLs** stay on the primary's scheme and port (`defaultFallbackServerUrls`).
- **`agent-for-handle.mjs`:**
  - It fails silently and exits 0.
  - The aify-wrapper pin `34b2a95` matches package.json, the lock and `node_modules/aify-wrapper`.
  - All three templates call it with the bound endpoint. hermes exports
    `AIFY_SERVER_URL=$HARNESS_ENDPOINT` at line 335, before its call at 402.
  - The `CODEX_HOME` change is correct.
- **Doctor:**
  - `installedHostTier` and `spawnHostVerdict` return `unknown-all` for a missing launcher or the
    `.cmd` shim.
  - `bridge-current` reads `/bridges`.
  - Sidecars (the claude channel and the hermes delivery loop) send `bridgeBuild` on every beat, and
    an empty build never overwrites a stored one (`bridge_liveness_beat.py:95-102`).
  - A skip is marked `skipped` and `ok:false` in `--json` (`doctor-report.mjs:48`), and
    `deploy-delta.sh` reads it.
- **Deleted exports** (`extractRuntimeSessionHandleFromArgv`, `pidsForResumeHandle`,
  `processesThisBridgeDoesNotKnow`, `credentialRefIn`, `launcherDelegation`,
  `spawnDelegationVerdict`, `delegationOptedIn`, `TERMINAL_CONTROL_POLL_MS`) have no remaining
  references in aify-comms, aify-env or aify-wrapper. Control: `extractRuntimeSessionHandleFromCommand`
  was found in 3 files.
- **`hermes-daemon-cli.js`** keeps only `stop`, and both launchers call only `stop`
  (`install.sh:1096`, `hermes-aify.sh.in:690`).
- **redeploy.sh:** the `[ -n ] && ARR=(...)` line is safe under `set -e` (not the final command of
  its `&&` list). The `${ARR[@]+"${ARR[@]}"}` form is correct under `set -u`.
- **`installed-env-endpoint.sh`:**
  - It reads the file and never runs it.
  - It rejects an unrendered `@@` placeholder.
  - A pre-0.7 launcher with an empty value falls back to the default.
- **stamp.sh** has no `set -e`, so a failing git is non-fatal. `_build_stamp.json` is gitignored, so
  stamping does not dirty the next run. The live tree reads clean.
- **install.sh:**
  - The plugin-refresh self-copy guard compares `pwd -P` of both paths.
  - The `--client`/`--mcp-transport`/`--emit-*wrappers` value check is correct.
  - The `aify-doctor` `.cmd` shim reuses `install_windows_cmd_shim`.
  - The removed opencode and pi writers have no remaining callers.
- **deploy-delta.sh** now uses node and handles CRLF.
- **`followRunOutcome`'s `always` cannot leak an unhandled rejection:** `clearTurnBusy` catches its
  own error.
- **`comms_unsend`** local mode now requires an exact id and matching sender. **`comms_listen`**
  reports non-2xx. **`comms_dashboard`** no longer prints the key and opens the page without a shell.
  B13's `detectRuntime()` exists and cannot throw.
- **Tests sampled** drive real call sites, not copies: the notify hook (spawned script), listen,
  dashboard, `agent-for-handle`, and the redeploy test (the real redeploy.sh with a recording
  install.sh).
- **Size:** `pi-session.js` 994, `doctor.js` 822 and `doctor-predicates.js` 810 are pre-existing and
  shrank or held in 0.7.0. `install.sh` fell from 2910 to 2772 lines. `doctor-predicates.js` still
  holds several verdict subjects: service build, bridge currency, skills, proc env, host tier and
  orphans. It is a split candidate, but not new.
