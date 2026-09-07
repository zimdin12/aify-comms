# v0.6.2 whole-diff review — REVISE

Reply target: comms-tech-lead, message 1788812367208-ee876199.

## Frozen objects and evidence limits

- aify-comms: `018b1a59^..5f286d6632b727b448e92d0c1ec466c6c1e23bdd` — 42 changed paths.
- aify-env: `a993ee7^..ce9d7947218a6fa06aa1fddf9fa9d6ebeae1551b` — 14 changed paths.
- aify-wrapper: complete `2df47866bc22cee90b650a18837db5f8edc3b4e5` — 2 changed paths.

`coverage.json` enumerates every changed path, review lane, and SHA-256 of its exact reviewed bytes. All snapshot bytes were compared with the corresponding committed Git blobs. Coverage is a read/review manifest, not proof that every behavior was tested. The authentication child timed out; the parent recovered its artifacts, inspected its probes, and reran them and its focused tests before adopting findings.

The comms primary tree subsequently acquired another writer's changes to `service/api_core/terminal_controls_io.py` and a new race test. Those are NOT reviewed or approved here. The parent repeated the removal and console witnesses and Python focus in a detached worktree at the frozen SHA. No production source edits, installs, deployments, tags, restarts, or live process-control actions were performed by this review. Supplied full-suite counts and service/client deployment equality remain author-reported, not independently certified here.

## High severity — P1

### R1. A successful stop claim can still return no control

**Comms `service/api_core/terminal_controls_io.py:95-101,108-124`; new waiter/caller in `service/api_core/agent_terminal_ops.py` and `service/routers/agents/identity.py:189-204`.**

The claimant commits `status='claimed'`, then rereads the control and its terminal/agent metadata. The new removal wait sees no pending stop and permits the cascading deletion between that commit and those reads. The response is then `{ok:true, controls:[]}`. Even capturing the control alone would leave the later PID/runtime/agent/session-mode reads vulnerable.

Deterministic reproduction uses the actual removal HTTP route and actual claim function with a temporary SQLite fixture. It pauses the claimant immediately after its real commit, lets removal complete, then resumes response assembly. Observed: removal `{ok:true, agentId:'sc-removed'}` and claim `{ok:true, controls:[]}`. Positive control without deletion returns one stop. Both witnesses repeated on the detached exact SHA.

**Required:** capture the complete authorized claim payload before exposing claimed state to removal, preserving exclusive-claim correctness. A claim is still not execution acknowledgment: do not promise guaranteed shutdown from this wait. Increasing 2 seconds cannot fix this successful-claim schedule.

### R2. Ambient URL selection can send a stored key to a different endpoint

**Comms `mcp/stdio/aify-http.mjs:36-38,56-59,66-68`; `registry-credential.mjs:83-94`.**

The URL comes from legacy/current environment variables; the new fallback reads the service-name credential without checking the registry entry's endpoint against that selected URL. A stale or foreign URL therefore receives a credential previously absent from this process's HTTP path.

Safe receiver reproduction: registry endpoint `http://127.0.0.2:1`, effective endpoint a different ephemeral port, no environment key. The actual HTTP factory sent the synthetic stored key to that other receiver. Repeated with a conflicting `CLAUDE_MCP_SERVER_URL`, which wins URL precedence, with the same result. Only fake keys and local test receivers were used.

**Required:** resolve target identity, endpoint and credential as one validated object. Refuse a mismatched registry fallback rather than automatically pairing a local service secret with an arbitrary inherited destination.

### R3. Ref grammar is not secure credential-store resolution

**Comms `mcp/stdio/registry-credential.mjs:87-94`.**

The new runtime reader does `readFileSync(...,'utf8').trim()`. It bypasses aify-env's exact-name, file/root security, link, ownership/ACL, bounded byte and credential decoding checks. Moving an existing doctor reader into a default runtime authentication path expands the exposure; it does not inherit the store's safety contract.

Reproduced on this Windows filesystem: ref `mixedcase.key` read synthetic content from `MixedCase.key`; the actual aify-env reader returned `CREDENTIAL_INSECURE` for that spelling mismatch. CRLF, extra newline, embedded NUL, oversized data and invalid UTF-8 were all accepted by the new reader but refused by the actual decoder. The symlink probe was blocked by Windows EPERM, so a successful symlink exploit is NOT claimed; the missing no-follow check is a source observation. Live credential ACLs were not inspected or changed.

**Required:** reuse the secure store contract, or inject its already-validated result at the launch/composition boundary. A pathname grammar alone is insufficient.

## Medium severity — P2

### R4. The credential repair covers the claimer, not the outbound MCP HTTP owner

**Comms `aify-http.mjs:56-59`, `aify-service-endpoint.mjs` (`API_KEY = apiKeyFrom()`), `send-tools.mjs` (imports its `httpCall`).**

The two HTTP owners disagree with no environment key. A fake authenticated receiver accepted the actual aify-http request; the actual registered `comms_send` handler then received 401 through aify-service-endpoint. Supplying the synthetic environment key made both succeed. Thus the prose that every runtime component uses the repaired reader is false. Keep the narrower standalone-claimer approval separate from end-to-end messaging.

This session's native comms_send also returned 401 while inbound delivery worked. The review reply succeeded through an endpoint-checked transport using the existing secure credential resolver. That observation is NOT sufficient to attribute the native MCP client's historical 401 to this source split: its live configuration/process environment was not independently inspected.

### R5. Doctor fabricates green results from failed or unobserved evidence

**Comms `doctor.js:540-568`.**

Executing the exact gatherer with injected IO produced:
- HTTP 500 and 404 -> `serviceRequiresKey:false` -> green `no-key-required`.
- Malformed JSON or EACCES -> empty client list -> green `none-installed`.
- An active config under injected `HERMES_HOME`, without the legacy file -> green `none-installed`; only `~/.hermes/config.yaml` is searched.
- A client configured for another endpoint is judged against this service and blamed as keyless.

Use typed unknown/partial for failures, honor the actual config root, and bind each judged client to the probed service. Only a successful unauthenticated response establishes that this endpoint accepts the request without credentials.

### R6. YAML key detection repeats the false-presence class

**Comms `client-api-key-check.mjs:73-91`.**

Actual detector returns true for `# AIFY_API_KEY: old-key`, `AIFY_API_KEY: "" # no key`, `NOT_AIFY_API_KEY`, a description containing the token outside `env`, and an `aify-comms` block under the wrong root. A quoted valid entry returns null and is then discarded by the gatherer. It also checks any nonempty key rather than the effective precedence result.

Use a real config parse or an explicitly bounded parser that validates root, entry, env, key and scalar semantics; malformed/unsupported forms must remain UNKNOWN. The check earns a place for a real independent configuration question, not in its current false-green form. Key presence alone never proves authentication, and MCP configs do not prove standalone-process credentials.

### R7. Client resize subscription is unreachable

**Env `bin/aify-env-tui.mjs:102,112-115`.**

The resize registration follows the never-resolving interactive keepalive await. Exact-binary execution with fake terminal dependencies: `started=1`, `keepalives=1`, `resizeListeners=0`, no resize calls. Moving the identical registration above that await in a scratch control yielded one listener and a resize call for 80x20. The direct dashboard/daemon tests do not exercise this entrypoint. Register before waiting.

### R8. The new notice sends users into blind keyboard attachment

**Env `lib/pane-buffer.mjs:38-41,173-179`; composed ConsoleSession/dashboard path.**

“Press Enter to attach” does not invoke the separate raw-terminal attach command. It switches key forwarding on but keeps the same notice-only renderer. The real SSE -> follower -> buffer -> dashboard composition retained the notice after Enter, changed the title to “typing here”, and forwarded `hello` to the fake agent input callback. Provide real terminal takeover or direct the user to the actual separate attach command; do not imply Enter restores the display.

### R9. Painting protection is chunk-dependent and expires into executable controls

**Env `lib/pane-buffer.mjs:125-134,179-190`.**

Whole `ESC[12;40Hfragment` activates the notice. Splitting those identical bytes into `ESC[12;` and `40Hfragment` bypasses detection and returns the executable control. Independently, a correctly detected paint returns the notice at N+30000, then releases the old escape at N+30001 without any new output. Pyte confirmed both cases write at row 12, column 40 rather than the pane origin at row 3, column 44.

Track parser state across chunks and never treat elapsed silence as evidence that buffered cursor controls became safe log text. Non-SGR containment was already incomplete before this range; these findings specifically falsify the new detector/expiry protection.

### R10. Width and clipping guarantees still fail independent oracles

**Env `lib/text-width.mjs:45-76,120-135`.**

Common emoji/flags/keycaps including melting face, watch, check mark, US flag, heart presentation and keycap 1 are counted as one cell instead of two. A row admitted as 80 cells measured 115 with independent wcwidth and wrapped in pyte. The same fixture at the exact base measured 80, although that older renderer has separate surrogate-splitting debt. Several width tests ask the implementation under review to validate its own width.

Also `clipToWidth("e\\x1b[31m\\u0301x",1)` loses the combining accent while the unstyled control preserves it: measurement strips SGR before segmentation, clipping segments different bytes. Use a maintained terminal-cell model and a consistent visible-grapheme/token mapping.

### R11. The documentation corrections remain materially inaccurate

- **Wrapper `docs/REGISTRY.md:80-90`:** claims environmental purity, but `mcpEntriesFor` reads `process.env`, and strict-fragment consumers inherit that dependency. Identical synthetic registry + changed synthetic environment produced changed output. No-problem return is `""`, not null.
- **Both comms `dispatch-bridges.md` mirrors:149-155:** still promise resume/compaction auto-answer, managed-Claude/not-mid-turn gates and once-per-appearance behavior. Current `console_prompts.py:100-137` only accepts the dev-channels acknowledgment, refuses resume menus, and deduplicates per terminal/rule. `terminal_output.py:190-241` has no corresponding runtime/session/turn gates. Reinstalling wrappers is not how a service-Python change is deployed.
- **Comms README:103,106:** tier-version checks a required aify-env minimum, not comms version equality; context-window can report near-full while an agent still answers or unknown-all, not only a dead conversation. The table encourages unnecessary reset if read literally.

## Low severity — P3

- Env `daemon-view.mjs:108-113` retains its resize listener after stop; real-dashboard/fake-fetch probe observed another collection. `dashboard.mjs:369-373` collects before checking stopped.
- Env `console-view.mjs:55-56` clips away the mode indicator for long labels: watched and attached titles became byte-identical at a normal narrow pane width. Reserve indicator space first.
- Wrapper docs/test prose saying `endpointFor` was never exported is false. Git history shows introduction at `c07734f41648d2b35be2d60dc4b9894af147e0bb` and removal at `4bec3c628934ef2f475d43dcc0189b492dbc80a3`, both ancestors. Removing the stale export from current docs is correct; the history explanation is not.
- The herdr plan's on-demand-only decision conflicts with Stage 2 automatically opening panes on spawn; picker instructions also disagree. Resolve before implementation.

## Direct answers and bounded non-findings

**2.0 seconds / bulk cost:** this is an uncalibrated best-effort budget, not a proven safe threshold. Ten sequential exhausted budgets contribute 20 seconds, plus database/network/UI refresh overhead; this is calculated from configuration, not a live bulk-removal measurement. It is not a hard end-to-end deadline because query time is not bounded by that clock. Do not increase it to address R1.

**Single writer:** confirmed. At the actual route's wait boundary `db.in_transaction` was false before and after waiting; a second connection committed an update while the waiter remained active. The caller commits before the polling reads. The temporary-fixture probe does not establish production latency under load.

**Must controls disappear after removal?** Current cascade still removes them. Related session deletion and terminal-history cleanup were inspected; no new retained-control contract is approved by this patch. A durable outbox redesign would require its own custody/retention review. Precommit capture of the response data does not require retaining DB rows or changing the removal response `{ok:deleted>0}`. Preserve full target metadata, not just the control ID/body.

**`if signalled:`:** keep as an explicit cheap-query optimization, or remove for simplicity; do not claim the current tests prove it load-bearing. It is equivalent for the exercised no-terminal/no-pending fixture because the unconditional wait returns on its first zero count. That is not universal equivalence for all pre-existing terminal/control states.

**Layer/startup:** extract a neutral credential subject if shared, but keep endpoint-bound secure resolution at a composition boundary and inject the result into both HTTP consumers. Do not add an insecure store reader to another owner merely to unify names. Instrumentation confirmed two store reads without an environment key and zero with one. One fresh module-import sample was about 14 ms, but this is neither a cold filesystem benchmark nor full MCP discovery measurement. No 0.75-second discovery acceptance is claimed. Values are captured at module load, so rotation behavior must be explicitly documented/tested.

**Four self-corrections:** the auto-answer source exists, but that does not establish feature/safety parity; the guard correction is properly scoped only to its fixture; the historic Hermes config/key claim remains author-reported; source proves the installer can write API_KEY, but neither file mtime nor source proves who changed that field on the claimed date. The July move date also conflicts with the September source introduction history.

**`--shared`/attach omission:** operator README/install guidance is a defensible boundary; do not add it to every agent skill merely for symmetry. Exact scan found neither phrase across 36 tracked files in the two skill mirrors. This says nothing about whether Enter implements a real attach — R8 stands independently.

**Bridge version and binding slices:** inspected the changed heartbeat/serialization/managed-env code and tests. No additional blocking finding found there in the exercised scope. That is not a renewed global ownership-authority or deployed-fleet approval.

## Dashboard console corruption: cheaper falsification before replacing transport

A separate pre-existing consumer race was reproduced at the frozen comms SHA in `service/new_dashboard/console-actions.mjs:69-77`. While snapshot GET is pending, live seq 11 paints over base seq 10. Snapshot seq 10 then resets the screen, removing frame 11, but `lastSeq=max(...)` remains 11. A replay of 11 is now discarded. Actual production resync function with a controlled fetch/terminal produced `{screen:'BASE',lastSeq:11,lost:'LIVE11',replayedSeq11WouldBeDropped:true}`. Existing console/realtime tests still passed 40/40.

This demonstrates a specific consumption bug, NOT the root cause of the reported live scattered-character incident. PTY dimensions and presence of outputSeq do not prove cursor/mode parity or correct consumption. Raw ordered deltas are normal input for xterm; replaying an arbitrary history suffix is a different problem. Pyte's current screen projection also is not automatically a complete xterm parser/mode checkpoint.

Before streaming rendered screens, make snapshot application and live delta consumption atomic: buffer frames during fetch, apply the snapshot with its exact sequence and grid, then replay only later frames once; reject stale terminal generations. Test the actual consumer with an older snapshot and in-flight frames. For the reported incident, compare exact sequence/grid/cursor/mode state at first divergence. Server-rendered streaming remains a possible design choice, not a root-cause-derived fix from the present evidence.

## Executed verification

- Frozen comms Python focus: 27 passed, exit 0; repeated in detached tree.
- Actual stop claim/removal schedule plus positive control: 2 passed, exit 0.
- Actual wait-boundary independent writer probe: 1 passed, exit 0.
- Auth focus: 105 tests in 10 files, all exit 0, sealed child environments.
- Removing the production registry fallback in scratch: the 11-test claimer file went 10 pass / 1 fail at THE CALL SITE. This mutation IS caught; no surviving-mutant claim is made.
- Env focused suite: 185 passed, no failures/skips, exit 0; parent reran the executable entrypoint/stream/width/expiry probes and emulator oracle.
- Dashboard console/realtime focus: 40 passed, exit 0. Parent's deterministic stale-snapshot witness succeeds in reproducing data loss.
- Wrapper doc-gate mutation: 1 positive control passed and 3 expected failures, exit 1. Synthetic ambient-environment probe passed.
- Subagent doc gate executions: size ratchet 6, mirror functions 2, AST extraction 8, wrapper doc gate 4 passed. These overlap other scope; no aggregate grand total is claimed.
- Exact range diff checks passed. No full five-suite rerun, live rendering acceptance, fleet convergence, or release/tag authorization is claimed.

## Reproduction artifacts

Parent directory: `C:/Users/Administrator/AppData/Local/Temp/aify-v062-review/` — this report, coverage manifest, executable removal/transaction/resync probes, and logs from the recovered auth/env probes.

Auth exact snapshot and artifacts: `C:/Users/Administrator/auth-review-5f286d66/`.
Env exact snapshot and detailed report: `C:/Users/Administrator/AppData/Local/Temp/aify-env-review-d71o99s0/`.
Documentation detailed report and gate artifacts: `C:/Users/Administrator/aify-doc-review-evidence/`.

The initial R1 reply was delivered via run `run_1788812802236_2b8cfe8f`, verified completed with a delivered event. Delivery is a transport claim, not evidence that the recipient implemented or accepted this review.
